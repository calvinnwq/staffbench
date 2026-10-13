#!/usr/bin/env python3
"""Fail when public StaffBench content contains private or credential-shaped text.

Usage: check_public_content.py [PATH ...]

With no paths, every file tracked by Git is scanned. Directories are scanned recursively.
Built-in rules always apply. When STAFFBENCH_PRIVATE_TERMS names a file, its patterns apply too:
one Python regular expression per line, '#' starts a comment, '(?i)' makes a line case-insensitive.
A line containing 'public-content: allow' is skipped, for tests that must name a forbidden shape.
Prints 'path:line: matched-text' per hit and exits 1 on any hit.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "public-content: allow"
_ALLOWED_EMAIL = re.compile(r"@(users\.noreply\.github\.com|example\.(com|org|net))$", re.I)
BUILT_IN = [
    re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+|/Volumes/"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}"
               r"|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,})\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def load_private_terms(path: str | None) -> list[re.Pattern[str]]:
    if not path:
        return []
    patterns = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(re.compile(line))
    return patterns


def iter_files(paths: list[str]) -> list[Path]:
    if not paths:
        out = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout
        return [Path(p) for p in out.decode().split("\0") if p]
    files: list[Path] = []
    for p in map(Path, paths):
        files.extend(sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p])
    return files


def scan_text(text: str, patterns: list[re.Pattern[str]]) -> list[tuple[int, str]]:
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for pattern in patterns:
            for match in pattern.finditer(line):
                found = match.group(0)
                if "@" in found and pattern is BUILT_IN[1] and _ALLOWED_EMAIL.search(found):
                    continue
                hits.append((number, found))
    return hits


def main(argv: list[str]) -> int:
    patterns = BUILT_IN + load_private_terms(os.environ.get("STAFFBENCH_PRIVATE_TERMS"))
    total = 0
    for path in iter_files(argv):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for number, found in scan_text(text, patterns):
            print(f"{path}:{number}: {found}")
            total += 1
    if total:
        print(f"{total} public-content hit(s)", file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
