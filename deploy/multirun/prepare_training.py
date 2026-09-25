"""(C) Copyright 2026, by Ross Richardson

Prepare a fresh, fingerprinted public training dataset for local MultiRun proofs.
Uses the existing isolated preparation main class; no SingleRun state is changed.

@author ross richardson
"""

import argparse
import os
from pathlib import Path
import shutil

from .artifacts import ArtifactError, digest, fingerprint, inventory, snapshot_files, write_attribution, write_json
from .local_process import java_command, require_local_runtime, run_java

ROOT = Path(__file__).resolve().parents[2]
RECEIPT_VERSION = "simpaths.multirun.prepared-training.v1-draft"


def training_sources(input_directory):
    """Only bundled CSV/UKMOD examples and model parameter workbooks; never a DB."""
    root = Path(input_directory)
    sources = {p.name: p for p in root.glob("*.xlsx")
               if p.name not in ("DatabaseCountryYear.xlsx", "EUROMODpolicySchedule.xlsx")}
    training = root / "EUROMODoutput" / "training"
    sources["EUROMODpolicySchedule.xlsx"] = training / "EUROMODpolicySchedule.xlsx"
    sources["InitialPopulations/population_initial_UK_2019.csv"] = (
        root / "InitialPopulations" / "training" / "population_initial_UK_2019.csv")
    # The bundled schedule refers to every annual policy through 2026. Keep the
    # native schedule unchanged, including policies before the simulation starts.
    for year in range(2011, 2027):
        name = f"uk_{year}_std.txt"
        sources["EUROMODoutput/" + name] = training / name
    return sources


def prepare(input_directory, jar, output, timeout_seconds=3600):
    require_local_runtime()
    if timeout_seconds <= 0:
        raise ValueError("A positive execution deadline is required")
    output = Path(output).absolute()
    output.mkdir(mode=0o700)  # Refuse reuse of any existing destination.
    try:
        write_attribution(output)
        sources = training_sources(input_directory)
        required = sum(p.stat().st_size for p in sources.values()) + 3 * 1024**3
        if shutil.disk_usage(output).free < required:
            raise ArtifactError("Insufficient space for a source copy, prepared database and 2 GiB headroom")
        model = fingerprint(jar, output / "model.jar")
        working = output / "working"
        working.mkdir(mode=0o700)
        print("Copying and fingerprinting bundled training inputs", flush=True)
        source_files = snapshot_files(sources, working / "input")
        print("Preparing population and donor database in a separate JVM", flush=True)
        seconds = run_java(java_command(output / "model.jar",
                           "simpaths.experiment.SimPathsUserDataPreparation", "2019", heap="3g"),
                           working, output / "preparation.log", timeout_seconds)
        if "WEB_PREP:Preparation complete" not in (output / "preparation.log").read_text():
            raise ArtifactError("Preparation completion marker missing")
        prepared = inventory(working / "input")
        for name in ("input.mv.db", "tax_donor_population_UK.csv", "DatabaseCountryYear.xlsx"):
            if name not in prepared or prepared[name]["bytes"] == 0:
                raise ArtifactError("Expected prepared output is missing or empty")
        # The worker must not rewrite the selected source data or schedule.
        if any(prepared.get(name) != value for name, value in source_files.items()):
            raise ArtifactError("Preparation changed a selected source file")
        identity = {"format": RECEIPT_VERSION, "source": "bundled-public-training",
                    "country": "UK", "start_year": 2019, "policy_years": [2011, 2026],
                    "model": model, "sources": source_files, "prepared": prepared}
        receipt = {"identity": identity, "sha256": digest(identity),
                   "preparation_seconds": seconds}
        (working / "input").rename(output / "input")
        shutil.rmtree(working)
        for path in (output / "input").rglob("*"):
            if path.is_file():
                os.chmod(path, 0o400)
        os.chmod(output / "model.jar", 0o400)
        write_json(output / "receipt.json", receipt)  # Ready only after all checks.
        return receipt
    except BaseException:
        # Keep diagnostics, but do not keep a partial dataset/large failed copy.
        for name in ("working", "input"):
            if (output / name).is_dir():
                shutil.rmtree(output / name)
        (output / "model.jar").unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "input")
    parser.add_argument("--jar", type=Path, default=ROOT / "multirun.jar")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    try:
        receipt = prepare(args.input, args.jar, args.output, args.timeout_seconds)
    except (ArtifactError, RuntimeError, OSError) as error:
        parser.exit(1, f"Preparation stopped: {error}\n")
    print(f"Prepared public dataset: {args.output}/receipt.json ({receipt['sha256']})")


if __name__ == "__main__":
    main()
