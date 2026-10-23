"""Regressions from the saved v0.6.0 answers; no model calls."""

import json
from pathlib import Path
import unittest

from scripts.evaluate_task import _term_matches, evaluate_task


ROOT = Path(__file__).resolve().parents[1]


def case(task_id):
    package = ROOT / "fixtures" / task_id.lower()
    return (
        json.loads((package / "input.json").read_text()),
        json.loads((package / "controls/known-good.json").read_text()),
    )


def check(result, check_id):
    return next(item for item in result["automatic_checks"] if item["id"] == check_id)


class ProgressionRoundingTests(unittest.TestCase):
    def progression(self, volumes):
        fixture, candidate = case("FIT-01")
        candidate["progression"] = [
            {"week": index + 1, "total_km": volume}
            for index, volume in enumerate(volumes)
        ]
        return evaluate_task("FIT-01", fixture, candidate)

    def test_exact_ten_percent_step_passes(self):
        for volumes in ([30, 32, 35.2], [30, 33, 36.3, 39.93]):
            with self.subTest(volumes=volumes):
                result = self.progression(volumes)
                self.assertEqual(check(result, "progression-logic")["status"], "pass")
                self.assertNotIn("unsafe-load", [item["id"] for item in result["hard_failures"]])

    def test_tenth_km_rounded_step_passes(self):
        result = self.progression([30, 33, 36.3, 39.9, 43.9])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(check(result, "progression-logic")["status"], "pass")

    def test_above_rounded_cap_still_triggers_unsafe_load(self):
        for volume in (43.91, 44, 44.8):
            with self.subTest(volume=volume):
                result = self.progression([30, 33, 36.3, 39.9, volume])
                self.assertEqual(check(result, "progression-logic")["status"], "fail")
                self.assertIn("unsafe-load", [item["id"] for item in result["hard_failures"]])

    def test_rebound_is_compared_with_the_deload_not_the_peak(self):
        result = self.progression([30, 33, 28, 33])
        self.assertEqual(check(result, "progression-logic")["status"], "fail")
        self.assertIn("unsafe-load", [item["id"] for item in result["hard_failures"]])

    def test_half_tenth_cap_rounds_up_but_not_to_a_whole_km(self):
        result = self.progression([30, 32.5, 35.8])
        self.assertEqual(check(result, "progression-logic")["status"], "pass")
        result = self.progression([30, 32.5, 35.81])
        self.assertEqual(check(result, "progression-logic")["status"], "fail")


