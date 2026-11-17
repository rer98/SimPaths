"""(C) Copyright 2026, by Ross Richardson
Exercise real H2 compaction, full-value preservation and refusal of unsafe inputs.
Use a local existing application JAR; never run a scientific model or Docker.
@author ross richardson
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
JAR = Path(os.environ.get('SIMPATHS_COMPACTION_TEST_JAR', HERE.parents[1]/'singlerun.jar'))
spec = importlib.util.spec_from_file_location('compaction_packager', HERE/'package_image.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class CompactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not JAR.is_file():
            raise RuntimeError('Set SIMPATHS_COMPACTION_TEST_JAR to the existing tested application JAR')
        cls.tools = tempfile.TemporaryDirectory()
        cls.toolroot = Path(cls.tools.name)
        p.compile_compactor(cls.toolroot, JAR)
        cls.cp = os.pathsep.join((str(cls.toolroot/'input-compactor.jar'), str(JAR)))

    @classmethod
    def tearDownClass(cls):
        cls.tools.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.base = self.root/'input'
        self.db = self.root/'input.mv.db'
        self.access = self.root/'query-access.json'
        self.receipt = self.root/'input-compaction.json'
        self.sql("CREATE TABLE SAMPLE(ID BIGINT PRIMARY KEY, V VARCHAR, N NUMERIC(30,9), B VARBINARY);"
                 "INSERT INTO SAMPLE VALUES(1,'£ é <plain text> '' quoted',123.456789123,X'0001ff'),(2,NULL,NULL,NULL);"
                 "CREATE SEQUENCE FUTURE_ID START WITH 73;"
                 "CREATE USER JASMINE_WEB_READER PASSWORD '';GRANT SELECT ON SAMPLE TO JASMINE_WEB_READER;")
        self.access.write_text(json.dumps(dict(format_version=1, principal='JASMINE_WEB_READER',
            privileges='SELECT on approved local base tables', source_database_sha256='a'*64,
            database_sha256=p.digest(self.db))))

    def sql(self, sql, readonly=False):
        url = 'jdbc:h2:file:'+str(self.base)+(';IFEXISTS=TRUE;ACCESS_MODE_DATA=r' if readonly else '')
        value = subprocess.run(['java', '-Xmx256m', '-cp', str(JAR), 'org.h2.tools.Shell',
            '-url', url, '-user', 'sa', '-password', '', '-sql', sql],
            capture_output=True, text=True, timeout=30, check=True)
        self.assertNotIn('Error', value.stdout + value.stderr)
        return value.stdout

    def run_helper(self, cp=None):
        return subprocess.run(['java', '-Xmx512m', '-cp', cp or self.cp,
            'simpaths.packaging.CompactInputDatabase', str(self.base), str(self.access), str(self.receipt)],
            capture_output=True, text=True, timeout=60)

    def refused(self):
        before = p.digest(self.db)
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(p.digest(self.db), before)
        self.assertFalse(self.receipt.exists())

    def test_real_compaction_preserves_values_nulls_binary_sequences_and_reader_grants(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(self.receipt.read_text())
        self.assertTrue(receipt['logical_contents_preserved'])
        self.assertLessEqual(receipt['compacted_bytes'], receipt['original_bytes'])
        self.assertEqual(p.digest(self.db), receipt['database_sha256'])
        self.assertEqual(json.loads(self.access.read_text())['database_sha256'], p.digest(self.db))
        value = self.sql("SELECT ID,V,N,RAWTOHEX(B) FROM SAMPLE ORDER BY ID; SELECT BASE_VALUE FROM INFORMATION_SCHEMA.SEQUENCES;"
                         "SELECT PRIVILEGE_TYPE FROM INFORMATION_SCHEMA.TABLE_PRIVILEGES WHERE GRANTEE='JASMINE_WEB_READER'", True)
        for text in ('123.456789123', '£ é <plain text>', '0001ff', 'null', '73', 'SELECT'):
            self.assertIn(text, value)
        self.assertFalse(list(self.root.glob('.input-compaction-*')))

    def test_wrong_query_receipt_is_refused_before_database_changes(self):
        value = json.loads(self.access.read_text());value['database_sha256']='0'*64
        self.access.write_text(json.dumps(value));self.refused()

    def test_active_or_interrupted_database_is_refused(self):
        (self.root/'input.lock.db').write_text('fixture active marker');self.refused()

    def test_linked_database_is_refused(self):
        target=self.root/'original.mv.db';self.db.rename(target);self.db.symlink_to(target);self.refused()

    def test_existing_receipt_is_preserved(self):
        self.receipt.write_text('saved outcome')
        before=p.digest(self.db);result=self.run_helper()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.receipt.read_text(),'saved outcome');self.assertEqual(p.digest(self.db),before)

    def test_equal_row_counts_cannot_hide_changed_values(self):
        # Substitute a deliberately faulty compactor; the real before/after
        # logical comparison must catch its one-cell change with the same row count.
        source=self.root/'MVStoreTool.java'
        replacement=self.root/'replacement.mv.db'
        shutil.copy2(self.db,replacement)
        original_base=self.base;self.base=self.root/'replacement'
        try:self.sql("UPDATE SAMPLE SET V='altered' WHERE ID=1")
        finally:self.base=original_base
        source.write_text('''/* (C) Copyright 2026, by Ross Richardson
Faulty compactor fixture for logical-content rejection. @author ross richardson */
package org.h2.mvstore;
public class MVStoreTool {
 public static void compactCleanUp(String file) { }
 public static void compact(String file, boolean compression) throws Exception {
  java.nio.file.Files.copy(java.nio.file.Path.of(REPLACEMENT), java.nio.file.Path.of(file),
      java.nio.file.StandardCopyOption.REPLACE_EXISTING);
 }
}'''.replace('REPLACEMENT',json.dumps(str(replacement))))
        subprocess.run(['javac','--release','17','-d',str(self.root),str(source)],check=True,timeout=30)
        before=p.digest(self.db)
        result=self.run_helper(str(self.root)+os.pathsep+self.cp)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('changed logical database contents',result.stderr)
        self.assertEqual(p.digest(self.db),before);self.assertFalse(self.receipt.exists())


if __name__ == '__main__':
    unittest.main()
