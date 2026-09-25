"""(C) Copyright 2026, by Ross Richardson

Prepared training dataset provenance, reuse, compatibility and safe import tests.

@author ross richardson
"""
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.multirun.artifacts import ArtifactError, copy_verified, digest, fingerprint, inventory, write_json
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.configuration import normalise
from deploy.multirun.container_adapter import SimPathsContainerAdapter, container_submission
from deploy.multirun.import_quickstart import extract_inputs, import_dataset, UnconfirmedVerification
from deploy.multirun.prepared_dataset import (FORMAT, SOURCE, allocation, check_receipt,
    read_quickstart, revision, verify_snapshot)
from deploy.multirun.queue_adapter import read_prepared, submission_arguments

IMAGE = "sha256:" + "b" * 64


def profile(population):
    return dict(format_version=1, profile_id=f"uk-2019-training-{population}-seed606",
        profile=dict(country="UK", start_year=2019, end_year=2026, requested_population=population,
                     seed=606, include_observer=True, use_weights=False,
                     ignore_population_targets=False, training_data=True),
        actual_counts=dict(person=population - 1, household=100, benefitunit=120),
        fresh_jvm_loading_verified=True)


def inputs(path):
    path.mkdir(mode=0o700)
    for name in ("input.mv.db", "DatabaseCountryYear.xlsx", "EUROMODpolicySchedule.xlsx",
                 "EUROMODoutput/training/EUROMODpolicySchedule.xlsx"):
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture-" + target.name.encode())


class PreparedDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def dataset(self, population=20000):
        path = self.root / str(population)
        path.mkdir()
        (path / "model.jar").write_bytes(b"model-jar")
        inputs(path / "input")
        manifest = inventory(path / "input")
        identity = dict(format=FORMAT, source=SOURCE, country="UK", start_year=2019,
            policy_years=[2011, 2026], population=population, source_image=IMAGE,
            quickstart_profile=profile(population), model=fingerprint(path / "model.jar"),
            source_inputs=manifest, prepared=manifest)
        receipt = dict(identity=identity, sha256=digest(identity), revision=revision(identity))
        write_json(path / "receipt.json", receipt)
        return path, receipt

    def config(self, receipt):
        value = proof_configuration().editable_configuration()
        value["dataset_revision"] = receipt["revision"]
        value["common"]["population"] = receipt["identity"]["population"]
        return value

    def test_both_profiles_submit_with_fixed_dataset_and_shared_seeds(self):
        for population in (20000, 50000):
            path, receipt = self.dataset(population)
            self.assertEqual(read_prepared(path), receipt)
            self.assertEqual(read_quickstart(path), receipt)
            arguments = container_submission(self.config(receipt), path, IMAGE)
            self.assertEqual(arguments["dataset_id"], receipt["revision"])
            self.assertEqual(arguments["seed_plan"], ["606", "607", "608"])
            self.assertEqual(len(arguments["run_sets"]), 2)

    def test_wrong_population_year_revision_or_preparation_settings_rejected(self):
        path, receipt = self.dataset()
        for field, value in (("population", 50000), ("start_year", 2018), ("end_year", 2027)):
            config = self.config(receipt)
            config["common"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                submission_arguments(config, path)
        config = self.config(receipt)
        config["dataset_revision"] = "another-owner-dataset"
        with self.assertRaises(ArtifactError):
            submission_arguments(config, path)
        for setting in ("useWeights", "ignoreTargetsAtPopulationLoad"):
            config = normalise(self.config(receipt)).as_dict()
            config["run_sets"][0]["model_args"][setting] = True
            from deploy.multirun.prepared_dataset import validate_selection
            with self.assertRaises(ArtifactError):
                validate_selection(config, receipt)

    def test_revision_changes_when_jar_inputs_or_source_image_change(self):
        _, receipt = self.dataset()
        original = receipt["revision"]
        for field in ("model", "prepared", "source_image"):
            identity = json.loads(json.dumps(receipt["identity"]))
            if field == "model":
                identity[field]["sha256"] = "c" * 64
            elif field == "prepared":
                identity[field]["input.mv.db"]["sha256"] = "c" * 64
            else:
                identity[field] = "sha256:" + "c" * 64
            self.assertNotEqual(revision(identity), original)

    def test_independent_writable_copies_leave_reusable_original_unchanged(self):
        path, receipt = self.dataset()
        for name in ("run-set-a", "run-set-b", "later-experiment"):
            copy_verified(path / "input", receipt["identity"]["prepared"], self.root / name)
        (self.root / "run-set-a/input.mv.db").write_bytes(b"changed-by-run-a")
        verify_snapshot(path, receipt)
        self.assertEqual(inventory(self.root / "run-set-b"), receipt["identity"]["prepared"])
        self.assertEqual(inventory(self.root / "later-experiment"), receipt["identity"]["prepared"])

    def test_changed_extra_or_linked_inputs_are_rejected_before_dispatch(self):
        path, receipt = self.dataset()
        extra = path / "input/extra.xlsx"
        extra.write_bytes(b"unrecorded")
        with self.assertRaises(ArtifactError):
            verify_snapshot(path, receipt)
        extra.unlink()
        target = path / "input/input.mv.db"
        original = target.read_bytes()
        target.write_bytes(b"different")
        with self.assertRaises(ArtifactError):
            verify_snapshot(path, receipt)
        target.unlink()
        outside = self.root / "outside"
        outside.write_bytes(original)
        target.symlink_to(outside)
        with self.assertRaises(ArtifactError):
            verify_snapshot(path, receipt)

    def test_50000_uses_larger_heap_and_rejects_smaller_allocation(self):
        path, receipt = self.dataset(50000)
        arguments = container_submission(self.config(receipt), path, IMAGE)
        lease = SimpleNamespace(specification={**arguments, "seeds": arguments["seed_plan"],
            "prepared_fingerprint": receipt["sha256"]}, configuration_id="savings-0001",
            resources=allocation(receipt))
        request = self.root / "request"
        request.mkdir()
        adapter = SimPathsContainerAdapter(path, IMAGE)
        self.assertEqual(adapter.container_command(lease, request).argv[-1], "3g")
        self.assertEqual(lease.resources["memory_mib"], 5120)
        lease.resources["memory_mib"] = 4096
        with self.assertRaises(ArtifactError):
            adapter.container_command(lease, request)
        with self.assertRaises(ArtifactError):
            SimPathsContainerAdapter(path, "sha256:" + "c" * 64)

    def test_receipt_tampering_or_nonpublic_source_rejected(self):
        _, receipt = self.dataset()
        receipt["identity"]["source"] = "user-uploaded-db"
        receipt["sha256"] = digest(receipt["identity"])
        receipt["revision"] = revision(receipt["identity"])
        with self.assertRaises(ArtifactError):
            check_receipt(receipt)

    def archive(self, entries):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, kind in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.size = 3 if kind == tarfile.REGTYPE else 0
                member.linkname = "/tmp/outside" if kind == tarfile.SYMTYPE else ""
                archive.addfile(member, io.BytesIO(b"abc") if member.size else None)
        stream.seek(0)
        return stream

    def test_stream_import_copies_only_ordinary_known_inputs(self):
        output = self.root / "input"
        extract_inputs(self.archive([("input", tarfile.DIRTYPE), ("input/input.mv.db", tarfile.REGTYPE),
                                    ("input/nested/parameters.xlsx", tarfile.REGTYPE)]), output)
        self.assertEqual((output / "input.mv.db").read_bytes(), b"abc")
        self.assertEqual((output / "nested/parameters.xlsx").read_bytes(), b"abc")

    def test_stream_import_rejects_traversal_links_duplicates_and_unexpected_files(self):
        cases = [[("input/../escape.xlsx", tarfile.REGTYPE)], [("input/x.xlsx", tarfile.SYMTYPE)],
                 [("input/x.xlsx", tarfile.LNKTYPE)], [("input/x.xlsx", tarfile.FIFOTYPE)],
                 [("elsewhere/x.xlsx", tarfile.REGTYPE)], [("input/code.py", tarfile.REGTYPE)],
                 [("input/x.xlsx", tarfile.REGTYPE), ("input/x.xlsx", tarfile.REGTYPE)]]
        for index, entries in enumerate(cases):
            with self.subTest(entries=entries), self.assertRaises(ArtifactError):
                extract_inputs(self.archive(entries), self.root / str(index))

    def test_stream_import_enforces_total_size_and_disk_reserve(self):
        entries = [("input/input.mv.db", tarfile.REGTYPE)]
        with self.assertRaises(ArtifactError):
            extract_inputs(self.archive(entries), self.root / "limit", maximum_bytes=2)
        with patch("deploy.multirun.import_quickstart.shutil.disk_usage") as disk:
            disk.return_value.free = 1024**3
            with self.assertRaises(ArtifactError):
                extract_inputs(self.archive(entries), self.root / "disk")

    def test_import_publishes_only_after_verification_and_keeps_immutable_image(self):
        jar = self.root / "model.jar"
        jar.write_bytes(b"jar")
        output = self.root / "imported"
        def check(path, image, frontend):
            self.assertFalse((path / "receipt.json").exists())
            self.assertEqual(image, IMAGE)
        with patch("deploy.multirun.import_quickstart.docker") as docker, \
             patch("deploy.multirun.import_quickstart.image_profile", return_value=profile(20000)), \
             patch("deploy.multirun.import_quickstart.copy_image_inputs", side_effect=lambda c, p: inputs(p)), \
             patch("deploy.multirun.import_quickstart.verify_in_container", side_effect=check):
            docker.side_effect = [json.dumps([{"Id": IMAGE, "Config": {}}]), "container-id", "container-id"]
            receipt = import_dataset("mutable-tag", 20000, jar, output, self.root)
        self.assertEqual(read_quickstart(output), receipt)
        self.assertEqual(receipt["identity"]["source_image"], IMAGE)
        self.assertEqual((output / "input/input.mv.db").stat().st_mode & 0o777, 0o400)
        verify_snapshot(output, receipt)

    def test_failed_verification_never_publishes_and_removes_large_copies(self):
        jar = self.root / "model.jar"
        jar.write_bytes(b"jar")
        output = self.root / "failed"
        with patch("deploy.multirun.import_quickstart.docker") as docker, \
             patch("deploy.multirun.import_quickstart.image_profile", return_value=profile(20000)), \
             patch("deploy.multirun.import_quickstart.copy_image_inputs", side_effect=lambda c, p: inputs(p)), \
             patch("deploy.multirun.import_quickstart.verify_in_container", side_effect=ArtifactError("incompatible")):
            docker.side_effect = [json.dumps([{"Id": IMAGE, "Config": {}}]), "container-id", "container-id"]
            with self.assertRaises(ArtifactError):
                import_dataset("tag", 20000, jar, output, self.root)
        self.assertFalse((output / "receipt.json").exists())
        self.assertFalse((output / "input").exists())
        self.assertFalse((output / "model.jar").exists())

    def test_uncertain_container_retains_inputs_without_publishing(self):
        jar = self.root / "model.jar"
        jar.write_bytes(b"jar")
        output = self.root / "uncertain"
        with patch("deploy.multirun.import_quickstart.docker") as docker, \
             patch("deploy.multirun.import_quickstart.image_profile", return_value=profile(20000)), \
             patch("deploy.multirun.import_quickstart.copy_image_inputs", side_effect=lambda c, p: inputs(p)), \
             patch("deploy.multirun.import_quickstart.verify_in_container", side_effect=UnconfirmedVerification()):
            docker.side_effect = [json.dumps([{"Id": IMAGE, "Config": {}}]), "container-id", "container-id"]
            with self.assertRaises(UnconfirmedVerification):
                import_dataset("tag", 20000, jar, output, self.root)
        self.assertFalse((output / "receipt.json").exists())
        self.assertTrue((output / "input").is_dir())


if __name__ == "__main__":
    unittest.main()