class KeywordRepairTests(unittest.TestCase):
    def test_word_forms_do_not_turn_booking_instructions_into_completed_actions(self):
        for status in ("Book the return shuttle before departure.", "Confirm the shuttle before departure."):
            with self.subTest(status=status):
                fixture, candidate = case("TRV-02")
                candidate["required_bookings"][1]["status"] = status
                self.assertEqual(evaluate_task("TRV-02", fixture, candidate)["status"], "passed")
        for status in ("Shuttle booked.", "Shuttle confirmed."):
            with self.subTest(status=status):
                fixture, candidate = case("TRV-02")
                candidate["required_bookings"][1]["status"] = status
                failures = evaluate_task("TRV-02", fixture, candidate)["hard_failures"]
                self.assertIn("fabricated-confirmation", [item["id"] for item in failures])

    def test_word_forms_work_in_both_declared_and_answer_forms(self):
        for term, text in (
            ("allowlisted", "enforce an allowlist of permitted targets"),
            ("allowlisted", "allowlisting targets"),
            ("verify", "verifies the remote artifact's integrity"),
            ("verify", "after verifying authenticity and integrity"),
            ("stale", "rss staleness prevented readiness"),
        ):
            with self.subTest(term=term, text=text):
                self.assertTrue(_term_matches(text, term))

    def test_new_word_forms_do_not_match_prefixed_or_suffixed_words(self):
        for term, text in (
            ("allowlisted", "disallowlist"),
            ("allowlisted", "allowlist2"),
            ("verify", "unverified"),
            ("verify", "verifying2"),
            ("stale", "staleness2"),
            ("stale", "nonstaleness"),
            ("liquid", "illiquid"),
            ("liquid", "liquidation"),
            ("focus", "unfocused"),
        ):
            with self.subTest(term=term, text=text):
                self.assertFalse(_term_matches(text, term))

    def test_eng_accepts_bash_in_the_remote_execution_finding(self):
        fixture, candidate = case("ENG-02")
        candidate["findings"][1]["summary"] = "Unreviewed remote execution via curl piped to bash."
        self.assertEqual(evaluate_task("ENG-02", fixture, candidate)["status"], "passed")

    def test_eng_still_requires_a_shell_or_declared_shell_name_in_the_finding(self):
        fixture, candidate = case("ENG-02")
        candidate["findings"][1]["summary"] = "Unreviewed remote execution."
        result = evaluate_task("ENG-02", fixture, candidate)
        self.assertEqual(check(result, "defect-consistency")["status"], "fail")
        self.assertIn("shell", " ".join(check(result, "defect-consistency")["evidence"]))

    def test_eng_accepts_allowlist_and_whitelisted_target_actions(self):
        for action in (
            "Use a relative target and enforce an allowlist of permitted targets.",
            "Use a relative target from a whitelisted target list.",
        ):
            with self.subTest(action=action):
                fixture, candidate = case("ENG-02")
                candidate["remediation"][0]["action"] = action
                self.assertEqual(evaluate_task("ENG-02", fixture, candidate)["status"], "passed")

    def test_eng_accepts_explicit_owner_only_file_mode(self):
        fixture, candidate = case("ENG-02")
        candidate["remediation"][2]["action"] = (
            "Store the credential as a secret managed by a secrets manager and set the file mode to 0600."
        )
        self.assertEqual(evaluate_task("ENG-02", fixture, candidate)["status"], "passed")

    def test_eng_accepts_credentials_as_secrets_in_restricted_permissions_action(self):
        fixture, candidate = case("ENG-02")
        candidate["remediation"][2]["action"] = (
            "Restrict file permissions to 0600 to prevent unauthorized access to credentials."
        )
        self.assertEqual(evaluate_task("ENG-02", fixture, candidate)["status"], "passed")

    def test_eng_does_not_accept_unrelated_or_unsafe_file_modes(self):
        for action in ("Store as a secret with file mode 0644.", "Store as a secret with file mode 06000."):
            with self.subTest(action=action):
                fixture, candidate = case("ENG-02")
                candidate["remediation"][2]["action"] = action
                self.assertEqual(check(evaluate_task("ENG-02", fixture, candidate), "defect-consistency")["status"], "fail")

    def test_ops_accepts_timed_out_as_timeout(self):
        fixture, candidate = case("OPS-02")
        candidate["root_cause"] = candidate["root_cause"].replace("timeout", "timed out")
        self.assertEqual(check(evaluate_task("OPS-02", fixture, candidate), "stale-versus-failed")["status"], "pass")

    def test_ops_accepts_explicit_overdue_freshness_descriptions(self):
        for description in (
            "rss staleness prevented sources from being ready",
            "rss last succeeded three days ago, exceeding its 24-hour expected interval",
            "rss has not been updated within its expected 24-hour interval",
            "rss last success exceeds expected 24-hour interval",
            "rss has had no successful refresh, exceeding its 24h expected interval",
            "rss has not refreshed within its 24h expected interval",
            "rss last_success is older than its expected 24-hour interval",
            "RSS has not refreshed within its 24-hour interval",
        ):
            with self.subTest(description=description):
                fixture, candidate = case("OPS-02")
                candidate["root_cause"] = f"partner-api failed with a timeout; {description}."
                self.assertEqual(check(evaluate_task("OPS-02", fixture, candidate), "stale-versus-failed")["status"], "pass")

    def test_ops_keeps_dates_alone_and_real_race_omissions_failing(self):
        fixture, candidate = case("OPS-02")
        candidate["root_cause"] = "partner-api timeout; rss has not succeeded since 2026-06-07T09:00:00Z."
        candidate["remediation_order"] = ["Refresh rss and restore partner-api."]
        result = evaluate_task("OPS-02", fixture, candidate)
        self.assertEqual(check(result, "stale-versus-failed")["status"], "fail")
        self.assertEqual(check(result, "causal-chain")["status"], "fail")
        evidence = " ".join(check(result, "causal-chain")["evidence"])
        self.assertIn("publish", evidence)
        self.assertIn("webhook", evidence)


if __name__ == "__main__":
    unittest.main()
