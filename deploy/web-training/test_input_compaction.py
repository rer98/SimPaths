"""(C) Copyright 2026, by Ross Richardson
Run the full H2 preservation/fault-injection checks against the runtime compactor.
@author ross richardson
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('quickstart_compaction_fixture',
    ROOT/'deploy/web-quickstart/test_input_compaction.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class RuntimeCompactionTests(fixture.CompactionTests):
    @classmethod
    def setUpClass(cls):
        cls.tools = tempfile.TemporaryDirectory()
        cls.toolroot = Path(cls.tools.name)
        subprocess.run(['javac', '-proc:none', '-cp', str(fixture.JAR), '-d', str(cls.toolroot),
            str(ROOT/'src/main/java/simpaths/experiment/SimPathsInputCompaction.java')],check=True,timeout=60)
        cls.cp = str(cls.toolroot)+os.pathsep+str(fixture.JAR)

    def run_helper(self, cp=None):
        return subprocess.run(['java','-Xmx512m','-cp',cp or self.cp,
            'simpaths.experiment.SimPathsInputCompaction',str(self.base),str(self.receipt),str(self.access)],
            capture_output=True,text=True,timeout=60)
