"""(C) Copyright 2026, by Ross Richardson

Build an optional observer once, outside job execution, for an immutable model.
The scientific JAR and its source inputs are never modified.
@author ross richardson
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def file_hash(path):
    with open(path,'rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()


def observer_source():
    return Path(__file__).resolve().parents[2]/'src/main/java/simpaths/experiment/SimPathsPerformanceRun.java'


def prepare(state,model):
    from jasmine_web.batch.performance_review import root_path
    root=root_path(state)/'performance-observers'
    root.mkdir(mode=0o700,exist_ok=True)
    if root.is_symlink() or root.stat().st_mode&0o077:raise ValueError('Unsafe observer cache')
    source=observer_source()
    model_hash=file_hash(model)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    destination=root/(model_hash+'.jar')
    receipt=destination.with_suffix('.json')
    expected=dict(format='simpaths.performance-observer.v1',model=model_hash,source=source_hash,java_release=17)
    if destination.is_file() and not destination.is_symlink() and receipt.is_file():
        value=json.loads(receipt.read_text())
        if all(value.get(k)==v for k,v in expected.items()) and value.get('sha256')==file_hash(destination):
            return destination
    with tempfile.TemporaryDirectory(dir=root,prefix='.compile-') as temporary:
        work=Path(temporary);classes=work/'classes';classes.mkdir()
        result=subprocess.run(['javac','--release','17','-proc:none','-cp',str(Path(model).resolve()),'-d',str(classes),str(source)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
        if result.returncode:raise RuntimeError('Observer compilation failed; check the model API and JDK')
        built=work/'observer.jar'
        with zipfile.ZipFile(built,'w',compression=zipfile.ZIP_DEFLATED) as jar:
            for file in sorted(classes.rglob('*.class')):jar.write(file,file.relative_to(classes).as_posix())
        os.chmod(built,0o600)
        expected['sha256']=file_hash(built)
        from jasmine_web.batch.local_executor import atomic_json
        os.replace(built,destination);atomic_json(receipt,expected)
    return destination


def installed(state,model,*,model_hash=None):
    """Launch-time reads verify a small observer, never compile on a user's job."""
    from jasmine_web.batch.performance_trace import read_small
    digest=model_hash or file_hash(model)
    root=Path(state)/'performance-observers'
    try:
        value=json.loads(read_small(root,digest+'.json'))
        body=read_small(root,digest+'.jar',256*1024)
    except FileNotFoundError:return None
    if (value.get('format')!='simpaths.performance-observer.v1' or value.get('java_release')!=17 or
            value.get('model')!=digest or value.get('source')!=hashlib.sha256(observer_source().read_bytes()).hexdigest()
            or hashlib.sha256(body).hexdigest()!=value.get('sha256')):
        return None
    return body
