"""(C) Copyright 2026, by Ross Richardson

Explicit scalar field profile for the first MultiRun configuration validation slice.
This is a mechanical contract, not certification of scientific parameter ranges.

@author ross richardson
"""

from dataclasses import dataclass
import math


SCHEMA_VERSION = "simpaths.multirun.v1-draft"
PROFILE_VERSION = "simpaths-uk-basic-v1-draft"
SEED_PROFILE = "simpaths-standard-v1"
OUTPUT_CONTRACT = "simpaths.visualiser.v1-draft"
LONG_MIN, LONG_MAX = -(2**63), 2**63 - 1
INT_MIN, INT_MAX = -(2**31), 2**31 - 1


class ConfigurationError(ValueError):
    """A field-specific error safe to display without echoing uploaded contents."""

    def __init__(self, path, message):
        self.path = path
        super().__init__(f"{path}: {message}")


@dataclass(frozen=True)
class Limits:
    """Trusted parser/expansion ceilings, not deployment capacity recommendations."""

    max_bytes: int = 65536
    max_nodes: int = 16000
    max_depth: int = 16
    max_scalar_chars: int = 4096
    max_run_sets: int = 100
    max_repetitions: int = 1000
    max_simulations: int = 10000

    def __post_init__(self):
        for value in vars(self).values():
            if type(value) is not int or value <= 0:
                raise ValueError("Limits must be positive integers")


@dataclass(frozen=True)
class Field:
    kind: str
    default: object

    def validate(self, value, path):
        if self.kind == "boolean":
            if type(value) is not bool:
                raise ConfigurationError(path, "expected a boolean")
        elif self.kind in {"int", "long"}:
            lo, hi = (INT_MIN, INT_MAX) if self.kind == "int" else (LONG_MIN, LONG_MAX)
            if type(value) is not int or not lo <= value <= hi:
                raise ConfigurationError(path, f"expected a Java {self.kind} integer")
        elif self.kind == "double":
            if type(value) not in {int, float}:
                raise ConfigurationError(path, "expected a finite number")
            try:
                value = float(value)
            except (OverflowError, ValueError):
                raise ConfigurationError(path, "expected a finite Java double") from None
            if not math.isfinite(value):
                raise ConfigurationError(path, "expected a finite Java double")
        else:
            raise RuntimeError("Unrecognised trusted field type")
        return value


# These are explicit reviewed declarations, never runtime reflection over user keys.
# Keep the Java declaration drift test in sync when reviewing a model release.
MODEL_FIELDS = {
    "maxAge": Field("int", 130),
    "timeTrendStopsIn": Field("int", 2023),
    "timeTrendStopsInMonetaryProcesses": Field("int", 2023),
    "sIndexTimeWindow": Field("int", 5),
    "sIndexAlpha": Field("double", 2.0),
    "sIndexDelta": Field("double", 0.98),
    "savingRate": Field("double", 0.056),
}
MODEL_FIELDS.update({key: Field("boolean", default) for key, default in {
    "fixTimeTrend": True,
    "initialisePotentialEarningsFromDatabase": True,
    "useWeights": False,
    "projectMortality": True,
    "alignPopulation": True,
    "alignFertility": False,
    "alignEducation": False,
    "alignInSchool": False,
    "alignCohabitation": True,
    "alignEmployment": False,
    "labourMarketCovid19On": False,
    "projectFormalChildcare": True,
    "projectSocialCare": True,
    "flagSuppressChildcareCosts": False,
    "flagSuppressSocialCareCosts": False,
    "donorPoolAveraging": True,
    "taxDonorUpratingByWage": False,
    "addRegressionStochasticComponent": True,
    "fixRegressionStochasticComponent": False,
    "flagDefaultToTimeSeriesAverages": False,
    "ignoreTargetsAtPopulationLoad": False,
}.items()})

# Advanced capabilities need their own preparation/effect proof before exposure.
FIXED_MODEL = {
    "fixRandomSeed": True,
    "enableIntertemporalOptimisations": False,
    "useSavedBehaviour": False,
    "lifetimeIncomeGenerate": False,
    "lifetimeIncomeImpute": False,
    "printSBAMMatchingMatrix": False,
    "saveImperfectTaxDBMatches": False,
}
COLLECTOR_FIELDS = {key: Field("boolean", default) for key, default in {
    "calculateGiniCoefficients": False,
    "exportToDatabase": False,
    "exportToCSV": True,
    "persistWealthIncomeStatistics": True,
    "persistDemographicStatistics": True,
    "persistAlignmentStatistics": True,
    "persistLabourStatistics": True,
    "persistHealthStatistics": True,
    "persistWellbeingByGender": True,
    "persistPersons": True,
    "persistBenefitUnits": True,
    "persistHouseholds": True,
}.items()}
COLLECTOR_FIELDS.update({
    "dataDumpStartTime": Field("long", 0),
    "dataDumpTimePeriod": Field("double", 1.0),
})
REQUIRED_OUTPUT = {
    "exportToCSV": True, "persistPersons": True, "persistBenefitUnits": True,
    "dataDumpStartTime": 0, "dataDumpTimePeriod": 1.0,
}
