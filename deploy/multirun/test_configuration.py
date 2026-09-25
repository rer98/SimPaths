"""(C) Copyright 2026, by Ross Richardson

Regression/security checks for draft MultiRun configuration and native YAML output.

@author ross richardson
"""

from copy import deepcopy
from pathlib import Path
import re
import unittest
import yaml

from deploy.multirun.configuration import import_native_yaml, normalise, normalise_yaml
from deploy.multirun.schema import (
    COLLECTOR_FIELDS, ConfigurationError, FIXED_MODEL, Limits, LONG_MAX, LONG_MIN,
    MODEL_FIELDS, OUTPUT_CONTRACT, SCHEMA_VERSION,
)
from deploy.multirun.yaml_input import load_yaml


ROOT = Path(__file__).resolve().parents[2]


def document():
    return {
        "schema_version": SCHEMA_VERSION, "model_release": "test-release",
        "dataset_revision": "test-dataset", "experiment": {"name": "Savings comparison"},
        "common": {"country": "UK", "start_year": 2019, "end_year": 2030, "population": 50000},
        "seed_plan": {"mode": "standard", "repetitions": 3},
        "run_sets": [{"id": "baseline", "name": "Baseline", "model_args": {"savingRate": 0.056}}],
        "output_contract": OUTPUT_CONTRACT,
    }


class ConfigurationTests(unittest.TestCase):
    def test_standard_and_alternative_seeds_are_shared_native_settings(self):
        draft = document()
        draft["run_sets"].append({"id": "scenario", "name": "Scenario", "model_args": {"savingRate": 0.06}})
        for mode, first in (("standard", 606), ("starting_seed", 9007199254740993)):
            with self.subTest(mode=mode):
                draft["seed_plan"] = {"mode": mode, "repetitions": 3}
                if mode == "starting_seed":
                    draft["seed_plan"]["first_seed"] = str(first)
                snapshot = normalise(draft)
                self.assertEqual([str(first + i) for i in range(3)], snapshot.as_dict()["seed_plan"]["seeds"])
                for identifier in ("scenario", "baseline", "scenario"):
                    native = yaml.safe_load(snapshot.native_yaml(identifier))
                    self.assertEqual(first, native["randomSeed"])
                    self.assertIs(type(native["randomSeed"]), int)
                    self.assertEqual(3, native["maxNumberOfRuns"])
                    self.assertTrue(native["innovation_args"]["randomSeedInnov"])
                    self.assertNotIn("randomSeedIfFixed", native["model_args"])
                    self.assertEqual(2030, native["endYear"])

    def test_starting_seed_boundaries_and_overflow(self):
        for first in (LONG_MIN, LONG_MAX - 2):
            draft = document()
            draft["seed_plan"] = {"mode": "starting_seed", "repetitions": 3, "first_seed": str(first)}
            self.assertEqual(str(first + 2), normalise(draft).as_dict()["seed_plan"]["seeds"][-1])
        for first in (LONG_MAX, LONG_MIN - 1, "1.5", "1e3", True, 606, "0606", "9" * 50):
            draft = document()
            raw = str(first) if type(first) is int and abs(first) > 1000 else first
            draft["seed_plan"] = {"mode": "starting_seed", "repetitions": 3, "first_seed": raw}
            with self.subTest(first=first), self.assertRaises(ConfigurationError):
                normalise(draft)

    def test_unsupported_or_conflicting_seed_modes(self):
        for update in ({"mode": "list", "seeds": [606, 607]}, {"increment": 2},
                       {"first_seed": "606"}, {"repetitions": 0}, {"repetitions": True}):
            draft = document()
            draft["seed_plan"].update(update)
            with self.subTest(update=update), self.assertRaises(ConfigurationError):
                normalise(draft)
        for key, value in {"randomSeedIfFixed": 123, "fixRandomSeed": False, "startYear": 2020,
                           "popSize": 1, "endYear": 2031, "readGrid": "../../etc/passwd",
                           "enableIntertemporalOptimisations": True, "unionMatchingMethod": "SBAM"}.items():
            draft = document()
            draft["run_sets"][0]["model_args"][key] = value
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                normalise(draft)

    def test_form_yaml_and_editable_export_round_trip(self):
        draft = document()
        draft["run_sets"][0]["model_args"].update({"timeTrendStopsIn": 2028, "taxDonorUpratingByWage": True})
        a = normalise(draft)
        b = normalise_yaml(yaml.safe_dump(draft))
        c = normalise_yaml(a.editable_yaml())
        self.assertEqual(a.canonical_json, b.canonical_json)
        self.assertEqual(a.canonical_json, c.canonical_json)
        self.assertEqual(2023, a.as_dict()["run_sets"][0]["model_args"]["timeTrendStopsInMonetaryProcesses"])
        draft["common"]["population"] = 1
        changed = a.as_dict()
        changed["common"]["population"] = 2
        self.assertEqual(50000, a.as_dict()["common"]["population"])
        self.assertEqual(a.configuration_sha256, c.configuration_sha256)

    def test_types_and_output_requirements(self):
        for key, value in (("savingRate", "0.04"), ("savingRate", float("nan")),
                           ("savingRate", float("inf")), ("projectMortality", "false"),
                           ("maxAge", 130.5), ("maxAge", True), ("maxAge", 2**31)):
            draft = document()
            draft["run_sets"][0]["model_args"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ConfigurationError):
                normalise(draft)
        for key, value in (("persistPersons", False), ("exportToCSV", False),
                           ("dataDumpStartTime", "0L"), ("dataDumpTimePeriod", 2)):
            draft = document()
            draft["run_sets"][0]["collector_args"] = {key: value}
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                normalise(draft)

    def test_platform_fields_and_unknown_keys_rejected(self):
        for key in ("owner", "image", "command", "working_directory", "retry_policy", "provenance"):
            draft = document()
            draft[key] = "untrusted"
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                normalise(draft)
        for field in ("model_release", "dataset_revision"):
            draft = document()
            draft[field] = "../../private"
            with self.subTest(field=field), self.assertRaises(ConfigurationError):
                normalise(draft)

    def test_duplicate_ids_and_effective_settings_rejected(self):
        draft = document()
        draft["run_sets"].append({"id": "copy", "name": "Copy"})
        with self.assertRaisesRegex(ConfigurationError, "duplicate effective"):
            normalise(draft)
        draft["run_sets"][1].update({"id": "baseline", "model_args": {"savingRate": 0.06}})
        with self.assertRaisesRegex(ConfigurationError, "duplicate Run Set ID"):
            normalise(draft)


