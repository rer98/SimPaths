"""(C) Copyright 2026, by Ross Richardson

Run a local public-data native/normalised MultiRun comparison in fresh workspaces.
This is a maintainer proof, not an upload endpoint, queue worker or sandbox.

@author ross richardson
"""

import argparse
from collections import Counter
import csv
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile

from .artifacts import ArtifactError, canonical, copy_verified, digest, fingerprint, verify, write_attribution, write_json
from .configuration import normalise
from .local_process import java_command, require_local_runtime, run_java
from .prepare_training import RECEIPT_VERSION
from .schema import OUTPUT_CONTRACT, SCHEMA_VERSION

HERE = Path(__file__).resolve().parent


def proof_configuration():
    return normalise({
        "schema_version": SCHEMA_VERSION, "model_release": "local-pinned-jar",
        "dataset_revision": "local-prepared-public-training",
        "experiment": {"name": "Native compatibility proof"},
        "common": {"country": "UK", "start_year": 2019, "end_year": 2020, "population": 2000},
        "seed_plan": {"mode": "standard", "repetitions": 3},
        "sweep": {"id_prefix": "savings", "combination": "all", "base": {},
                  "parameters": {"model_args.savingRate": [0.04, 0.06]}},
        "output_contract": OUTPUT_CONTRACT,
    })


def csv_summary(path):
    """Exact multiset comparison: ignore only operational 'run'; retain every ID/value."""
    row_hashes, wealth_hashes, years, runs = [], [], set(), set()
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        if not header or len(set(header)) != len(header) or not {"run", "time"} <= set(header):
            raise ArtifactError("Missing or invalid CSV header")
        keep = [i for i, name in enumerate(header) if name != "run"]
        for row in reader:
            if len(row) != len(header):
                raise ArtifactError("Incomplete CSV record")
            time = Decimal(row[header.index("time")])
            if not time.is_finite():
                raise ArtifactError("Non-finite CSV time")
            years.add(format(time.normalize(), "f"))
            runs.add(row[header.index("run")])
            row_hashes.append(hashlib.sha256(canonical([row[i] for i in keep]).encode()).digest())
            if "wealthTotValue" in header:
                # Preserve pairing by time/entity, without the operational run ID.
                indices = [i for i, name in enumerate(header)
                           if name in ("time", "wealthTotValue") or name.startswith("id_")]
                wealth_hashes.append(hashlib.sha256(canonical([row[i] for i in indices]).encode()).digest())
    if not row_hashes or len(runs) != 1:
        raise ArtifactError("CSV must contain records for exactly one native run")
    hasher = hashlib.sha256(canonical([header[i] for i in keep]).encode())
    for row_hash in sorted(row_hashes):
        hasher.update(row_hash)
    return {"records": len(row_hashes), "years": sorted(years), "run_values": sorted(runs),
            "scientific_sha256": hasher.hexdigest(),
            "wealth_sha256": hashlib.sha256(b"".join(sorted(wealth_hashes))).hexdigest() if wealth_hashes else None,
            "file": fingerprint(path)}


def inspect_outputs(output, destination, expected_rate):
    options = sorted(output.glob("*/input/options.txt"))
    if len(options) != 3:
        raise ArtifactError("Expected exactly three native repetitions")
    runs = {}
    for option_file in options:
        fields = {}
        for line in option_file.read_text().splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                if key in fields:
                    raise ArtifactError("Duplicate native run metadata")
                fields[key] = value
        seed = fields.get("randomSeedIfFixed")
        if seed not in {"606", "607", "608"} or seed in runs:
            raise ArtifactError("Unexpected/repeated native seed")
        expected = {"country": "UK", "startYear": "2019", "endYear": "2020",
                    "popSize": "2000", "savingRate": str(expected_rate)}
        if any(fields.get(key) != value for key, value in expected.items()):
            raise ArtifactError("Native run metadata does not match the submitted settings")
        run = option_file.parent.parent
        csvs = {p.name: csv_summary(p) for p in sorted((run / "csv").glob("*.csv"))}
        for name in ("Person.csv", "BenefitUnit.csv"):
            if name not in csvs or csvs[name]["years"] != ["2019", "2020"]:
                raise ArtifactError("Required annual microdata missing or incomplete")
        run_evidence = destination / seed
        run_evidence.mkdir(mode=0o700)
        shutil.copy2(option_file, run_evidence / "options.txt")
        shutil.copytree(run / "csv", run_evidence / "csv")
        runs[seed] = csvs
    return runs


