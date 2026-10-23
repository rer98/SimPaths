#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Qualify fresh PostgreSQL initialization with the production role SQL.
Uses one installed image, an isolated memory-backed database and fictional rows.
No ports, network access, scientific simulations or real credentials are used.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import uuid

ROLE_QUERY="""SELECT json_build_object('current_user',current_user,
    'superuser',r.rolsuper,'create_db',r.rolcreatedb,'create_role',r.rolcreaterole,
    'replication',r.rolreplication,'bypass_rls',r.rolbypassrls,
    'owns_database',(SELECT datdba=r.oid FROM pg_database WHERE datname=current_database()))
    FROM pg_roles r WHERE r.rolname=current_user;"""
EXPECTED_ROLE=dict(current_user='simpaths_online',superuser=False,create_db=False,
    create_role=False,replication=False,bypass_rls=False,owns_database=True)


class Commands:
    def __init__(self,output):self.output=output

    def __call__(self,*args,input=None,environment=None,allowed=(0,),timeout=60):
        env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
        env['DOCKER_HOST']='unix:///var/run/docker.sock'
        if environment:env.update(environment)
        result=subprocess.run(list(map(str,args)),input=input,capture_output=True,env=env,timeout=timeout)
        with (self.output/'commands.log').open('a') as stream:
            stream.write(json.dumps(list(map(str,args)))+'\n')
            stream.write(result.stdout.decode(errors='replace'))
            stream.write(result.stderr.decode(errors='replace'))
            stream.write('\nexit='+str(result.returncode)+'\n')
        if result.returncode not in allowed:raise RuntimeError('PostgreSQL role proof command failed')
        return result.stdout.decode()


def execute(image,init_sql,output,*,command=None):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
        raise ValueError('Select an immutable, already installed image ID')
    init_sql=Path(init_sql).resolve(strict=True)
    output=Path(output).absolute()
    output.mkdir(mode=0o700)
    run=command or Commands(output)
    inspected=json.loads(run('docker','image','inspect',image))[0]
    env=dict(value.split('=',1) for value in inspected['Config'].get('Env',[]) if '=' in value)
    if inspected['Id']!=image or inspected.get('Os')!='linux' or env.get('PG_MAJOR')!='17':
        raise ValueError('Select the reviewed PostgreSQL 17 image')
    password=secrets.token_urlsafe(48)
    secret=output/'fictional-password'
    fd=os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(password+'\n')
    name='simpaths-postgres-role-proof-'+uuid.uuid4().hex
    def sql(query,*,user='simpaths_online',tcp=True,allowed=(0,),credential=password):
        return run('docker','exec','-i','--env','PGPASSWORD',name,'psql','-X',
            '--no-psqlrc','--no-align','--tuples-only','--set=ON_ERROR_STOP=1',
            *(['--host=127.0.0.1'] if tcp else []),
            '--username='+user,'--dbname=jasmine_multirun',input=query.encode(),
            environment={'PGPASSWORD':credential},allowed=allowed).strip()
    created=False
    try:
        run('docker','create','--name',name,'--label','org.simpaths.postgres-role-proof=true',
            '--network','none','--memory','512m','--memory-swap','512m','--cpus','1',
            '--pids-limit','128','--shm-size','64m','--security-opt','no-new-privileges',
            '--log-driver','local','--log-opt','max-size=1m','--log-opt','max-file=2',
            '--env','POSTGRES_USER=postgres','--env','POSTGRES_DB=jasmine_multirun',
            '--env','POSTGRES_PASSWORD_FILE=/run/secrets/fictional_password',
            '--env','POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=trust',
            '--mount','type=bind,source='+str(secret)+',target=/run/secrets/fictional_password,readonly',
            '--mount','type=bind,source='+str(init_sql)+',target=/docker-entrypoint-initdb.d/01-service-role.sql,readonly',
            '--tmpfs','/var/lib/postgresql/data:rw,noexec,nosuid,nodev,size=268435456',image)
        created=True
        run('docker','start',name)
        deadline=time.monotonic()+60
        while True:
            # The entrypoint's bootstrap server accepts Unix sockets only.
            ready=run('docker','exec',name,'pg_isready','--host=127.0.0.1',
                '--username=simpaths_online','--dbname=jasmine_multirun',allowed=(0,1,2))
            if 'accepting connections' in ready:break
            state=json.loads(run('docker','inspect','--format','{{json .State}}',name))
            if not state.get('Running') or time.monotonic()>deadline:
                run('docker','logs','--tail','80',name)
                raise RuntimeError('Fresh PostgreSQL initialization did not complete')
            time.sleep(.25)
        role=json.loads(sql(ROLE_QUERY))
        if role!=EXPECTED_ROLE:raise ValueError('Application role is not restricted or does not own its database')
        bootstrap=json.loads(sql("""SELECT json_build_object('name',rolname,'superuser',rolsuper,
            'login',rolcanlogin,'has_password',rolpassword IS NOT NULL) FROM pg_authid WHERE oid=10;""",
            user='postgres',tcp=False))
        if bootstrap!=dict(name='postgres',superuser=True,login=True,has_password=False):
            raise ValueError('Bootstrap role must retain superuser status without a TCP password')
        sql('CREATE SCHEMA fictional; CREATE TABLE fictional.proof(value integer); INSERT INTO fictional.proof VALUES (42);')
        if sql('SELECT value FROM fictional.proof;')!='42':raise ValueError('Application cannot use its own schema')
        sql('SELECT 1;',credential='deliberately-incorrect-fictional-password',allowed=(2,))
        sql('SELECT 1;',user='postgres',allowed=(2,))
        report=dict(status='passed',image_id=image,init_sql_sha256=hashlib.sha256(init_sql.read_bytes()).hexdigest(),
            application_role=role,bootstrap=bootstrap,wrong_password_rejected=True,
            bootstrap_tcp_rejected=True,application_schema_read_write=True,network='none',
            data_storage='disposable_tmpfs',credentials_retained=False)
    finally:
        if created:
            info=json.loads(run('docker','inspect',name))[0]
            if info.get('Config',{}).get('Labels',{}).get('org.simpaths.postgres-role-proof')!='true':
                raise RuntimeError('Disposable container identity changed')
            run('docker','rm','--force','--volumes',name)
        secret.unlink()
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--init-sql',type=Path,default=Path(__file__).resolve().parents[1]/'multirun/vm/postgres-init.sql')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    execute(args.image,args.init_sql,args.output)
    print('PASSED: '+str(args.output/'report.json'))


if __name__=='__main__':main()
