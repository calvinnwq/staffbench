"""Summary numbers and uncertainty for one report's published results.

Pure functions over a contract-shaped leaderboard and the task ledger's task order.
The pass rate is the scoring policy's mean of per-task pass rates. Ranges and the
tied-for-first band use a paired task bootstrap with a fixed seed, so every build
produces the same numbers (the method of the approved prototype).
"""
from __future__ import annotations

import random

SEED = 20261013
DRAWS = 5000
RATE_TOLERANCE = 1e-5


class DataError(ValueError):
    """Input data that the site cannot render faithfully."""


def bootstrap_draws(task_count: int, draws: int = DRAWS, seed: int = SEED) -> list[list[int]]:
    rng = random.Random(seed)
    return [[rng.randrange(task_count) for _ in range(task_count)] for _ in range(draws)]


def draw_means(values: list[float], draws: list[list[int]]) -> list[float]:
    """Sorted mean of `values` over each draw of task indices."""
    count = len(values)
    return sorted(sum(values[i] for i in draw) / count for draw in draws)


def interval(sorted_means: list[float]) -> tuple[float, float]:
    """Central 95% range of a sorted bootstrap distribution."""
    n = len(sorted_means)
    return sorted_means[int(0.025 * n)], sorted_means[int(0.975 * n) - 1]


def clearly_better(a: list[float], b: list[float], draws: list[list[int]]) -> bool:
    """True when a beats b on at least 97.5% of paired draws (the lower 95% bound is above zero)."""
    diffs = [x - y for x, y in zip(a, b)]
    return draw_means(diffs, draws)[int(0.025 * len(draws))] > 0


def _cells(model: dict, task_ids: list[str]) -> list[tuple[str, int, int]]:
    by_task = {c["task_id"]: c for c in model["task_cells"]}
    if set(by_task) != set(task_ids):
        missing = sorted(set(task_ids) - set(by_task))
        extra = sorted(set(by_task) - set(task_ids))
        raise DataError(f"{model['model_id']}: task cells do not match the ledger (missing {missing}, unknown {extra})")
    return [(t, by_task[t]["comparable_full_contract_pass_runs"], by_task[t]["comparable_runs"]) for t in task_ids]


def frontier(models: list[dict]) -> list[dict]:
    """Models that no faster model beats on pass rate, fastest first."""
    timed = sorted((m for m in models if m["latency_s"] is not None), key=lambda m: m["latency_s"])
    front, best = [], -1.0
    for m in timed:
        if m["rate"] > best:
            front.append(m)
            best = m["rate"]
    return front


def summarize(board: dict, task_ids: list[str], roles: list[dict], names: dict[str, str]) -> dict:
    """Derive every number the report page shows from one leaderboard."""
    ranked = {r["model_id"]: r["rank"] for r in board["overall"]["ranked"]}
    by_id = {m["model_id"]: m for m in board["models"]}
    if not ranked:
        raise DataError("results rank no models")
    absent = sorted(set(ranked) - set(by_id))
    if absent:
        raise DataError(f"ranked models missing from the model list: {absent}")
    unnamed = sorted(set(by_id) - set(names))
    if unnamed:
        raise DataError(f"report config has no display name for: {unnamed}")

    models = []
    for model_id in sorted(ranked, key=lambda i: (ranked[i], i)):
        src = by_id[model_id]
        cells = _cells(src, task_ids)
        rates = [k / n if n else 0.0 for _, k, n in cells]
        metrics = src["metrics"]
        rate = metrics["full_contract_pass_rate"]
        if rate is None or abs(rate - sum(rates) / len(rates)) > RATE_TOLERANCE:
            raise DataError(f"{model_id}: full_contract_pass_rate {rate} is not the mean of its per-task pass rates")
        latency = metrics.get("median_latency_ms")
        models.append({
            "id": model_id,
            "name": names[model_id],
            "rank": ranked[model_id],
            "status": src["status"],
            "rate": rate,
            "cells": cells,
            "scored": sum(n for _, _, n in cells),
            "every": sum(1 for _, k, n in cells if n > 0 and k == n),
            "any": sum(1 for _, k, _ in cells if k > 0),
            "auto": metrics.get("automatic_check_pass_rate"),
            "hard": metrics.get("hard_failure_rate"),
            "json": metrics.get("strict_json_valid_rate"),
            "latency_s": None if latency is None else latency / 1000,
            "tokens": (src.get("usage") or {}).get("mean_output_tokens"),
            "_rates": rates,
            "_replicates": (src.get("coverage") or {}).get("maximum_replicates"),
        })

    draws = bootstrap_draws(len(task_ids))
    for m in models:
        m["ci"] = interval(draw_means(m["_rates"], draws))
    better = {(a["id"], b["id"]): clearly_better(a["_rates"], b["_rates"], draws)
              for a in models for b in models if a is not b}
    for m in models:
        m["band"] = 1 + sum(1 for o in models if o is not m and better[(o["id"], m["id"])])

    leader = models[0]
    behind = [m for m in models if m["band"] != 1]
    runs = max([m["_replicates"] or 0 for m in models] + [n for m in models for _, _, n in m["cells"]])

    totals = [(t, sum(m["cells"][i][1] for m in models), sum(m["cells"][i][2] for m in models))
              for i, t in enumerate(task_ids)]
    index = {t: i for i, t in enumerate(task_ids)}
    role_rows = {}
    for role in roles:
        idxs = [index[t] for t in role["task_ids"]]
        rows = [(m["name"], sum(m["cells"][i][1] for i in idxs), sum(m["cells"][i][2] for i in idxs), pos)
                for pos, m in enumerate(models)]
        rows.sort(key=lambda r: (-(r[1] / r[2] if r[2] else 0.0), r[3]))
        role_rows[role["id"]] = [r[:3] for r in rows]

    left_out = []
    for model_id in sorted(set(by_id) - set(ranked)):
        src = by_id[model_id]
        reason = src.get("exclusion_reason") or "; ".join(src.get("reason_codes") or []) or "not ranked"
        left_out.append((names[model_id], reason))

    for m in models:
        del m["_rates"], m["_replicates"]
    aggregate = board.get("aggregate") or {}
    return {
        "models": models,
        "task_ids": list(task_ids),
        "runs": runs,
        "draws": len(draws),
        "task_totals": totals,
        "never": [t for t, k, _ in totals if k == 0],
        "tied": [m for m in models if m["band"] == 1],
        "behind_leader": all(better[(leader["id"], m["id"])] for m in behind),
        "frontier": frontier(models),
        "role_rows": role_rows,
        "left_out": left_out,
        "aggregate": aggregate,
        "human_scores_assigned": bool(aggregate.get("human_scores_assigned")),
        "meta": {k: board.get(k) for k in ("benchmark_version", "policy_id", "policy_version", "release_lock_fingerprint")},
        "preview_notice": board.get("preview_notice"),
    }
