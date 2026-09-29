"""(C) Copyright 2026, by Ross Richardson

SimPaths-owned fields and descriptions for the generic MultiRun browser workflow.
The form exposes fixed configurations and a validated shared seed plan.

@author ross richardson
"""
from .artifacts import ArtifactError
from .configuration import normalise
from .prepared_dataset import FORMAT, INPUT_FORMAT
from .queue_adapter import read_prepared
from .schema import MODEL_FIELDS, OUTPUT_CONTRACT, SCHEMA_VERSION, SEED_PROFILE
from .submission_adapter import SubmissionModel
from .public_workbooks import public_workbooks
from .browser_yaml import BrowserYaml
from .browser_sweep import BrowserSweep
from .schema import COLLECTOR_FIELDS, REQUIRED_OUTPUT, ConfigurationError


class BrowserModel(BrowserYaml, BrowserSweep, SubmissionModel):
    def configuration_name(self, run):
        data = normalise(run['parameters']).as_dict()
        return next(item['name'] for item in data['run_sets'] if item['id'] == run['id'])

    def browser_form(self):
        repetition_word='repetition' if self.max_repetitions==1 else 'repetitions'
        return dict(title='SimPaths UK MultiRun', max_configurations=10, max_repetitions=self.max_repetitions,
            yaml=True, sweeps=True, first_seed='606',
            releases=[dict(id=k, name='SimPaths UK — uploaded inputs',
                workbooks=list(public_workbooks(v['defaults']))) for k,v in self.releases.items()],
            common=[dict(id='population',label='Simulated population',kind='int',default=20000,min=1,max=50000),
                    dict(id='start_year',label='First year',kind='int',default=2019,min=2011,max=2024),
                    dict(id='end_year',label='Last year',kind='int',default=2020,min=2011,max=2026)],
            fields=[dict(id=k,label=LABELS[k],kind=MODEL_FIELDS[k].kind,default=MODEL_FIELDS[k].default)
                    for k in MODEL_FIELD_ORDER],
            collector_fields=[dict(id=k,label=COLLECTOR_LABELS[k],kind=v.kind,default=v.default,
                locked=k in REQUIRED_OUTPUT) for k,v in COLLECTOR_FIELDS.items()],
            preparation=dict(year=2019, minimum_year=2011, maximum_year=2024,
                help='Upload population_initial_UK_YEAR.csv for your first year and the UKMOD text files. '
                     'Include a policy starting in 2015 for the model’s base-price calculations. '
                     'Add each policy to the schedule below. Replacement parameter workbooks are optional.'),
            note='Configurations inherit the default input dataset, or use their own selected dataset. '
                 'Each configuration uses the same seed sequence, beginning at the selected first random seed and increasing by one. '
                 'Configurations can run in parallel when capacity is available. '
                 f'This deployment supports up to {self.max_repetitions} {repetition_word} per configuration. '
                 'Completed configurations appear under My jobs → View results.')

    def browser_workbook(self, release, name):
        """Only public parameter defaults, never prepared/provider microdata."""
        if type(release) is not str or release not in self.releases:
            raise ArtifactError('Select an available model release')
        allowed = public_workbooks(self.releases[release]['defaults'])
        if name not in allowed:
            raise ArtifactError('Select an existing replacement parameter workbook')
        return allowed[name]

    def describe_dataset(self, resolved):
        if resolved.get('state')=='pending':
            definition=resolved['definition'];model=definition['model'];year=model['selection']['year']
            return dict(id=resolved['dataset_id'],name=resolved.get('display_name') or 'Your pending UK inputs',
                values=dict(start_year=year,population=20000,end_year=min(2026,year+1)),locked=['start_year'],
                inputs=dict(fingerprint=resolved['definition_hash'],comparison_state='pending',files={},
                    source_files={**model['release']['defaults'],**definition['uploads']},
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
                    inputs=dict(fingerprint=receipt['sha256'],comparison_state='prepared',
                        files={k:v for k,v in identity['prepared'].items()
                               if k=='input.mv.db' or k.lower().endswith(('.xlsx','.xls'))},
                        population=identity['prepared'].get('input.mv.db'),
                        workbooks={k: v for k,v in identity['prepared'].items() if k.lower().endswith('.xlsx')},
                        schedule=identity.get('selection',{}).get('schedule'),
                        policy_years=identity.get('policy_years')),
                    locked=['start_year','population'] if training else ['start_year'])

    def browser_configuration(self, dataset, form):
        required = {'name','common','repetitions','run_sets','baseline','auto_retry'}
        if (not isinstance(form,dict) or not required <= set(form) or set(form) - required - {'first_seed','model_release'}
                or not isinstance(form['common'],dict) or set(form['common']) != {'population','start_year','end_year'}
                or not isinstance(form['run_sets'],list) or not 1 <= len(form['run_sets']) <= 10
                or type(form['repetitions']) is not int or not 1 <= form['repetitions'] <= self.max_repetitions
                or type(form['auto_retry']) is not bool):
            raise ArtifactError(f'Supply the experiment fields, 1–10 configurations and 1–{self.max_repetitions} repetitions')
        for run in form['run_sets']:
            if not isinstance(run,dict) or set(run) - {'id','name','model_args','collector_args','dataset_revision','common','generation'} or not {'id','name','model_args'} <= set(run):
                raise ArtifactError('Each configuration needs a name and model settings')
        release = form.get('model_release', next(iter(self.releases)))
        if type(release) is not str or release not in self.releases:
            raise ArtifactError('The configuration model release is unavailable on this service')
        if form['baseline'] is not None and (type(form['baseline']) is not str or form['baseline'] not in [r['id'] for r in form['run_sets']]):
            raise ArtifactError('Select a baseline from the experiment configurations')
        first_seed = form.get('first_seed', '606')
        seeds = (dict(mode='standard',profile=SEED_PROFILE) if first_seed == '606'
                 else dict(mode='starting_seed',first_seed=first_seed))
        configuration = dict(schema_version=SCHEMA_VERSION,model_release=release,
            dataset_revision=dataset,experiment=dict(name=form['name']),common=dict(country='UK',**form['common']),
            seed_plan=dict(**seeds,repetitions=form['repetitions']),
            run_sets=form['run_sets'],output_contract=OUTPUT_CONTRACT)
        canonical = normalise(configuration, limits=self.submission_limits).editable_configuration()
        for path, common in [('common', canonical['common'])] + [(f'run_sets[{i}].common', r['common'])
                for i,r in enumerate(canonical['run_sets']) if 'common' in r]:
            if common['population'] > 50000 or common['end_year'] > 2026:
                raise ConfigurationError(path, 'this deployment supports up to 50,000 people and a last year of 2026')
        return dict(configuration=canonical,baseline=form['baseline'],auto_retry=form['auto_retry'])

    def browser_summary(self, request):
        data = normalise(request['configuration']).as_dict()
        runs = data['run_sets']
        different = [key for key in MODEL_FIELD_ORDER if len({str(r['model_args'][key]) for r in runs}) > 1]
        # Show all settings, including those identical across configurations, so
        # a review remains useful for a single configuration and default values.
        return dict(name=data['experiment']['name'],common=data['common'],
            configurations=[dict(id=r['id'],name=r['name'],settings=r['model_args'],
                collector=r['collector_args'],
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

COLLECTOR_LABELS = {
    'calculateGiniCoefficients':'Calculate Gini coefficients', 'exportToDatabase':'Export to database',
    'exportToCSV':'Export to CSV', 'persistWealthIncomeStatistics':'Save wealth and income statistics',
    'persistDemographicStatistics':'Save demographic statistics', 'persistAlignmentStatistics':'Save alignment statistics',
    'persistLabourStatistics':'Save labour statistics', 'persistHealthStatistics':'Save health statistics',
    'persistWellbeingByGender':'Save wellbeing by gender', 'persistPersons':'Save person records',
    'persistBenefitUnits':'Save benefit unit records', 'persistHouseholds':'Save household records',
    'dataDumpStartTime':'First output time', 'dataDumpTimePeriod':'Output interval',
}
