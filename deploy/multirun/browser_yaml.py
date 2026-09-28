"""(C) Copyright 2026, by Ross Richardson

Round-trip web experiment YAML and import the supported native SimPaths subset.
Dataset permissions and preparation are resolved separately by the web service.

@author ross richardson
"""
import re

import yaml

from .configuration import import_native_yaml, normalise
from .schema import ConfigurationError, Limits
from .yaml_input import load_yaml


WEB_FORMAT = 'simpaths.multirun.web.v1'


class BrowserYaml:
    def browser_import_yaml(self, text, *, dataset, name):
        document = load_yaml(text, self.submission_limits)
        if not isinstance(document, dict):
            raise ConfigurationError('yaml', 'expected an experiment or native SimPaths configuration')
        baseline, auto_retry = None, True
        notes = []
        if 'format' in document:
            if (document.get('format') != WEB_FORMAT or
                    set(document) != {'format', 'configuration', 'baseline', 'auto_retry'}):
                raise ConfigurationError('yaml', 'unsupported web experiment format or fields')
            baseline, auto_retry = document['baseline'], document['auto_retry']
            document = document['configuration']
            snapshot = normalise(document, limits=self.submission_limits)
        elif 'schema_version' in document:
            snapshot = normalise(document, limits=self.submission_limits)
            notes.append('This configuration has no saved baseline or retry preference; no baseline is selected and automatic retries are enabled.')
        else:
            if not dataset:
                raise ConfigurationError('dataset', 'choose a default input dataset before importing native SimPaths YAML')
            snapshot = import_native_yaml(text, model_release=next(iter(self.releases)),
                dataset_revision=dataset, name=name or 'Imported configuration', limits=self.submission_limits)
            notes.append('Native SimPaths settings were converted to one configuration using your selected default input dataset. No baseline is selected and automatic retries are enabled.')
        data = snapshot.as_dict()
        if data.get('sweep'):
            raise ConfigurationError('sweep', 'browser sweep import is not available yet; supply explicit run_sets')
        form = dict(name=data['experiment']['name'], model_release=data['model_release'],
            common={k:v for k,v in data['common'].items() if k != 'country'},
            repetitions=data['seed_plan']['repetitions'], first_seed=data['seed_plan']['first_seed'],
            run_sets=data['run_sets'], baseline=baseline, auto_retry=auto_retry)
        # The exact same model/form checks are used again at review/submission.
        self.browser_configuration(data['dataset_revision'], form)
        notes.append('Supported settings omitted from the YAML use the model defaults shown in the form. Check all values before submitting.')
        return dict(dataset=data['dataset_revision'], form=form, notes=notes)

    def browser_export_yaml(self, dataset, form):
        request = self.browser_configuration(dataset, form)
        document = dict(format=WEB_FORMAT, **request)
        text = ('# SimPaths MultiRun experiment settings. Input files are not included.\n'
                '# Dataset references require access on the service where this file is imported.\n'
                + yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
        if len(text.encode('utf-8')) > Limits().max_bytes:
            raise ConfigurationError('yaml', 'export exceeds the supported import size')
        name = re.sub(r'[^A-Za-z0-9_-]+', '-', form['name']).strip('-')[:80] or 'experiment'
        return dict(filename=name + '.yaml', text=text)
