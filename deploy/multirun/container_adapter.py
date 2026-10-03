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
from .queue_adapter import SimPathsLocalAdapter, WorkspaceSpaceError, require_workspace_space, workspace_required_bytes, submission_arguments
from .prepared_dataset import FORMAT, INPUT_FORMAT, allocation, verify_snapshot


@dataclass(frozen=True)
class ContainerExecution:
    image: str
    argv: tuple
    inputs: str


class SimPathsContainerAdapter(SimPathsLocalAdapter):
    def __init__(self, prepared, image, *, resource_policy=None):
        super().__init__(prepared)
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ArtifactError("Resolve the approved runtime image ID before submission")
        if (self.receipt["identity"]["format"] in (FORMAT, INPUT_FORMAT)
                and self.receipt["identity"]["source_image"] != image):
            raise ArtifactError("Revalidate the prepared dataset before changing its runtime image")
        self.image = image
        from .resource_policy import LEGACY_POLICY, check_policy
        self.resource_policy=check_policy(self.receipt['identity'].get('release',{}).get('resource_policy',
            LEGACY_POLICY if resource_policy is None else resource_policy))

    def _configuration(self, lease):
        if lease.specification["model_digest"] != self.image:
            raise ArtifactError("Queued runtime image changed")
        # The prepared receipt binds the JAR and data; the queue separately pins
        # the complete runtime image. Reuse model validation with the JAR identity.
        local = {**lease.specification, "model_digest": "sha256:" + self.receipt["identity"]["model"]["sha256"]}
        return super()._configuration(SimpleNamespace(specification=local, configuration_id=lease.configuration_id))

    def required_space(self, candidate):
        required=workspace_required_bytes(self.receipt['identity'])
        if self.resource_policy['simulation']['storage']['per_repetition_mib']:
            required=max(required,candidate.resources['storage_mib']*1024**2)
        return required

    def container_command(self, lease, request):
        config = self._configuration(lease)
        minimum = allocation(self.receipt,population=config.as_dict()['common']['population'],
                             repetitions=len(config.as_dict()['seed_plan']['seeds']),
                             resource_policy=self.resource_policy)
        if any(lease.resources[key] < value for key, value in minimum.items()):
            raise ArtifactError("Allocation is below the prepared example's CPU, RAM or storage requirement")
        if self.receipt["identity"]["format"] in (FORMAT, INPUT_FORMAT):
            verify_snapshot(self.prepared, self.receipt)
        try:
            require_workspace_space(self.receipt["identity"], request,minimum_bytes=self.required_space(lease))
        except WorkspaceSpaceError as error:
            # Use the same durable, count-only storage outcome as preparation.
            # Other validation failures must not be misclassified as low space.
            from jasmine_web.batch.docker_executor import InsufficientWorkspaceSpace
            raise InsufficientWorkspaceSpace(error.required_bytes, error.available_bytes) from error
        (request / "run.yml").write_text(config.native_yaml(lease.configuration_id))
        runner = self.prepared/'run.sh' if 'release' in self.receipt['identity'] else Path(__file__).with_name('container_run.sh')
        shutil.copyfile(runner, request / "run.sh")
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
