"""(C) Copyright 2026, by Ross Richardson

Normalise draft MultiRun forms/YAML and bounded sweeps into native configurations.
No simulation, file preparation, admission or authorisation is performed here.

@author ross richardson
"""

import argparse
from dataclasses import dataclass
from decimal import Decimal, localcontext
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys
import yaml

from .schema import (
    COLLECTOR_FIELDS, ConfigurationError, Field, FIXED_MODEL, INT_MAX, Limits,
    LONG_MAX, LONG_MIN, MODEL_FIELDS, OUTPUT_CONTRACT, PROFILE_VERSION,
    REQUIRED_OUTPUT, SCHEMA_VERSION, SEED_PROFILE,
)
from .yaml_input import check_tree, load_yaml


def _mapping(value, path, allowed, required=()):
    if type(value) is not dict:
        raise ConfigurationError(path, "expected a mapping")
    if set(value) - set(allowed):
        # Never reflect or print untrusted field names in an error path.
        raise ConfigurationError(path, "unsupported field; use the documented configuration fields")
    missing = set(required) - set(value)
    if missing:
        raise ConfigurationError(path, "missing required fields: " + ", ".join(sorted(missing)))
    return value


def _identifier(value, path):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ConfigurationError(path, "expected a catalogue/artifact identifier, not a path")
    return value


def _name(value, path):
    if type(value) is not str or not value.strip() or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise ConfigurationError(path, "expected a nonempty label of at most 120 characters")
    return value


