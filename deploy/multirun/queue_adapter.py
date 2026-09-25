"""(C) Copyright 2026, by Ross Richardson

Model-owned adapter for an isolated local queue proof with prepared public data.
No upload endpoint or production isolation; the native scientific code is unchanged.

@author ross richardson
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import shutil
import sys

from .artifacts import (ArtifactError, copy_verified, digest, fingerprint, inventory,
                        verify, write_json)
from .configuration import normalise
from .local_process import java_command, require_local_runtime
from .prepare_training import RECEIPT_VERSION, ROOT
from . import prepared_dataset

# These supported model fields are absent from the native options.txt writer.
# Their assignment is covered by the existing Java configuration fixture; do not
# invent completion evidence or change the scientific model merely to export them.
OPTIONS_NOT_EXPORTED = frozenset({"ignoreTargetsAtPopulationLoad", "lifetimeIncomeGenerate",
                                  "lifetimeIncomeImpute", "sIndexTimeWindow"})


@dataclass(frozen=True)
class ExecutionCommand:
    argv: tuple
    env: dict


def read_prepared(prepared):
    prepared = Path(prepared)
    fingerprint(prepared / "receipt.json")  # Reject symlinks/non-ordinary files.
    receipt = json.loads((prepared / "receipt.json").read_text())
    identity = receipt["identity"]
    if identity["format"] == prepared_dataset.FORMAT:
        return prepared_dataset.check_receipt(receipt)
    if (identity["format"] != RECEIPT_VERSION or identity["source"] != "bundled-public-training"
            or identity["country"] != "UK" or identity["start_year"] != 2019
            or digest(identity) != receipt["sha256"]):
        raise ArtifactError("Unsupported or invalid prepared public-data receipt")
    return receipt


def submission_arguments(configuration, prepared):
    """Translate the model normaliser to the generic queue without importing it."""
    normalised = normalise(configuration)
    frozen = normalised.as_dict()
    receipt = read_prepared(prepared)
    common = frozen["common"]
    if receipt["identity"]["format"] == prepared_dataset.FORMAT:
        prepared_dataset.validate_selection(frozen, receipt)
    elif (common["start_year"] != 2019 or common["end_year"] != 2020
            or common["population"] > 2000 or len(frozen["seed_plan"]["seeds"]) > 3):
        raise ArtifactError("Local queue proof supports 2019–2020, at most 2000 people and three repetitions")
    runs = []
    for item in frozen["run_sets"]:
        editable = normalised.editable_configuration()
        editable.pop("sweep", None)
        editable["run_sets"] = [item]
        runs.append({"id": item["id"], "parameters": editable})
    return dict(label=frozen["experiment"]["name"],
                model_digest="sha256:" + receipt["identity"]["model"]["sha256"],
                dataset_id=frozen["dataset_revision"], seed_plan=frozen["seed_plan"]["seeds"],
                run_sets=runs)


class SimPathsLocalAdapter:
    def __init__(self, prepared):
        self.prepared = Path(prepared).absolute()
        self.receipt = read_prepared(self.prepared)

    def _configuration(self, lease):
        spec = lease.specification
        if (spec["prepared_fingerprint"] != self.receipt["sha256"]
                or spec["model_digest"] != "sha256:" + self.receipt["identity"]["model"]["sha256"]):
            raise ArtifactError("Queued model or prepared inputs do not match the approved revision")
        item = next(item for item in spec["run_sets"] if item["id"] == lease.configuration_id)
        config = normalise(item["parameters"])
        translated = submission_arguments(config.editable_configuration(), self.prepared)
        if (translated["seed_plan"] != spec["seeds"] or translated["dataset_id"] != spec["dataset_id"]
                or [r["id"] for r in translated["run_sets"]] != [lease.configuration_id]):
            raise ArtifactError("Model configuration differs from the queued Run Set or seeds")
        return config

    def command(self, lease, work):
        config = self._configuration(lease)
        # All expensive copying/hashing runs under the supervisor's deadline.
        write_json(work / "request.json", dict(prepared=str(self.prepared),
            prepared_fingerprint=self.receipt["sha256"], configuration=config.editable_configuration(),
            run_set_id=lease.configuration_id))
        return ExecutionCommand((sys.executable, "-m", "deploy.multirun.queue_adapter", "--execute"),
                                {"PYTHONPATH": str(ROOT)})

    def validate(self, lease, work):
        config = self._configuration(lease)
        return validate_outputs(work / "output", config, lease.configuration_id)


def _equals(actual, expected):
    if isinstance(expected, bool):
        return actual == str(expected).lower()
    if isinstance(expected, (int, float)):
        try:
            return Decimal(actual) == Decimal(str(expected))
        except (InvalidOperation, TypeError):
            return False
    return actual == str(expected)


def require_workspace_space(identity, workspace):
    """Allow for the private copy and the native first-run input snapshot.

    ExperimentManager.setupExperiment copies only top-level .xls/.xlsx/.db
    entries. MultiRun disables further snapshots after the first repetition.
    Keep 1 GiB for database growth, CSVs, logs and temporary files. This is a
    minimum launch check, not a general research-job storage estimate or quota.
    """
    inputs = identity["prepared"]
    native_copy = sum(item["bytes"] for name, item in inputs.items()
                      if "/" not in name and name.endswith((".xls", ".xlsx", ".db")))
    required = (sum(item["bytes"] for item in inputs.values()) + native_copy
                + identity["model"]["bytes"] + 1024**3)
    available = shutil.disk_usage(workspace).free
    if available < required:
        raise ArtifactError("Insufficient space for private inputs, native snapshot and 1 GiB reserve: "
                            f"need {required / 1024**3:.2f} GiB; available {available / 1024**3:.2f} GiB")


def validate_outputs(output, configuration, run_set_id):
    """Verify actual seeds, settings and required annual CSVs, then hash outputs.

    This checks completion against the submission, not equality between repeated
    scientific executions; it does not waive the known receipt-flag RNG issue.
    """
    import csv
    data = configuration.as_dict()
    seeds = data["seed_plan"]["seeds"]
    native = configuration.native_configuration(run_set_id)
    expected = {"country": "UK", "startYear": native["startYear"], "endYear": native["endYear"],
                "popSize": native["popSize"], **native["model_args"]}
    expected = {key: value for key, value in expected.items() if key not in OPTIONS_NOT_EXPORTED}
    options = sorted(Path(output).glob("*/input/options.txt"))
    if len(options) != len(seeds):
        raise ArtifactError("Missing or extra completed repetitions")
    results = {}
    for path in options:
        fingerprint(path)
        fields = {}
        for line in path.read_text().splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                if key in fields:
                    raise ArtifactError("Duplicate run metadata")
                fields[key] = value
        seed = fields.get("randomSeedIfFixed")
        if seed not in seeds or seed in results:
            raise ArtifactError("Unexpected or repeated native seed")
        if any(not _equals(fields.get(key), value) for key, value in expected.items()):
            raise ArtifactError("Actual model settings differ from the submitted configuration")
        csv_dir = path.parent.parent / "csv"
        manifest = inventory(csv_dir)
        run_id = None
        years_expected = set(range(native["startYear"], native["endYear"] + 1))
        for name in ("Person.csv", "BenefitUnit.csv"):
            if name not in manifest:
                raise ArtifactError("Required scientific output missing")
            years, count = set(), 0
            with (csv_dir / name).open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                columns = reader.fieldnames or []
                if len(set(columns)) != len(columns) or not {"run", "time"} <= set(columns):
                    raise ArtifactError("Invalid scientific CSV header")
                for row in reader:
                    if None in row or None in row.values():
                        raise ArtifactError("Truncated scientific CSV")
                    try:
                        year = Decimal(row["time"])
                    except InvalidOperation as error:
                        raise ArtifactError("Invalid scientific output year") from error
                    if not year.is_finite() or year != int(year) or int(year) not in years_expected:
                        raise ArtifactError("Unexpected scientific output year")
                    years.add(int(year))
                    run_id = run_id or row["run"]
                    if not row["run"] or run_id != row["run"]:
                        raise ArtifactError("CSV mixes native runs")
                    count += 1
            if not count or years != years_expected:
                raise ArtifactError("Scientific output has missing years or no records")
        results[seed] = {"seed": seed, "fingerprint": digest({"options": fingerprint(path), "csv": manifest})}
    return [results[seed] for seed in seeds]


def execute():
    require_local_runtime()
    work = Path.cwd()
    request = json.loads((work / "request.json").read_text())
    prepared = Path(request["prepared"])
    receipt = read_prepared(prepared)
    if receipt["sha256"] != request["prepared_fingerprint"]:
        raise ArtifactError("Prepared dataset revision changed before execution")
    config = normalise(request["configuration"])
    # Recheck the restricted local-proof profile inside the supervised program.
    submission_arguments(config.editable_configuration(), prepared)
    identity = receipt["identity"]
    require_workspace_space(identity, work)
    if fingerprint(prepared / "model.jar", work / "model.jar") != identity["model"]:
        raise ArtifactError("Model JAR changed before execution")
    copy_verified(prepared / "input", identity["prepared"], work / "input")
    (work / "config").mkdir(mode=0o700)
    (work / "tmp").mkdir(mode=0o700)
    (work / "config/run.yml").write_text(config.native_yaml(request["run_set_id"]))
    command = java_command(work / "model.jar", "simpaths.experiment.SimPathsMultiRun",
                           "-config", "run.yml", "-P", "root",
                           heap="3g" if identity.get("population") == 50000 else "2g")
    # Keep the supervised PID and its parent-death guard when replacing Python.
    os.execv(command[0], command)


if __name__ == "__main__":
    if sys.argv[1:] != ["--execute"]:
        raise SystemExit("Use the local queue proof command; this is an internal adapter")
    execute()
