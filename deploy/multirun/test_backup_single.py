"""(C) Copyright 2026, by Ross Richardson

SingleRun closed-output selection and streamed Docker archive safety regressions.
All model/HTTP/Docker records are fictional; no container or server is contacted.
@author ross richardson
"""
from contextlib import ExitStack
import hashlib
import io
from pathlib import Path
import tempfile
import tarfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import backup_single as single
from .artifacts import ArtifactError
from .test_backup import IMAGE


class Container:
    def __init__(self):
        self.status='running'; self.changed=False; self.inventory_calls=0; self.member=None; self.duplicate=False
        self.attrs=dict(Image=IMAGE,Config=dict(Labels={'jasmine.deployment_id':'deployment','jasmine.session_id':'SID','jasmine.model_id':'fictional'}))
        from jasmine_web.session_security import network_name
        self.attrs['NetworkSettings']=dict(Ports={},Networks={network_name('SID'):dict(IPAddress='172.30.0.2',NetworkID='private-network')})
        self.files={
            '/app/output/20261001_120000':{'20261001_120000/csv/Person.csv':b'closed fictional CSV'},
            '/app/output/20261002_120000':{'20261002_120000/csv/Person.csv':b'mutable fictional CSV'},
            '/app/input/scenario.xlsx':{'scenario.xlsx':b'fictional workbook'},
            '/app/.simpaths-startup/uploads/population.csv':{'population.csv':b'fictional supplied population'}}
    def reload(self): pass
    def exec_run(self,args):
        if '-type' in args:
            names=b'scenario.xlsx\0' if args[1]=='/app/input' else b'population.csv\0'
            return SimpleNamespace(exit_code=0,output=names)
        self.inventory_calls+=1
        return SimpleNamespace(exit_code=0,output=b'changed' if self.changed and self.inventory_calls>1 else b'stable native inode/stat inventory')
    def get_archive(self,path,**kwargs):
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w') as archive:
            if self.member: archive.addfile(self.member)
            else:
                for name,value in self.files[path].items():
                    entry=tarfile.TarInfo(name); entry.size=len(value); archive.addfile(entry,io.BytesIO(value))
                    if self.duplicate: archive.addfile(entry,io.BytesIO(value))
        content=data.getvalue()
        return (content[i:i+513] for i in range(0,len(content),513)),{}


class SingleCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name); self.container=Container()
    def capture(self, *, running=True, allowed=True):
        from jasmine_web import vm_state
        versions=[dict(version=i,checksum=hashlib.sha256(Path(vm_state.__file__).with_name(n).read_bytes()).hexdigest())
            for i,n in enumerate(('vm_state_schema.sql','vm_state_002_shared_pool.sql'),1)]
        record=dict(id='SID',model_id='fictional',status='ready',detail=dict(container_id='container',host='172.30.0.2',backend_secret='fictional'))
        def query(statement):
            text=str(statement)
            value=versions if 'schema_version' in text else [dict(deployment_id='deployment')] if 'deployment_id' in text else [record]
            return SimpleNamespace(fetchall=lambda:value,fetchone=lambda:value[0])
        from jasmine_web.session_security import network_name
        network=SimpleNamespace(reload=lambda:None,name=network_name('SID'),id='private-network',attrs=dict(
            Internal=True,Driver='bridge',Labels=dict(self.container.attrs['Config']['Labels']),
            IPAM=dict(Config=[dict(Subnet='172.30.0.0/24',Gateway='172.30.0.1')])))
        docker=SimpleNamespace(containers=SimpleNamespace(get=lambda _:self.container),networks=SimpleNamespace(get=lambda _:network),
            __enter__=lambda self:self,__exit__=lambda *args:None)
        def request(url,**kwargs):
            if url.endswith('/status'): value=dict(status='running' if running else 'paused',built=True)
            elif url.endswith('current-params'): value=dict(seed=606,endYear=2026)
            else: value=dict(files=[dict(timestamp='20261001_120000'),dict(timestamp='20261002_120000')])
            return SimpleNamespace(status_code=403 if not allowed and url.endswith('export/list') else 200,content=b'{}',json=lambda:value)
        # Context-manager protocol uses type methods, so provide ExitStack-managed
        # patches for the real constructors rather than a fake socket/client.
        with ExitStack() as stack:
            from contextlib import nullcontext
            stack.enter_context(patch('docker.DockerClient',return_value=nullcontext(docker)))
            stack.enter_context(patch('httpx.Client',return_value=nullcontext(SimpleNamespace(get=request))))
            return single.capture(SimpleNamespace(execute=query),'unused','fictional_vm',self.root,self.root)

    def test_running_output_is_omitted_but_closed_export_settings_and_original_inputs_survive(self):
        value=self.capture()
        session=value['sessions'][0]
        self.assertTrue(session['active_output_omitted']); self.assertEqual(['20261001_120000'],[r['timestamp'] for r in session['archives']])
        self.assertEqual([IMAGE],value['images'])
        self.assertTrue((self.root/session['parameters']).exists())
        self.assertFalse(any(p.suffix=='.db' for p in self.root.rglob('*')))
        self.assertEqual(2,len(list((self.root/session['directory']/'inputs').iterdir())))

    def test_paused_exports_are_preserved_without_claiming_scientific_completion(self):
        value=self.capture(running=False)
        self.assertEqual(2,len(value['sessions'][0]['archives'])); self.assertFalse(value['sessions'][0]['active_output_omitted'])

    def test_provider_export_denial_and_wrong_model_identity_are_respected(self):
        value=self.capture(allowed=False)
        self.assertFalse(value['sessions'][0]['download_allowed']); self.assertEqual([],value['sessions'][0]['archives'])
        self.container.attrs['Config']['Labels']['jasmine.session_id']='other-owner'
        with self.assertRaises(ArtifactError): self.capture()

    def test_changed_closed_file_aborts_capture(self):
        self.container.changed=True
        with self.assertRaises(ArtifactError): single.container_tree(self.container,'/app/output/20261001_120000',self.root/'changed')

    def test_links_traversal_and_duplicate_tar_entries_cannot_escape_private_capture(self):
        for name,kind in [('20261001_120000/../../outside',tarfile.REGTYPE),('20261001_120000/link',tarfile.SYMTYPE),('/absolute',tarfile.REGTYPE)]:
            self.container.member=tarfile.TarInfo(name); self.container.member.type=kind
            with self.subTest(name=name),self.assertRaises(ArtifactError):
                single.container_tree(self.container,'/app/output/20261001_120000',self.root/str(len(list(self.root.iterdir()))))
        self.assertFalse((self.root/'outside').exists())
        self.container.member=None; self.container.duplicate=True
        with self.assertRaises(ArtifactError):
            single.container_tree(self.container,'/app/output/20261001_120000',self.root/'duplicate')
