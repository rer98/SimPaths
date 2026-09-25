"""(C) Copyright 2026, by Ross Richardson

Import an installed, maintainer-approved Quick Start image as reusable training data.
Never accepts uploaded databases. Reads a stopped image, verifies the selected
model against its saved population in Docker, and publishes a receipt last.

@author ross richardson
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

from deploy._workflow import frontend_path, quickstart_image
from .artifacts import ArtifactError, digest, fingerprint, inventory, relative_name, write_attribution, write_json
from .prepared_dataset import FORMAT, SOURCE, check_receipt, profile_population, revision, verify_snapshot

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]


class UnconfirmedVerification(RuntimeError):
    pass


def docker(*args):
    return subprocess.check_output([*DOCKER, *args], text=True, timeout=60).strip()


def extract_inputs(stream, destination, *, prefix="input", maximum_bytes=8 * 1024**3):
    """Stream only known SimPaths inputs; never extract tar links or metadata."""
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    total, seen = 0, set()
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for count, member in enumerate(archive):
            if count >= 2000:
                raise ArtifactError("Too many image input entries")
            name = member.name.rstrip("/")
            relative_name(name)
            if name in seen:
                raise ArtifactError("Repeated image input path")
            seen.add(name)
            if name == prefix and member.isdir():
                continue
            if not name.startswith(prefix + "/"):
                raise ArtifactError("Unexpected image archive root")
            relative = name[len(prefix) + 1:]
            target = destination / relative
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            if not member.isfile() or member.size < 0:
                raise ArtifactError("Image inputs must contain ordinary files, not links")
            if relative != "input.mv.db" and Path(relative).suffix.lower() not in (".xls", ".xlsx"):
                raise ArtifactError("Unexpected file in prepared Quick Start image")
            total += member.size
            if total > maximum_bytes or shutil.disk_usage(destination).free < member.size + 1024**3:
                raise ArtifactError("Insufficient space for inputs and 1 GiB reserve, or input limit exceeded")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                os.chmod(target, 0o600)
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if target.stat().st_size != member.size:
                raise ArtifactError("Incomplete image input archive")


def copy_image_inputs(container, output):
    with subprocess.Popen([*DOCKER, "cp", container + ":/app/input", "-"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            extract_inputs(process.stdout, output)
            if process.wait(timeout=30):
                raise ArtifactError("Docker did not finish copying the prepared inputs")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def image_profile(container):
    result = subprocess.run([*DOCKER, "cp", container + ":/app/profile.json", "-"],
                            stdout=subprocess.PIPE, check=True, timeout=30)
    import io
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        members = archive.getmembers()
        if (len(members) != 1 or members[0].name != "profile.json"
                or not members[0].isfile() or members[0].size > 1024 * 1024):
            raise ArtifactError("Invalid Quick Start profile archive")
        return json.load(archive.extractfile(members[0]))


def verify_in_container(output, image, frontend):
    # General container isolation/recovery stays in JAS-mine-web.
    sys.path.insert(0, str(frontend))
    from jasmine_web.batch.docker_executor import DockerExecutor, ContainerCommand
    # Keep dispatcher receipts outside the read-only dataset mount, too.
    control = Path(tempfile.mkdtemp(prefix="simpaths-dataset-check-", dir=output.parent))
    classes = control / "classes"
    classes.mkdir(mode=0o700)
    try:
        with (output / "verification-compile.log").open("w") as log:
            subprocess.run(["javac", "-proc:none", "-cp", str(output / "model.jar"),
                            "-d", str(classes), str(Path(__file__).with_name("VerifyPreparedQuickStart.java"))],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    except BaseException:
        shutil.rmtree(control)
        raise
    attempt = str(uuid4())
    lease = SimpleNamespace(attempt_id=attempt, execution_key="batch-" + attempt,
        configuration_id="verify-prepared", specification={"model_digest": image},
        resources=dict(cpu_millis=2000, memory_mib=1024, storage_mib=64),
        deadline=datetime.now(timezone.utc) + timedelta(seconds=180))
    class Adapter:
        def container_command(self, lease, request):
            shutil.copytree(classes, request / "classes")
            return ContainerCommand(image, ("/opt/java/openjdk/bin/java", "-Xmx512m",
                "-XX:ActiveProcessorCount=2", "-Djava.awt.headless=true", "-cp",
                "/request/classes:/inputs/model.jar", "simpaths.experiment.VerifyPreparedQuickStart"), str(output))
    executor = DockerExecutor(control / "attempts", approved_images=[image], input_roots=[output])
    with executor.exclusive():
        try:
            executor.start(lease, Adapter())
            end = time.monotonic() + 210
            while time.monotonic() < end:
                state = executor.inspect(lease)
                if state["state"] == "stopped":
                    break
                time.sleep(0.5)
            else:
                raise ArtifactError("Prepared database verification did not finish")
            log = executor.workspace(lease) / "execution.log"
            shutil.copyfile(log, output / "verification.log")
            if state["outcome"] != "success" or "MULTIRUN_PREPARED_PROFILE_VALIDATED" not in log.read_text():
                raise ArtifactError("Prepared database is incompatible; see verification.log")
        finally:
            try:
                state = executor.inspect(lease)
                if state["state"] != "stopped":
                    executor.stop(lease, "cancelled")
                    end = time.monotonic() + 15
                    while time.monotonic() < end and executor.inspect(lease)["state"] == "running":
                        time.sleep(0.2)
                executor.cleanup(lease)
            except Exception as error:
                raise UnconfirmedVerification("Verification state is uncertain; retain " + str(output)
                                              + " and " + str(control)) from error
            shutil.rmtree(control)


def import_dataset(image, population, jar, output, frontend):
    output, jar = Path(output).absolute(), Path(jar).absolute()
    if output.exists() or output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ArtifactError("Choose a new dataset directory without symlink ancestors")
    if output.is_relative_to(ROOT):
        raise ArtifactError("Keep prepared datasets outside the source checkout")
    metadata = json.loads(docker("image", "inspect", image))[0]
    image = metadata["Id"]
    if metadata["Config"].get("Volumes"):
        raise ArtifactError("Prepared image must not create implicit volumes")
    output.mkdir(mode=0o700)
    write_attribution(output)
    name = "simpaths-dataset-import-" + uuid4().hex
    copying = False
    try:
        model = fingerprint(jar, output / "model.jar")
        copying = True  # Also clean up a create call whose response was lost.
        container = docker("create", "--name", name, "--pull", "never", "--network", "none",
                           "--entrypoint", "/bin/true", image)
        profile = image_profile(container)
        if profile_population(profile) != population:
            raise ArtifactError("Image contains the wrong prepared population")
        write_json(output / "profile.json", profile)
        print("Copying prepared inputs from the installed image", flush=True)
        copy_image_inputs(container, output / "input")
        docker("rm", container)
        copying = False
        source_inputs = inventory(output / "input")
        # Quick Start selects this schedule on every Build. Make that selection
        # explicit once; MultiRun trainingFlag=false must not replace it later.
        training_schedule = output / "input/EUROMODoutput/training/EUROMODpolicySchedule.xlsx"
        if "EUROMODoutput/training/EUROMODpolicySchedule.xlsx" not in source_inputs:
            raise ArtifactError("Prepared training policy schedule is missing")
        shutil.copyfile(training_schedule, output / "input/EUROMODpolicySchedule.xlsx")
        prepared = inventory(output / "input")
        identity = dict(format=FORMAT, source=SOURCE, country="UK", start_year=2019,
                        policy_years=[2011, 2026], population=population, source_image=image,
                        quickstart_profile=profile, model=model, source_inputs=source_inputs, prepared=prepared)
        receipt = dict(identity=identity, sha256=digest(identity), revision=revision(identity))
        check_receipt(receipt)
        print("Checking saved population and donor tables in an isolated container", flush=True)
        verify_in_container(output, image, frontend)
        verify_snapshot(output, receipt)
        for path in [output / "model.jar", *(output / "input").rglob("*")]:
            if path.is_file():
                os.chmod(path, 0o400)
        write_json(output / "receipt.json", receipt)  # The readiness marker is published last.
        return receipt
    except UnconfirmedVerification:
        raise
    except BaseException:
        # Preserve small diagnostics but remove partial large copies.
        shutil.rmtree(output / "input", ignore_errors=True)
        (output / "model.jar").unlink(missing_ok=True)
        raise
    finally:
        if copying:
            subprocess.run([*DOCKER, "rm", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=int, choices=(20000, 50000), required=True)
    parser.add_argument("--frontend", type=Path, default=frontend_path())
    parser.add_argument("--image", help="Override the trusted frontend catalogue image")
    parser.add_argument("--jar", type=Path, default=ROOT / "multirun.jar")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    image = quickstart_image(args.frontend, args.population, args.image)
    print(f"Prepared UK training population: {args.population:,}; source: {image}")
    if not args.apply:
        print("Preview only. Add --apply to copy, verify and publish the reusable dataset.")
        return
    receipt = import_dataset(image, args.population, args.jar, args.output, args.frontend)
    print(f"Ready: {args.output}/receipt.json\nDataset revision: {receipt['revision']}")


if __name__ == "__main__":
    main()
