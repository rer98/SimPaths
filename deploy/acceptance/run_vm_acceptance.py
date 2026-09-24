#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Validate isolated VM Compose startup, capacity, private networks and browser flows.

@author ross richardson
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.parse import unquote_plus

import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
workflow = load_tool('_workflow.py')

browser_tools = load_tool('acceptance/run_browser_acceptance.py')
free_port = browser_tools.free_port
assert_private_network = browser_tools.assert_private_network
remove_test_networks = browser_tools.remove_test_networks


def new_project_name():
    # Keep disposable Compose resources recognisable; classification uses labels.
    return 'vm-acceptance-' + secrets.token_hex(6)


def isolated_config(template, work, web_port, redis_port):
    """Retain production settings, substituting only local test resources."""
    import copy
    config = copy.deepcopy(template)
    redis = config['services']['redis']
    redis['ports'] = [f'127.0.0.1:{redis_port}:6379']
    web = config['services']['web']
    web['build']['context'] = str(work)
    web['env_file'] = [str(work/'deploy/.env')]
    web['environment']['REDIS_URL'] = f'redis://127.0.0.1:{redis_port}/0'
    web['command'] = ['uvicorn', 'acceptance_server:app', '--host', '127.0.0.1',
                      '--port', str(web_port), '--workers', '1']
    web['volumes'] = ['/var/run/docker.sock:/var/run/docker.sock',
                      f'{work}/models.json:/app/models.json:ro',
                      f'{work}/acceptance_server.py:/app/acceptance_server.py:ro']
    return config


