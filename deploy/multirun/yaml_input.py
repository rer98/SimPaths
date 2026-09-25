"""(C) Copyright 2026, by Ross Richardson

Bounded plain-data YAML decoding for MultiRun configuration imports.

@author ross richardson
"""

import math
import yaml
from yaml import events
from .schema import ConfigurationError, Limits


class _PlainLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if type(key) is not str:
                raise ConfigurationError("yaml", "mapping keys must be strings")
            if key == "<<" or key in result:
                raise ConfigurationError("yaml", "duplicate keys and merge keys are not allowed")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def check_tree(value, limits=Limits()):
    """Apply the same graph/type/size budget to form mappings as decoded YAML."""
    nodes = 0
    size = 0
    active = set()

    def visit(item, depth):
        nonlocal nodes, size
        nodes += 1
        size += 8
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise ConfigurationError("configuration", "structure exceeds the input budget")
        kind = type(item)
        if kind is str:
            if len(item) > limits.max_scalar_chars:
                raise ConfigurationError("configuration", "text value is too long")
            try:
                size += len(item.encode("utf-8"))
            except UnicodeError:
                raise ConfigurationError("configuration", "text must be valid UTF-8") from None
        elif kind is int:
            if item.bit_length() > 64:
                raise ConfigurationError("configuration", "integer exceeds 64-bit input budget")
        elif kind is float:
            if not math.isfinite(item):
                raise ConfigurationError("configuration", "numbers must be finite")
        elif kind in {list, dict}:
            identity = id(item)
            if identity in active:
                raise ConfigurationError("configuration", "recursive data is not allowed")
            active.add(identity)
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ConfigurationError("configuration", "mapping keys must be strings")
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
            active.remove(identity)
        elif kind not in {bool, type(None)}:
            raise ConfigurationError("configuration", "only plain configuration data is supported")
        if size > limits.max_bytes:
            raise ConfigurationError("configuration", "contents exceed the input budget")

    visit(value, 0)


def load_yaml(text, limits=Limits()):
    if type(text) is bytes:
        if len(text) > limits.max_bytes:
            raise ConfigurationError("yaml", "file is too large")
        try:
            text = text.decode("utf-8")
        except UnicodeError:
            raise ConfigurationError("yaml", "expected UTF-8 text") from None
    if type(text) is not str:
        raise ConfigurationError("yaml", "expected UTF-8 text")
    if len(text) > limits.max_bytes:
        raise ConfigurationError("yaml", "file is too large")
    try:
        if len(text.encode("utf-8")) > limits.max_bytes:
            raise ConfigurationError("yaml", "file is too large")
        depth = nodes = documents = 0
        # Events are streamed: reject aliases/deep input before constructing a graph.
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, events.DocumentStartEvent):
                documents += 1
                if documents > 1:
                    raise ConfigurationError("yaml", "expected one document")
            if isinstance(event, events.AliasEvent) or getattr(event, "anchor", None):
                raise ConfigurationError("yaml", "anchors and aliases are not supported")
            if getattr(event, "tag", None):
                raise ConfigurationError("yaml", "explicit tags are not supported")
            if isinstance(event, (events.MappingStartEvent, events.SequenceStartEvent)):
                depth += 1
            elif isinstance(event, (events.MappingEndEvent, events.SequenceEndEvent)):
                depth -= 1
            if isinstance(event, (events.ScalarEvent, events.CollectionStartEvent)):
                nodes += 1
            if depth > limits.max_depth or nodes > limits.max_nodes:
                raise ConfigurationError("yaml", "structure exceeds the input budget")
            if isinstance(event, events.ScalarEvent) and len(event.value) > limits.max_scalar_chars:
                raise ConfigurationError("yaml", "text value is too long")
        value = yaml.load(text, Loader=_PlainLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        # Parser messages can contain the entire offending line; do not echo it.
        raise ConfigurationError("yaml", "invalid or unsupported YAML syntax") from None
    check_tree(value, limits)
    return value