class SweepTests(unittest.TestCase):
    def sweep(self):
        draft = document()
        draft["run_sets"] = []
        draft["seed_plan"]["repetitions"] = 10
        draft["sweep"] = {"id_prefix": "savings", "combination": "all", "base": {},
                          "parameters": {"model_args.savingRate": [0.04, 0.05, 0.06]}}
        return draft

    def test_sweep_three_rates_ten_seeds_native_equivalence(self):
        draft = self.sweep()
        snapshot = normalise(draft)
        data = snapshot.as_dict()
        self.assertEqual({"run_sets": 3, "simulations": 30}, data["totals"])
        self.assertEqual([str(n) for n in range(606, 616)], data["seed_plan"]["seeds"])
        self.assertEqual(snapshot.canonical_json, normalise_yaml(snapshot.editable_yaml()).canonical_json)
        manual = deepcopy(draft)
        manual.pop("sweep")
        manual["run_sets"] = data["run_sets"]
        manual_snapshot = normalise(manual)
        for item in data["run_sets"]:
            self.assertEqual(snapshot.native_configuration(item["id"]),
                             manual_snapshot.native_configuration(item["id"]))

    def test_multiple_dimensions_deterministic_regardless_of_key_order(self):
        a = self.sweep()
        a["sweep"]["parameters"]["model_args.useWeights"] = [False, True]
        b = deepcopy(a)
        b["sweep"]["parameters"] = dict(reversed(list(b["sweep"]["parameters"].items())))
        self.assertEqual(normalise(a).canonical_json, normalise(b).canonical_json)
        self.assertEqual(60, normalise(a).as_dict()["totals"]["simulations"])

    def test_decimal_and_descending_ranges(self):
        draft = self.sweep()
        for range_, expected in (({"start": .04, "end": .06, "step": .01}, [.04, .05, .06]),
                                 ({"start": .06, "end": .04, "step": -.01}, [.06, .05, .04])):
            draft["sweep"]["parameters"]["model_args.savingRate"] = range_
            data = normalise(draft).as_dict()
            self.assertEqual(expected, [run["model_args"]["savingRate"] for run in data["run_sets"]])
        draft["sweep"]["parameters"] = {"model_args.maxAge": {"start": 120, "end": 130, "step": 5}}
        self.assertEqual([120, 125, 130], [run["model_args"]["maxAge"] for run in normalise(draft).as_dict()["run_sets"]])

    def test_invalid_ranges_values_and_dimensions(self):
        for value in ([], [0.04, 0.04], [0, 0.0], [True],
                      {"start": .04, "end": .06, "step": 0},
                      {"start": .04, "end": .06, "step": -.01},
                      {"start": .04, "end": .065, "step": .01},
                      {"start": -1e308, "end": 1e308, "step": 1e-308}):
            draft = self.sweep()
            draft["sweep"]["parameters"]["model_args.savingRate"] = value
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                normalise(draft)
        for key in ("randomSeed", "model_args.randomSeedIfFixed", "collector_args.persistPersons", "model_args.readGrid"):
            draft = self.sweep()
            draft["sweep"]["parameters"] = {key: [1, 2]}
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                normalise(draft)

    def test_expansion_limits_include_manual_runs_and_repetitions(self):
        draft = self.sweep()
        draft["run_sets"] = document()["run_sets"]
        for limits in (Limits(max_run_sets=3), Limits(max_simulations=39), Limits(max_repetitions=9)):
            with self.subTest(limits=limits), self.assertRaises(ConfigurationError):
                normalise(draft, limits=limits)
        draft["run_sets"][0]["model_args"]["savingRate"] = .04
        with self.assertRaisesRegex(ConfigurationError, "duplicate effective"):
            normalise(draft)
        # A looser repetition limit must not allocate a huge seed list before
        # the tighter total-work limit is applied.
        draft["seed_plan"]["repetitions"] = 2**31
        with self.assertRaisesRegex(ConfigurationError, "seed_plan.repetitions"):
            normalise(draft, limits=Limits(max_repetitions=2**31))


