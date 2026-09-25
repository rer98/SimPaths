"""(C) Copyright 2026, by Ross Richardson

Test MultiRun snapshot isolation, mutation checks and native proof validation.

@author ross richardson
"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy.multirun.artifacts import ArtifactError, copy_verified, digest, inventory, snapshot_files, verify
from deploy.multirun.compare_native import (
    check_results, csv_summary, differing_columns, inspect_outputs, keyed_rows,
    same_scientific_outputs, scenario_income_changes,
)
from deploy.multirun.local_process import require_local_runtime, run_java
from deploy.multirun.prepare_training import prepare, training_sources
from deploy.multirun.run_local_proof import run


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.csv"
        self.source.write_text("source")

    def test_snapshot_does_not_share_inodes_or_mutate_original(self):
        sealed = self.root / "sealed"
        manifest = snapshot_files({"InitialPopulations/test.csv": self.source}, sealed)
        copy_verified(sealed, manifest, self.root / "attempt")
        copy = self.root / "attempt/InitialPopulations/test.csv"
        copy.write_text("modified by model")
        verify(sealed, manifest)
        self.assertEqual("source", self.source.read_text())
        self.assertNotEqual(self.source.stat().st_ino, copy.stat().st_ino)
        self.assertEqual(0o700, sealed.stat().st_mode & 0o777)

    def test_equal_length_change_addition_and_removal_are_rejected(self):
        sealed = self.root / "sealed"
        manifest = snapshot_files({"test.csv": self.source}, sealed)
        file = sealed / "test.csv"
        timestamp = file.stat().st_mtime_ns
        file.write_text("change")
        os.utime(file, ns=(timestamp, timestamp))
        with self.assertRaises(ArtifactError):
            verify(sealed, manifest)
        file.write_text("source")
        extra = sealed / "extra.csv"
        extra.write_text("extra")
        with self.assertRaises(ArtifactError):
            copy_verified(sealed, manifest, self.root / "attempt")
        self.assertFalse((self.root / "attempt").exists())
        extra.unlink()
        file.unlink()
        with self.assertRaises(ArtifactError):
            verify(sealed, manifest)

    def test_traversal_symlinks_fifo_and_existing_destination_rejected(self):
        for name in ("../test", "/test", "a//b", "a/./b", "C:\\test", "a/../b"):
            with self.subTest(name=name), self.assertRaises(ArtifactError):
                snapshot_files({name: self.source}, self.root / "destination")
        link = self.root / "link"
        link.symlink_to(self.source)
        with self.assertRaises(ArtifactError):
            snapshot_files({"test": link}, self.root / "destination")
        self.assertFalse((self.root / "destination").exists())
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ArtifactError):
            snapshot_files({"test": fifo}, self.root / "destination")
        with self.assertRaises(FileExistsError):
            snapshot_files({"test": self.source}, self.root)
        self.assertEqual("source", self.source.read_text())

    def test_inventory_rejects_nested_symlink(self):
        (self.root / "subdirectory").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ArtifactError):
            inventory(self.root)

    def test_training_selection_excludes_active_database_and_generated_workbooks(self):
        for name in ("DatabaseCountryYear.xlsx", "EUROMODpolicySchedule.xlsx", "input.mv.db", "reg_wages.xlsx"):
            (self.root / name).touch()
        sources = training_sources(self.root)
        self.assertNotIn("input.mv.db", sources)
        self.assertNotIn("DatabaseCountryYear.xlsx", sources)
        self.assertEqual(self.root / "EUROMODoutput/training/EUROMODpolicySchedule.xlsx",
                         sources["EUROMODpolicySchedule.xlsx"])
        self.assertIn("reg_wages.xlsx", sources)

    def test_scientific_comparison_ignores_only_run_column_and_row_order(self):
        csv = self.root / "a.csv"
        csv.write_text("run,time,id_Person,value\nfirst,2019.0,1,4.0\nfirst,2020.0,1,5.0\n")
        before = csv_summary(csv)
        csv.write_text("run,time,id_Person,value\nsecond,2020.0,1,5.0\nsecond,2019.0,1,4.0\n")
        after = csv_summary(csv)
        self.assertEqual(["2019", "2020"], before["years"])
        same_scientific_outputs({"606": {"Person.csv": before}}, {"606": {"Person.csv": after}})
        csv.write_text("run,time,id_Person,value\nsecond,2020.0,1,6.0\nsecond,2019.0,1,4.0\n")
        with self.assertRaises(ArtifactError):
            same_scientific_outputs({"606": {"Person.csv": before}},
                                    {"606": {"Person.csv": csv_summary(csv)}})

    def test_empty_truncated_and_missing_repetitions_fail(self):
        for content in ("", "run,time,value\n", "run,time,value\nx,2019\n",
                        "run,time,value\nx,2019,1\ny,2020,2\n", "run,time,value\nx,NaN,1\n"):
            self.source.write_text(content)
            with self.subTest(content=content), self.assertRaises(ArtifactError):
                csv_summary(self.source)
        with self.assertRaises(ArtifactError):
            inspect_outputs(self.root, self.root, 0.04)

    def test_subprocess_deadline_and_nonzero_exit_are_failures(self):
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            run_java(["/bin/sleep", "10"], self.root, self.root / "timeout.log", 0.05)
        with self.assertRaisesRegex(RuntimeError, "status 1"):
            run_java(["/bin/false"], self.root, self.root / "failure.log", 2)

    def test_socket_preflight_fails_before_copying(self):
        with patch("deploy.multirun.local_process.socket.socket", side_effect=PermissionError):
            with self.assertRaisesRegex(RuntimeError, "local socket"):
                require_local_runtime()
            with self.assertRaisesRegex(RuntimeError, "local socket"):
                prepare(self.root, self.source, self.root / "prepared")
        self.assertFalse((self.root / "prepared").exists())

    def test_receipt_only_written_after_complete_valid_preparation(self):
        def worker(command, workspace, log, timeout):
            for name in ("input.mv.db", "tax_donor_population_UK.csv", "DatabaseCountryYear.xlsx"):
                (workspace / "input" / name).write_text("prepared")
            log.write_text("WEB_PREP:Preparation complete\n")
            return 1.0

        with patch("deploy.multirun.prepare_training.require_local_runtime"), \
             patch("deploy.multirun.prepare_training.training_sources", return_value={"test.csv": self.source}), \
             patch("deploy.multirun.prepare_training.shutil.disk_usage") as disk, \
             patch("deploy.multirun.prepare_training.run_java", side_effect=worker):
            disk.return_value.free = 10 * 1024**3
            ready = self.root / "prepared"
            receipt = prepare(self.root, self.source, ready)
            self.assertEqual(receipt, json.loads((ready / "receipt.json").read_text()))
            self.assertEqual(digest(receipt["identity"]), receipt["sha256"])
            verify(ready / "input", receipt["identity"]["prepared"])
            self.assertFalse((ready / "working").exists())
            self.assertEqual(0o400, (ready / "input/test.csv").stat().st_mode & 0o777)

    def test_failed_preparation_cleans_copies_without_deleting_logs_or_source(self):
        def worker(command, workspace, log, timeout):
            log.write_text("diagnostic retained")
            raise RuntimeError("failed")

        with patch("deploy.multirun.prepare_training.require_local_runtime"), \
             patch("deploy.multirun.prepare_training.training_sources", return_value={"test.csv": self.source}), \
             patch("deploy.multirun.prepare_training.shutil.disk_usage") as disk, \
             patch("deploy.multirun.prepare_training.run_java", side_effect=worker):
            disk.return_value.free = 10 * 1024**3
            ready = self.root / "failed"
            with self.assertRaises(RuntimeError):
                prepare(self.root, self.source, ready)
            self.assertEqual({"preparation.log", "COPYRIGHT.md"}, {p.name for p in ready.iterdir()})
            self.assertEqual("source", self.source.read_text())

    def test_complete_native_outputs_require_expected_seeds_settings_and_years(self):
        for index, seed in enumerate((606, 607, 608)):
            run = self.root / "output" / str(seed)
            (run / "input").mkdir(parents=True)
            (run / "csv").mkdir()
            (run / "input/options.txt").write_text(
                f"country: UK\nstartYear: 2019\nendYear: 2020\npopSize: 2000\n"
                f"savingRate: 0.04\nrandomSeedIfFixed: {seed}\n")
            for name in ("Person", "BenefitUnit"):
                (run / "csv" / (name + ".csv")).write_text(
                    f"run,time,id_{name},wealthTotValue\n{index},2019.0,1,2\n{index},2020.0,1,3\n")
        evidence = self.root / "evidence"
        evidence.mkdir()
        results = inspect_outputs(self.root / "output", evidence, 0.04)
        self.assertEqual({"606", "607", "608"}, set(results))
        self.assertIsNotNone(results["606"]["BenefitUnit.csv"]["wealth_sha256"])
        # Mismatched metadata is rejected before copying any evidence.
        with self.assertRaises(ArtifactError):
            inspect_outputs(self.root / "output", self.root / "wrong", 0.06)

    def test_combined_proof_removes_temporary_data_on_success_and_failure(self):
        def preparation(input_directory, jar, destination, timeout):
            destination.mkdir()
            (destination / "input").mkdir()
            (destination / "input/input.mv.db").write_text("temporary database")
            (destination / "receipt.json").write_text('{"sha256":"example"}')
            (destination / "preparation.log").write_text("diagnostic")
            return {"sha256": "example"}

        for failure in (None, RuntimeError("comparison failed")):
            with self.subTest(failure=failure), \
                 patch("deploy.multirun.run_local_proof.require_local_runtime"), \
                 patch("deploy.multirun.run_local_proof.prepare", side_effect=preparation), \
                 patch("deploy.multirun.run_local_proof.compare", side_effect=failure):
                output = self.root / ("failed-proof" if failure else "passed-proof")
                if failure:
                    with self.assertRaises(RuntimeError):
                        run(self.root, self.source, output)
                else:
                    run(self.root, self.source, output)
                self.assertEqual(not bool(failure), json.loads((output / "report.json").read_text())["passed"])
                self.assertEqual([], list(output.glob("work-*")))
                self.assertTrue((output / "receipt.json").is_file())
                self.assertEqual("diagnostic", (output / "preparation.log").read_text())

    def test_column_diagnostics_preserve_scientific_failures_without_exposing_rows(self):
        before, after = self.root / "before.csv", self.root / "after.csv"
        before.write_text("run,time,id_BenefitUnit,yBenUCReceivedFlag\na,2020,90001,0\n")
        after.write_text("run,time,id_BenefitUnit,yBenUCReceivedFlag\nb,2020,90001,1\n")
        result = differing_columns(before, after)
        self.assertEqual({"yBenUCReceivedFlag": 1}, result["columns"])
        self.assertEqual({"2020": {"yBenUCReceivedFlag": 1}}, result["by_time"])
        self.assertNotIn("90001", json.dumps(result))
        with self.assertRaises(ArtifactError):
            same_scientific_outputs({"606": {"BenefitUnit.csv": csv_summary(before)}},
                                    {"606": {"BenefitUnit.csv": csv_summary(after)}})
        before.write_text("run,time,id_BenefitUnit,value\na,2020,1,1\na,2020,1,2\n")
        with self.assertRaisesRegex(ArtifactError, "Duplicate entity/time"):
            keyed_rows(before)

    def test_scenario_income_effect_requires_each_seed_and_survives_baseline_failure(self):
        attempts = {}
        for label, income in (("native-reference", 1.0), ("normalised-baseline", 1.0),
                              ("normalised-scenario", 2.0)):
            attempts[label] = {"runs": {}}
            for seed in ("606", "607", "608"):
                directory = self.root / label / seed / "csv"
                directory.mkdir(parents=True)
                person = directory / "Person.csv"
                person.write_text(f"run,time,id_Person,yMiscPersGrossMonth\na,2020,1,{income}\n")
                benefit = directory / "BenefitUnit.csv"
                flag = 0 if label == "native-reference" else 1
                benefit.write_text(f"run,time,id_BenefitUnit,yBenUCReceivedFlag,wealthTotValue\na,2020,1,{flag},100\n")
                attempts[label]["runs"][seed] = {p.name: csv_summary(p) for p in (person, benefit)}
        report = {}
        with self.assertRaisesRegex(ArtifactError, "Scientific CSV differences"):
            check_results(attempts, self.root, report)
        self.assertTrue(report["scenario_effect"]["passed"])
        self.assertEqual(3, len(report["csv_differences"]))
        for value in ("1.0", "NaN", ""):
            (self.root / "normalised-scenario/608/csv/Person.csv").write_text(
                f"run,time,id_Person,yMiscPersGrossMonth\na,2020,1,{value}\n")
            with self.subTest(value=value):
                self.assertFalse(scenario_income_changes(self.root)["passed"])


if __name__ == "__main__":
    unittest.main()
