"""(C) Copyright 2026, by Ross Richardson

Private, hash-checked file snapshots for the local MultiRun proof tools.
These functions take trusted server/maintainer paths, never client path choices.

@author ross richardson
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat


class ArtifactError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def relative_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", name):
        raise ArtifactError("Invalid artifact path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")):
        raise ArtifactError("Invalid artifact path")
    return path


def fingerprint(path, destination=None):
    """Hash an ordinary file; optionally copy the exact bytes being hashed."""
    path = Path(path)
    # Ancestors are trusted, private directories. Reject pre-existing symlinks,
    # including ancestors; O_NOFOLLOW also closes a final-component replacement.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ArtifactError("Symlink in artifact path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactError("Artifact must be an ordinary file")
        hasher = hashlib.sha256()
        output = None
        try:
            if destination is not None:
                output = Path(destination).open("xb")
                os.chmod(destination, 0o600)
            while block := source.read(1024 * 1024):
                hasher.update(block)
                if output is not None:
                    output.write(block)
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ArtifactError("Artifact changed while being read")
        finally:
            if output is not None:
                output.close()
    return {"bytes": before.st_size, "sha256": hasher.hexdigest()}


def snapshot_files(sources, destination):
    """Create a new private directory; never overwrite an existing destination."""
    names = [str(relative_name(name)) for name in sources]
    if len({name.lower() for name in names}) != len(names):
        raise ArtifactError("Duplicate artifact names")
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    try:
        manifest = {}
        for name in sorted(names):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            manifest[name] = fingerprint(sources[name], target)
        return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def inventory(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ArtifactError("Expected an artifact directory")
    result = {}
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories:
            if (Path(parent) / name).is_symlink():
                raise ArtifactError("Symlink in artifact directory")
        for name in files:
            path = Path(parent) / name
            key = path.relative_to(directory).as_posix()
            relative_name(key)
            result[key] = fingerprint(path)
    return dict(sorted(result.items()))


def verify(directory, expected):
    if inventory(directory) != expected:
        raise ArtifactError("Artifact content does not match its receipt")


def copy_verified(directory, expected, destination):
    # Verify *all* names as well as copying/hash-checking selected files. This
    # detects additions, deletions and content changes, not just matching sizes.
    verify(directory, expected)
    actual = snapshot_files({name: Path(directory) / name for name in expected}, destination)
    if actual != expected:
        shutil.rmtree(destination)
        raise ArtifactError("Artifact changed during copy")


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.chmod(path, 0o600)


def write_attribution(directory):
    (Path(directory) / "COPYRIGHT.md").write_text(
        "<!-- (C) Copyright 2026, by Ross Richardson\n"
        "Attribution for generated local MultiRun proof evidence.\n"
        "@author ross richardson\n-->\n\n"
        "# Evidence attribution\n\n(C) Copyright 2026, by Ross Richardson.\n\n"
        "@author ross richardson\n\n"
        "Generated receipts, configuration snapshots and comparison reports are attributed here.\n"
        "Model binaries, datasets, scientific outputs and third-party log contents retain their\n"
        "existing attribution and licences; this notice does not relicense them.\n",
        encoding="utf-8")
