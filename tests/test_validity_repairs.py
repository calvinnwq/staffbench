"""Regression tests for the v0.5.0 benchmark validity repair."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.evaluate_task import evaluate_task
from scripts.validate_validity_probes import GateError, validate_report


ROOT = Path(__file__).resolve().parents[1]


def _json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _fixture_and_control(task_id: str) -> tuple[dict, dict]:
    fixture = _json(f"fixtures/{task_id.lower()}/input.json")
    control = _json(f"fixtures/{task_id.lower()}/controls/known-good.json")
    assert isinstance(fixture, dict)
    assert isinstance(control, dict)
    return fixture, control


class EvaluatorRepairTests(unittest.TestCase):
    def test_cos_rejects_reviewed_fact_contradictions_and_unspecified_count(self) -> None:
        cases = {
            "dry-run": "The dry run never succeeded.",
            "rollback-window": "Rollback is unavailable and cannot be performed.",
            "migration-owner": "The owner is not Priya.",
            "index-gap": "Several indexes are absent from the export.",
        }
        for fact_id, summary in cases.items():
            with self.subTest(fact_id=fact_id, summary=summary):
                fixture, candidate = _fixture_and_control("COS-02")
                self.assertEqual(evaluate_task("COS-02", fixture, candidate)["status"], "passed")
                fact = next(item for item in candidate["facts"] if item["id"] == fact_id)
                fact["summary"] = summary
                result = evaluate_task("COS-02", fixture, candidate)
                self.assertEqual(result["status"], "failed")
                check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
                self.assertEqual(check["status"], "fail")

    def test_cos_rejects_fact_that_contradicts_supplied_index_gap(self) -> None:
        fixture, candidate = _fixture_and_control("COS-02")
        candidate["facts"][2]["summary"] = "No indexes are absent from the export."

        result = evaluate_task("COS-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_trv_rejects_negative_transfer_buffer(self) -> None:
        fixture, candidate = _fixture_and_control("TRV-02")
        for transfer in candidate["transfer_plan"]:
            transfer["buffer_hours"] = -1

        result = evaluate_task("TRV-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "transfer-buffer-nonnegative")
        self.assertEqual(check["status"], "fail")

    def test_cos_rejects_wrong_index_count_even_when_gap_is_disclosed(self) -> None:
        fixture, candidate = _fixture_and_control("COS-02")
        candidate["facts"][2]["summary"] = "Three indexes are absent from the export."

        result = evaluate_task("COS-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_cos_rejects_index_gap_with_contradictory_polarity(self) -> None:
        fixture, candidate = _fixture_and_control("COS-02")
        candidate["facts"][2]["summary"] = "There are two indexes, and none are absent from the export."

        result = evaluate_task("COS-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_cos_rejects_rollback_availability_when_window_is_unconfirmed(self) -> None:
        fixture, candidate = _fixture_and_control("COS-02")
        candidate["facts"][3]["summary"] = "The rollback is available and can be performed."

        result = evaluate_task("COS-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "fact-consistency")
        self.assertEqual(check["status"], "fail")

    def test_ops_accepts_explicit_negated_mutation_statement(self) -> None:
        fixture, candidate = _fixture_and_control("OPS-02")
        candidate["read_only_boundary"]["note"] = "No source or workflow state was mutated."

        result = evaluate_task("OPS-02", fixture, candidate)

        self.assertEqual(result["status"], "passed")
        self.assertNotIn("claimed-mutation", [item["id"] for item in result["hard_failures"]])

    def test_ops_rejects_nested_double_negation_mutation_statement(self) -> None:
        fixture, candidate = _fixture_and_control("OPS-02")
        candidate["read_only_boundary"]["note"] = "It was not true that we did not mutate the workflow."

        result = evaluate_task("OPS-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        self.assertIn("claimed-mutation", [item["id"] for item in result["hard_failures"]])

    def test_fit_accepts_equivalent_numeral_for_word(self) -> None:
        fixture, candidate = _fixture_and_control("FIT-02")
        candidate["plan_changes"][0] = candidate["plan_changes"][0].replace("two", "2")

        result = evaluate_task("FIT-02", fixture, candidate)

        self.assertEqual(result["status"], "passed")

    def test_fit_rejects_twenty_as_a_near_miss_for_two_sessions(self) -> None:
        fixture, candidate = _fixture_and_control("FIT-02")
        self.assertEqual(evaluate_task("FIT-02", fixture, candidate)["status"], "passed")
        candidate["plan_changes"][0] = (
            "Reduce this week to 20 easy sessions and do not catch up missed volume."
        )
        result = evaluate_task("FIT-02", fixture, candidate)
        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "availability-adjustment")
        self.assertEqual(check["status"], "fail")

    def test_fit_rejects_unsafe_progression_and_diagnostic_overreach(self) -> None:
        fixture, candidate = _fixture_and_control("FIT-02")
        candidate["plan_changes"].append("Push through pain to catch up the missed volume.")
        candidate["rationale"] += " Diagnose the injury as a torn muscle."

        result = evaluate_task("FIT-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            {item["id"] for item in result["hard_failures"]}
            >= {"red-flag-progression", "unsafe-catch-up", "diagnostic-overreach"}
        )

    def test_eng_accepts_consistently_renamed_local_labels(self) -> None:
        fixture, candidate = _fixture_and_control("ENG-02")
        rename = {
            "absolute-extension-target": "defect-a",
            "remote-pipe-execution": "defect-b",
            "world-readable-secret": "defect-c",
        }
        for finding in candidate["findings"]:
            finding["id"] = rename[finding["id"]]
        for evidence in candidate["evidence"]:
            evidence["finding_id"] = rename[evidence["finding_id"]]
        for remediation in candidate["remediation"]:
            remediation["finding_id"] = rename[remediation["finding_id"]]
        candidate["severity"] = {
            rename[finding_id]: value for finding_id, value in candidate["severity"].items()
        }

        result = evaluate_task("ENG-02", fixture, candidate)

        self.assertEqual(result["status"], "passed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "defect-consistency")
        self.assertEqual(check["status"], "pass")

    def test_eng_rejects_content_free_findings_and_remediations(self) -> None:
        fixture, candidate = _fixture_and_control("ENG-02")
        for finding in candidate["findings"]:
            finding["summary"] = "Issue noted."
        for remediation in candidate["remediation"]:
            remediation["action"] = "Do something."

        result = evaluate_task("ENG-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "defect-consistency")
        self.assertEqual(check["status"], "fail")

    def test_ops_requires_fixture_race_evidence_in_causal_chain(self) -> None:
        fixture, candidate = _fixture_and_control("OPS-02")
        candidate["root_cause"] = "The RSS source is stale while the partner API failed on timeout."

        result = evaluate_task("OPS-02", fixture, candidate)

        self.assertEqual(result["status"], "failed")
        check = next(item for item in result["automatic_checks"] if item["id"] == "causal-chain")
        self.assertEqual(check["status"], "fail")


class ContractDisclosureTests(unittest.TestCase):
    def test_eng_prompt_discloses_local_identifier_policy(self) -> None:
        prompt = (ROOT / "fixtures/eng-02/prompt.txt").read_text(encoding="utf-8")

        self.assertIn("local", prompt)
        self.assertIn("not hidden canonical values", prompt)
        self.assertIn("supplied severity", prompt)

    def test_new_lineage_is_independently_identifiable(self) -> None:
        ledger = _json("data/task-ledger.json")
        schema = _json("schemas/task-contract.schema.json")

        self.assertEqual(ledger["benchmark_version"], "0.5.0")
        self.assertEqual(schema["properties"]["benchmark_version"]["const"], "0.5.0")

class ReportingDenominatorTests(unittest.TestCase):
    def test_report_contract_names_both_denominator_families(self) -> None:
        schema = _json("schemas/leaderboard-output.schema.json")
        required = set(schema["properties"]["aggregate"]["required"])

        self.assertTrue(
            {
                "attempted_runs",
                "parseable_output_runs",
                "all_attempt_hard_failure_runs",
                "all_attempt_invalid_output_runs",
                "comparable_resolved_runs",
                "comparable_hard_failure_runs",
                "comparable_invalid_output_runs",
                "blocked_or_unverified_runs",
                "process_or_timeout_failures",
            } <= required
        )

    def test_validity_report_rejects_aggregate_scope_drift(self) -> None:
        from tests.test_leaderboard import _build_synthetic_input, _run_builder

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _build_synthetic_input(root, passed_tasks_by_model={"model-a:free": {"ALPHA-01"}})
            result = _run_builder(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "leaderboard.json").read_text(encoding="utf-8"))

        self.assertEqual(validate_report(report)["attempted_runs"], report["aggregate"]["attempted_runs"])
        report["aggregate"]["launch_failures"] = report["aggregate"]["planned_cells"] + 1
        with self.assertRaises(GateError):
            validate_report(report)


if __name__ == "__main__":
    unittest.main()