def _count(value, path, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ConfigurationError(path, f"expected an integer from 1 to {maximum}")
    return value


def _fields(values, fields, path):
    _mapping(values, path, fields)
    return {key: field.validate(values.get(key, field.default), f"{path}.{key}")
            for key, field in fields.items()}


def _settings(value, path):
    _mapping(value, path, {"model_args", "collector_args"})
    model = _fields(value.get("model_args", {}), MODEL_FIELDS, path + ".model_args")
    collector = _fields(value.get("collector_args", {}), COLLECTOR_FIELDS, path + ".collector_args")
    for key, expected in REQUIRED_OUTPUT.items():
        if collector[key] != expected:
            raise ConfigurationError(path + ".collector_args." + key,
                                     "required by the provisional annual visualiser output contract")
    return {"model_args": model, "collector_args": collector}


def _seed_plan(value, limits):
    _mapping(value, "seed_plan", {"mode", "profile", "repetitions", "first_seed"},
             {"mode", "repetitions"})
    repetitions = _count(value["repetitions"], "seed_plan.repetitions",
                         min(limits.max_repetitions, limits.max_simulations, INT_MAX))
    if value["mode"] == "standard":
        if value.get("profile", SEED_PROFILE) != SEED_PROFILE or "first_seed" in value:
            raise ConfigurationError("seed_plan", "standard mode uses the fixed standard profile")
        first = 606
    elif value["mode"] == "starting_seed":
        if "profile" in value:
            raise ConfigurationError("seed_plan", "starting_seed mode cannot specify a standard profile")
        raw = value.get("first_seed")
        if type(raw) is not str or not re.fullmatch(r"-?(0|[1-9][0-9]{0,18})", raw):
            raise ConfigurationError("seed_plan.first_seed", "expected a signed 64-bit decimal string")
        first = int(raw)
    else:
        raise ConfigurationError("seed_plan.mode", "use standard or starting_seed; lists are not supported")
    if not LONG_MIN <= first <= LONG_MAX or first + repetitions - 1 > LONG_MAX:
        raise ConfigurationError("seed_plan", "seed progression exceeds signed 64-bit range")
    return {
        "mode": value["mode"],
        **({"profile": SEED_PROFILE} if value["mode"] == "standard" else {}),
        "repetitions": repetitions, "first_seed": str(first), "increment": 1,
        "seeds": [str(first + i) for i in range(repetitions)],
    }


def _dimension(value, field, path, limits):
    if type(value) is dict:
        _mapping(value, path, {"start", "end", "step"}, {"start", "end", "step"})
        if field.kind not in {"int", "double"}:
            raise ConfigurationError(path, "ranges require a numeric scientific field")
        numbers = [field.validate(value[key], path + "." + key) for key in ("start", "end", "step")]
        with localcontext() as context:
            # Enough precision to bound/count a range of finite Java doubles.
            context.prec = 700
            start, end, step = map(lambda x: Decimal(str(x)), numbers)
            if step == 0 or (end - start) * step < 0:
                raise ConfigurationError(path, "step must move from start toward end")
            distance = (end - start) / step
            if distance >= limits.max_run_sets:
                raise ConfigurationError(path, "range exceeds the Run Set limit")
            if distance != distance.to_integral_value():
                raise ConfigurationError(path, "end must be reached exactly by start plus whole steps")
            value = [int(start + step * i) if field.kind == "int" else float(start + step * i)
                     for i in range(int(distance) + 1)]
    if type(value) is not list or not value or len(value) > limits.max_run_sets:
        raise ConfigurationError(path, "expected a nonempty bounded list of values")
    result = [field.validate(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if len(set(result)) != len(result):
        raise ConfigurationError(path, "duplicate values after numeric normalisation")
    return result


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class NormalisedExperiment:
    """Detached immutable review snapshot; reference permissions remain unchecked."""

    canonical_json: str

    @property
    def configuration_sha256(self):
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    def as_dict(self):
        return json.loads(self.canonical_json)

    def editable_configuration(self):
        """Return form/YAML data that round-trips without losing resolved values."""
        data = self.as_dict()
        result = {key: data[key] for key in (
            "schema_version", "model_release", "dataset_revision", "experiment",
            "common", "output_contract", "run_sets",
        )}
        seeds = data["seed_plan"]
        result["seed_plan"] = {"mode": seeds["mode"], "repetitions": seeds["repetitions"]}
        if seeds["mode"] == "standard":
            result["seed_plan"]["profile"] = SEED_PROFILE
        else:
            result["seed_plan"]["first_seed"] = seeds["first_seed"]
        if "sweep" in data:
            recipe = data["sweep"]
            generated = {item["run_set_id"] for item in recipe["generated"]}
            result["run_sets"] = [item for item in data["run_sets"] if item["id"] not in generated]
            result["sweep"] = {key: recipe[key] for key in ("id_prefix", "combination", "base", "parameters")}
        return result

    def editable_yaml(self):
        return yaml.safe_dump(self.editable_configuration(), sort_keys=True)

    def run_configuration(self, run_set_id):
        """Collapse inheritance to one self-contained executable configuration."""
        editable = self.editable_configuration()
        editable.pop('sweep', None)
        item = next((r for r in self.as_dict()['run_sets'] if r['id'] == run_set_id), None)
        if item is None:
            raise ConfigurationError('run_set_id', 'Run Set not found')
        editable['dataset_revision'] = item.pop('dataset_revision', editable['dataset_revision'])
        editable['common'] = item.pop('common', editable['common'])
        editable['run_sets'] = [item]
        return editable

    def native_configuration(self, run_set_id):
        data = self.as_dict()
        run_set = next((item for item in data["run_sets"] if item["id"] == run_set_id), None)
        if run_set is None:
            raise ConfigurationError("run_set_id", "Run Set not found")
        common, seeds = run_set.get("common", data["common"]), data["seed_plan"]
        return {
            "countryString": "United Kingdom", "startYear": common["start_year"],
            "endYear": common["end_year"], "popSize": common["population"],
            "maxNumberOfRuns": seeds["repetitions"], "randomSeed": int(seeds["first_seed"]),
            "executeWithGui": False, "integrationTest": False,
            "model_args": {**run_set["model_args"], **FIXED_MODEL},
            "collector_args": run_set["collector_args"],
            "innovation_args": {
                "randomSeedInnov": True, "flagDatabaseSetup": False,
                "intertemporalElasticityInnov": False, "labourSupplyElasticityInnov": False,
            },
            "parameter_args": {"trainingFlag": False},
        }

    def native_yaml(self, run_set_id):
        return yaml.safe_dump(self.native_configuration(run_set_id), sort_keys=True)


def _common(value, path):
    common = dict(_mapping(value, path,
                           {"country", "start_year", "end_year", "population"},
                           {"country", "start_year", "end_year", "population"}))
    if common["country"] != "UK":
        raise ConfigurationError(path + ".country", "this draft profile supports UK only")
    for key in ("start_year", "end_year", "population"):
        _count(common[key], path + "." + key, INT_MAX)
    if not 2011 <= common["start_year"] <= 2024:
        raise ConfigurationError(path + ".start_year", "outside this model version's 2011–2024 bounds")
    if common["end_year"] < common["start_year"]:
        raise ConfigurationError(path + ".end_year", "must not precede start_year")
    return common


def normalise(document, *, limits=Limits()):
    """Validate settings only; dataset/release resolution and preparation are separate."""
    check_tree(document, limits)
    required = {"schema_version", "model_release", "experiment", "common",
                "dataset_revision", "seed_plan", "output_contract"}
    _mapping(document, "configuration", required | {"run_sets", "sweep"}, required)
    if document["schema_version"] != SCHEMA_VERSION:
        raise ConfigurationError("schema_version", "unsupported schema version")
    if document["output_contract"] != OUTPUT_CONTRACT:
        raise ConfigurationError("output_contract", "unsupported output contract")
    experiment = _mapping(document["experiment"], "experiment", {"name"}, {"name"})
    common = _common(document["common"], "common")
    seed_plan = _seed_plan(document["seed_plan"], limits)
    run_sets, ids, fingerprints = [], set(), set()

    def add_run(identifier, name, settings, path, overrides=None):
        identifier = _identifier(identifier, path + ".id")
        name = _name(name, path + ".name")
        if identifier in ids:
            raise ConfigurationError(path + ".id", "duplicate Run Set ID")
        overrides = overrides or {}
        fingerprint = _json(dict(settings=settings, dataset=overrides.get('dataset_revision', document['dataset_revision']),
                                 common=overrides.get('common', common)))
        if fingerprint in fingerprints:
            raise ConfigurationError(path, "duplicate effective configuration; review the Run Sets")
        if len(run_sets) >= limits.max_run_sets or (len(run_sets) + 1) * seed_plan["repetitions"] > limits.max_simulations:
            raise ConfigurationError("run_sets", "expanded work exceeds the configured limits")
        ids.add(identifier)
        fingerprints.add(fingerprint)
        run_sets.append({"id": identifier, "name": name, **settings, **overrides})

    manual = document.get("run_sets", [])
    if type(manual) is not list or len(manual) > limits.max_run_sets:
        raise ConfigurationError("run_sets", "expected a bounded list")
    for i, item in enumerate(manual):
        path = f"run_sets[{i}]"
        _mapping(item, path, {"id", "name", "model_args", "collector_args", "dataset_revision", "common"}, {"id", "name"})
        settings = _settings({key: item[key] for key in ("model_args", "collector_args") if key in item}, path)
        overrides = {}
        if 'dataset_revision' in item:
            overrides['dataset_revision'] = _identifier(item['dataset_revision'], path + '.dataset_revision')
        if 'common' in item:
            overrides['common'] = _common(item['common'], path + '.common')
        add_run(item["id"], item["name"], settings, path, overrides)

    recipe = None
    if "sweep" in document:
        sweep = _mapping(document["sweep"], "sweep", {"id_prefix", "base", "parameters", "combination"},
                         {"id_prefix", "base", "parameters", "combination"})
        prefix = _identifier(sweep["id_prefix"], "sweep.id_prefix")
        if len(prefix) > 80:
            raise ConfigurationError("sweep.id_prefix", "prefix must be at most 80 characters")
        if sweep["combination"] != "all":
            raise ConfigurationError("sweep.combination", "use all combinations or explicit Run Sets")
        base = _settings(sweep["base"], "sweep.base")
        fields = {"model_args." + key: value for key, value in MODEL_FIELDS.items()}
        dimensions = _mapping(sweep["parameters"], "sweep.parameters", fields)
        if not dimensions:
            raise ConfigurationError("sweep.parameters", "choose at least one scientific field")
        expanded, count = {}, 1
        # Sorted dimension names make form and YAML ordering immaterial.
        for key in sorted(dimensions):
            expanded[key] = _dimension(dimensions[key], fields[key], "sweep.parameters." + key, limits)
            count *= len(expanded[key])
            if count + len(run_sets) > limits.max_run_sets or (count + len(run_sets)) * seed_plan["repetitions"] > limits.max_simulations:
                raise ConfigurationError("sweep", "combination count exceeds the configured workload limits")
        generated = []
        for i, combination in enumerate(itertools.product(*expanded.values()), start=1):
            model = dict(base["model_args"])
            for key, value in zip(expanded, combination):
                model[key.split(".", 1)[1]] = value
            settings = _settings({"model_args": model, "collector_args": base["collector_args"]}, "sweep.generated")
            identifier = f"{prefix}-{i:04d}"
            add_run(identifier, f"{prefix} {i}", settings, "sweep.generated")
            generated.append({"run_set_id": identifier, "values": dict(zip(expanded, combination))})
        recipe = {"version": 1, "id_prefix": prefix, "combination": "all", "base": base,
                  "parameters": expanded, "generated": generated}
    if not run_sets:
        raise ConfigurationError("run_sets", "provide at least one Run Set or parameter sweep")
    data = {
        "schema_version": SCHEMA_VERSION, "profile_version": PROFILE_VERSION,
        "model_release": _identifier(document["model_release"], "model_release"),
        "dataset_revision": _identifier(document["dataset_revision"], "dataset_revision"),
        "experiment": {"name": _name(experiment["name"], "experiment.name")},
        "common": common, "seed_plan": seed_plan, "run_sets": run_sets,
        "output_contract": OUTPUT_CONTRACT,
        "totals": {"run_sets": len(run_sets), "simulations": len(run_sets) * seed_plan["repetitions"]},
        **({"sweep": recipe} if recipe else {}),
    }
    return NormalisedExperiment(_json(data))


def normalise_yaml(text, *, limits=Limits()):
    return normalise(load_yaml(text, limits), limits=limits)


def import_native_yaml(text, *, model_release, dataset_revision, name, limits=Limits()):
    """Import the supported native subset without inheriting ambiguous launcher defaults."""
    native = load_yaml(text, limits)
    required = {"maxNumberOfRuns", "randomSeed", "startYear", "endYear", "popSize"}
    _mapping(native, "native", required | {"countryString", "executeWithGui", "integrationTest",
                                          "model_args", "collector_args", "innovation_args", "parameter_args"}, required)
    if native.get("countryString", "United Kingdom") != "United Kingdom":
        raise ConfigurationError("native.countryString", "only United Kingdom is supported")
    for key in ("executeWithGui", "integrationTest"):
        if native.get(key, False) is not False:
            raise ConfigurationError("native." + key, "must be false for this profile")
    innovations = native.get("innovation_args")
    innovations = {} if innovations is None else innovations
    fixed = {"randomSeedInnov": True, "flagDatabaseSetup": False,
             "intertemporalElasticityInnov": False, "labourSupplyElasticityInnov": False}
    _mapping(innovations, "native.innovation_args", fixed)
    for key, value in innovations.items():
        if value is not fixed[key]:
            raise ConfigurationError("native.innovation_args." + key, "conflicts with fixed-configuration repetitions")
    parameters = native.get("parameter_args")
    parameters = {} if parameters is None else parameters
    _mapping(parameters, "native.parameter_args", {"trainingFlag"})
    if parameters.get("trainingFlag", False) is not False:
        raise ConfigurationError("native.parameter_args.trainingFlag", "prepared inputs must not be replaced by training copies")
    model = native.get("model_args")
    model = {} if model is None else model
    _mapping(model, "native.model_args", set(MODEL_FIELDS) | set(FIXED_MODEL))
    model = dict(model)
    for key, fixed_value in FIXED_MODEL.items():
        if key in model and model.pop(key) is not fixed_value:
            raise ConfigurationError("native.model_args." + key, "unsupported capability or conflicting managed setting")
    collector = native.get("collector_args")
    seed = Field("long", 606).validate(native["randomSeed"], "native.randomSeed")
    document = {
        "schema_version": SCHEMA_VERSION, "model_release": model_release,
        "dataset_revision": dataset_revision, "experiment": {"name": name},
        "common": {"country": "UK", "start_year": native["startYear"],
                   "end_year": native["endYear"], "population": native["popSize"]},
        "seed_plan": {"mode": "starting_seed", "first_seed": str(seed), "repetitions": native["maxNumberOfRuns"]},
        "run_sets": [{"id": "imported", "name": name, "model_args": model,
                      "collector_args": {} if collector is None else collector}],
        "output_contract": OUTPUT_CONTRACT,
    }
    return normalise(document, limits=limits)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review draft MultiRun settings; does not prepare or run simulations")
    parser.add_argument("configuration", type=Path)
    parser.add_argument("--native", metavar="RUN_SET_ID", help="print generated native YAML for one reviewed Run Set")
    args = parser.parse_args(argv)
    try:
        with args.configuration.open("rb") as stream:
            snapshot = normalise_yaml(stream.read(Limits().max_bytes + 1))
        print(snapshot.native_yaml(args.native) if args.native else json.dumps(snapshot.as_dict(), indent=2), end="\n")
    except (ConfigurationError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
