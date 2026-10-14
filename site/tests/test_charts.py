"""Unit tests for the uncertainty maths and the scatter label placement."""
from __future__ import annotations

import itertools
import json
import re
import unittest

from helpers import ALPHA, LEDGER, REPORTS, build  # noqa: F401  (helpers puts site/ on sys.path)

import charts
import stats


def summary():
    ledger = build.load_ledger(LEDGER)
    report = build.load_reports(REPORTS)[0]
    board = json.loads(ALPHA.read_text(encoding="utf-8"))
    return stats.summarize(board, ledger["task_order"], ledger["roles"], report["models"])


class Stats(unittest.TestCase):
    def test_bootstrap_is_seeded(self):
        self.assertEqual(stats.bootstrap_draws(6), stats.bootstrap_draws(6))
        self.assertNotEqual(stats.bootstrap_draws(6, seed=1), stats.bootstrap_draws(6, seed=2))

    def test_interval_is_the_central_95_percent(self):
        values = [i / 999 for i in range(1000)]
        lo, hi = stats.interval(values)
        self.assertEqual((lo, hi), (values[25], values[974]))

    def test_identical_models_are_never_clearly_better(self):
        draws = stats.bootstrap_draws(6)
        rates = [0.0, 1.0, 0.5, 0.25, 1.0, 0.0]
        self.assertFalse(stats.clearly_better(rates, rates, draws))

    def test_dominant_model_is_clearly_better(self):
        draws = stats.bootstrap_draws(6)
        self.assertTrue(stats.clearly_better([1.0] * 6, [0.0] * 6, draws))

    def test_summary_bands_and_frontier(self):
        s = summary()
        self.assertEqual([m["band"] for m in s["models"]], [1, 1, 3, 4])
        self.assertEqual([m["name"] for m in s["frontier"]], ["Lark 2 Fast", "Lark 2", "Quill 1"])
        self.assertEqual(s["never"], ["COS-02"])
        self.assertEqual(s["runs"], 3)

    def test_pass_rate_must_equal_mean_of_task_rates(self):
        ledger = build.load_ledger(LEDGER)
        report = build.load_reports(REPORTS)[0]
        board = json.loads(ALPHA.read_text(encoding="utf-8"))
        board["models"][0]["metrics"]["full_contract_pass_rate"] = 0.99
        with self.assertRaises(stats.DataError):
            stats.summarize(board, ledger["task_order"], ledger["roles"], report["models"])

    def test_task_cells_must_match_the_ledger(self):
        ledger = build.load_ledger(LEDGER)
        report = build.load_reports(REPORTS)[0]
        board = json.loads(ALPHA.read_text(encoding="utf-8"))
        board["models"][0]["task_cells"].pop()
        with self.assertRaisesRegex(stats.DataError, "do not match the ledger"):
            stats.summarize(board, ledger["task_order"], ledger["roles"], report["models"])


def boxes(svg: str) -> tuple[list, list]:
    """Label boxes and dot centres recomputed from the rendered SVG."""
    labels = []
    for x, y, anchor, name in re.findall(r'<text class="lb" x="([\d.]+)" y="([\d.]+)" text-anchor="(\w+)">([^<]+)</text>', svg):
        labels.append((name, charts._box(float(x), float(y), anchor, charts.text_width(name))))
    dots = [(float(x), float(y)) for x, y in re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="7"/>', svg)]
    return labels, dots


class ScatterLabels(unittest.TestCase):
    def assert_clear(self, svg: str):
        labels, dots = boxes(svg)
        self.assertEqual(len(labels), len(dots))
        for (a, ba), (b, bb) in itertools.combinations(labels, 2):
            self.assertFalse(charts._boxes_overlap(ba, bb, 0), f"{a} overlaps {b}")
        for name, box in labels:
            for cx, cy in dots:
                self.assertFalse(charts._box_hits_circle(box, cx, cy, charts.DOT_R), f"{name} overlaps a dot")
            self.assertGreaterEqual(box[0], charts.PAD_L)
            self.assertLessEqual(box[2], charts.W)
            self.assertGreaterEqual(box[1], 0)
            self.assertLessEqual(box[3], charts.H - charts.PAD_B)

    def test_fixture_scatter_labels_are_clear(self):
        self.assert_clear(charts.scatter(summary()))

    def test_crowded_cluster_is_still_placed_without_overlap(self):
        # Eight models packed into a small region of speed and score.
        s = summary()
        base = s["models"][0]
        models = []
        for i in range(8):
            m = dict(base, id=f"m{i}", name=f"Crowded Model {i}", rank=i + 1,
                     rate=0.40 + 0.01 * (i % 3), latency_s=10 + 0.6 * i, ci=(0.30, 0.55))
            models.append(m)
        s = dict(s, models=models, frontier=stats.frontier(models))
        self.assert_clear(charts.scatter(s))

    def test_labels_move_with_the_data_not_a_name_table(self):
        s = summary()
        moved = dict(s, models=[dict(m, latency_s=m["latency_s"] * 1.7) for m in s["models"]])
        moved["frontier"] = stats.frontier(moved["models"])
        first, second = boxes(charts.scatter(s))[0], boxes(charts.scatter(moved))[0]
        self.assertNotEqual(first, second)
        self.assert_clear(charts.scatter(moved))

    def test_impossible_layout_raises(self):
        points = [{"x": 100 + i * 0.1, "y": 100, "label": "A very long model label " + str(i)} for i in range(40)]
        labels = charts.place_labels(points, (0, 0, 220, 220))
        self.assertFalse(all(lb["ok"] for lb in labels))


if __name__ == "__main__":
    unittest.main()
