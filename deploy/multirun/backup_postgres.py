"""(C) Copyright 2026, by Ross Richardson

PostgreSQL native dump/restore clients with credentials passed in the environment.
An explicitly selected local database container can supply its matching clients.
@author ross richardson
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .artifacts import ArtifactError


class PostgresTools:
    def __init__(self, dsn, *, container=None):
        from psycopg.conninfo import conninfo_to_dict
        self.values=conninfo_to_dict(dsn)
        self.container=container
        if container:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',container):
                raise ArtifactError('Invalid PostgreSQL client container identifier')
            result=subprocess.run(['docker','--host','unix:///var/run/docker.sock','container','inspect',container],
                capture_output=True,text=True,timeout=10)
            if result.returncode:
                raise ArtifactError('The PostgreSQL client container is unavailable')
            info=json.loads(result.stdout)[0]
            bindings=info['NetworkSettings']['Ports'].get('5432/tcp')
            if (not info['State']['Running'] or self.values.get('host') not in ('localhost','127.0.0.1') or
                    self.values.get('hostaddr', '127.0.0.1')!='127.0.0.1' or
                    bindings!=[dict(HostIp='127.0.0.1',HostPort=self.values.get('port','5432'))]):
                raise ArtifactError('Client container must be the PostgreSQL server selected by the loopback connection')
            self.values.update(host='127.0.0.1',port='5432')
        elif not all(shutil.which(tool) for tool in ('pg_dump','pg_restore')):
            raise ArtifactError('Install PostgreSQL clients or supply the selected local database client container')
        mapping=dict(dbname='PGDATABASE',user='PGUSER',password='PGPASSWORD',host='PGHOST',hostaddr='PGHOSTADDR',
            port='PGPORT',sslmode='PGSSLMODE',sslrootcert='PGSSLROOTCERT',sslcert='PGSSLCERT',sslkey='PGSSLKEY',
            sslpassword='PGSSLPASSWORD',passfile='PGPASSFILE',connect_timeout='PGCONNECT_TIMEOUT',
            application_name='PGAPPNAME',options='PGOPTIONS',target_session_attrs='PGTARGETSESSIONATTRS',
            channel_binding='PGCHANNELBINDING')
        if set(self.values)-set(mapping) or not self.values.get('dbname'):
            raise ArtifactError('Unsupported PostgreSQL client connection settings')
        if container and any(k in self.values for k in ('sslrootcert','sslcert','sslkey','passfile')):
            raise ArtifactError('Certificate and password files require native PostgreSQL clients')
        self.env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
        self.pg={mapping[k]:v for k,v in self.values.items()}
        self.pg.update(PGCONNECT_TIMEOUT='5',PGAPPNAME='simpaths-offline-backup')
        self.env.update(self.pg)

    def run(self, tool, args, *, source=None, destination=None):
        if self.container:
            command=['docker','--host','unix:///var/run/docker.sock','exec','-i']
            for key in self.pg: command += ['--env',key]
            command += [self.container,tool]
        else:
            command=[shutil.which(tool)]
        result=subprocess.run(command+list(args),env=self.env,stdin=source,
            stdout=destination if destination else subprocess.PIPE,stderr=subprocess.PIPE,timeout=3600)
        if result.returncode:
            # PostgreSQL error text can contain connection secrets and private data.
            raise ArtifactError('PostgreSQL dump or restore failed; the destination remains inactive')
        return result.stdout

    def check(self, connection):
        server=int(connection.execute('SHOW server_version_num').fetchone()['server_version_num'])//10000
        for tool in ('pg_dump','pg_restore'):
            version=self.run(tool,['--version']).decode()
            match=re.search(r'PostgreSQL\) (\d+)',version)
            if not match or int(match[1])!=server:
                raise ArtifactError('Use dump and restore clients matching the PostgreSQL server major version')
        return server

    def dump(self, path, *, schema, snapshot):
        with Path(path).open('xb') as stream:
            os.chmod(path,0o600)
            self.run('pg_dump',['--format=custom','--compress=6','--no-owner','--no-privileges',
                '--strict-names','--schema='+schema,'--snapshot='+snapshot],destination=stream)
            stream.flush(); os.fsync(stream.fileno())

    def restore(self, path):
        with Path(path).open('rb') as stream:
            self.run('pg_restore',['--single-transaction','--exit-on-error','--no-owner','--no-privileges',
                '--no-tablespaces','--dbname='+self.values['dbname']],source=stream)
