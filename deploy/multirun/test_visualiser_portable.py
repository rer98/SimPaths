"""(C) Copyright 2026, by Ross Richardson

Cross-repository ZIP-to-chart proof using real export, local reads and compiled UI.
Fictional aggregate sections only; no native sockets, Docker or scientific model.
@author ross richardson
"""
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from deploy._workflow import frontend_path

BUILD=os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD')
DEPENDENCIES=os.environ.get('SIMPATHS_VISUALISER_DEPENDENCIES')


@unittest.skipUnless(BUILD and DEPENDENCIES and shutil.which('node'),'Set a candidate build and reviewed JS dependencies')
class PortableRoundTripTests(unittest.TestCase):
    def test_real_server_zip_reopens_without_network_and_preserves_chart_csvs(self):
        frontend=frontend_path()
        with tempfile.TemporaryDirectory(prefix='simpaths-portable-test-') as temporary:
            root=Path(temporary);(root/'source').mkdir(mode=0o700)
            old=list(sys.path);sys.path.insert(0,str(frontend))
            try:
                spec=importlib.util.spec_from_file_location('portable_export_fixture',frontend/'tests/test_visualiser_export.py')
                fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
                source=fixture.Fixture(root/'source')
                stream=fixture.export.archive(source,'owner',source.key)
                try:body=b''.join(stream.iterator)
                finally:stream.close()
            finally:sys.path[:]=old
            with zipfile.ZipFile(io.BytesIO(body)) as saved:
                self.assertTrue(all('..' not in Path(n).parts and not n.startswith('/') for n in saved.namelist()))
                saved.extractall(root/'saved')
            folder=next((root/'saved').iterdir())
            result=subprocess.run(['node',str(Path(__file__).parent/'visualiser/test_portable_bundle.cjs')],
                env={**os.environ,'SIMPATHS_PORTABLE_TEST_FOLDER':str(folder)},
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout)


if __name__=='__main__':unittest.main()
