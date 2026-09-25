"""(C) Copyright 2026, by Ross Richardson

Prepare public inputs, compare native MultiRun execution and clean large temporary copies.
Retain receipts, configurations, private logs, CSV outputs and the comparison report.

@author ross richardson
"""

import argparse
from pathlib import Path
import shutil
import sys
import tempfile

from .artifacts import write_attribution, write_json
from .compare_native import compare
from .local_process import require_local_runtime
from .prepare_training import ROOT, prepare


def run(input_directory, jar, output, preparation_timeout=3600, run_timeout=1200):
    require_local_runtime()
    if preparation_timeout <= 0 or run_timeout <= 0:
        raise ValueError("Positive preparation and run deadlines are required")
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    write_attribution(output)
    summary = {"passed": False, "stage": "preparation"}
    try:
        with tempfile.TemporaryDirectory(prefix="work-", dir=output) as temporary:
            prepared = Path(temporary) / "prepared"
            try:
                receipt = prepare(input_directory, jar, prepared, preparation_timeout)
                summary["prepared_sha256"] = receipt["sha256"]
                summary["stage"] = "native comparison"
                compare(prepared, output / "comparison", run_timeout)
                summary.update(passed=True, stage="complete")
            finally:
                for name in ("receipt.json", "preparation.log"):
                    if (prepared / name).is_file():
                        shutil.copy2(prepared / name, output / name)
    except Exception as error:
        summary["error"] = str(error)
        raise
    finally:
        write_json(output / "report.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "input")
    parser.add_argument("--jar", type=Path, default=ROOT / "multirun.jar")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preparation-timeout", type=int, default=3600)
    parser.add_argument("--run-timeout", type=int, default=1200)
    args = parser.parse_args()
    try:
        run(args.input, args.jar, args.output, args.preparation_timeout, args.run_timeout)
    except (ValueError, RuntimeError, OSError) as error:
        print(f"STOPPED: {str(error).rstrip('.')}. Evidence, if started: {args.output}", file=sys.stderr)
        return 1
    print(f"PASS: local MultiRun comparison. Evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