class YamlSecurityTests(unittest.TestCase):
    def test_tags_aliases_duplicates_merges_and_documents(self):
        for text in ("!!python/object/apply:os.system ['echo UNSAFE']", "x: !custom 1",
                     "x: &shared [1]\ny: *shared", "x: &recursive [*recursive]", "x: 1\nx: 2",
                     "x: {a: 1, a: 2}", "x: {<<: {a: 1}}", "x: 1\n---\nx: 2",
                     "? [x, y]\n: 1", "x: 2026-09-24", "x: .inf"):
            with self.subTest(text=text), self.assertRaises(ConfigurationError):
                load_yaml(text)

    def test_parser_limits_and_error_redaction(self):
        cases = (("x: " + "a" * 30, Limits(max_bytes=20)),
                 ("x: " + "[" * 20 + "1" + "]" * 20, Limits(max_depth=8)),
                 ("x: [1,2,3,4,5]", Limits(max_nodes=5)),
                 ("x: " + "a" * 30, Limits(max_scalar_chars=10)),
                 (b"\xff", Limits()))
        for text, limits in cases:
            with self.subTest(text=text), self.assertRaises(ConfigurationError):
                load_yaml(text, limits)
        with self.assertRaises(ConfigurationError) as exc:
            load_yaml("SECRET_CONFIGURATION: [invalid")
        self.assertNotIn("SECRET_CONFIGURATION", str(exc.exception))

    def test_form_cannot_bypass_tree_budgets(self):
        recursive = {}
        recursive["x"] = recursive
        for value in (recursive, {"x": object()}, {"x": 2**80}, {1: "bad"}, {"x": "\ud800"}):
            with self.subTest(value=type(value)), self.assertRaises(ConfigurationError):
                normalise(value)