def same_scientific_outputs(reference, candidate):
    if reference.keys() != candidate.keys():
        raise ArtifactError("Native and normalised seed sets differ")
    differences = []
    for seed in reference:
        if reference[seed].keys() != candidate[seed].keys():
            raise ArtifactError("Native and normalised output file sets differ")
        for name in reference[seed]:
            if reference[seed][name]["scientific_sha256"] != candidate[seed][name]["scientific_sha256"]:
                differences.append(f"{seed}/{name}")
    if differences:
        raise ArtifactError("Scientific CSV differences: " + ", ".join(differences))


def keyed_rows(path):
    """Read retained local proof CSVs without discarding duplicate entity/time keys."""
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        header = reader.fieldnames or []
        identifiers = [name for name in header if name.startswith("id_")]
        if len(header) != len(set(header)) or len(identifiers) != 1 or "time" not in header:
            raise ArtifactError("Expected one entity ID and time in proof CSV")
        rows = {}
        for row in reader:
            if None in row or None in row.values():
                raise ArtifactError("Incomplete proof CSV record")
            key = (row["time"], row[identifiers[0]])
            if key in rows:
                raise ArtifactError("Duplicate entity/time in proof CSV")
            rows[key] = row
    return header, rows


def differing_columns(reference, candidate):
    """Summarise differences without putting row contents/identifiers in diagnostics."""
    before_header, before = keyed_rows(reference)
    after_header, after = keyed_rows(candidate)
    columns, by_time = Counter(), {}
    for key in before.keys() & after.keys():
        for column in (set(before_header) & set(after_header)) - {"run"}:
            if before[key][column] != after[key][column]:
                columns[column] += 1
                by_time.setdefault(key[0], Counter())[column] += 1
    return {"reference_rows": len(before), "candidate_rows": len(after),
            "missing_entity_times": len(before.keys() - after.keys()),
            "added_entity_times": len(after.keys() - before.keys()),
            "missing_columns": sorted(set(before_header) - set(after_header)),
            "added_columns": sorted(set(after_header) - set(before_header)),
            "columns": dict(sorted(columns.items())),
            "by_time": {key: dict(sorted(value.items())) for key, value in sorted(by_time.items())}}


def scenario_income_changes(evidence):
    # Wealth projection is disabled in this profile. Person.updateNonLabourIncome
    # applies savingRate / SAVINGS_RATE to capital/pension income instead.
    result = {}
    for seed in ("606", "607", "608"):
        tables = []
        for label in ("normalised-baseline", "normalised-scenario"):
            header, rows = keyed_rows(Path(evidence) / label / seed / "csv/Person.csv")
            if "yMiscPersGrossMonth" not in header:
                raise ArtifactError("Missing non-labour income field for saving-rate check")
            tables.append(rows)
        before, after = tables
        matched = changed = 0
        for key in before.keys() & after.keys():
            if Decimal(key[0]) != 2020:
                continue
            try:
                values = [float(table[key]["yMiscPersGrossMonth"]) for table in tables]
            except ValueError:
                continue  # Native missing values are not evidence of an income effect.
            if all(math.isfinite(value) for value in values):
                matched += 1
                changed += values[0] != values[1]
        result[seed] = {"matched_finite_2020_records": matched, "changed_records": changed}
    return {"field": "Person.yMiscPersGrossMonth", "by_seed": result,
            "passed": all(item["changed_records"] > 0 for item in result.values())}


