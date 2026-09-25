"""(C) Copyright 2026, by Ross Richardson

Model-owned queue adapter tests: frozen inputs, native settings and output evidence.

@author ross richardson
"""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.multirun.artifacts import ArtifactError, digest, fingerprint, write_json
from deploy.multirun.compare_native import proof_configuration
from deploy.multirun.prepare_training import RECEIPT_VERSION
from deploy.multirun.queue_adapter import (OPTIONS_NOT_EXPORTED, SimPathsLocalAdapter,
    read_prepared, require_workspace_space, submission_arguments, validate_outputs)


class QueueAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prepared = self.root / "prepared"
        self.prepared.mkdir()
        identity = dict(format=RECEIPT_VERSION, source="bundled-public-training", country="UK",
                        start_year=2019, model={"sha256": "a" * 64, "bytes": 1}, prepared={})
        write_json(self.prepared / "receipt.json", {"identity": identity, "sha256": digest(identity)})
        self.configuration = proof_configuration()
        self.arguments = submission_arguments(self.configuration.editable_configuration(), self.prepared)
        self.spec = dict(self.arguments, seeds=self.arguments["seed_plan"], prepared_fingerprint=digest(identity))
        self.lease = SimpleNamespace(specification=self.spec, configuration_id="savings-0001")
        self.work = self.root / "work"
        self.work.mkdir()

    def outputs(self):
        native = self.configuration.native_configuration("savings-0001")
        settings = dict(country="UK", startYear=2019, endYear=2020, popSize=2000, **native["model_args"])
        settings = {key: value for key, value in settings.items() if key not in OPTIONS_NOT_EXPORTED}
        for seed in self.spec["seeds"]:
            run = self.work / "output" / seed
            (run / "input").mkdir(parents=True)
            (run / "csv").mkdir()
            fields = settings | {"randomSeedIfFixed": seed}
            (run / "input/options.txt").write_text("\n".join(
                f"{key}: {str(value).lower() if isinstance(value, bool) else value}" for key, value in fields.items()))
            for name in ("Person.csv", "BenefitUnit.csv"):
                (run / "csv" / name).write_text(f"run,time,id,value\nrun-{seed},2019,1,2\nrun-{seed},2020,1,3\n")

    def validate(self):
        return validate_outputs(self.work / "output", self.configuration, "savings-0001")

    def test_translation_freezes_each_fixed_configuration_with_same_seed_plan(self):
        self.assertEqual(self.arguments["seed_plan"], ["606", "607", "608"])
        self.assertEqual(len(self.arguments["run_sets"]), 2)
        for item in self.arguments["run_sets"]:
            self.assertEqual(len(item["parameters"]["run_sets"]), 1)
            self.assertNotIn("sweep", item["parameters"])

    def test_command_uses_internal_module_and_only_explicit_environment(self):
        command = SimPathsLocalAdapter(self.prepared).command(self.lease, self.work)
        self.assertEqual(command.argv[-2:], ("deploy.multirun.queue_adapter", "--execute"))
        self.assertEqual(set(command.env), {"PYTHONPATH"})
        request = json.loads((self.work / "request.json").read_text())
        self.assertEqual(request["run_set_id"], "savings-0001")

    def test_wrong_dataset_model_seed_or_run_identity_rejected(self):
        adapter = SimPathsLocalAdapter(self.prepared)
        for key, value in (("prepared_fingerprint", "b" * 64), ("model_digest", "sha256:" + "b" * 64),
                           ("seeds", ["600"]), ("dataset_id", "different")):
            original = self.spec[key]
            self.spec[key] = value
            with self.assertRaises(ArtifactError):
                adapter.command(self.lease, self.work)
            self.spec[key] = original

    def test_unsupported_proof_profile_and_receipt_tampering_rejected(self):
        document = self.configuration.editable_configuration()
        document["common"]["population"] = 50000
        with self.assertRaises(ArtifactError):
            submission_arguments(document, self.prepared)
        receipt = json.loads((self.prepared / "receipt.json").read_text())
        receipt["identity"]["source"] = "unapproved-dataset"
        (self.prepared / "receipt.json").write_text(json.dumps(receipt))
        with self.assertRaises(ArtifactError):
            read_prepared(self.prepared)

    def test_output_validation_requires_exact_seed_plan_and_settings(self):
        self.outputs()
        receipts = self.validate()
        self.assertEqual([item["seed"] for item in receipts], ["606", "607", "608"])
        path = self.work / "output/606/input/options.txt"
        original = path.read_text()
        for updated in (original.replace("randomSeedIfFixed: 606", "randomSeedIfFixed: 607"),
                        original.replace("savingRate: 0.04", "savingRate: 0.07"),
                        original + "\nrandomSeedIfFixed: 606"):
            path.write_text(updated)
            with self.assertRaises(ArtifactError):
                self.validate()
        path.write_text(original)
        path.unlink()
        with self.assertRaises(ArtifactError):
            self.validate()

    def test_empty_truncated_missing_year_and_mixed_run_csvs_rejected(self):
        self.outputs()
        path = self.work / "output/606/csv/Person.csv"
        for contents in ("", "run,time,id\n", "run,time,id\nx,2019\n", "run,time,id\nx,2019,1\n",
                         "run,time,id\nx,2019,1\ny,2020,1\n", "run,time,id\nx,NaN,1\n",
                         "run,time,id\nx,not-a-year,1\n"):
            path.write_text(contents)
            with self.subTest(contents=contents), self.assertRaises(ArtifactError):
                self.validate()

    def test_output_symlinks_rejected(self):
        self.outputs()
        path = self.work / "output/606/csv/Person.csv"
        target = self.root / "other.csv"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(ArtifactError):
            self.validate()

    def test_space_admission_counts_native_snapshot_and_preserves_reserve(self):
        gib = 1024**3
        identity = {"model": {"bytes": 64 * 1024**2}, "prepared": {
            "input.mv.db": {"bytes": gib},
            "parameters.xlsx": {"bytes": 1024**2},
            "legacy.xls": {"bytes": 1024**2},
            "EUROMODoutput/donors.txt": {"bytes": gib},
            "InitialPopulations/population.csv": {"bytes": 20 * 1024**2},
            "tax_donor_population_UK.csv": {"bytes": 30 * 1024**2},
        }}
        # All inputs get one private copy; only the DB and two workbooks get
        # another native output copy. Model JAR and a full GiB remain budgeted.
        required = 4 * gib + (64 + 4 + 20 + 30) * 1024**2
        with patch("deploy.multirun.queue_adapter.shutil.disk_usage") as usage:
            usage.return_value.free = required
            require_workspace_space(identity, self.work)
            usage.return_value.free = required - 1
            with self.assertRaisesRegex(ArtifactError, "need .* GiB; available .* GiB"):
                require_workspace_space(identity, self.work)


if __name__ == "__main__":
    unittest.main()
