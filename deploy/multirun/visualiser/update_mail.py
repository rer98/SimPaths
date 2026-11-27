"""(C) Copyright 2026, by Ross Richardson
Deliver pending administrator notices even while the MultiRun executor is down.
@author ross richardson
"""
import os
from pathlib import Path
import sys

from ..vm_config import load_config
from ..privilege_config import trusted_installation


def main():
    config = Path('/etc/simpaths-online/multirun.toml')
    trusted_installation(config)
    options = load_config(config)
    if not options.notification_emails or not options.administration['enabled']:
        return
    trusted_installation(options.frontend,directory=True)
    sys.path.insert(0,str(options.frontend))
    from ..vm_web import database_dsn
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.notifications import smtp_sender
    from jasmine_web.batch.visualiser_updates import deliver_notice
    queue = Queue(database_dsn(options),options.pool_id)
    sender = smtp_sender(os.environ['SMTP_FROM_EMAIL'])
    for _ in range(20):
        if not deliver_notice(queue,options.administration['emails'],sender,options.origin):
            break
