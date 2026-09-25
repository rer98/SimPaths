"""(C) Copyright 2026, by Ross Richardson

Exercise prepared public SimPaths inputs through the isolated PostgreSQL worker.
Uses JAS-mine-web tooling explicitly; never depends on private planning files.

@author ross richardson
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy.multirun.artifacts import (fingerprint, verify,
                                      write_attribution, write_json)
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.local_process import require_local_runtime
from deploy.multirun.prepare_training import prepare
from deploy.multirun.queue_adapter import SimPathsLocalAdapter, submission_arguments, read_prepared
from deploy.multirun.prepared_dataset import FORMAT, allocation, verify_snapshot
from deploy.multirun.configuration import normalise


def execute_proof(output):
    # Optional platform imports occur only within the explicit queued proof.
    from jasmine_web.batch.local_executor import LocalExecutor
    from jasmine_web.batch.policy import Policy, Resources
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
    from psycopg import sql

    reused = os.environ.get("SIMPATHS_QUEUE_PREPARED")
    if not reused:
        require_local_runtime()
    output = output.absolute()
    output.mkdir(mode=0o700)
    write_attribution(output)
    image = os.environ.get("SIMPATHS_QUEUE_PROOF_IMAGE")
    report = {"passed": False, "scope": "Docker public-data queue execution" if image else "trusted local public-data queue execution",
              "scientific_reproducibility": "Not tested here; reported receipt-flag RNG issue remains open"}
    if image:
        report["runtime_image_id"] = image
    schema = "proof_batch_" + uuid4().hex
    queue = Queue(os.environ["JASMINE_BATCH_TEST_DSN"], "simpaths-local-proof", schema=schema)
    # Large copies use temporary storage, independently of the evidence folder.
    # The laptop's encrypted home filesystem can have much less free space.
    work = Path(tempfile.mkdtemp(prefix="simpaths-queue-proof-"))
    worker = None
    safe_cleanup = True
    try:
        print(f"Temporary model workspaces: {work}", flush=True)
        if reused:
            prepared = Path(reused)
            receipt = read_prepared(prepared)
            verify_snapshot(prepared, receipt)
            if not image:
                raise ValueError("Prepared Quick Start proof requires container execution")
            print("Reusing verified prepared dataset: " + receipt["revision"], flush=True)
            report["reused_dataset_revision"] = receipt["revision"]
        else:
            receipt = prepare(ROOT / "input", ROOT / "multirun.jar", work / "prepared")
            prepared = work / "prepared"
        report["prepared_fingerprint"] = receipt["sha256"]
        configuration = proof_configuration().editable_configuration()
        if reused:
            configuration["dataset_revision"] = receipt["revision"]
            configuration["common"]["population"] = receipt["identity"].get("population", 2000)
            configuration["common"]["start_year"] = receipt["identity"]["start_year"]
            configuration["common"]["end_year"] = receipt["identity"]["start_year"] + 1
        configuration = normalise(configuration).editable_configuration()
        resources = Resources(**allocation(receipt))
        queue.migrate()
        # One JVM at a time on the laptop. The model configurations/seed plan
        # remain the same when production admission allows more concurrent jobs.
        queue.create_pool(resources,
            policy=Policy(per_user_active=1, attempt_seconds=1500, total_seconds=4500))
        queue.approve("local-proof")
        if image:
            from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
            arguments = container_submission(configuration, prepared, image)
        else:
            arguments = submission_arguments(configuration, prepared)
        queue.register_dataset(arguments["dataset_id"], receipt["sha256"])
        queue.grant_dataset("local-proof", arguments["dataset_id"])
        experiment = queue.submit("local-proof", "queue-proof", **arguments, resources=resources,
                                  baseline=arguments["run_sets"][0]["id"])
        report["experiment_id"] = experiment
        if image:
            from jasmine_web.batch.docker_executor import DockerExecutor
            executor = DockerExecutor(work / "attempts", approved_images=[image], input_roots=[prepared])
            adapter = SimPathsContainerAdapter(prepared, image)
        else:
            executor = LocalExecutor(work / "attempts")
            adapter = SimPathsLocalAdapter(prepared)
        worker = Worker(queue, executor, adapter, "proof-worker")
        known_leases = {}
        def tick(**options):
            known_leases.update(worker.leases)
            try:
                return worker.tick(**options)
            finally:
                known_leases.update(worker.leases)
        print("Running two queued Run Sets, each with seeds 606, 607, 608", flush=True)
        finished = set()
        next_progress = time.monotonic() + 30
        safe_cleanup = False
        with worker.open():
            try:
                while True:
                    for job_id, state in tick(claim_new=False):
                        print(f"Run Set {job_id}: {state}", flush=True)
                    snapshot = queue.inspect("local-proof", experiment)
                    for job in snapshot["jobs"]:
                        if job["state"] == "succeeded" and job["id"] not in finished:
                            # Keep small configuration/seed metadata; release the
                            # native database copies before the next configuration.
                            for directory in (work / "attempts").glob("batch-*"):
                                identity_path = directory / "identity.json"
                                if not identity_path.exists():
                                    continue
                                identity = json.loads(identity_path.read_text())
                                if identity["configuration_id"] != job["configuration_id"]:
                                    continue
                                evidence = output / job["configuration_id"]
                                evidence.mkdir(mode=0o700, exist_ok=True)
                                for option in (directory / "work/output").glob("*/input/options.txt"):
                                    shutil.copy2(option, evidence / (option.parent.parent.name + "-options.txt"))
                                for filename in ("exit.json", "execution.log", "identity.json", "container-policy.json", "container.json"):
                                    if (directory / filename).is_file():
                                        shutil.copy2(directory / filename, evidence / filename)
                                if reused and receipt["identity"]["format"] == FORMAT and (directory / "execution.log").read_text().count(
                                        "Found processed dataset - preparing for simulation") != len(snapshot["specification"]["seeds"]):
                                    raise RuntimeError("Not every repetition reused the prepared population")
                                write_json(evidence / "repetitions.json", worker.adapter.validate(
                                    # Reconstruct this completed lease from its
                                    # persisted request; output is still private.
                                    SimpleNamespace(specification=snapshot["specification"],
                                         configuration_id=job["configuration_id"]), directory / "work"))
                                if image:
                                    executor.cleanup(known_leases[identity["attempt_id"]])
                                shutil.rmtree(directory / "work")
                            finished.add(job["id"])
                    states = [job["state"] for job in snapshot["jobs"]]
                    if all(state == "succeeded" for state in states):
                        report["jobs"] = [dict(id=job["id"], configuration_id=job["configuration_id"],
                                               state=job["state"], attempts=job["attempts"]) for job in snapshot["jobs"]]
                        break
                    if any(state in ("review", "cancelled") for state in states):
                        raise RuntimeError("A queued Run Set did not complete; inspect the retained evidence")
                    if not worker.leases:
                        tick()  # Admit after earlier large workspaces were removed.
                    if time.monotonic() >= next_progress:
                        print("Queue states: " + ", ".join(states), flush=True)
                        next_progress = time.monotonic() + 30
                    time.sleep(0.5)
            finally:
                all_stopped = True
                until = time.monotonic() + 15
                for lease in known_leases.values():
                    try:
                        if executor.inspect(lease)["state"] != "stopped":
                            executor.stop(lease, "cancelled")
                        while executor.inspect(lease)["state"] == "running" and time.monotonic() < until:
                            time.sleep(0.1)
                        if executor.inspect(lease)["state"] != "stopped":
                            all_stopped = False
                        elif image:
                            executor.cleanup(lease)
                    except Exception:
                        # A full disk or unreadable receipt is not evidence that
                        # the process stopped. Never erase a live workspace.
                        all_stopped = False
                safe_cleanup = all_stopped
        verify(prepared / "input", receipt["identity"]["prepared"])
        if fingerprint(prepared / "model.jar") != receipt["identity"]["model"]:
            raise RuntimeError("Prepared model changed during proof")
        report["passed"] = True
        if reused and receipt["identity"]["format"] == FORMAT:
            report["processed_population_reused_each_repetition"] = True
    except BaseException as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        prepared = Path(reused) if reused else work / "prepared"
        for name in ("receipt.json", "preparation.log"):
            if (prepared / name).is_file():
                shutil.copy2(prepared / name, output / name)
        if safe_cleanup:
            # Preserve diagnostic logs on failure before removing large data.
            for path in (work / "attempts").glob("batch-*"):
                evidence = output / "attempt-diagnostics" / path.name
                evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
                for name in ("execution.log", "supervisor.log", "exit.json", "identity.json", "container-policy.json", "container.json", "removed.json"):
                    if (path / name).is_file():
                        shutil.copy2(path / name, evidence / name)
            shutil.rmtree(work, ignore_errors=True)
            with queue._connection() as connection:
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        report["workspaces_cleaned"] = safe_cleanup and not work.exists()
        if not report["workspaces_cleaned"]:
            report["retained_workspace"] = str(work)
        write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=Path, help="JAS-mine-web checkout")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container-image", help="Installed approved Temurin 25 image; enables isolated Docker execution")
    parser.add_argument("--prepared", type=Path, help="Reuse validated prepared inputs; do not prepare again")
    parser.add_argument("--execute-proof", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute_proof:
        execute_proof(args.output)
        print(f"PASSED: {args.output / 'report.json'}")
        return 0
    if not args.frontend:
        parser.error("--frontend must identify the JAS-mine-web checkout")
    if not args.prepared:
        require_local_runtime()
        if not (ROOT / "multirun.jar").is_file():
            parser.error("Build multirun.jar first")
    # Generic platform test infrastructure owns PostgreSQL and dependency setup.
    command = [sys.executable, str(args.frontend.resolve() / "scripts/test_batch_queue.py"),
        "--output", str(args.output.resolve()), "--proof-script", str(Path(__file__).resolve()),
        "--proof-requirements", str(Path(__file__).with_name("requirements.txt"))]
    env = dict(os.environ)
    env.pop("SIMPATHS_QUEUE_PROOF_IMAGE", None)
    env.pop("SIMPATHS_QUEUE_PREPARED", None)
    if args.prepared:
        prepared = args.prepared.expanduser().resolve(strict=True)
        receipt = read_prepared(prepared)
        env["SIMPATHS_QUEUE_PREPARED"] = str(prepared)
        args.container_image = args.container_image or receipt["identity"]["source_image"]
    if args.container_image:
        image = subprocess.check_output(["docker", "image", "inspect", args.container_image,
                                          "--format", "{{.Id}}"], text=True).strip()
        env["SIMPATHS_QUEUE_PROOF_IMAGE"] = image
        command.append("--docker-tests")
    return subprocess.call(command, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
