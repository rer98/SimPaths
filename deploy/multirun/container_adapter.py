"""(C) Copyright 2026, by Ross Richardson

SimPaths-owned Docker adapter for the bounded, public-data MultiRun queue proof.
Platform execution/isolation belongs to JAS-mine-web; Java model code is unchanged.

@author ross richardson
"""
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from types import SimpleNamespace

from .artifacts import ArtifactError, relative_name
from .queue_adapter import SimPathsLocalAdapter, require_workspace_space, submission_arguments
from .prepared_dataset import FORMAT, INPUT_FORMAT, allocation, verify_snapshot


@dataclass(frozen=True)
class ContainerExecution:
    image: str
    argv: tuple
    inputs: str


class SimPathsContainerAdapter(SimPathsLocalAdapter):
    def __init__(self, prepared, image):
        super().__init__(prepared)
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ArtifactError("Resolve the approved runtime image ID before submission")
        if (self.receipt["identity"]["format"] in (FORMAT, INPUT_FORMAT)
                and self.receipt["identity"]["source_image"] != image):
            raise ArtifactError("Revalidate the prepared dataset before changing its runtime image")
        self.image = image

    def _configuration(self, lease):
        if lease.specification["model_digest"] != self.image:
            raise ArtifactError("Queued runtime image changed")
        # The prepared receipt binds the JAR and data; the queue separately pins
        # the complete runtime image. Reuse model validation with the JAR identity.
        local = {**lease.specification, "model_digest": "sha256:" + self.receipt["identity"]["model"]["sha256"]}
        return super()._configuration(SimpleNamespace(specification=local, configuration_id=lease.configuration_id))

    def container_command(self, lease, request):
        config = self._configuration(lease)
        minimum = allocation(self.receipt)
        if config.as_dict()['common']['population'] > 20000:
            minimum['memory_mib'] = 5120
        if any(lease.resources[key] < value for key, value in minimum.items()):
            raise ArtifactError("Allocation is below the prepared example's CPU, RAM or storage requirement")
        if self.receipt["identity"]["format"] in (FORMAT, INPUT_FORMAT):
            verify_snapshot(self.prepared, self.receipt)
        require_workspace_space(self.receipt["identity"], request)
        (request / "run.yml").write_text(config.native_yaml(lease.configuration_id))
        shutil.copyfile(Path(__file__).with_name("container_run.sh"), request / "run.sh")
        manifest = []
        identity = self.receipt["identity"]
        for name, item in {"model.jar": identity["model"],
                           **{"input/" + k: v for k, v in identity["prepared"].items()}}.items():
            relative_name(name)
            if not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                raise ArtifactError("Invalid prepared input checksum")
            manifest.append(item["sha256"] + "  /inputs/" + name)
        (request / "inputs.sha256").write_text("\n".join(manifest) + "\n")
        (request / "input-files.txt").write_text("".join(name + "\n" for name in sorted(identity["prepared"])))
        heap = "3g" if config.as_dict()['common']['population'] > 20000 else "2g"
        return ContainerExecution(self.image, ("/bin/sh", "/request/run.sh", heap), str(self.prepared))


def container_submission(configuration, prepared, image):
    # Instantiate to validate both immutable identities before accepting the job.
    adapter = SimPathsContainerAdapter(prepared, image)
    arguments = submission_arguments(configuration, prepared)
    return {**arguments, "model_digest": adapter.image}
