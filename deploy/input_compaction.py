"""(C) Copyright 2026, by Ross Richardson
Build a narrowly scoped preparation patch against an existing scientific release.
The full-JAR variant preserves every other entry byte-for-byte for MultiRun.
@author ross richardson
"""
import argparse
from copy import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('SimPathsInputCompaction', 'SimPathsTrainingStartup', 'SimPathsUserDataPreparation')
PREFIX = 'simpaths/experiment/'


def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()


def allowed(name):
    return any(name == PREFIX+n+'.class' or name.startswith(PREFIX+n+'$') and name.endswith('.class') for n in NAMES)


def build(original, output, *, full_jar=False):
    original, output = Path(original).absolute(), Path(output).absolute()
    for path in (original,output):
        if any(p.is_symlink() for p in (path,*path.parents)): raise ValueError('Linked release path')
    if output.exists(): raise ValueError('Use a new candidate JAR; retain the original release')
    before = digest(original)
    sources = [ROOT/'src/main/java'/PREFIX/(n+'.java') for n in NAMES]
    source_hashes = {str(p.relative_to(ROOT)):digest(p) for p in sources}
    with tempfile.TemporaryDirectory(prefix='compaction-classes-',dir=output.parent) as temporary:
        classes = Path(temporary)
        subprocess.run(['javac','-proc:none','-implicit:none','-sourcepath','', '-cp',str(original),
                        '-d',str(classes),*map(str,sources)],check=True,capture_output=True,timeout=60)
        compiled = {p.relative_to(classes).as_posix():p.read_bytes() for p in classes.rglob('*.class')}
        if (not {PREFIX+n+'.class' for n in NAMES} <= compiled.keys()
                or not all(allowed(name) for name in compiled)):
            raise ValueError('Unexpected preparation patch classes')
        with zipfile.ZipFile(original) as old:
            entries = old.namelist()
            if len(entries) != len(set(entries)) or any(name.upper().endswith(('.SF','.RSA','.DSA','.EC')) for name in entries):
                raise ValueError('Review duplicate or signed JAR entries before patching')
            try:
                with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as new:
                    if full_jar:
                        for entry in old.infolist():
                            if not allowed(entry.filename): new.writestr(copy(entry),old.read(entry))
                    for name,body in sorted(compiled.items()): new.writestr(name,body)
                with zipfile.ZipFile(output) as new:
                    if full_jar:
                        for name in entries:
                            if not allowed(name) and old.read(name) != new.read(name):
                                raise ValueError('An unrelated scientific release entry changed')
                    if digest(original) != before or source_hashes != {str(p.relative_to(ROOT)):digest(p) for p in sources}:
                        raise ValueError('Inputs changed while building the preparation patch')
            except BaseException:
                output.unlink(missing_ok=True)
                raise
    return dict(passed=True, source_jar_sha256=before,candidate_jar_sha256=digest(output),
        full_jar=full_jar, sources=source_hashes,
        classes={name:hashlib.sha256(body).hexdigest() for name,body in sorted(compiled.items())},
        scientific_entries_preserved=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--full-jar',action='store_true')
    args=parser.parse_args()
    print(json.dumps(build(args.original,args.output,full_jar=args.full_jar),indent=2))
