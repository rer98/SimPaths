"""(C) Copyright 2026, by Ross Richardson
Verify immutable full-JAR rewriting and the preparation-only class patch boundary.
@author ross richardson
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from deploy import input_compaction as builder


class PatchArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.original=self.root/'original.jar';self.output=self.root/'new.jar'
        sources=self.root/'src/main/java/simpaths/experiment';sources.mkdir(parents=True)
        for name in builder.NAMES:(sources/(name+'.java')).write_text('fictional '+name)
        self.entries={'META-INF/MANIFEST.MF':b'Manifest-Version: 1.0\n',
            'simpaths/experiment/SimPathsTrainingStartup.class':b'old preparation class',
            'simpaths/model/SimPathsModel.class':b'unchanged scientific model',
            'org/h2/Driver.class':b'unchanged database dependency','empty/':b''}
        with zipfile.ZipFile(self.original,'w',compression=zipfile.ZIP_DEFLATED) as jar:
            for name,body in self.entries.items():jar.writestr(name,body)
        self.before=self.original.read_bytes()

    def compiler(self,argv,**kwargs):
        root=Path(argv[argv.index('-d')+1])/'simpaths/experiment';root.mkdir(parents=True)
        for name in builder.NAMES:(root/(name+'.class')).write_bytes(('new '+name).encode())

    def build(self,full=True):
        with patch.object(builder,'ROOT',self.root),patch.object(builder.subprocess,'run',self.compiler):
            return builder.build(self.original,self.output,full_jar=full)

    def test_full_copy_rechecks_original_offsets_after_preparation_entry_changes(self):
        proof=self.build()
        self.assertEqual(self.original.read_bytes(),self.before);self.assertTrue(proof['scientific_entries_preserved'])
        with zipfile.ZipFile(self.output) as jar:
            for name,body in self.entries.items():
                if not builder.allowed(name):self.assertEqual(jar.read(name),body)
            self.assertEqual(jar.read('simpaths/experiment/SimPathsTrainingStartup.class'),b'new SimPathsTrainingStartup')

    def test_small_patch_contains_only_preparation_classes(self):
        self.build(full=False)
        with zipfile.ZipFile(self.output) as jar:self.assertEqual(set(jar.namelist()),{builder.PREFIX+n+'.class' for n in builder.NAMES})
        self.assertEqual(self.original.read_bytes(),self.before)

    def test_signed_release_is_refused_without_creating_replacement(self):
        with zipfile.ZipFile(self.original,'a') as jar:jar.writestr('META-INF/SIGNATURE.SF',b'signature')
        before=self.original.read_bytes()
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(self.original.read_bytes(),before);self.assertFalse(self.output.exists())

    def test_existing_destination_is_never_overwritten(self):
        self.output.write_bytes(b'keep this')
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(self.output.read_bytes(),b'keep this');self.assertEqual(self.original.read_bytes(),self.before)

    def test_unrelated_compiler_output_is_refused(self):
        def compiler(argv,**kwargs):
            self.compiler(argv,**kwargs)
            (Path(argv[argv.index('-d')+1])/'Unrelated.class').write_bytes(b'unexpected')
        with patch.object(builder,'ROOT',self.root),patch.object(builder.subprocess,'run',compiler),self.assertRaises(ValueError):
            builder.build(self.original,self.output,full_jar=True)
        self.assertFalse(self.output.exists());self.assertEqual(self.original.read_bytes(),self.before)

    def test_links_cannot_redirect_the_release_or_destination(self):
        link=self.root/'linked.jar';link.symlink_to(self.original)
        with self.assertRaises(ValueError):builder.build(link,self.output)
        self.output.symlink_to(self.original)
        with self.assertRaises(ValueError):self.build()
        self.assertEqual(self.original.read_bytes(),self.before)

if __name__=='__main__':unittest.main()
