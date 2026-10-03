"""(C) Copyright 2026, by Ross Richardson

Native HTTPS-proxied MultiRun host using persistent operator-managed PostgreSQL.
No local database bootstrap, console verification codes or laptop deadline credit.
@author ross richardson
"""
import argparse
from email.message import EmailMessage
import os
from pathlib import Path
import stat
import sys

from .vm_config import load_config


def private_text(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Credential paths must not contain symlinks')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077 or info.st_size > 8192 or info.st_nlink != 1):
            raise ValueError('Credential file must be private, bounded and owned by the service user')
        value = source.read(8193).strip()
    if not value or '\x00' in value or '\n' in value or '\r' in value:
        raise ValueError('Credential file must contain one nonempty line')
    return value


def check_smtp(env):
    from jasmine_web.batch.access import email_address
    if not env.get('SMTP_HOST', '').strip() or any(c in env['SMTP_HOST'] for c in '\n\r\x00'):
        raise ValueError('VM sign-in requires SMTP_HOST')
    email_address(env.get('SMTP_FROM_EMAIL', ''))
    if env.get('SMTP_USE_TLS', 'true').lower() != 'true':
        raise ValueError('VM sign-in requires SMTP STARTTLS')
    port = int(env.get('SMTP_PORT', '587'))
    if not 1 <= port <= 65535 or bool(env.get('SMTP_LOGIN_EMAIL')) != bool(env.get('SMTP_PASSWORD')):
        raise ValueError('Invalid SMTP port or incomplete SMTP login')


async def verification_mail(email, code):
    from jasmine_web.contact import deliver_email
    from jasmine_web.batch.access import email_address
    message = EmailMessage()
    message['From'] = email_address(os.environ['SMTP_FROM_EMAIL'])
    message['To'] = email_address(email)
    message['Subject'] = 'Your SimPaths Online sign-in code'
    message.set_content(f'Your sign-in code is {code}. It expires in 10 minutes.\n'
                        'If you did not request this code, ignore this message.\n')
    # Access.issue runs this coroutine in the existing HTTP thread pool.
    deliver_email(message)


def check_storage(options):
    for path in (options.private_root, options.state, options.frontend, *options.prepared):
        if not path.is_dir() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Install real storage/source directories without symlink ancestors')
    for path in (options.private_root, options.state):
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError('Private root and state must be mode 0700 and service-user owned')
    if options.state.stat().st_dev != options.private_root.stat().st_dev:
        raise ValueError('State must use the configured private filesystem')
    if options.dedicated_storage and (not options.private_root.is_mount() or
            options.private_root.stat().st_dev == Path('/').stat().st_dev):
        raise ValueError('Dedicated storage is required: mount private storage outside the OS filesystem')


def database_dsn(options):
    from psycopg.conninfo import conninfo_to_dict
    dsn = private_text(options.dsn_file)
    try:
        values = conninfo_to_dict(dsn)
        host = values.get('host', '')
        address = values.get('hostaddr', host)
        if (not values.get('dbname') or not values.get('user') or ',' in host or ',' in address or
                address not in ('', 'localhost', '127.0.0.1', '::1') and not address.startswith('/') and
                (values.get('sslmode') != 'verify-full' or not host) or
                host not in ('', 'localhost', '127.0.0.1', '::1') and not host.startswith('/') and
                values.get('sslmode') != 'verify-full'):
            raise ValueError()
    except Exception:
        raise ValueError('Use a private PostgreSQL connection with a database/user and verified TLS for a remote host') from None
    return dsn


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'preflight', 'serve', 'approve', 'revoke', 'status'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--email')
    parser.add_argument('--json',action='store_true',help='JSON output for status')
    parser.add_argument('--limit',type=int,default=20,help='Maximum job details for status')
    args = parser.parse_args(argv)
    if args.command=='status':
        from .operator_status import main as status
        return status(['--config',str(args.config),'--limit',str(args.limit)]+(['--json'] if args.json else []))
    try:
        options = load_config(args.config)
        if args.command == 'check':
            print('VM configuration is valid. No resources were contacted or changed.')
            return 0
        sys.path.insert(0, str(options.frontend.resolve(strict=True)))
        os.umask(0o077)
        from jasmine_web.batch.store import Queue
        from jasmine_web.batch.policy import Resources
        check_storage(options)
        q = Queue(database_dsn(options), options.pool_id)
        if args.command == 'preflight':
            check_smtp(os.environ)
            from jasmine_web.batch.docker_executor import DockerCLI
            import json
            from .releases import ReleaseRegistry
            registry=ReleaseRegistry(options.state)
            images={options.image}
            if registry.catalogue.exists() or (options.state/'release'/'release.json').exists():
                images.update(r['image'] for r in registry.load(expected_image=options.image).values())
            for identity in images:
                image = json.loads(DockerCLI().call('image', 'inspect', identity))[0]
                if image['Id'] != identity or image['Config'].get('Volumes'):
                    raise ValueError('Install all retained pinned model images without implicit volumes')
            with q._connection() as connection:
                connection.execute('SELECT 1')
            print('Storage, SMTP settings, pinned image and PostgreSQL connection checked. No mail was sent or jobs created.')
            return 0
        options.command, options.email = args.command, args.email
        if args.command == 'serve':
            check_smtp(os.environ)
            from jasmine_web.batch.access import email_address
            if options.admin_email:
                email_address(options.admin_email)
        q.migrate()
        from .runtime import create_application, create_registry
        access, datasets, preparations, keys = create_registry(options, q, options.state, verification_mail,
            capacity=Resources(**options.capacity), per_user_active=options.per_user_active)
        if args.command != 'serve':
            return 0
        # Registration/default selection is an explicit operator action. The
        # registry checks this image and retains every older queued release.
        app = create_application(options, q, options.state, access, datasets, preparations, keys,
            options.image, options.origin, terminate_on_dispatch_failure=True)
        import uvicorn
        print('SimPaths Online VM service: '+options.origin+'; loopback listener only.', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=options.port, workers=1,
                    proxy_headers=True, forwarded_allow_ips='127.0.0.1', access_log=False)
        return 0
    except Exception as error:
        # Connection errors can contain credentials; never print their text.
        if isinstance(error, (ValueError, FileNotFoundError)):
            print('VM setup failed: '+str(error), file=sys.stderr)
        else:
            print('VM setup failed: '+type(error).__name__+'. Check private operator logs and configuration.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
