"""(C) Copyright 2026, by Ross Richardson

SingleRun closed-output selection and streamed Docker archive safety regressions.
All model/HTTP/Docker records are fictional; no container or server is contacted.
@author ross richardson
"""
from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import tarfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
import zipfile
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
        self.closed_clients=[]
    def capture(self, *, running=True, allowed=True, stream=None, empty=False, http_error=False):
        from jasmine_web import vm_state
        versions=[dict(version=i,checksum=hashlib.sha256(Path(vm_state.__file__).with_name(n).read_bytes()).hexdigest())
            for i,n in enumerate(('vm_state_schema.sql','vm_state_002_shared_pool.sql','vm_state_003_availability.sql','vm_state_004_interest.sql'),1)]
        record=dict(id='SID',model_id='fictional',status='ready',detail=dict(container_id='container',host='172.30.0.2',backend_secret='fictional'))
        def query(statement):
            text=str(statement)
            value=versions if 'schema_version' in text else [dict(deployment_id='deployment')] if 'deployment_id' in text else ([] if empty else [record])
            return SimpleNamespace(fetchall=lambda:value,fetchone=lambda:value[0])
        from jasmine_web.session_security import network_name
        network=SimpleNamespace(reload=lambda:None,name=network_name('SID'),id='private-network',attrs=dict(
            Internal=True,Driver='bridge',Labels=dict(self.container.attrs['Config']['Labels']),
            IPAM=dict(Config=[dict(Subnet='172.30.0.0/24',Gateway='172.30.0.1')])))
        docker=SimpleNamespace(containers=SimpleNamespace(get=lambda _:self.container),networks=SimpleNamespace(get=lambda _:network),
            close=lambda:self.closed_clients.append('docker'))
        def request(url,**kwargs):
            if url.endswith('/status'): value=dict(status='running' if running else 'paused',built=True)
            elif url.endswith('current-params'): value=dict(seed=606,endYear=2026)
            else: value=dict(files=[dict(timestamp='20261001_120000'),dict(timestamp='20261002_120000')])
            return SimpleNamespace(status_code=403 if not allowed and url.endswith('export/list') else 200,content=b'{}',json=lambda:value)
        # DockerClient exposes close(), without context-manager methods. HTTPX
        # does implement the context protocol; keep each fixture faithful to it.
        with ExitStack() as stack:
            from contextlib import nullcontext
            stack.enter_context(patch('docker.DockerClient',return_value=docker))
            options=dict(side_effect=RuntimeError('Fictional HTTP client startup failure')) if http_error else dict(
                return_value=nullcontext(SimpleNamespace(get=request)))
            stack.enter_context(patch('httpx.Client',**options))
            return single.capture(SimpleNamespace(execute=query),'unused','fictional_vm',self.root,self.root,stream=stream)

    def test_empty_registry_uses_docker_sdk_close_protocol(self):
        self.assertEqual(self.capture(empty=True),dict(images=[],sessions=[]))
        self.assertEqual(self.closed_clients,['docker'])

    def test_http_client_startup_failure_closes_the_created_docker_client(self):
        with self.assertRaisesRegex(RuntimeError,'HTTP client startup'):
            self.capture(http_error=True)
        self.assertEqual(self.closed_clients,['docker'])

    def test_direct_stream_preserves_inputs_and_closed_zip_without_raw_staging(self):
        from .backup_stream import Archive
        content=io.BytesIO()
        with tarfile.open(fileobj=content,mode='w|') as tar:
            stream=Archive(tar)
            value=self.capture(stream=stream)
            stream.copy_tree(self.root,'state')
        self.assertFalse(any(p.suffix in ('.csv','.xlsx','.zip') for p in self.root.rglob('*')))
        with tarfile.open(fileobj=io.BytesIO(content.getvalue())) as tar:
            names=tar.getnames()
            self.assertTrue(any(n.endswith('/scenario.xlsx') for n in names))
            self.assertTrue(any(n.endswith('/population.csv') for n in names))
            zipped='backup/state/'+value['sessions'][0]['archives'][0]['file']
            with tar.extractfile(zipped) as incoming, zipfile.ZipFile(io.BytesIO(incoming.read())) as archive:
                self.assertEqual(archive.read('20261001_120000/csv/Person.csv'),b'closed fictional CSV')

    def test_real_find_executes_inventory_formats_and_preserves_filenames_in_both_capture_modes(self):
        """Exercise the OS argv boundary and GNU find, rather than mock its output."""
        native=self.root/'native-container'
        for source,files in self.container.files.items():
            for name,body in files.items():
                path=native/Path(source).parent.relative_to('/')/name
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
        extra=native/'app/input/alternative policy é.xlsx';extra.write_bytes(b'fictional alternative')
        self.container.files['/app/input/alternative policy é.xlsx']={extra.name:extra.read_bytes()}
        calls=[]
        def real_find(args):
            calls.append(args)
            result=subprocess.run([args[0],str(native/Path(args[1]).relative_to('/')),*args[2:]],capture_output=True)
            return SimpleNamespace(exit_code=result.returncode,output=result.stdout+result.stderr)
        self.container.exec_run=real_find
        value=self.capture()
        saved=self.root/value['sessions'][0]['directory']
        record=json.loads((saved/'capture.json').read_text())
        self.assertEqual({row['source'] for row in record['input_sources']},
            {'/app/input/scenario.xlsx','/app/input/alternative policy é.xlsx','/app/.simpaths-startup/uploads/population.csv'})
        self.assertTrue(any('-type' in call for call in calls))
        self.assertTrue(any('-type' not in call for call in calls))
        # The stream path uses the same checked inventories for ZIPs and inputs.
        shutil.rmtree(self.root/'singlerun')
        from .backup_stream import Archive
        content=io.BytesIO()
        with tarfile.open(fileobj=content,mode='w|') as tar:
            self.capture(stream=Archive(tar))
        with tarfile.open(fileobj=io.BytesIO(content.getvalue())) as tar:
            self.assertTrue(any(n.endswith('/alternative policy é.xlsx') for n in tar.getnames()))

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
        self.assertEqual(self.closed_clients,['docker','docker'])

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
