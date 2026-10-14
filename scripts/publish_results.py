#!/usr/bin/env python3
"""Publish a built leaderboard as results/<slug>/leaderboard.json.

The published copy drops every run_id, record_path and raw_output_reference field and the
input block, so it carries no pointer back to private run evidence. Every other field is kept
unchanged. The output is then scanned with check_public_content.py (built-in rules plus any
STAFFBENCH_PRIVATE_TERMS patterns); on a hit the output is removed and the command exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from check_public_content import BUILT_IN, load_private_terms, scan_text
except ImportError:  # pragma: no cover - package-style import
    from scripts.check_public_content import BUILT_IN, load_private_terms, scan_text


ROOT = Path(__file__).resolve().parents[1]
STRIPPED_FIELDS = frozenset({"run_id", "record_path", "raw_output_reference"})
REQUIRED_FIELDS = ("schema_version", "benchmark_id", "benchmark_version", "models", "aggregate", "roles", "input")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PublishError(ValueError):
    """The input or destination cannot be published safely."""


def strip_run_identity(node: Any) -> Any:
    """Return a copy without run identity fields at any depth."""
    if isinstance(node, dict):
        return {key: strip_run_identity(value) for key, value in node.items() if key not in STRIPPED_FIELDS}
    if isinstance(node, list):
        return [strip_run_identity(value) for value in node]
    return node


def published_copy(leaderboard: Any) -> dict[str, Any]:
    if not isinstance(leaderboard, dict) or any(field not in leaderboard for field in REQUIRED_FIELDS):
        raise PublishError("input is not a built leaderboard (missing " + ", ".join(REQUIRED_FIELDS) + ")")
    return strip_run_identity({key: value for key, value in leaderboard.items() if key != "input"})


def publish(source: Path, slug: str, results_root: Path, *, force: bool = False) -> tuple[Path, list[str]]:
    """Write the published copy; return its path and any leak-check hits (output removed on a hit)."""
    if not SLUG_PATTERN.fullmatch(slug):
        raise PublishError("slug must be lowercase letters, digits and hyphens, starting with a letter or digit")
    try:
        leaderboard = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublishError(f"unable to read leaderboard ({type(exc).__name__})") from exc
    document = published_copy(leaderboard)
    destination = results_root / slug / "leaderboard.json"
    if destination.exists() and not force:
        raise PublishError(f"{destination} already exists; pass --force to replace it")
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    patterns = BUILT_IN + load_private_terms(os.environ.get("STAFFBENCH_PRIVATE_TERMS"))
    hits = [f"{destination}:{number}: {found}" for number, found in scan_text(text, patterns)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    if hits:
        destination.unlink()
    return destination, hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a built leaderboard under results/<slug>/.")
    parser.add_argument("leaderboard", type=Path, help="leaderboard.json written by build_leaderboard.py")
    parser.add_argument("--slug", required=True, help="report slug, for example 01-free-pool")
    parser.add_argument("--results-root", type=Path, default=ROOT / "results")
    parser.add_argument("--force", action="store_true", help="replace an existing published copy")
    args = parser.parse_args(argv)
    try:
        destination, hits = publish(args.leaderboard, args.slug, args.results_root, force=args.force)
    except PublishError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 2
    if hits:
        print("\n".join(hits))
        print(f"{len(hits)} public-content hit(s); nothing was published", file=sys.stderr)
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
