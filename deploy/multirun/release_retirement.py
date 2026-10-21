"""(C) Copyright 2026, by Ross Richardson

Automatic reference-aware retirement of recovery-only MultiRun releases.
Keeps current/rollback/live work and inputs, and archives before removing bundles.
@author ross richardson
"""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import time

from .artifacts import ArtifactError
from .releases import ReleaseRegistry, atomic_json, read_json
from . import release_archives


class ReleaseRetirement:
    def __init__(self,queue,state,*,protected=(),interval_seconds=3600,grace_days=30):
        self.queue=queue;self.registry=ReleaseRegistry(state)
        self.protected=set(protected)
        if type(grace_days) is not int or grace_days<30:raise ArtifactError('Release retirement grace must be at least 30 days')
        self.grace=timedelta(days=grace_days)
        self.interval=interval_seconds;self.last=None

    def references(self,c,entries):
        """Conservative inventory across this queue schema, not only one owner."""
        held={key:set() for key in entries}
        images={}
        for key,entry in entries.items():
            directory=self.registry.state/'release' if entry['legacy'] else self.registry.root/key
            path=directory/'release.json'
            from .artifacts import fingerprint
            if fingerprint(path)!=entry['manifest']:raise ArtifactError('Release manifest changed during retirement checks')
            metadata=read_json(path)
            images[key]=(metadata if entry['legacy'] else metadata['identity'])['image']
        def retain(key,reason,image=None):
            if key in held:held[key].add(reason)
            elif image:
                for candidate,value in images.items():
                    if image==value:held[candidate].add(reason+' (unidentified release)')
        rows=c.execute('''SELECT j.state,j.execution_run,j.configuration_id,j.model_digest,e.specification
            FROM jobs j JOIN experiments e ON e.id=j.experiment_id
            WHERE j.state NOT IN ('succeeded','cancelled','expired') LIMIT 10001''').fetchall()
        if len(rows)>10000:raise ArtifactError('Release reference inventory exceeds its safe bound')
        for row in rows:
            spec=row['specification']
            run=row['execution_run'] or next(r for r in spec['run_sets'] if r['id']==row['configuration_id'])
            params=run['parameters']
            release=(params.get('model',{}).get('release',{}).get('release') if spec.get('operation')=='prepare'
                     else params.get('model_release'))
            retain(release,'unfinished/recoverable work',row['model_digest'])
        locations=c.execute('''SELECT p.*,d.prepared_fingerprint FROM prepared_locations p
            JOIN datasets d ON d.pool_id=p.pool_id AND d.id=p.dataset_id
            LEFT JOIN dataset_lifecycle l ON l.pool_id=p.pool_id AND l.dataset_id=p.dataset_id
            WHERE l.state IS NULL OR l.state<>'deleted' LIMIT 10001''').fetchall()
        if len(locations)>10000:raise ArtifactError('Prepared release inventory exceeds its safe bound')
        from .queue_adapter import read_prepared
        for row in locations:
            try:
                receipt=read_prepared(row['location'])
                if receipt['sha256']!=row['prepared_fingerprint']:raise ArtifactError('Prepared identity differs')
                release=receipt['identity'].get('release',{}).get('id')
                retain(release,'retained prepared inputs',row['model_digest'])
            except (OSError,ValueError,KeyError):
                retain(None,'unverified prepared inputs',row['model_digest'])
        return held

    def tick(self,*,now=None,force=False):
        from jasmine_web.batch.backup_guard import BackupBusy, file_guard
        stamp=time.monotonic()
        control=self.registry.state/'release-retirement-policy.json'
        settings=read_json(control) if control.exists() else dict(enabled=True,grace_days=self.grace.days,interval_seconds=self.interval)
        if (set(settings)!={'enabled','grace_days','interval_seconds'} or type(settings['enabled']) is not bool
                or type(settings['grace_days']) is not int or settings['grace_days']<30
                or type(settings['interval_seconds']) is not int or not 60<=settings['interval_seconds']<=86400):
            raise ArtifactError('Invalid release retirement policy')
        if not settings['enabled']:return None
        self.grace=timedelta(days=settings['grace_days'])
        if not force and self.last is not None and stamp-self.last<settings['interval_seconds']:return None
        self.last=stamp
        try:
            with file_guard(self.registry.state):
                return self.sweep(now=now)
        except BackupBusy:return None

    def sweep(self,*,now=None):
        registry=self.registry
        with registry.locked():
            catalogue=registry._catalogue() or registry._legacy()
            if catalogue is None:raise ArtifactError('Register the active release before retirement')
            with self.queue._transaction() as (c,_,database_now):
                now=now or database_now
                held=self.references(c,catalogue['entries'])
            protected=self.protected|{catalogue['default'],catalogue['rollback']}
            settings=registry.state/'operator-settings.json'
            if settings.exists():protected.add(read_json(settings).get('model_release'))
            report=dict(format='simpaths.multirun.release-retirement.v1',at=now.isoformat(),retired=[],releases={})
            candidate=None
            for key,entry in catalogue['entries'].items():
                reasons=held[key]
                if key in protected:reasons.add('active/default/rollback release')
                if entry['availability']!='recovery':reasons.add('available for new work or experiment additions')
                if reasons:entry['unreferenced_since']=None
                elif entry['unreferenced_since'] is None:entry['unreferenced_since']=now.isoformat()
                since=datetime.fromisoformat(entry['unreferenced_since']) if entry['unreferenced_since'] else now
                if not reasons and now-since>=self.grace and candidate is None:candidate=key
                report['releases'][key]=dict(availability=entry['availability'],reasons=sorted(reasons),
                    unreferenced_since=entry['unreferenced_since'])
            atomic_json(registry.catalogue,catalogue)
            if candidate is not None:
                # Expensive compression/hash work is outside the queue lock.
                release_archives.create(registry,candidate,catalogue['entries'][candidate])
                with self.queue._transaction() as (c,_,_):
                    latest=self.references(c,catalogue['entries'])
                    if not latest[candidate]:
                        catalogue['entries'].pop(candidate)
                        atomic_json(registry.catalogue,catalogue)
                        report['retired'].append(candidate)
                    else:
                        catalogue['entries'][candidate]['unreferenced_since']=None
                        atomic_json(registry.catalogue,catalogue)
                        report['releases'][candidate].update(reasons=sorted(latest[candidate]),unreferenced_since=None)
                if candidate in report['retired']:release_archives.cleanup(registry,candidate)
            # Complete an interrupted post-commit unlink, never an active bundle.
            archived=registry.root/'retired'
            if archived.exists():
                for path in archived.glob('*.json'):
                    key=path.stem
                    if key not in catalogue['entries']:release_archives.cleanup(registry,key)
            atomic_json(registry.state/'release-retirement.json',report)
            return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('status','sweep','policy'))
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--dsn-env',default='JASMINE_BATCH_DSN')
    parser.add_argument('--enabled',choices=('true','false'))
    parser.add_argument('--grace-days',type=int,default=30)
    args=parser.parse_args(argv)
    if args.command=='policy':
        if args.enabled is None or args.grace_days<30:parser.error('Set --enabled true/false and a grace of at least 30 days')
        registry=ReleaseRegistry(args.state)
        with registry.locked():
            atomic_json(args.state/'release-retirement-policy.json',dict(enabled=args.enabled=='true',grace_days=args.grace_days,interval_seconds=3600))
        print('Retirement policy updated; running jobs continue unchanged.');return 0
    if args.command=='status':
        import json
        path=args.state/'release-retirement.json'
        print(json.dumps(read_json(path) if path.exists() else dict(status='No retirement pass recorded yet'),indent=2))
        return 0
    settings=read_json(args.state/'operator-settings.json')
    from jasmine_web.batch.store import Queue
    queue=Queue(os.environ[args.dsn_env],settings['pool_id'],schema=settings['schema'])
    queue.backup_state=args.state
    result=ReleaseRetirement(queue,args.state).tick(force=True)
    print('Retired releases: '+str(len(result['retired'])) if result else 'Cleanup deferred while a backup is running')
    return 0


if __name__=='__main__':raise SystemExit(main())
