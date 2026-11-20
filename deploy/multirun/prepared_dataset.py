"""(C) Copyright 2026, by Ross Richardson

Versioned, model-owned prepared Quick Start datasets for MultiRun examples.
Receipts are produced by trusted import tooling, never accepted from a browser.
Dataset execution/download grants remain the platform's responsibility.

@author ross richardson
"""
import json
from pathlib import Path
import re

from .artifacts import ArtifactError, digest, fingerprint, relative_name, verify
from .workload_envelope import MAX_END_YEAR, validate_workload

FORMAT = "simpaths.multirun.prepared-quickstart.v1"
INPUT_FORMAT = "simpaths.multirun.prepared-inputs.v1"
SOURCE = "maintainer-quickstart-public-training"


FIXED_POPULATION_MODEL_ARGS=dict(useWeights=False,ignoreTargetsAtPopulationLoad=False)


class PreparedDatasetSettingsError(ArtifactError):
    """Public scalar parameter requirements read from a verified dataset receipt."""
    def __init__(self, requirements):
        self.fixed_model_args=dict(requirements)
        super().__init__('These population settings require a different preparation: '+
            ', '.join(key+'='+json.dumps(value) for key,value in requirements.items()))


def model_parameter_requirements(receipt):
    """Shared validation/UI contract; callers first validate the immutable receipt.

    Current saved populations derive requirements from their certified profile.
    Trusted preparation tooling may certify additional scalar requirements in
    the receipt identity; its existing digest/revision binds them automatically.
    Unknown formats, fields and contradictions fail closed.
    """
    from .schema import MODEL_FIELDS
    identity=receipt['identity'];kind=identity['format']
    if kind==FORMAT:
        profile=identity['quickstart_profile']['profile']
        requirements=dict(useWeights=profile['use_weights'],ignoreTargetsAtPopulationLoad=profile['ignore_population_targets'])
    elif kind==INPUT_FORMAT:requirements={}
    else:raise ArtifactError('Unsupported dataset parameter certification')
    certified=identity.get('model_parameter_requirements',{})
    if type(certified) is not dict or len(certified)>len(MODEL_FIELDS):raise ArtifactError('Invalid certified model parameter requirements')
    for key,value in certified.items():
        if key not in MODEL_FIELDS:raise ArtifactError('Unknown certified model parameter')
        MODEL_FIELDS[key].validate(value,'model_parameter_requirements.'+key)
        if key in requirements and requirements[key]!=value:raise ArtifactError('Certified model parameter requirements conflict with the population profile')
        requirements[key]=value
    return requirements


def validate_parameter_requirements(configuration,receipt):
    requirements=model_parameter_requirements(receipt)
    if any(any(run['model_args'][key]!=value for key,value in requirements.items()) for run in configuration['run_sets']):
        raise PreparedDatasetSettingsError(requirements)


def profile_population(profile):
    population = profile.get("profile", {}).get("requested_population")
    expected = dict(country="UK", start_year=2019, end_year=2026,
                    requested_population=population, seed=606, include_observer=True,
                    use_weights=False, ignore_population_targets=False, training_data=True)
    # JSON equality also distinguishes booleans from integer 0/1 here.
    if (type(population) is not int or population not in (20000, 50000)
            or type(profile.get("format_version")) is not int or profile["format_version"] != 1
            or profile.get("profile_id") != f"uk-2019-training-{population}-seed606"
            or digest(profile.get("profile")) != digest(expected)
            or profile.get("fresh_jvm_loading_verified") is not True
            or any(type(profile.get("actual_counts", {}).get(k)) is not int
                   or profile["actual_counts"][k] <= 0 for k in ("person", "household", "benefitunit"))):
        raise ArtifactError("Not a verified UK Quick Start training profile")
    return population


def revision(identity):
    return f"quickstart-{identity['population']}-{digest(identity)}"


def check_manifest(manifest):
    if not isinstance(manifest, dict) or not manifest or len(manifest) > 2000:
        raise ArtifactError("Invalid prepared input inventory")
    for name, item in manifest.items():
        relative_name(name)
        if (not isinstance(item, dict) or set(item) != {"bytes", "sha256"}
                or type(item["bytes"]) is not int or item["bytes"] < 0
                or not re.fullmatch(r"[a-f0-9]{64}", str(item["sha256"]))):
            raise ArtifactError("Invalid prepared file fingerprint")


def check_compaction(identity):
    proof = identity.get('input_compaction')
    if proof is None:
        return
    if (type(proof) is not dict or set(proof) != {'format_version', 'passed', 'source_receipt_sha256',
            'source_database_sha256', 'database_sha256', 'logical_sql_sha256', 'original_bytes',
            'compacted_bytes', 'logical_contents_preserved'}
            or type(proof['format_version']) is not int or proof['format_version'] != 1
            or proof['passed'] is not True or proof['logical_contents_preserved'] is not True
            or any(not re.fullmatch(r'[a-f0-9]{64}', str(proof[key])) for key in
                ('source_receipt_sha256', 'source_database_sha256', 'database_sha256', 'logical_sql_sha256'))
            or type(proof['original_bytes']) is not int or type(proof['compacted_bytes']) is not int
            or not 0 < proof['compacted_bytes'] <= proof['original_bytes']
            or identity['prepared'].get('input.mv.db') != dict(bytes=proof['compacted_bytes'],sha256=proof['database_sha256'])):
        raise ArtifactError('Invalid input compaction certificate')


