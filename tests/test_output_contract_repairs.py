"""Focused regressions for the v0.6.0 output-contract and scoring repairs."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts import replay_task
from scripts.evaluate_task import _term_matches, evaluate_task
from scripts.validate_benchmark import EXPECTED_TASK_IDS


ROOT = Path(__file__).resolve().parents[1]


def load_case(task_id):
    package = ROOT / "fixtures" / task_id.lower()
    fixture_name = "request-packet.json" if task_id == "COS-01" else "input.json"
    fixture = json.loads((package / fixture_name).read_text())
    candidate = json.loads((package / "controls" / "known-good.json").read_text())
    return fixture, candidate


def checks(evaluation):
    return {item["id"]: item for item in evaluation["automatic_checks"]}


class OutputContractRepairTests(unittest.TestCase):
    def test_every_model_input_includes_exact_schema_and_new_composition_version(self):
        self.assertEqual(replay_task.INPUT_COMPOSITION_VERSION, "prompt-plus-fixture-plus-schema-v2")
        from scripts.build_leaderboard import _trusted_task_bindings
        bindings = _trusted_task_bindings(EXPECTED_TASK_IDS)
        for task_id in sorted(EXPECTED_TASK_IDS):
            with self.subTest(task=task_id):
                package = ROOT / "fixtures" / task_id.lower()
                fixture = package / ("request-packet.json" if task_id == "COS-01" else "input.json")
                schema = ROOT / "schemas" / f"{task_id.lower()}-output.schema.json"
                composed = replay_task.compose_model_input(package / "prompt.txt", fixture, schema)
                self.assertEqual(
                    composed,
                    (package / "prompt.txt").read_bytes()
                    + replay_task.INPUT_SEPARATOR + fixture.read_bytes()
                    + replay_task.OUTPUT_CONTRACT_SEPARATOR + schema.read_bytes(),
                )
                self.assertIn(b"Do not add keys", composed)
                self.assertNotIn(b'"hard_rules"', composed)
                self.assertEqual(bindings[task_id]["input_fingerprint"], "sha256:" + hashlib.sha256(composed).hexdigest())

    def test_schema_bytes_affect_composed_input(self):
        package = ROOT / "fixtures" / "cos-01"
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "schema.json"
            schema.write_text('{"type":"object"}')
            before = replay_task.compose_model_input(package / "prompt.txt", package / "request-packet.json", schema)
            schema.write_text('{"type":"object","additionalProperties":false}')
            after = replay_task.compose_model_input(package / "prompt.txt", package / "request-packet.json", schema)
        self.assertNotEqual(before, after)

    def test_fit01_counts_running_days_in_a_seven_day_schedule(self):
        fixture, candidate = load_case("FIT-01")
        runs = {item["day"]: item for item in candidate["weekly_schedule"] if item["distance_km"] > 0}
        candidate["weekly_schedule"] = [
            runs.get(day, {"day": day, "distance_km": 0, "session": "rest"})
            for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        ]
        self.assertEqual(evaluate_task("FIT-01", fixture, candidate)["status"], "passed")
        wrong_count = copy.deepcopy(candidate)
        wrong_count["weekly_schedule"][0]["distance_km"] = 1
        self.assertEqual(checks(evaluate_task("FIT-01", fixture, wrong_count))["session-count"]["status"], "fail")
        short = copy.deepcopy(candidate)
        short["weekly_schedule"] = list(runs.values())
        self.assertEqual(checks(evaluate_task("FIT-01", fixture, short))["required-fields"]["status"], "fail")
        fixture["baseline"]["sessions_per_week"] = 4
        self.assertEqual(checks(evaluate_task("FIT-01", fixture, candidate))["session-count"]["status"], "fail")

    def test_cos01_retained_constraint_label_is_only_a_format_failure(self):
        fixture, candidate = load_case("COS-01")
        candidate["scope_in"][0]["status"] = "confirmed"
        evaluation = evaluate_task("COS-01", fixture, candidate)
        self.assertEqual(checks(evaluation)["required-fields"]["status"], "fail")
        self.assertEqual(checks(evaluation)["constraint-coverage"]["status"], "pass")
        self.assertNotIn("dropped-hard-constraint", {item["id"] for item in evaluation["hard_failures"]})
        candidate["scope_in"].pop()
        self.assertIn("dropped-hard-constraint", {item["id"] for item in evaluate_task("COS-01", fixture, candidate)["hard_failures"]})

    def test_cos01_extra_or_duplicate_constraints_are_not_dropped_constraints(self):
        fixture, candidate = load_case("COS-01")
        candidate["scope_in"].append(copy.deepcopy(candidate["scope_in"][0]))
        evaluation = evaluate_task("COS-01", fixture, candidate)
        self.assertEqual(checks(evaluation)["constraint-coverage"]["status"], "fail")
        self.assertNotIn("dropped-hard-constraint", {item["id"] for item in evaluation["hard_failures"]})
        candidate["scope_in"][-1]["constraint_id"] = "unknown"
        evaluation = evaluate_task("COS-01", fixture, candidate)
        self.assertNotIn("dropped-hard-constraint", {item["id"] for item in evaluation["hard_failures"]})
        candidate["scope_in"][0]["status"] = "dropped"
        self.assertIn("dropped-hard-constraint", {item["id"] for item in evaluate_task("COS-01", fixture, candidate)["hard_failures"]})

    def test_new_runs_cannot_use_the_published_report_lineage(self):
        ledger = json.loads((ROOT / "data/task-ledger.json").read_text())
        self.assertEqual(ledger["benchmark_version"], "0.6.0")
        from scripts.validate_benchmark import validate_schema_instance
        report = json.loads((ROOT / "results/nous-portal-free-models-2026-10/leaderboard.json").read_text())
        output_schema = json.loads((ROOT / "schemas/leaderboard-output.schema.json").read_text())
        self.assertEqual(report["benchmark_version"], "0.5.0")
        # Published copies omit the private input manifest; validate their release identity.
        self.assertEqual(validate_schema_instance(report["benchmark_version"], output_schema["properties"]["benchmark_version"]), [])
        roster_schema = json.loads((ROOT / "schemas/model-roster.schema.json").read_text())
        errors = validate_schema_instance({"benchmark_version": "0.5.0"}, roster_schema)
        self.assertTrue(any("benchmark_version" in error for error in errors))

    def test_explicit_empty_findings_are_valid_but_missing_or_wrong_types_are_not(self):
        for task_id, field in (
            ("FIN-01", "constraint_gaps"), ("RES-01", "conflicts"),
            ("ENG-01", "residual_risks"), ("DES-02", "open_questions"), ("PRD-02", "open_questions"),
        ):
            with self.subTest(task=task_id, field=field):
                fixture, candidate = load_case(task_id)
                candidate[field] = []
                self.assertEqual(evaluate_task(task_id, fixture, candidate)["status"], "passed")
                for invalid in (None, "none", {}):
                    candidate[field] = invalid
                    self.assertEqual(checks(evaluate_task(task_id, fixture, candidate))["required-fields"]["status"], "fail")
                del candidate[field]
                self.assertEqual(checks(evaluate_task(task_id, fixture, candidate))["required-fields"]["status"], "fail")
        fixture, candidate = load_case("FIN-01")
        candidate["allocation"] = []
        self.assertEqual(checks(evaluate_task("FIN-01", fixture, candidate))["required-fields"]["status"], "fail")

    def test_keyword_inflections_remain_bounded(self):
        for term, forms in (
            ("liquid", ("liquid", "liquidity")),
            ("reduce", ("reduce", "reduces", "reduced", "reducing", "reduction", "reductions")),
            ("focus", ("focus", "focuses", "focused", "focusing", "focusable")),
        ):
            for form in forms:
                with self.subTest(term=term, form=form):
                    self.assertTrue(_term_matches(f"({form.upper()})", term))
        for term, text in (
            ("liquid", "illiquid"), ("liquid", "liquidation"), ("reduce", "irreducible"),
            ("focus", "unfocused"), ("focus", "refocusable"), ("rest", "interest"),
            ("risk-free", "brisk-free"), ("focus", "focusable2"),
        ):
            with self.subTest(term=term, text=text):
                self.assertFalse(_term_matches(text, term))


if __name__ == "__main__":
    unittest.main()
