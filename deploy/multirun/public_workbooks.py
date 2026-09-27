"""(C) Copyright 2026, by Ross Richardson

Public SimPaths parameter defaults permitted for browser viewing and download.
Keep this declaration separate from the broader input preparation inventory.
Adding a filename requires reviewing every worksheet (including hidden sheets)
of its release default for publication; an Excel extension proves nothing about
whether a file contains microdata. Never point defaults at private dataset trees.

@author ross richardson
"""
from pathlib import Path

from .artifacts import ArtifactError
from .prepare_inputs import replacement_workbooks


# Initial names: the 41 Git-tracked top-level Excel files in SimPaths/input on
# 27 September 2026, minus DatabaseCountryYear.xlsx and EUROMODpolicySchedule.xlsx.
# This inventory was not a separate maintainer approval or cell-by-cell audit.
# No patterns: additional files must not silently become browser-accessible.
PUBLIC_WORKBOOK_NAMES = frozenset({
    'align_educLevel.xlsx',
    'align_popProjections.xlsx',
    'alignment_adjustment_series.xlsx',
    'alignment_targets_employment.xlsx',
    'alignment_targets_inSchool.xlsx',
    'alignment_targets_partnered_share.xlsx',
    'economic_time_series.xlsx',
    'marriageTypes2.xlsx',
    'projections_fertility.xlsx',
    'projections_mortality.xlsx',
    'reg_RMSE.xlsx',
    'reg_childcarecost.xlsx',
    'reg_education.xlsx',
    'reg_employment_selection.xlsx',
    'reg_eq5d.xlsx',
    'reg_fertility.xlsx',
    'reg_financial_distress.xlsx',
    'reg_health.xlsx',
    'reg_health_mental.xlsx',
    'reg_health_wellbeing.xlsx',
    'reg_home_ownership.xlsx',
    'reg_income.xlsx',
    'reg_labourCovid19.xlsx',
    'reg_labourSupplyUtility.xlsx',
    'reg_leave_parental_home.xlsx',
    'reg_lifetime_incomes.xlsx',
    'reg_partnership.xlsx',
    'reg_retirement.xlsx',
    'reg_socialcare.xlsx',
    'reg_unemployment.xlsx',
    'reg_wages.xlsx',
    'scenario_CPI.xlsx',
    'scenario_employments_furloughed.xlsx',
    'scenario_parametricMatching.xlsx',
    'scenario_retirementAgeFixed.xlsx',
    'social_care_parameters.xlsx',
    'system_bu_names.xlsx',
    'time_series_factor.xlsx',
    'validation_statistics.xlsx',
})


def public_workbooks(defaults):
    """Intersect existing release defaults with explicitly public filenames."""
    root = Path(defaults).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ArtifactError('Public workbook storage must not use symlinks')
    return {name: path for name, path in replacement_workbooks(root).items()
            if name in PUBLIC_WORKBOOK_NAMES}
