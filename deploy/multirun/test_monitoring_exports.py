"""(C) Copyright 2026, by Ross Richardson

Offline command, private copies, MIME attachments and combined record regressions.
No PostgreSQL, Docker, network, scientific runs or actual email delivery.
@author ross richardson
"""
from contextlib import redirect_stdout,redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from deploy._workflow import frontend_path
from . import monitoring_exports as cli


class MonitoringExportCommandTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0,str(frontend_path()))
        from jasmine_web.batch import performance_archive as archive
        self.archive=archive
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.root.chmod(0o700);self.folder=self.root/'mail'
        self.folder.mkdir(mode=0o700)
        at='2026-10-20T12:00:00+00:00'
        self.event=str(uuid4())
        body=archive.canonical(dict(id=self.event,subject=str(uuid4()),kind='host',event='sample',
            session_id=None,observed_at=at,data=dict(working_memory_bytes=1024)))
        row=dict(dataset='performance_trace',key=archive.canonical([self.event]),revision=1,captured_at=at,
            payload=body,sha256=hashlib.sha256(body.encode()).hexdigest())
        self.manifest,self.parts=archive.build_parts([row],origin=str(uuid4()),epoch=str(uuid4()),
            parent=None,parent_revision=None,batch=str(uuid4()),pool='fictional')
        self.name=archive.filename(self.manifest,1)

    def arguments(self):
        return ['--frontend',str(frontend_path()),'merge','--folder',str(self.folder),
            '--database',str(self.root/'combined.sqlite'),'--html',str(self.root/'report.html'),'--csv',str(self.root/'record.csv')]

    def test_copy_is_private_verified_and_does_not_overwrite_conflicting_files(self):
        with self.assertRaises(ValueError):cli.save_part(self.folder,'../outside.json.gz',self.parts[0])
        cli.save_part(self.folder,self.name,self.parts[0])
        cli.save_part(self.folder,self.name,self.parts[0])
        self.assertEqual((self.folder/self.name).stat().st_mode&0o777,0o600)
        (self.folder/self.name).write_bytes(b'bad')
        with self.assertRaises(ValueError):cli.save_part(self.folder,self.name,self.parts[0])
        self.assertEqual((self.folder/self.name).read_bytes(),b'bad')

    def test_offline_merge_is_repeatable_and_flags_bad_additional_mail(self):
        cli.save_part(self.folder,self.name,self.parts[0])
        with patch('socket.socket',side_effect=AssertionError('Offline')),redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(self.arguments()),0)
            self.assertEqual(cli.main(self.arguments()),0)
        c=self.archive.connect(self.root/'combined.sqlite')
        try:self.assertEqual(self.archive.completeness(c)['observations'],1)
        finally:c.close()
        (self.folder/'damaged.json.gz').write_bytes(b'not a valid archive')
        with redirect_stdout(io.StringIO()):self.assertEqual(cli.main(self.arguments()),2)
        self.assertIn('rejected_attachment',(self.root/'report.html').read_text())
        self.assertIn('Working RAM GiB',(self.root/'report.html').read_text())

    def test_live_controls_need_no_database_and_no_running_jobs_change(self):
        from jasmine_web.batch import performance_exports as exports
        args=['--frontend',str(frontend_path()),'--state',str(self.root)]
        with patch('socket.socket',side_effect=AssertionError('Control files only')),redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args+['enable','--archive-seconds','60']),0)
            self.assertTrue(exports.control(self.root)['enabled'])
            self.assertEqual(cli.main(args+['disable']),0)
            self.assertFalse(exports.control(self.root)['enabled'])
        with redirect_stderr(io.StringIO()):self.assertEqual(cli.main(args+['enable','--mail']),1)

    def test_existing_smtp_sender_preserves_numbered_compressed_attachments(self):
        from jasmine_web.batch.performance_exports import smtp_sender
        sent=[]
        with patch('jasmine_web.contact.deliver_email',side_effect=sent.append):
            smtp_sender('service@example.invalid')('admin@example.invalid','A record','Summary',[(self.name,self.parts[0])],'a'*64)
        self.assertEqual(len(sent),1)
        msg=sent[0];attachment=list(msg.iter_attachments())[0]
        self.assertEqual(attachment.get_filename(),self.name)
        self.assertEqual(attachment.get_payload(decode=True),self.parts[0])
        self.assertEqual(msg['To'],'admin@example.invalid')
        self.assertIn('a'*64,str(msg['Message-ID']))


if __name__=='__main__':unittest.main()