def capacity_checks(base, model_id, docker_client):
    import httpx
    def launch(client):
        response = client.post('/launch', data={'model_key': model_id})
        assert response.status_code == 303, response.text
        target = response.headers['location']
        assert target.startswith('/sim/'), unquote_plus(target)
        return target.rsplit('/', 1)[-1]
    def ready(client, sid):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = client.get('/session-state/'+sid)
            response.raise_for_status()
            state = response.json()
            if state['status'] == 'ready':
                return
            assert state['status'] == 'starting', state
            time.sleep(0.5)
        raise TimeoutError('Session did not become ready')
    def leave(client, sid):
        response = client.post('/leave/'+sid)
        assert response.status_code == 303 and response.headers['location'] == '/', response.text
    with httpx.Client(base_url=base, timeout=180, headers={'Origin': base}) as first, httpx.Client(base_url=base, timeout=180, headers={'Origin': base}) as second:
        sid = launch(first)
        denied = second.post('/launch', data={'model_key': model_id})
        assert denied.status_code == 303 and 'Server busy' in unquote_plus(denied.headers['location']), denied.text
        ready(first, sid)
        containers = docker_client.containers.list(filters={'label': f'jasmine.model_id={model_id}'})
        assert len(containers) == 1, len(containers)
        network = assert_private_network(containers[0], docker_client)
        leave(first, sid)
        assert not docker_client.networks.list(filters={'id': network['network_id']}), 'Session network was not removed'
        replacement = launch(second)
        ready(second, replacement)
        replacement_container = docker_client.containers.list(filters={'label': f'jasmine.session_id={replacement}'})[0]
        replacement_network = assert_private_network(replacement_container, docker_client)
        leave(second, replacement)
        assert not docker_client.networks.list(filters={'id': replacement_network['network_id']})
        return {'limit': 1, 'busy_response': True, 'leave_releases_slot': True,
                **network, 'networks_removed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=workflow.frontend_path())
    parser.add_argument('--image', help='Quick Start 20,000 image; defaults to the catalogue')
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--catalogue', type=Path, help='Catalogue for default image selection')
    args = parser.parse_args()
    args.frontend = args.frontend.expanduser().resolve()
    args.image = workflow.quickstart_image(args.frontend, 20000, args.image, args.catalogue)
    import docker
    import httpx
    import yaml
    # Preflight before creating test resources.
    subprocess.run(['docker', 'compose', 'version'], check=True)
    client = docker.from_env()
    client.ping()
    image = client.images.get(args.image)
    if client.containers.list(filters={'label': 'jasmine.created_at'}):
        raise RuntimeError('Other JAS-mine sessions are running. Finish/Leave them before this test; none were changed.')
    frontend = args.frontend.resolve()
    out = (args.output or Path.home()/'simpaths-benchmarks'/dt.datetime.now().strftime('vm-acceptance-%Y%m%d-%H%M%S')).resolve()
    out.mkdir(parents=True, exist_ok=False)
    out.chmod(0o700)
    (out/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n\nGenerated VM acceptance evidence and configuration.\n\n@author ross richardson\n-->\n')
    root = Path('/tmp/codex-rer')
    root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='vm-acceptance-', dir=root))
    project = new_project_name()
    model_id = 'simpaths-acceptance-' + secrets.token_hex(6)
    report = {'status': 'running', 'image': args.image, 'image_id': image.id, 'checks': [],
              'project': project, 'model_id': model_id,
              'limitations': ['HTTP only; no TLS/remote SSH verification',
                              'Host-wide orphan sweep disabled to protect unrelated sessions',
                              'Loopback publication checked; external firewall not tested'],
              'frontend_revision': subprocess.check_output(['git','-C',str(frontend),'rev-parse','HEAD'],text=True).strip(),
              'frontend_status': subprocess.check_output(['git','-C',str(frontend),'status','--short'],text=True)}
    compose = ['docker','compose','-p',project,'-f',str(work/'compose.json')]
    log = (out/'compose.log').open('w')
    started = False
    try:
        tracked = subprocess.check_output(['git','-C',str(frontend),'ls-files','-z']).decode().split('\0')
        for name in tracked:
            source = frontend/name
            if not name or source.is_symlink() or not source.is_file():
                continue
            if not (source.suffix == '.py' or name.startswith('static/') or name == 'Dockerfile' or name.startswith('requirements')):
                continue
            target = work/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (work/'models.json').write_text(json.dumps({'models': [{
            'id': model_id, 'name':'SimPaths UK Quick Start - 20,000 people', 'requiresAuth':False,
            'icon':'fa-chart-line', 'color':'#26734d', 'description':'Isolated Compose acceptance test',
            'deployment':{'image': args.image,'java_heap_gib':2,'memory':'4Gi','cpu':2,
                'session_storage':{'allowance_gib':10,'warning_free_gib':3,'build_reserve_gib':2,'cleanup_enabled':True}}
        }]}))
        web_port, redis_port = free_port(), free_port()
        while redis_port == web_port:
            redis_port = free_port()
        (work/'deploy').mkdir()
        env = work/'deploy/.env'
        env.write_text('# (C) Copyright 2026, by Ross Richardson\n# Temporary local acceptance settings.\n# @author ross richardson\nDEPLOY_MODE=vm\nVM_SECURITY_MODE=development\nCOOKIE_SECURE=false\nENABLE_HSTS=false\nVM_MAX_SESSIONS=1\n' +
            ''.join(f'{key}={secrets.token_urlsafe(32)}\n' for key in ('SESSION_SECRET','ADMIN_PASSWORD','HARD_RESET_SECRET')))
        env.chmod(0o600)
        (work/'acceptance_server.py').write_text('''# (C) Copyright 2026, by Ross Richardson
# Test entrypoint: isolated Redis provides independent cleanup ownership.
# @author ross richardson
from app import app
''')
        config = isolated_config(yaml.safe_load((frontend/'deploy/compose.vm.yaml').read_text()), work, web_port, redis_port)
        (work/'compose.json').write_text(json.dumps(config))
        # Save the template and non-secret generated configuration as evidence.
        shutil.copy2(frontend/'deploy/compose.vm.yaml', out/'production-template.yaml')
        (out/'compose.json').write_text(json.dumps(config, indent=2))
        (out/'models.json').write_text((work/'models.json').read_text())
        subprocess.run([*compose,'config','--quiet'], check=True, stdout=log, stderr=subprocess.STDOUT)
        print('compose-build-and-start', flush=True)
        started = True
        subprocess.run([*compose,'up','-d','--build','--wait','--wait-timeout','180'], check=True, stdout=log, stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{web_port}'
        deadline = time.monotonic()+90
        while True:
            try:
                if httpx.get(base+'/health',timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic()>deadline:
                raise TimeoutError('Compose frontend did not become healthy')
            time.sleep(.5)
        report['checks'].append({'name':'Compose frontend and Redis startup','status':'passed'})
        print('capacity-and-private-networks', flush=True)
        report['checks'].append({'name':'capacity and private model binding','status':'passed', **capacity_checks(base, model_id, client)})
        print('browser-build-run-pause-reset-storage', flush=True)
        with (out/'browser.log').open('w') as browser_log:
            subprocess.run([sys.executable,str(Path(__file__).with_name('run_browser_acceptance.py')),
                '--external-url',base,'--model-id',model_id,'--frontend',str(frontend),
                '--population','20000','--image',args.image,'--deployment-profile','--storage-check',
                '--detailed-access','--output',str(out/'browser')], check=True, stdout=browser_log, stderr=subprocess.STDOUT)
        report['checks'].append({'name':'full browser acceptance','status':'passed'})
        report['status']='passed'
    except (Exception, KeyboardInterrupt):
        report['status']='failed'
        report['error']=traceback.format_exc()
        print(report['error'], file=sys.stderr)
    finally:
        if started:
            subprocess.run([*compose,'logs','--no-color'], stdout=log, stderr=subprocess.STDOUT)
            # Stop the frontend before removing any test model workload.
            stop = subprocess.run([*compose,'stop','web'], stdout=log, stderr=subprocess.STDOUT)
            if stop.returncode:
                report['status']='failed'
                report['stop_error']='Could not stop test frontend; inspect Compose project '+project
        try:
            for container in client.containers.list(all=True,filters={'label':f'jasmine.model_id={model_id}'}):
                (out/f'model-{container.short_id}.log').write_bytes(container.logs())
                container.remove(force=True)
            remove_test_networks(client, model_id)
            if started:
                subprocess.run([*compose,'down','--volumes'], check=True, stdout=log, stderr=subprocess.STDOUT)
        except Exception:
            report['status']='failed'
            report['cleanup_error']=traceback.format_exc()
        log.close()
        if report.get('cleanup_error') or report.get('stop_error'):
            # Keep only on cleanup failure so project resources remain manageable.
            report['retained_work_directory']=str(work)
        else:
            shutil.rmtree(work)
        (out/'report.json').write_text(json.dumps(report,indent=2))
        print(f"{report['status'].upper()}: {out}/report.json",flush=True)
    return 0 if report['status']=='passed' else 1


if __name__ == '__main__':
    sys.exit(main())