def check_receipt(receipt):
    identity = receipt["identity"]
    population = profile_population(identity["quickstart_profile"])
    if (identity["format"] != FORMAT or identity["source"] != SOURCE
            or identity["population"] != population or identity["country"] != "UK"
            or identity["start_year"] != 2019 or identity["policy_years"] != [2011, 2026]
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", identity["source_image"])
            or receipt["sha256"] != digest(identity) or receipt["revision"] != revision(identity)):
        raise ArtifactError("Invalid prepared Quick Start receipt")
    check_manifest(identity["prepared"])
    check_manifest({"model.jar": identity["model"]})
    for name in ("input.mv.db", "DatabaseCountryYear.xlsx", "EUROMODpolicySchedule.xlsx"):
        if identity["prepared"].get(name, {}).get("bytes", 0) <= 0:
            raise ArtifactError("Prepared Quick Start inputs are incomplete")
    model_parameter_requirements(receipt)
    check_compaction(identity)
    return receipt


def verify_snapshot(prepared, receipt):
    prepared = Path(prepared)
    verify(prepared / "input", receipt["identity"]["prepared"])
    if fingerprint(prepared / "model.jar") != receipt["identity"]["model"]:
        raise ArtifactError("Prepared model JAR changed")
    release=receipt['identity'].get('release')
    if release is not None and fingerprint(prepared/'run.sh') != release['runner']:
        raise ArtifactError('Prepared execution script changed')


def validate_selection(configuration, receipt):
    """Constrain saved-population reuse; a different preparation needs a revision."""
    check_receipt(receipt)
    common = configuration["common"]
    revisions = {receipt["revision"]}
    identity = receipt["identity"]
    if identity.get("input_compaction") is not None:
        # Preparations.register_revision binds the catalogue ID to the original
        # dataset ID and the compact copy's fingerprint. The file receipt keeps
        # its own native Quick Start revision for standalone callers.
        source_hash = identity["input_compaction"]["source_receipt_sha256"]
        original = f"quickstart-{identity['population']}-{source_hash}"
        revisions.add("dataset-" + digest(dict(original=original, fingerprint=receipt["sha256"])))
    if (configuration["dataset_revision"] not in revisions
            or common["country"] != "UK" or common["start_year"] != 2019
            or common["population"] != receipt["identity"]["population"]
            or not 2019 <= common["end_year"] <= MAX_END_YEAR):
        raise ArtifactError("Configuration does not match the selected prepared dataset")
    validate_parameter_requirements(configuration,receipt)
    # Repetition admission belongs to SubmissionModel. Rechecking dataset
    # compatibility during execution must preserve an already accepted seed plan.


def allocation(receipt, *, population=None, repetitions=1, resource_policy=None, years=None, collectors=None):
    from .resource_policy import LEGACY_POLICY, check_policy, simulation_resources
    identity=receipt['identity']
    policy=check_policy(identity.get('release',{}).get('resource_policy',
                                         LEGACY_POLICY if resource_policy is None else resource_policy))
    from .queue_adapter import workspace_required_bytes
    minimum=workspace_required_bytes(identity) if (policy.get('profiles') or policy['simulation']['storage']['per_repetition_mib']) else 0
    resources=simulation_resources(policy,population=identity.get('population',20000) if population is None else population,
                                   repetitions=repetitions,minimum_setup_bytes=minimum,years=years,collectors=collectors)
    if identity['format'] not in (FORMAT, INPUT_FORMAT):
        resources['storage_mib']=6144  # Preserve the original small local proof.
    return resources


def check_input_receipt(receipt):
    identity = receipt['identity']
    year = identity['start_year']
    if (identity['format'] != INPUT_FORMAT or identity['source'] != 'validated-selected-files'
            or identity['country'] != 'UK' or type(year) is not int or not 2011 <= year <= 2024
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', identity['source_image'])
            or receipt['sha256'] != digest(identity) or receipt['revision'] != 'inputs-'+digest(identity)):
        raise ArtifactError('Invalid prepared input receipt')
    check_manifest(identity['prepared'])
    check_manifest({'model.jar': identity['model']})
    if 'release' in identity:
        from .releases import CONTRACT
        from .resource_policy import check_policy
        release=identity['release']
        if (type(release) is not dict or set(release) != {'id','contract','resource_policy','runner'}
                or not re.fullmatch(r'release-[a-f0-9]{64}',str(release['id']))
                or release['contract']!=CONTRACT):
            raise ArtifactError('Prepared input release requires a compatible hosting adapter')
        check_policy(release['resource_policy'])
        check_manifest({'run.sh':release['runner']})
    for kind in ('defaults', 'uploads'):
        check_manifest(identity['sources'][kind])
    from .prepare_inputs import selection
    request = identity['selection']
    if request != selection({k:v for k,v in request.items() if k!='source'}) or request['year'] != year:
        raise ArtifactError('Prepared selection changed')
    for name in ('input.mv.db', 'tax_donor_population_UK.csv', 'DatabaseCountryYear.xlsx',
                 'EUROMODpolicySchedule.xlsx'):
        if identity['prepared'].get(name,{}).get('bytes',0) <= 0:
            raise ArtifactError('Prepared inputs are incomplete')
    model_parameter_requirements(receipt)
    check_compaction(identity)
    return receipt


def validate_input_selection(configuration, receipt):
    check_input_receipt(receipt)
    common = configuration['common']
    if common['country'] != 'UK' or common['start_year'] != receipt['identity']['start_year']:
        raise ArtifactError('Configuration does not match the prepared start year')
    # Dataset/profile bounds are independent of the deployment repetition limit.
    validate_workload(common)
    validate_parameter_requirements(configuration,receipt)


def read_quickstart(prepared):
    path = Path(prepared) / "receipt.json"
    if fingerprint(path)["bytes"] > 1024 * 1024:
        raise ArtifactError("Prepared receipt exceeds 1 MiB")
    return check_receipt(json.loads(path.read_text()))
