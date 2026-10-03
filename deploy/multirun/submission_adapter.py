"""(C) Copyright 2026, by Ross Richardson

SimPaths review and durable preparation adapter for the generic batch service.
Maintainers supply release paths; browser selections contain only IDs and fields.
Prepared artifacts remain private and separate from disposable attempt workspaces.

@author ross richardson
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

from .artifacts import ArtifactError, digest, fingerprint, inventory, snapshot_files, verify
from .configuration import normalise
from .container_adapter import ContainerExecution, SimPathsContainerAdapter, container_submission
from .prepare_inputs import selection, selected_sources
from .prepared_dataset import INPUT_FORMAT, allocation, check_input_receipt, verify_snapshot
from .queue_adapter import read_prepared
from .schema import (DEFAULT_MAX_CONFIGURATIONS, DEFAULT_MAX_REPETITIONS, Limits,
                     deployment_configuration_limit, deployment_repetition_limit)
from .resource_policy import LEGACY_POLICY, check_policy, simulation_resources, simulation_storage


class SubmissionModel:
    """Server-configured immutable releases and model-owned execution policies."""
    def __init__(self, releases, *, max_repetitions=DEFAULT_MAX_REPETITIONS,
                 max_configurations=DEFAULT_MAX_CONFIGURATIONS):
        self.releases = releases
        self.max_repetitions = deployment_repetition_limit(max_repetitions)
        self.max_configurations = deployment_configuration_limit(max_configurations)
        self.submission_limits = Limits(max_repetitions=self.max_repetitions,
                                       max_run_sets=self.max_configurations)

    def preparation(self, release, uploads, request):
        from jasmine_web.batch.policy import Resources
        if release not in self.releases:
            raise ArtifactError('Select an available model release')
        configured = self.releases[release]
        image = configured['image']
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
            raise ArtifactError('Release must pin an immutable installed image')
        request = selection(request)
        defaults, _ = selected_sources(configured['defaults'], uploads, request)
        helper = configured.get('helper', Path(__file__).with_name('PrepareDataset.java'))
        identity = dict(release=release, image=image, model=fingerprint(configured['jar']),
                        defaults={k:fingerprint(p) for k,p in sorted(defaults.items())},
                        helper=fingerprint(helper))
        policy = check_policy(configured.get('resource_policy', LEGACY_POLICY))
        if 'runner' in configured:
            identity.update(contract=configured['contract'],resource_policy=policy,
                            runner=fingerprint(configured['runner']))
        frozen=configured.get('identity')
        if frozen and (any(identity[key]!=frozen[key] for key in ('image','model','defaults'))
                or 'runner' in configured and any(identity[key]!=frozen[key]
                    for key in ('helper','runner','contract','resource_policy'))):
            raise ArtifactError('Retained model release changed; restore its verified bundle')
        return dict(image=image, parameters=dict(release=identity, selection=request),
                    resources=Resources(**policy['preparation']))

    def experiment(self, resolved, request):
        from jasmine_web.batch.policy import Resources
        if not isinstance(request, dict) or set(request) - {'configuration','baseline','auto_retry'} or 'configuration' not in request:
            raise ArtifactError('Supply a configuration and optional baseline/retry setting')
        config = normalise(request['configuration'], limits=self.submission_limits)
        if config.as_dict().get('sweep'):
            raise ArtifactError('Use fixed configuration cards for this first submission workflow')
        if config.as_dict()['dataset_revision'] != resolved['dataset_id']:
            raise ArtifactError('Configuration must use the selected prepared dataset')
        choices = resolved.get('datasets', {resolved['dataset_id']: resolved})
        if not self.dataset_ids(request) <= choices.keys():
            raise ArtifactError('Input dataset is unavailable')
        if len({self.model_identity(chosen) for chosen in choices.values()}) != 1:
            raise ArtifactError('All configurations must use inputs prepared for the same model version')
        selected = self.releases.get(config.as_dict()['model_release'])
        if selected is None or fingerprint(selected['jar'])['sha256'] != self.model_identity(resolved):
            raise ArtifactError('Select inputs prepared for the configuration’s model version')
        runs, budgets, storage = [], [], []
        for item in config.as_dict()['run_sets']:
            single = config.run_configuration(item['id'])
            chosen = choices[single['dataset_revision']]
            run,resources = self.bind_run(chosen,dict(id=item['id'],parameters=single),config.as_dict()['seed_plan']['seeds'])
            runs.append(dict(run, execution=dict(dataset_id=chosen['dataset_id'],
                model_digest=chosen['model_digest'], resources=resources.__dict__)))
            budgets.append(resources.__dict__)
            storage.append(dict(configuration=item['id'],**self.storage_review(chosen,single,resources)))
        return dict(label=config.as_dict()['experiment']['name'], dataset_id=resolved['dataset_id'],
            model_digest=resolved['model_digest'], seed_plan=config.as_dict()['seed_plan']['seeds'],
            run_sets=runs, baseline=request.get('baseline'), auto_retry=request.get('auto_retry', True),
            resources=Resources(**budgets[0]),storage=storage)

    def storage_review(self,resolved,data,resources):
        """Count-only working allowance; never return paths or scientific rows."""
        pending=resolved.get('state')=='pending'
        if pending:
            policy=resolved['definition']['model']['release'].get('resource_policy',LEGACY_POLICY)
            minimum=0
        else:
            receipt=read_prepared(resolved['location'])
            selected=self.releases.get(data['model_release'],{})
            policy=receipt['identity'].get('release',{}).get('resource_policy',
                selected.get('resource_policy',LEGACY_POLICY))
            from .queue_adapter import workspace_required_bytes
            minimum=workspace_required_bytes(receipt['identity']) if policy['simulation']['storage']['per_repetition_mib'] else 0
        details=simulation_storage(policy,repetitions=data['seed_plan']['repetitions'],minimum_setup_bytes=minimum)
        # Small standalone proof receipts keep their original separate quota.
        details['storage_mib']=resources.storage_mib
        return dict(details,provisional=pending and bool(details['per_repetition_mib']))

    def model_identity(self, resolved):
        if resolved.get('state')=='pending':
            return resolved['definition']['model']['release']['model']['sha256']
        return read_prepared(resolved['location'])['identity']['model']['sha256']

    def bind_run(self, resolved, run, seeds):
        from jasmine_web.batch.policy import Resources
        # Existing jobs retain their frozen repetitions through preparation,
        # replacement, dispatch and recovery even if new-submission limits fall.
        # Admission is enforced by experiment(), not reapplied to accepted work.
        config=normalise(run['parameters'])
        data=config.as_dict()
        if data['dataset_revision']!=resolved['dataset_id'] or data['seed_plan']['seeds']!=seeds or [r['id'] for r in data['run_sets']]!=[run['id']]:
            raise ArtifactError('Configuration identity or seeds changed')
        if resolved.get('state')=='pending':
            year=resolved['definition']['model']['selection']['year']
            common=data['common']
            if (common['country']!='UK' or common['start_year']!=year or common['end_year']>2026
                    or common['population']>50000):
                raise ArtifactError('Configuration must match the selected inputs and supported population/years')
            release=resolved['definition']['model']['release']
            resources=simulation_resources(release.get('resource_policy',LEGACY_POLICY),
                population=common['population'],repetitions=len(seeds))
            result=dict(id=run['id'],parameters=config.editable_configuration())
        else:
            receipt=read_prepared(resolved['location'])
            if receipt['sha256']!=resolved['prepared_fingerprint']:
                raise ArtifactError('Prepared dataset changed')
            verify_snapshot(resolved['location'],receipt)
            args=container_submission(config.editable_configuration(),resolved['location'],resolved['model_digest'])
            result=args['run_sets'][0]
            selected=self.releases.get(data['model_release'],{})
            resources=allocation(receipt,population=data['common']['population'],repetitions=len(seeds),
                                 resource_policy=selected.get('resource_policy',LEGACY_POLICY))
        return result,Resources(**resources)

    def replace_run(self, previous, resolved, run, seeds):
        if self.model_identity(previous)!=self.model_identity(resolved):
            raise ArtifactError('Replacement inputs must use the same model version')
        data=normalise(run['parameters']).editable_configuration()
        data['dataset_revision']=resolved['dataset_id']
        for item in data['run_sets']:
            item.pop('dataset_revision',None)
        return self.bind_run(resolved,dict(id=run['id'],parameters=data),seeds)

    def dataset_ids(self, request):
        data = normalise(request['configuration']).as_dict()
        return {data['dataset_revision']} | {r.get('dataset_revision', data['dataset_revision'])
                                             for r in data['run_sets']}



class PreparationAdapter:
    def __init__(self, datasets, releases, artifacts):
        from jasmine_web.batch.preparation import Preparations
        self.datasets, self.releases = datasets, releases
        self.preparations = Preparations(datasets)
        self.artifacts = Path(artifacts).absolute()
        if any(p.is_symlink() for p in (self.artifacts, *self.artifacts.parents)):
            raise ArtifactError('Prepared storage must not use symlinks')
        self.artifacts.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.artifacts.stat().st_mode & 0o077 or self.artifacts.stat().st_uid != os.getuid():
            raise ArtifactError('Prepared storage must be private and service-owned')

    def parameters(self, lease):
        if lease.specification.get('operation') != 'prepare' or lease.specification['seeds'] != ['0']:
            raise ArtifactError('Expected a preparation completion unit')
        parameters = lease.specification['run_sets'][0]['parameters']
        release = parameters['model']['release']
        if (release['image'] != lease.specification['model_digest']
                or release['release'] not in self.releases):
            raise ArtifactError('Preparation release is unavailable')
        if digest(parameters) != lease.specification['prepared_fingerprint']:
            raise ArtifactError('Preparation selection changed')
        return parameters

    def required_space(self, candidate):
        params = self.parameters(candidate)
        release = params['model']['release']
        return (3*sum(v['bytes'] for v in params['uploads'].values())
                + sum(v['bytes'] for v in release['defaults'].values()) + (2<<30))

    def container_command(self, lease, request):
        params = self.parameters(lease)
        release = params['model']['release']
        configured = self.releases[release['release']]
        minimum=check_policy(release.get('resource_policy',LEGACY_POLICY))['preparation']
        if any(lease.resources[k] < v for k,v in minimum.items()):
            raise ArtifactError('Preparation allocation is too small')
        helper = configured.get('helper', Path(__file__).with_name('PrepareDataset.java'))
        if fingerprint(helper) != release['helper'] or configured['image'] != release['image']:
            raise ArtifactError('Preparation adapter or image changed; retain the reviewed release')
        if request.stat().st_dev != self.artifacts.stat().st_dev:
            raise ArtifactError('Preparation workspaces and retained artifacts must share a filesystem')
        expected = params['uploads']
        needed = self.required_space(lease)
        available = shutil.disk_usage(request).free
        if available < needed:
            from jasmine_web.batch.docker_executor import InsufficientWorkspaceSpace
            raise InsufficientWorkspaceSpace(needed, available)
        sources = request/'sources'
        sources.mkdir(mode=0o700)
        model = fingerprint(configured['jar'], sources/'model.jar')
        if model != release['model']:
            raise ArtifactError('Model changed since review')
        defaults = snapshot_files({k:Path(configured['defaults'])/k for k in release['defaults']}, sources/'defaults')
        if defaults != release['defaults']:
            raise ArtifactError('Default workbooks changed since review')
        paths = {}
        for name, item in expected.items():
            if not re.fullmatch(r'[a-f0-9]{32}', item['id']):
                raise ArtifactError('Invalid upload identity')
            paths[name] = self.datasets.root/item['id']
        uploaded = snapshot_files(paths, sources/'uploads')
        if uploaded != {k:{a:v[a] for a in ('bytes','sha256')} for k,v in expected.items()}:
            raise ArtifactError('Uploaded inputs changed since review')
        from jasmine_web.batch.local_executor import atomic_json
        atomic_json(request/'selection.json', params['model']['selection'])
        classes = request/'classes'
        classes.mkdir(mode=0o700)
        subprocess.run(['javac','-proc:none','-cp',str(sources/'model.jar'),'-d',str(classes),str(helper)],
                       check=True,capture_output=True,timeout=60)
        return ContainerExecution(release['image'],('/opt/java/openjdk/bin/java','-Xmx512m',
            '-XX:ActiveProcessorCount=2','-Djava.awt.headless=true','-cp',
            '/request/classes:/inputs/model.jar','simpaths.experiment.PrepareDataset'),str(sources))

    def validate(self, lease, work):
        from jasmine_web.batch.local_executor import atomic_json
        params = self.parameters(lease)
        release = params['model']['release']
        target = self.artifacts/lease.execution_key
        target.mkdir(mode=0o700, exist_ok=True)
        if target.is_symlink():
            raise ArtifactError('Invalid prepared location')
        sources = work.parent/'request/sources'
        uploaded = {k:{a:v[a] for a in ('bytes','sha256')} for k,v in params['uploads'].items()}
        verify(sources/'defaults', release['defaults'])
        verify(sources/'uploads', uploaded)
        if fingerprint(sources/'model.jar') != release['model']:
            raise ArtifactError('Preparation model changed')
        log = work.parent/'execution.log'
        if 'MULTIRUN_INPUTS_PREPARED_AND_VALIDATED' not in log.read_text():
            raise ArtifactError('Preparation did not validate its output')
        # Rename, not copy, the stopped worker's large result. If publication is
        # interrupted, recovery validates the same target and creates no new data.
        candidate = target/'input' if (target/'input').exists() else work/'input'
        prepared = inventory(candidate)
        for name,item in uploaded.items():
            relative = ('InitialPopulations/' if name.endswith('.csv') else
                        'EUROMODoutput/' if name.endswith('.txt') else '')+name
            if prepared.get(relative) != item:
                raise ArtifactError('Preparation changed a selected input')
        for name,item in release['defaults'].items():
            if name not in uploaded and prepared.get(name) != item:
                raise ArtifactError('Preparation changed a default workbook')
        identity = dict(format=INPUT_FORMAT,source='validated-selected-files',country='UK',
            start_year=params['model']['selection']['year'],source_image=release['image'],model=release['model'],
            sources=dict(defaults=release['defaults'],uploads=uploaded),selection=params['model']['selection'],prepared=prepared)
        if 'runner' in release:
            identity['release']=dict(id=release['release'],contract=release['contract'],
                resource_policy=release['resource_policy'],runner=release['runner'])
            runner=self.releases[release['release']]['runner']
            if not (target/'run.sh').exists():
                temporary=target/'.run.sh.pending'
                temporary.unlink(missing_ok=True)
                if fingerprint(runner,temporary)!=release['runner']:
                    raise ArtifactError('Frozen execution script changed')
                with temporary.open('rb') as copied:
                    os.fsync(copied.fileno())
                temporary.replace(target/'run.sh')
            if fingerprint(target/'run.sh')!=release['runner']:
                raise ArtifactError('Prepared execution script changed')
        receipt = dict(identity=identity,sha256=digest(identity),revision='inputs-'+digest(identity))
        check_input_receipt(receipt)
        if candidate != target/'input':
            candidate.rename(target/'input')
        if not (target/'model.jar').exists():
            temporary = target/'.model.jar.pending'
            temporary.unlink(missing_ok=True)
            if fingerprint(sources/'model.jar',temporary) != release['model']:
                raise ArtifactError('Prepared model copy changed')
            with temporary.open('rb') as copied:
                os.fsync(copied.fileno())
            temporary.replace(target/'model.jar')
        if fingerprint(target/'model.jar') != release['model']:
            raise ArtifactError('Prepared model changed')
        for file in [target/'model.jar',*([target/'run.sh'] if 'runner' in release else []),*(target/'input').rglob('*')]:
            if file.is_file():
                os.chmod(file,0o400)
        if (target/'receipt.json').exists() and read_prepared(target) != receipt:
            raise ArtifactError('Prepared receipt changed during recovery')
        atomic_json(target/'receipt.json',receipt)
        return [dict(seed='0',fingerprint=receipt['sha256'])]

    def publication(self, lease, work, receipts):
        return self.preparations.publication(lease, prepared_fingerprint=receipts[0]['fingerprint'],
                                             location=str(self.artifacts/lease.execution_key))

    def retire_failed(self, lease, *, measure=False):
        """An interrupted publication may leave an unregistered prepared copy.

        Called by the platform only for a finished, confirmed-stopped failure.
        Registered datasets have their own lifecycle and are never removed here.
        """
        from jasmine_web.batch.output_management import remove_targets
        from jasmine_web.batch.storage import measured_bytes
        from jasmine_web.batch.policy import Conflict
        import re
        if (lease.specification.get('operation') != 'prepare'
                or not re.fullmatch(r'batch-[a-f0-9-]{36}',lease.execution_key)):
            raise Conflict('Invalid preparation cleanup reference')
        target = self.artifacts/lease.execution_key
        with self.datasets.queue._connection() as c:
            locations = c.execute('SELECT location FROM prepared_locations').fetchall()
        if any(target==Path(r['location']) or target.is_relative_to(Path(r['location']))
               or Path(r['location']).is_relative_to(target) for r in locations):
            return 0
        if measure:
            return measured_bytes(target)
        remove_targets(self.artifacts,[lease.execution_key])


class DispatchAdapter:
    """Route both operation kinds through one queue/executor resource pool."""
    def __init__(self, preparation):
        self.preparation = preparation

    def adapter(self, lease):
        if lease.specification.get('operation') == 'prepare':
            return self.preparation
        q = self.preparation.datasets.queue
        with q._connection() as c:
            row = c.execute('SELECT * FROM prepared_locations WHERE pool_id=%s AND dataset_id=%s',
                            (q.pool_id,lease.specification['dataset_id'])).fetchone()
        if row is None:
            raise ArtifactError('Prepared dataset is unavailable')
        run=next(r for r in lease.specification['run_sets'] if r['id']==lease.configuration_id)
        release=self.preparation.releases.get(run['parameters'].get('model_release'),{})
        return SimPathsContainerAdapter(row['location'],row['model_digest'],
                                       resource_policy=release.get('resource_policy'))

    def required_space(self, candidate):
        return self.adapter(candidate).required_space(candidate)

    def container_command(self, lease, request):
        return self.adapter(lease).container_command(lease,request)

    def validate(self, lease, work):
        return self.adapter(lease).validate(lease,work)

    def publication(self, lease, work, receipts):
        if lease.specification.get('operation') == 'prepare':
            return self.preparation.publication(lease,work,receipts)
        return None