def check_results(attempts, evidence, report):
    # Record independent checks even when baseline equality fails; do not hide
    # real scientific differences by excluding further fields from comparison.
    report["scenario_effect"] = scenario_income_changes(evidence)
    reference, candidate = attempts["native-reference"]["runs"], attempts["normalised-baseline"]["runs"]
    report["csv_differences"] = {}
    for seed in reference.keys() & candidate.keys():
        for name in reference[seed].keys() & candidate[seed].keys():
            if reference[seed][name]["scientific_sha256"] != candidate[seed][name]["scientific_sha256"]:
                report["csv_differences"][f"{seed}/{name}"] = differing_columns(
                    Path(evidence) / "native-reference" / seed / "csv" / name,
                    Path(evidence) / "normalised-baseline" / seed / "csv" / name)
    same_scientific_outputs(reference, candidate)
    if not report["scenario_effect"]["passed"]:
        raise ArtifactError("Saving-rate scenario has no exported non-labour income effect")


def compare(prepared, output, timeout_seconds=1200):
    require_local_runtime()
    if timeout_seconds <= 0:
        raise ValueError("A positive execution deadline is required")
    prepared, output = Path(prepared).absolute(), Path(output).absolute()
    output.mkdir(mode=0o700)
    report = {"passed": False, "scope": "local public training, 2000 people, 2019-2020, three repetitions",
              "persistence": "root", "ignored_csv_columns": ["run"], "attempts": {}}
    try:
        write_attribution(output)
        receipt = json.loads((prepared / "receipt.json").read_text())
        identity = receipt["identity"]
        if identity["format"] != RECEIPT_VERSION or digest(identity) != receipt["sha256"]:
            raise ArtifactError("Invalid local preparation receipt")
        if fingerprint(prepared / "model.jar") != identity["model"]:
            raise ArtifactError("Prepared model JAR has changed")
        verify(prepared / "input", identity["prepared"])
        report["prepared_sha256"] = receipt["sha256"]
        report["model"] = identity["model"]
        snapshot = proof_configuration()
        write_json(output / "configuration.json", snapshot.as_dict())
        report["configuration_sha256"] = snapshot.configuration_sha256
        configurations = [("native-reference", (HERE / "native_reference.yml").read_text(), 0.04),
                          ("normalised-baseline", snapshot.native_yaml("savings-0001"), 0.04),
                          ("normalised-scenario", snapshot.native_yaml("savings-0002"), 0.06)]
        for label, configuration, rate in configurations:
            print(f"Running {label}: seeds 606, 607, 608", flush=True)
            evidence = output / label
            evidence.mkdir(mode=0o700)
            (evidence / "native.yml").write_text(configuration)
            size = sum(item["bytes"] for item in identity["prepared"].values())
            # Native MultiRun additionally copies workbooks/DB for its first run.
            if shutil.disk_usage(output).free < 2 * size + 1024**3:
                raise ArtifactError("Insufficient space for a run workspace and native output copy")
            with tempfile.TemporaryDirectory(prefix="attempt-", dir=output) as temporary:
                workspace = Path(temporary)
                copy_verified(prepared / "input", identity["prepared"], workspace / "input")
                (workspace / "config").mkdir()
                (workspace / "config" / "proof.yml").write_text(configuration)
                # Explicitly preserve native root population reuse within each
                # Run Set; each independent Run Set starts from a fresh copy.
                seconds = run_java(java_command(prepared / "model.jar",
                                   "simpaths.experiment.SimPathsMultiRun", "-config", "proof.yml", "-P", "root"),
                                   workspace, evidence / "native.log", timeout_seconds)
                runs = inspect_outputs(workspace / "output", evidence, rate)
            report["attempts"][label] = {"seconds": seconds, "runs": runs}
        check_results(report["attempts"], output, report)
        verify(prepared / "input", identity["prepared"])
        if fingerprint(prepared / "model.jar") != identity["model"]:
            raise ArtifactError("Model JAR changed during proof")
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()
    try:
        compare(args.prepared, args.output, args.timeout_seconds)
    except (ArtifactError, RuntimeError, OSError) as error:
        print(f"FAILED: {error}. Evidence: {args.output}", file=sys.stderr)
        return 1
    print(f"PASS: native outputs match and scenario changes output. Evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
