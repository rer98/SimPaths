"""(C) Copyright 2026, by Ross Richardson

Restore editable experiment settings from owned, frozen queue specifications.
Legacy submissions are reconstructed from each configuration's effective settings.

@author ross richardson
"""
from copy import deepcopy

from .browser_yaml import WEB_FORMAT
from .configuration import normalise
from .schema import ConfigurationError, Limits


class BrowserCopy:
    def browser_snapshot(self, request):
        """Saved by the service after validation, never supplied as queue metadata."""
        return dict(format=WEB_FORMAT,
            configuration=normalise(request['configuration']).editable_configuration(),
            baseline=request.get('baseline'),auto_retry=request.get('auto_retry',True))

    def browser_copy(self, specification, *, replacements, auto_retry):
        notes=[]
        runs=specification['run_sets']
        if not 1<=len(runs)<=Limits().max_run_sets:
            raise ConfigurationError('copy','the saved configuration count exceeds the supported format')
        if len(runs)>self.max_configurations:
            notes.append(f'This experiment has {len(runs)} configurations; remove some to meet the current limit of {self.max_configurations} before submitting.')
        effective={}
        for run in runs:
            data=normalise(run['parameters']).as_dict()
            if (len(data['run_sets'])!=1 or data['run_sets'][0]['id']!=run['id'] or
                    data['seed_plan']['seeds']!=specification['seeds'] or data.get('sweep')):
                raise ConfigurationError('copy','saved configuration identity or seed plan is inconsistent')
            effective[run['id']]=data
        if len(effective)!=len(runs) or len({r['model_release'] for r in effective.values()})!=1:
            raise ConfigurationError('copy','saved configurations must have distinct IDs and one model release')
        saved=specification.get('browser_settings')
        if saved is not None:
            if type(saved) is not dict or set(saved)!={'format','configuration','baseline','auto_retry'} or saved['format']!=WEB_FORMAT:
                raise ConfigurationError('copy','unsupported saved browser settings')
            snapshot=normalise(saved['configuration'])
            data=snapshot.as_dict()
            if ([r['id'] for r in data['run_sets']]!=[r['id'] for r in runs] or
                    any(normalise(snapshot.run_configuration(key)).as_dict()!=value for key,value in effective.items())):
                raise ConfigurationError('copy','saved form does not match the submitted configurations')
            baseline,retry=saved['baseline'],saved['auto_retry']
        else:
            # Old submissions retain effective values but not default/override
            # choices. Make the first configuration the form default, then
            # preserve every other configuration through explicit overrides.
            data=deepcopy(next(iter(effective.values())))
            first=data['run_sets'][0]
            data['dataset_revision']=first.get('dataset_revision',data['dataset_revision'])
            data['common']=first.get('common',data['common'])
            data['run_sets']=[]
            for key,entry in effective.items():
                item=deepcopy(entry['run_sets'][0])
                dataset=item.pop('dataset_revision',entry['dataset_revision'])
                common=item.pop('common',entry['common'])
                if dataset!=data['dataset_revision']:item['dataset_revision']=dataset
                if common!=data['common']:item['common']=common
                data['run_sets'].append(item)
            baseline,retry=specification['baseline'],auto_retry
            notes.append('This older experiment was reconstructed from its saved configuration settings. The first configuration supplies the form defaults; every configuration retains its own effective inputs and settings.')
        if (baseline is not None and baseline not in effective) or type(retry) is not bool:
            raise ConfigurationError('copy','saved baseline or retry preference is invalid')
        if data['model_release'] not in self.releases:
            raise ConfigurationError('copy','the saved model release is unavailable on this service')
        if set(replacements)-effective.keys():
            raise ConfigurationError('copy','replacement configuration is not in the experiment')
        for i,run in enumerate(data['run_sets']):
            replacement=replacements.get(run['id'])
            if replacement is None:continue
            changed=normalise(replacement['parameters']).as_dict()
            if (replacement['id']!=run['id'] or [r['id'] for r in changed['run_sets']]!=[run['id']] or
                    changed['seed_plan']['seeds']!=specification['seeds'] or changed['model_release']!=data['model_release']):
                raise ConfigurationError('copy','replacement identity, model or seeds are inconsistent')
            item=deepcopy(changed['run_sets'][0])
            item['dataset_revision']=item.get('dataset_revision',changed['dataset_revision'])
            item['common']=item.get('common',changed['common'])
            data['run_sets'][i]=item
        if replacements:
            notes.append('The copy includes the input replacements approved for these configurations after submission.')
        repetitions=data['seed_plan']['repetitions']
        if repetitions>self.max_repetitions:
            notes.append(f'This experiment has {repetitions} repetitions; reduce this to the current limit of {self.max_repetitions} before submitting.')
        form=dict(name=specification['label'][:113]+' (copy)',model_release=data['model_release'],
            common={k:v for k,v in data['common'].items() if k!='country'},
            repetitions=repetitions,first_seed=data['seed_plan']['first_seed'],run_sets=data['run_sets'],
            baseline=baseline,auto_retry=retry)
        return dict(dataset=data['dataset_revision'],form=form,notes=notes)