class NativeCompatibilityTests(unittest.TestCase):
    def imported(self, text):
        return import_native_yaml(text, model_release="local-model", dataset_revision="prepared-inputs", name="Imported")

    def test_repository_default_yaml_imports(self):
        snapshot = self.imported((ROOT / "config/default.yml").read_text())
        native = snapshot.native_configuration("imported")
        self.assertEqual((606, 1, 50000, 2019, 2022), tuple(native[key] for key in
                         ("randomSeed", "maxNumberOfRuns", "popSize", "startYear", "endYear")))

    def test_java_fixture_matches_current_example_generator(self):
        snapshot = normalise_yaml((ROOT / "deploy/multirun/example.yml").read_bytes())
        fixture = yaml.safe_load((ROOT / "src/test/resources/multirun/native-configuration.yml").read_text())
        self.assertEqual(snapshot.native_configuration("savings-0001"), fixture)

    def test_generated_native_yaml_reimports_without_changing_values(self):
        snapshot = normalise(document())
        text = snapshot.native_yaml("baseline")
        self.assertEqual(snapshot.native_configuration("baseline"), self.imported(text).native_configuration("imported"))

    def test_native_overrides_paths_and_ignored_controls_rejected(self):
        native = normalise(document()).native_configuration("baseline")
        changes = (("model_args", "randomSeedIfFixed", 123), ("model_args", "fixRandomSeed", False),
                   ("innovation_args", "randomSeedInnov", False), ("innovation_args", "labourSupplyElasticityInnov", True),
                   ("parameter_args", "working_directory", "/tmp"), ("parameter_args", "trainingFlag", True))
        for group, key, value in changes:
            altered = deepcopy(native)
            altered[group][key] = value
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                self.imported(yaml.safe_dump(altered))
        for key in ("randomSeed", "startYear", "popSize"):
            altered = deepcopy(native)
            altered.pop(key)
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                self.imported(yaml.safe_dump(altered))

    def test_reviewed_fields_match_java_declarations_and_initialisers(self):
        # Catch source/profile drift instead of silently reusing defaults after a release.
        sources = (("model/SimPathsModel.java", MODEL_FIELDS, FIXED_MODEL),
                   ("experiment/SimPathsCollector.java", COLLECTOR_FIELDS, {}))
        type_kinds = {"Integer": "int", "int": "int", "Long": "long", "long": "long",
                      "boolean": "boolean", "Boolean": "boolean", "Double": "double", "double": "double"}
        for relative, fields, fixed in sources:
            source = (ROOT / "src/main/java/simpaths" / relative).read_text()
            source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
            for key, expected in {**{k: v.default for k, v in fields.items()}, **fixed}.items():
                with self.subTest(key=key):
                    match = re.search(r"\b(?:private|public|protected)\s+(\w+)\s+" + re.escape(key) + r"\s*=\s*([^;]+);", source)
                    self.assertIsNotNone(match, "review removed or changed field")
                    kind, literal = match.groups()
                    if key in fields:
                        self.assertEqual(fields[key].kind, type_kinds[kind])
                    if literal.strip() == "timeTrendStopsIn":
                        actual = MODEL_FIELDS["timeTrendStopsIn"].default
                    else:
                        literal = literal.strip().rstrip("L")
                        actual = {"true": True, "false": False}.get(literal)
                        if actual is None:
                            actual = float(literal) if type_kinds[kind] == "double" else int(literal)
                    self.assertEqual(expected, actual, "review changed native default")


if __name__ == "__main__":
    unittest.main()
