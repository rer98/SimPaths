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

FORMAT = "simpaths.multirun.prepared-quickstart.v1"
INPUT_FORMAT = "simpaths.multirun.prepared-inputs.v1"
SOURCE = "maintainer-quickstart-public-training"


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
    return receipt


def verify_snapshot(prepared, receipt):
    prepared = Path(prepared)
    verify(prepared / "input", receipt["identity"]["prepared"])
    if fingerprint(prepared / "model.jar") != receipt["identity"]["model"]:
        raise ArtifactError("Prepared model JAR changed")


def validate_selection(configuration, receipt):
    """Constrain saved-population reuse; a different preparation needs a revision."""
    check_receipt(receipt)
    common = configuration["common"]
    if (configuration["dataset_revision"] != receipt["revision"]
            or common["country"] != "UK" or common["start_year"] != 2019
            or common["population"] != receipt["identity"]["population"]
            or not 2019 <= common["end_year"] <= 2026):
        raise ArtifactError("Configuration does not match the selected prepared dataset")
    for run in configuration["run_sets"]:
        settings = run["model_args"]
        if settings["useWeights"] or settings["ignoreTargetsAtPopulationLoad"]:
            raise ArtifactError("These population settings require a different preparation")
    # Initial example workflow stays bounded until larger workloads are measured.
    if len(configuration["seed_plan"]["seeds"]) > 3:
        raise ArtifactError("Prepared example validation currently supports at most three repetitions")


def allocation(receipt):
    large = receipt["identity"].get("population") == 50000
    return dict(cpu_millis=2000, memory_mib=5120 if large else 4096,
                storage_mib=10240 if receipt["identity"]["format"] in (FORMAT, INPUT_FORMAT) else 6144)


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
    return receipt


def validate_input_selection(configuration, receipt):
    check_input_receipt(receipt)
    common = configuration['common']
    if common['country'] != 'UK' or common['start_year'] != receipt['identity']['start_year']:
        raise ArtifactError('Configuration does not match the prepared start year')
    # Development proof limits until resource/scientific coverage is measured.
    if (common['end_year'] > 2026 or common['population'] > 50000
            or len(configuration['seed_plan']['seeds']) > 3):
        raise ArtifactError('Prepared-input proof supports up to 50,000 people, 2026 and three repetitions')


def read_quickstart(prepared):
    path = Path(prepared) / "receipt.json"
    if fingerprint(path)["bytes"] > 1024 * 1024:
        raise ArtifactError("Prepared receipt exceeds 1 MiB")
    return check_receipt(json.loads(path.read_text()))
