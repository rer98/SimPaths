"""(C) Copyright 2026, by Ross Richardson

Owned persistent PostgreSQL and restricted credentials for a disposable outage proof.
No existing container, volume, role, database or application configuration is used.
@author ross richardson
"""
from pathlib import Path
import re
import secrets

from deploy.multirun.proxy_rehearsal import until

DATABASE = 'jasmine_queue_test'
LABEL = 'simpaths.postgres-outage-rehearsal'
ROLE_FLAGS = ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')


def restricted_connection(settings, dsn):
    from psycopg.conninfo import conninfo_to_dict
    values = conninfo_to_dict(dsn)
    role = 'simpaths_rehearsal_'+settings['tag']
    if (not re.fullmatch('[a-f0-9]{32}',settings['tag']) or settings.get('database_role') != role
            or type(settings.get('database_port')) is not int or not 1024 <= settings['database_port'] <= 65535
            or values.get('host') != '127.0.0.1' or values.get('dbname') != DATABASE
            or values.get('user') != role or values.get('port') != str(settings['database_port'])
            or not values.get('password') or set(values)-{'host','port','dbname','user','password'}):
        raise ValueError('Only this proof\'s restricted loopback database account is permitted')


class Database:
    """Persist test data across real stops/crashes; remove only verified owned resources."""
    def __init__(self, tag, port, *, client=None):
        if not re.fullmatch('[a-f0-9]{32}',tag) or type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Invalid disposable PostgreSQL identity or port')
        import docker
        self.client = client or docker.DockerClient(base_url='unix:///var/run/docker.sock')
        self.owns_client = client is None
        self.tag,self.port = tag,port
        self.name = 'simpaths-postgres-outage-'+tag
        self.volume = self.name+'-data'
        self.role = 'simpaths_rehearsal_'+tag
        self.password,self.app_password = secrets.token_hex(32),secrets.token_hex(32)
        self.image = None; self.container_owned = self.volume_owned = False

    def dsn(self, *, admin=False):
        from psycopg.conninfo import make_conninfo
        return make_conninfo(host='127.0.0.1',port=self.port,dbname=DATABASE,
            user='postgres' if admin else self.role,password=self.password if admin else self.app_password)

    def get(self, kind):
        from docker.errors import NotFound
        try:
            value = getattr(self.client,kind).get(self.name if kind == 'containers' else self.volume)
            value.reload()
            return value
        except NotFound:
            return None  # Daemon/permission errors are deliberately not treated as absence.

    def checked_volume(self):
        if not self.volume_owned: raise ValueError('Unowned PostgreSQL volume')
        value = self.get('volumes')
        if value and (value.attrs.get('Name') != self.volume or value.attrs.get('Labels',{}).get(LABEL) != self.tag):
            raise ValueError('Refusing to operate an unrelated PostgreSQL volume')
        return value

    def checked_container(self):
        if not self.container_owned: raise ValueError('Unowned PostgreSQL container')
        value = self.get('containers')
        if value:
            data = value.attrs
            ports = data['HostConfig'].get('PortBindings') or {}
            volumes = [m for m in data['Mounts'] if m['Type'] == 'volume']
            if (data.get('Name') != '/'+self.name or data['Image'] != self.image
                    or data['Config'].get('Labels',{}).get(LABEL) != self.tag
                    or ports != {'5432/tcp':[dict(HostIp='127.0.0.1',HostPort=str(self.port))]}
                    or len(data['Mounts']) != 1 or len(volumes) != 1
                    or volumes[0].get('Name') != self.volume
                    or volumes[0].get('Destination') != '/var/lib/postgresql/data'):
                raise ValueError('Refusing to operate an unrelated or reconfigured PostgreSQL container')
            if self.checked_volume() is None:
                raise ValueError('Owned PostgreSQL container has no confirmed data volume')
        return value

    def start(self):
        if self.container_owned or self.volume_owned: raise ValueError('Test database already created')
        if self.get('containers') or self.get('volumes'):
            raise ValueError('Refusing to take ownership of an existing PostgreSQL resource')
        self.image = self.client.images.get('postgres:17-alpine').id
        if not re.fullmatch('sha256:[a-f0-9]{64}',self.image): raise ValueError('Invalid PostgreSQL image identity')
        self.volume_owned = True  # Retain ownership of uncertain create responses.
        self.client.volumes.create(name=self.volume,labels={LABEL:self.tag})
        self.checked_volume()
        self.container_owned = True
        from docker.types import LogConfig
        self.client.containers.create(self.image,name=self.name,labels={LABEL:self.tag},
            environment=dict(POSTGRES_DB=DATABASE,POSTGRES_PASSWORD=self.password),
            ports={'5432/tcp':('127.0.0.1',self.port)},
            volumes={self.volume:dict(bind='/var/lib/postgresql/data',mode='rw')},
            mem_limit='768m',nano_cpus=1_000_000_000,pids_limit=128,
            security_opt=['no-new-privileges:true'],
            log_config=LogConfig(type='json-file',config={'max-size':'2m','max-file':'1'}),
            command=['postgres','-c','shared_buffers=32MB','-c','max_wal_size=64MB',
                     '-c','min_wal_size=32MB','-c','checkpoint_timeout=1min'])
        self.checked_container().start()
        until(self.ready,seconds=90)

    def ready(self):
        import psycopg
        try:
            with psycopg.connect(self.dsn(admin=True),connect_timeout=1) as c:
                return c.execute('SELECT 1').fetchone() == (1,)
        except psycopg.OperationalError:
            return False

    def prepare_role(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.dsn(admin=True),autocommit=True) as c:
            c.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(
                sql.Identifier(self.role),sql.Literal(self.app_password)))
            c.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(sql.Identifier(DATABASE),sql.Identifier(self.role)))
            c.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(DATABASE)))
            c.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
            guard = sql.Identifier('admin_guard_'+self.tag)
            c.execute(sql.SQL('CREATE SCHEMA {}').format(guard))
            c.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(guard))
            c.execute(sql.SQL('CREATE TABLE {}.canary (value text)').format(guard))
            c.execute(sql.SQL('INSERT INTO {}.canary VALUES (%s)').format(guard),('FICTIONAL_ADMIN_ONLY',))
        return self.verify_role()

    def verify_role(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        with psycopg.connect(self.dsn(),row_factory=dict_row,connect_timeout=3) as c:
            row = c.execute('SELECT rolname,'+','.join(ROLE_FLAGS)+' FROM pg_roles WHERE rolname=current_user').fetchone()
            if row['rolname'] != self.role or any(row[f] for f in ROLE_FLAGS):
                raise AssertionError('Fixture application account has unexpected cluster privileges')
            assert not c.execute('SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)').fetchone()
        for statement in (sql.SQL('SET ROLE postgres'),sql.SQL('SELECT * FROM {}.canary').format(sql.Identifier('admin_guard_'+self.tag))):
            try:
                with psycopg.connect(self.dsn(),connect_timeout=3) as c: c.execute(statement)
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError('Restricted account accessed administrator privileges or data')
        return dict(privileges={f:row[f] for f in ROLE_FLAGS},admin_role_denied=True,admin_data_denied=True)

    def stop(self, *, crash=False):
        value = self.checked_container()
        if not value or not value.attrs['State']['Running']: raise ValueError('Owned test PostgreSQL is not running')
        if crash: value.kill(signal='SIGKILL')
        else: value.stop(timeout=10)
        value = self.checked_container()
        if not value or value.attrs['State']['Running']:
            raise RuntimeError('Test PostgreSQL stop was not confirmed')

    def restart(self):
        value = self.checked_container()
        if not value or value.attrs['State']['Running']: raise ValueError('Owned test PostgreSQL is not stopped')
        value.start()
        until(self.ready,seconds=90)
        self.checked_container()
        return self.verify_role()  # Same persisted roles and guard table must remain.

    def logs(self, path):
        value = self.checked_container()
        if value: Path(path).write_bytes(value.logs(stdout=True,stderr=True))

    def close(self):
        if self.container_owned:
            value = self.checked_container()
            if value: value.remove(force=True)
            if self.get('containers') is not None: raise RuntimeError('Test PostgreSQL container remains')
            self.container_owned = False
        if self.volume_owned:
            value = self.checked_volume()
            if value: value.remove()
            if self.get('volumes') is not None: raise RuntimeError('Test PostgreSQL volume remains')
            self.volume_owned = False
        if self.owns_client: self.client.close()
