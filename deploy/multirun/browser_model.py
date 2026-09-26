"""(C) Copyright 2026, by Ross Richardson

SimPaths-owned fields and descriptions for the generic MultiRun browser workflow.
The first form exposes fixed configurations and the validated standard seed plan.

@author ross richardson
"""
from .artifacts import ArtifactError
from .configuration import normalise
from .prepared_dataset import FORMAT, INPUT_FORMAT
from .queue_adapter import read_prepared
from .schema import MODEL_FIELDS, OUTPUT_CONTRACT, SCHEMA_VERSION, SEED_PROFILE
from .submission_adapter import SubmissionModel


class BrowserModel(SubmissionModel):
    def browser_form(self):
        return dict(title='SimPaths UK MultiRun', max_configurations=10, max_repetitions=3,
            releases=[dict(id=k, name='SimPaths UK — uploaded inputs') for k in self.releases],
            common=[dict(id='population',label='Simulated population',kind='int',default=20000,min=1,max=50000),
                    dict(id='start_year',label='First year',kind='int',default=2019,min=2011,max=2024),
                    dict(id='end_year',label='Last year',kind='int',default=2020,min=2011,max=2026)],
            fields=[dict(id=k,label=LABELS[k],kind=MODEL_FIELDS[k].kind,default=MODEL_FIELDS[k].default)
                    for k in MODEL_FIELD_ORDER],
            preparation=dict(year=2019, minimum_year=2011, maximum_year=2024,
                help='Upload population_initial_UK_YEAR.csv for your first year and the UKMOD text files. '
                     'Include a policy starting in 2015 for the model’s base-price calculations. '
                     'Add each policy to the schedule below. Replacement parameter workbooks are optional.'),
            note='Configurations inherit the default input dataset, or use their own selected dataset. '
                 'Each configuration uses the same seed sequence, beginning 606, 607, 608. '
                 'Configurations can run in parallel when capacity is available. '
                 'This local version supports up to three repetitions per configuration. '
                 'Results visualisation and browser downloads will be added separately.')

    def describe_dataset(self, resolved):
        if resolved.get('state')=='pending':
            definition=resolved['definition'];model=definition['model'];year=model['selection']['year']
            return dict(id=resolved['dataset_id'],name=resolved.get('display_name') or 'Your pending UK inputs',
                values=dict(start_year=year,population=20000,end_year=min(2026,year+1)),locked=['start_year'],
                inputs=dict(fingerprint=resolved['definition_hash'],
                    population={k:v for k,v in definition['uploads'].items() if k.endswith('.csv')},
                    workbooks={**model['release']['defaults'],**{k:v for k,v in definition['uploads'].items() if k.endswith(('.xls','.xlsx'))}},
                    schedule=model['selection']['schedule']))
        receipt = read_prepared(resolved['location'])
        if receipt['sha256'] != resolved['prepared_fingerprint']:
            raise ArtifactError('Prepared dataset changed; ask the administrator to check it')
        identity = receipt['identity']
        training = identity['format'] == FORMAT
        if identity['format'] not in (FORMAT, INPUT_FORMAT):
            raise ArtifactError('Unsupported browser dataset')
        name = (f"UK training — {identity['population']:,} people" if training
                else f"Your prepared UK inputs — {identity['start_year']}")
        values = dict(start_year=identity['start_year'], population=identity.get('population',20000),
                      end_year=min(2026,identity['start_year']+1))
        return dict(id=resolved['dataset_id'],name=resolved.get('display_name') or name,values=values,
                    inputs=dict(fingerprint=receipt['sha256'],
                        population=identity['prepared'].get('input.mv.db'),
                        workbooks={k: v for k,v in identity['prepared'].items() if k.lower().endswith('.xlsx')},
                        schedule=identity.get('selection',{}).get('schedule'),
                        policy_years=identity.get('policy_years')),
                    locked=['start_year','population'] if training else ['start_year'])

    def browser_configuration(self, dataset, form):
        if (not isinstance(form,dict) or set(form) != {'name','common','repetitions','run_sets','baseline','auto_retry'}
                or not isinstance(form['common'],dict) or set(form['common']) != {'population','start_year','end_year'}
                or not isinstance(form['run_sets'],list) or not 1 <= len(form['run_sets']) <= 10
                or type(form['repetitions']) is not int or not 1 <= form['repetitions'] <= 3
                or type(form['auto_retry']) is not bool):
            raise ArtifactError('Supply the experiment fields, 1–10 configurations and 1–3 repetitions')
        for run in form['run_sets']:
            if not isinstance(run,dict) or set(run) - {'id','name','model_args','dataset_revision','common'} or not {'id','name','model_args'} <= set(run):
                raise ArtifactError('Each configuration needs a name and model settings')
        configuration = dict(schema_version=SCHEMA_VERSION,model_release=next(iter(self.releases)),
            dataset_revision=dataset,experiment=dict(name=form['name']),common=dict(country='UK',**form['common']),
            seed_plan=dict(mode='standard',profile=SEED_PROFILE,repetitions=form['repetitions']),
            run_sets=form['run_sets'],output_contract=OUTPUT_CONTRACT)
        canonical = normalise(configuration).editable_configuration()
        return dict(configuration=canonical,baseline=form['baseline'],auto_retry=form['auto_retry'])

    def browser_summary(self, request):
        data = normalise(request['configuration']).as_dict()
        runs = data['run_sets']
        different = [key for key in MODEL_FIELD_ORDER if len({str(r['model_args'][key]) for r in runs}) > 1]
        # Show all settings, including those identical across configurations, so
        # a review remains useful for a single configuration and default values.
        return dict(name=data['experiment']['name'],common=data['common'],
            configurations=[dict(id=r['id'],name=r['name'],settings=r['model_args'],
                common=r.get('common',data['common']), dataset=r.get('dataset_revision',data['dataset_revision'])) for r in runs],
            different=different,auto_retry=request['auto_retry'])


# Presentation order follows SimPathsModel declarations, including supported
# fields without @GUIparameter. Keep it separate from the execution schema:
# changing presentation must not change frozen experiment identities/defaults.
MODEL_FIELD_ORDER = (
    'maxAge', 'fixTimeTrend', 'timeTrendStopsIn', 'timeTrendStopsInMonetaryProcesses',
    'sIndexTimeWindow', 'sIndexAlpha', 'sIndexDelta', 'savingRate',
    'initialisePotentialEarningsFromDatabase', 'useWeights', 'ignoreTargetsAtPopulationLoad',
    'projectMortality', 'alignPopulation', 'alignFertility', 'alignEducation',
    'alignInSchool', 'alignCohabitation', 'alignEmployment',
    'addRegressionStochasticComponent', 'fixRegressionStochasticComponent',
    'labourMarketCovid19On', 'projectFormalChildcare', 'donorPoolAveraging',
    'taxDonorUpratingByWage', 'projectSocialCare', 'flagSuppressChildcareCosts',
    'flagSuppressSocialCareCosts', 'flagDefaultToTimeSeriesAverages',
)


LABELS = {
    'savingRate':'Saving rate', 'maxAge':'Maximum age', 'timeTrendStopsIn':'Last year of time trend',
    'timeTrendStopsInMonetaryProcesses':'Last year of monetary time trend',
    'sIndexTimeWindow':'Security index time window', 'sIndexAlpha':'Security index risk aversion',
    'sIndexDelta':'Security index discount factor', 'projectMortality':'Project mortality',
    'projectFormalChildcare':'Project formal childcare', 'projectSocialCare':'Project social care',
    'useWeights':'Use population weights', 'donorPoolAveraging':'Average across tax donors',
    'fixTimeTrend':'Fix time trend',
    'initialisePotentialEarningsFromDatabase':'Initialise potential earnings from input data',
    'ignoreTargetsAtPopulationLoad':'Ignore targets when loading population',
    'alignPopulation':'Align population', 'alignFertility':'Align fertility',
    'alignEducation':'Align education', 'alignInSchool':'Align school participation',
    'alignCohabitation':'Align cohabitation', 'alignEmployment':'Align employment',
    'addRegressionStochasticComponent':'Include regression stochastic component',
    'fixRegressionStochasticComponent':'Fix regression stochastic component',
    'labourMarketCovid19On':'Use COVID-19 labour supply module',
    'taxDonorUpratingByWage':'Uprate tax donor incomes by wage growth',
    'flagSuppressChildcareCosts':'Suppress childcare costs',
    'flagSuppressSocialCareCosts':'Suppress social care costs',
    'flagDefaultToTimeSeriesAverages':'Use time-series averages',
}
