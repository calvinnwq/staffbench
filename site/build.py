#!/usr/bin/env python3
"""Build the StaffBench static site.

Usage:
  python3 -S site/build.py --out dist [--ledger PATH] [--reports-dir DIR]
                           [--results-override SLUG=PATH ...] [--base-url URL]

Reads the task ledger (default data/task-ledger.json), every <reports-dir>/*/report.json and,
for each report, results/<slug>/leaderboard.json when present (or the override path).
Writes index.html, roles/index.html, reports/<slug>/index.html and 404.html.

Stdlib only, no scripts or remote assets in the output, and byte-identical across builds.
The build fails, leaving --out untouched, when a page has an unresolved relative link or
anchor, references a remote asset, or the public-content check finds a hit.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import charts  # noqa: E402
import stats  # noqa: E402
from charts import E, join_and, not_free_tag, pct, span  # noqa: E402

BRAND = "StaffBench"
LICENCE_NOTICE = ('<p>Code: <a href="https://github.com/calvinnwq/staffbench/blob/main/LICENSE">MIT</a>. '
                  'Tasks and results: <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. '
                  '<a href="https://github.com/calvinnwq/staffbench">GitHub repository</a>.</p>')
LEAK_CHECK = REPO / "scripts" / "check_public_content.py"
ALLOW_MARKER = "public-content: allow"
PAGE_NAMES = {"index.html", "404.html", "roles", "reports"}
SVG_NS = "http://www.w3.org/2000/svg"
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='" + SVG_NS + "' viewBox='0 0 32 32'%3E"
           "%3Ccircle cx='16' cy='16' r='11' fill='%23d4470f'/%3E%3C/svg%3E")
REPORT_KEYS = ("slug", "number", "title", "label", "published", "pool", "harness", "run_dates",
               "reasoning", "models", "catches", "notes")
WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
         "fifteen sixteen seventeen eighteen nineteen twenty").split()


class BuildError(Exception):
    """A problem that must stop the build."""


def word(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def emphasis(text: str) -> str:
    """Escape text and turn *words* into <em>words</em>."""
    return re.sub(r"\*([^*]+)\*", r"<em>\1</em>", E(text))


def plain(text: str) -> str:
    return text.replace("*", "")


def pending(value: str) -> bool:
    return str(value).strip().lower() == "pending"


# ---------- inputs ----------

def read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BuildError(f"{what} not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise BuildError(f"{what} is not valid JSON: {path}: {exc}") from None


def load_ledger(path: Path) -> dict:
    ledger = read_json(path, "task ledger")
    roles, tasks = ledger.get("roles"), ledger.get("tasks")
    if not roles or not tasks:
        raise BuildError(f"task ledger has no roles or tasks: {path}")
    by_id = {t["id"]: t for t in tasks}
    for role in roles:
        for key in ("id", "code", "name", "tag", "mission", "task_ids", "primary_dimensions"):
            if key not in role:
                raise BuildError(f"ledger role {role.get('id')!r} lacks {key!r}")
        unknown = [t for t in role["task_ids"] if t not in by_id]
        if unknown:
            raise BuildError(f"ledger role {role['id']!r} lists unknown tasks {unknown}")
    order = [t for role in roles for t in role["task_ids"]]
    if sorted(order) != sorted(by_id):
        raise BuildError("ledger roles must list every task exactly once")
    for task in tasks:
        for key in ("id", "role_id", "title", "prompt_contract", "measurement", "known_failure_modes"):
            if key not in task:
                raise BuildError(f"ledger task {task.get('id')!r} lacks {key!r}")
    ledger["task_order"] = order
    ledger["task_by_id"] = by_id
    return ledger


def load_reports(reports_dir: Path) -> list[dict]:
    reports = []
    for path in sorted(reports_dir.glob("*/report.json")):
        report = read_json(path, "report config")
        missing = [k for k in REPORT_KEYS if k not in report]
        if missing:
            raise BuildError(f"{path}: missing keys {missing}")
        if report["slug"] != path.parent.name:
            raise BuildError(f"{path}: slug {report['slug']!r} does not match its directory")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", report["slug"]):
            raise BuildError(f"{path}: slug must be lowercase words joined by hyphens")
        if not isinstance(report["number"], int) or report["number"] < 1:
            raise BuildError(f"{path}: number must be a positive integer")
        not_free = report.get("not_free_as_of", {})
        if not isinstance(not_free, dict):
            raise BuildError(f"{path}: not_free_as_of must be an object mapping display names to dates")
        months = tuple("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())
        for name, value in not_free.items():
            if name not in report["models"].values():
                raise BuildError(f"{path}: not_free_as_of has unknown model display name {name!r}")
            match = re.fullmatch(r"([1-9]|[12][0-9]|3[01]) ([A-Z][a-z]{2}) ([0-9]{4})", value) if isinstance(value, str) else None
            try:
                if match is None:
                    raise ValueError
                day, month, year = match.groups()
                date(int(year), months.index(month) + 1, int(day))
            except ValueError:
                raise BuildError(f"{path}: not_free_as_of[{name!r}] must be a real date like '21 Oct 2026', got {value!r}") from None
        reports.append(report)
    numbers = [r["number"] for r in reports]
    if len(set(numbers)) != len(numbers):
        raise BuildError(f"report numbers must be unique: {sorted(numbers)}")
    return sorted(reports, key=lambda r: r["number"])


def parse_overrides(values: list[str]) -> dict[str, Path]:
    out = {}
    for value in values:
        slug, sep, path = value.partition("=")
        if not sep or not slug or not path:
            raise BuildError(f"--results-override expects SLUG=PATH, got {value!r}")
        out[slug] = Path(path).expanduser()
    return out


def attach_results(reports: list[dict], ledger: dict, overrides: dict[str, Path], results_dir: Path) -> None:
    unknown = sorted(set(overrides) - {r["slug"] for r in reports})
    if unknown:
        raise BuildError(f"--results-override names unknown reports: {unknown}")
    for report in reports:
        path = overrides.get(report["slug"], results_dir / report["slug"] / "leaderboard.json")
        report["summary"] = None
        if report["slug"] not in overrides and not path.exists():
            continue
        board = read_json(path, f"results for {report['slug']}")
        try:
            report["summary"] = stats.summarize(board, ledger["task_order"], ledger["roles"], report["models"])
        except stats.DataError as exc:
            raise BuildError(f"results for {report['slug']}: {exc}") from None


# ---------- page shell ----------

class Site:
    def __init__(self, ledger: dict, reports: list[dict], css: str, base_url: str | None):
        self.ledger = ledger
        self.roles = ledger["roles"]
        self.tasks = ledger["task_by_id"]
        self.reports = reports
        self.css = css
        self.base_url = base_url
        notices = [r["summary"]["preview_notice"] for r in reports if r["summary"] and r["summary"]["preview_notice"]]
        if ledger.get("preview_notice"):
            notices.append(ledger["preview_notice"])
        self.notices = sorted(set(notices))
        with_results = [r for r in reports if r["summary"]]
        self.newest = with_results[-1] if with_results else None
        self.runs = self.newest["summary"]["runs"] if self.newest else None
        sizes = {len(r["task_ids"]) for r in self.roles}
        self.tasks_per_role = sizes.pop() if len(sizes) == 1 else None

    def shell(self, *, title: str, description: str, path: str | None, root: str, body: str,
              skip: str, og_type: str = "website") -> str:
        meta = []
        if self.base_url and path is not None:
            meta.append(f'<link rel="canonical" href="{E(self.base_url + path)}">')
            meta.append(f'<meta property="og:url" content="{E(self.base_url + path)}">')
        banner = "".join(f'<p class="draft" role="note">{E(n)}</p>' for n in self.notices)
        return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<meta name="description" content="{E(description)}">
{"".join(meta)}
<meta property="og:site_name" content="{BRAND}">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{E(description)}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{E(description)}">
<link rel="icon" href="{FAVICON}">
<style>{self.css}</style>
</head>
<body>
<a class="skip" href="#main">{skip}</a>
{banner}
{body}
</body>
</html>
'''

    def runs_phrase(self) -> str:
        return f"{word(self.runs)} runs each" if self.runs else "repeated runs"

    # ---------- home ----------

    def home(self) -> str:
        nr, nt = len(self.roles), len(self.tasks)
        hats = "".join(f'<li><a href="roles/#{E(r["id"])}"><code>{E(r["code"])}</code>{E(r["name"])}</a></li>' for r in self.roles)
        cards = [self.report_card(r) for r in self.reports]
        upcoming = (self.reports[-1]["number"] + 1) if self.reports else 1
        cards.append(f'''
      <li class="rep rep-next">
        <span class="rep-no">{upcoming:02d}</span>
        <span class="rep-main"><span class="rep-kicker">Next</span>
          <span class="rep-title">Another model pool</span>
          <span class="rep-sum">Each report compares one pool of models, such as leading paid models or models run on your own computer, on the same tasks.</span></span>
      </li>''')
        runs_rule = (f"<li><b>{cap(word(self.runs))} runs each.</b> Reports show how often a model passes every time, not just once.</li>"
                     if self.runs else "<li><b>Repeated runs.</b> Reports show how often a model passes every time, not just once.</li>")
        body = f'''<header class="mast">
  <div class="wrap">
    <div class="strip">
      <a class="brand" href="./">{BRAND}</a>
      <nav aria-label="Site"><a href="#reports">Reports</a><a href="roles/">The roles</a><a href="#how">How it works</a></nav>
    </div>
    <div class="headline">
      <p class="kicker">An open benchmark for personal AI agents</p>
      <h1>One person. <em>{cap(word(nr))}</em> hats. Which model can wear them?</h1>
      <p class="deck">{BRAND} tests models on the work a personal agent actually does: running your plans, your money, your code, your trips and your training. {nr} roles, {nt} fixed tasks, {self.runs_phrase()}, every answer checked automatically. Each report compares one pool of models.</p>
    </div>
    <ul class="hats" aria-label="The roles">{hats}</ul>
  </div>
</header>
<main id="main" class="wrap">
  <section id="reports" class="sec">
    <div class="sec-h"><span class="no">01</span><div><h2>Reports</h2>
      <p>A report is one comparison: one pool of models, one test setup, one set of run dates. The tasks stay fixed, so you can line reports up against each other.</p></div></div>
    <ol class="reps">{"".join(cards)}</ol>
  </section>
  <section id="how" class="sec">
    <div class="sec-h"><span class="no">02</span><div><h2>How it works</h2>
      <p>The same rules apply in every report, so a model is judged on the work rather than on luck with tools.</p></div></div>
    <ul class="rules-grid">
      <li><b>Fixed tasks, invented data.</b> {cap(word(self.tasks_per_role)) + " tasks" if self.tasks_per_role else "A few tasks"} per role, built from made-up people, numbers, logs and files.</li>
      <li><b>Offline.</b> No web and no tools. Everything the model needs is in the task.</li>
      <li><b>Checked, not judged by feel.</b> Automatic checks recalculate totals, match IDs and look for planted problems.</li>
      <li><b>Instant fails.</b> Serious mistakes, such as claiming an action was taken, fail the run.</li>
      {runs_rule}
      <li><b>Honest uncertainty.</b> Scores carry ranges, and models that cannot be told apart are shown as tied.</li>
    </ul>
  </section>
</main>
<footer class="foot"><div class="wrap"><p>{BRAND} measures how models handle the structured work a personal agent does. It is not a general intelligence ranking.</p>
<p><a href="roles/">The roles</a></p>{LICENCE_NOTICE}</div></footer>'''
        return self.shell(title=f"{BRAND} &middot; A benchmark for personal AI agents",
                          description=f"{BRAND}: an open benchmark of the jobs a personal AI agent does. {nr} roles, {nt} fixed tasks, and a separate report for each pool of models.",
                          path="", root="./", body=body, skip="Skip to reports")

    def report_card(self, r: dict) -> str:
        s = r["summary"]
        date = "Publication pending" if pending(r["published"]) else E(r["published"])
        question = E(plain(r["headline"])) + " " if r.get("headline") else ""
        if s:
            leader, tied, never = s["models"][0], len(s["tied"]), len(s["never"])
            leader_name = f'<b>{E(leader["name"])}</b>' + not_free_tag(leader["name"], r.get("not_free_as_of", {}))
            kicker = f'{date} &middot; {len(s["models"])} models &middot; {len(s["task_ids"])} tasks &times; {s["runs"]} runs'
            if tied > 1:
                standing = f'{leader_name} leads at {pct(leader["rate"], 0)}, but {tied} models are too close to tell apart'
            else:
                standing = f'{leader_name} leads at {pct(leader["rate"], 0)}, clear of every other model'
            unsolved = (f'{never} {plural(never, "task beats", "tasks beat")} every model' if never
                        else "every task was passed at least once")
            summary = f"{question}{standing}, and {unsolved}."
        else:
            kicker = f"{date} &middot; Results pending"
            summary = f"{question}Results are not published yet."
        return f'''
      <li class="rep">
        <a href="reports/{E(r["slug"])}/">
          <span class="rep-no">{r["number"]:02d}</span>
          <span class="rep-main">
            <span class="rep-kicker">{kicker}</span>
            <span class="rep-title">{E(r["label"])}</span>
            <span class="rep-sum">{summary}</span>
          </span>
          <span class="rep-go">Read report &#8594;</span>
        </a>
      </li>'''

    # ---------- roles ----------

    def roles_page(self) -> str:
        nr, nt = len(self.roles), len(self.tasks)
        index, sections = [], []
        for n, role in enumerate(self.roles, 1):
            rid = E(role["id"])
            index.append(f'<li><a href="#{rid}"><code>{E(role["code"])}</code><b>{E(role["name"])}</b><span>{E(role["mission"])}</span></a></li>')
            dims = "".join(f"<li>{E(d)}</li>" for d in role["primary_dimensions"])
            cards = "".join(self.task_card(t) for t in role["task_ids"])
            sections.append(f'''
  <section class="role" id="{rid}" aria-labelledby="{rid}-h">
    <header class="role-h">
      <span class="role-no">{n:02d}</span>
      <div><p class="role-code">{E(role["code"])}</p><h3 id="{rid}-h">{E(role["name"])}</h3>
        <p class="mission">{E(role["mission"])}</p></div>
      <div class="good"><h5>What good looks like</h5><ul>{dims}</ul></div>
    </header>
    <div class="tasks">{cards}</div>
  </section>''')
        per_role = f"{word(self.tasks_per_role)} fixed tasks" if self.tasks_per_role else "fixed tasks"
        runs_rule = (f"<li><b>{cap(word(self.runs))} runs each.</b> One pass is luck. The results page shows how often each model passes every time.</li>"
                     if self.runs else "<li><b>Repeated runs.</b> One pass is luck. The results page shows how often each model passes every time.</li>")
        body = f'''<header class="mast">
  <div class="wrap">
    <div class="strip">
      <a class="brand" href="../">{BRAND}</a>
      <nav aria-label="Site"><a href="../#reports">Reports</a><a href="#index">The roles</a><a href="#ground-rules">Ground rules</a></nav>
    </div>
    <div class="headline">
      <p class="kicker">The roles &middot; {nr} roles &middot; {nt} tasks</p>
      <h1>One person. <em>{cap(word(nr))}</em> hats.</h1>
      <p class="deck">A personal AI agent is not one job. On a single day it might run your plans, check your finances, review your code and get you up a mountain. Each role below is one of those jobs, tested with {per_role} built from invented people, numbers and files. Every report runs the same tasks, so results line up across reports.</p>
    </div>
  </div>
</header>
<main id="main" class="wrap">
  <section id="index" class="sec">
    <div class="sec-h"><span class="no">00</span><div><h2>The staff</h2>
      <p>Jump to any role for its tasks, what the model gets, what it must hand back, and the traps each task sets.</p></div></div>
    <ol class="role-index">{"".join(index)}</ol>
  </section>
  {"".join(sections)}
  <section id="ground-rules" class="sec">
    <div class="sec-h"><span class="no">{nr + 1:02d}</span><div><h2>Ground rules for every task</h2>
      <p>The same rules apply to all {nt} tasks, so a model is judged on the work rather than on luck with tools.</p></div></div>
    <ul class="rules-grid">
      <li><b>Offline and fixed.</b> No web, no tools. Every fact the model needs is in the task.</li>
      <li><b>Invented data only.</b> People, accounts, logs and catalogues are made up, so no real records are involved.</li>
      <li><b>Answer checked, not judged by feel.</b> Automatic checks recalculate totals, match IDs and look for the planted problems.</li>
      <li><b>Instant fails.</b> Serious mistakes, such as claiming an action was taken, fail the run however good the rest is.</li>
      <li><b>Known-good and known-bad answers.</b> Every task ships with both, to prove the checks catch what they should.</li>
      {runs_rule}
    </ul>
  </section>
</main>
<footer class="foot"><div class="wrap"><p>{BRAND} measures how models handle the structured work a personal agent does. It is not a general intelligence ranking.</p>
<p><a href="../">All reports</a></p>{LICENCE_NOTICE}</div></footer>'''
        return self.shell(title=f"The roles &middot; {BRAND}",
                          description=f"The {word(nr)} roles a personal AI agent plays, and the {nt} tasks that test each one.",
                          path="roles/", root="../", body=body, skip="Skip to the roles")

    def task_card(self, tid: str) -> str:
        t = self.tasks[tid]
        pc, ms = t["prompt_contract"], t["measurement"]

        def items(values):
            return "".join(f"<li>{E(v)}</li>" for v in values)

        chips = "".join(f"<li>{E(f.replace('_', ' '))}</li>" for f in pc.get("required_output_fields", []))
        fmt_key = pc.get("required_output_format", "")
        fmt = {"json": "One JSON object", "repository_change": "A code change plus a JSON report",
               "structured_markdown": "A structured document"}.get(fmt_key, cap(fmt_key.replace("_", " ")))
        human = "".join(f'<li>{E(h["label"])} <small>{round(h["weight"] * 100)}%</small></li>' for h in ms.get("human_dimensions", []))
        scored = self.newest and self.newest["summary"]["human_scores_assigned"]
        anchor = E(tid.lower())
        return f'''
      <article class="task" id="{anchor}">
        <header><code>{E(tid)}</code><h4>{E(t["title"])}</h4></header>
        <dl class="io">
          <div><dt>Given</dt><dd>{E(pc.get("input_description", ""))}</dd></div>
          <div><dt>Must return</dt><dd>{E(fmt)} with<ul class="chips">{chips}</ul></dd></div>
        </dl>
        <div class="traps"><h5>Traps it sets</h5><ul>{items(t["known_failure_modes"])}</ul></div>
        <details>
          <summary>How it is scored</summary>
          <div class="score">
            <div><h5>Rules</h5><ul>{items(pc.get("output_constraints", []))}</ul></div>
            <div><h5>Checked automatically</h5><ul>{items(c["description"] for c in ms.get("automatic_checks", []))}</ul></div>
            <div class="hardf"><h5>Instant fail if</h5><ul>{items(h["condition"] for h in ms.get("hard_failures", []))}</ul></div>
            <div><h5>Human review{"" if scored else " (not yet scored)"}</h5><ul class="hum">{human}</ul></div>
          </div>
        </details>
        {self.task_strip(tid)}
      </article>'''

    def task_strip(self, tid: str) -> str:
        if not self.newest:
            return '<div class="res"><p class="res-h"><span>Results pending: no report has published runs yet.</span></p></div>'
        r, s = self.newest, self.newest["summary"]
        i = s["task_ids"].index(tid)
        cells = [(m, m["cells"][i][1], m["cells"][i][2]) for m in s["models"]]
        k_all, n_all = sum(k for _, k, _ in cells), sum(n for _, _, n in cells)
        best = max((k / n for _, k, n in cells if n), default=0.0)
        leaders = [(m, k, n) for m, k, n in cells if best > 0 and n and k / n == best]
        pips = []
        for m, k, n in cells:
            dots = "".join(f'<i class="{"on" if j < k else "off"}"></i>' for j in range(n)) + '<i class="na"></i>' * (s["runs"] - n)
            pips.append(f'<li class="c{min(k, 3)}" title="{E(m["name"])}: {k} of {n} runs passed"><span class="pips">{dots}</span><span class="mn">{E(m["name"])}</span></li>')
        if leaders:
            m, k, n = leaders[0]
            lead = (f'Best: <b>{E(m["name"])}</b>' + not_free_tag(m["name"], r.get("not_free_as_of", {}))
                    + (f" +{len(leaders) - 1} tied" if len(leaders) > 1 else "") + f" ({k}/{n})")
        else:
            lead = '<b class="nil">No model passed a single run.</b>'
        return f'''
        <div class="res">
          <p class="res-src"><a href="../reports/{E(r["slug"])}/">Report {r["number"]:02d} &middot; {E(r["title"])}</a></p>
          <p class="res-h"><span><b>{k_all}</b> of {n_all} runs passed across {len(cells)} models</span><span>{lead}</span></p>
          <ol class="res-grid">{"".join(pips)}</ol>
        </div>'''

    # ---------- report ----------

    def report_page(self, r: dict) -> str:
        root, s = "../../", r["summary"]
        number = f'{r["number"]:02d}'
        date = "Publication pending" if pending(r["published"]) else f'Published {E(r["published"])}'
        run_dates = "run dates pending" if pending(r["run_dates"]) else E(r["run_dates"])
        headline = emphasis(r["headline"]) if r.get("headline") else E(r["title"])
        kicker = E(r.get("kicker") or r["label"])
        nt, nr = len(self.tasks), len(self.roles)
        if s:
            nm, runs = len(s["models"]), s["runs"]
            reliable = max(m["every"] for m in s["models"])
            deck = (f"This report covers one pool of models: {E(r['title'])}. We ran {nm} of them on the {nt} tasks behind "
                    f"{BRAND}'s {nr} roles, {word(runs)} runs each, every answer checked automatically. "
                    f"The most reliable passed <strong>{reliable} of {nt} tasks on every run.</strong>")
            runs_dd = f"{nt} tasks &times; {runs} runs per model, {E(r['reasoning'])} reasoning, {run_dates}"
            nav = ("standings", "Standings"), ("tasks", "Tasks"), ("reliability", "Reliability"), ("speed", "Speed"), \
                  ("roles", "Roles"), ("table", "Every number"), ("method", "Method")
            ledger = self.report_ledger(r, s)
            main = self.report_sections(r, s, root)
            description = (f"{BRAND} report {number}: {plain(r['label'])}. {nm} models on {nt} personal-agent tasks, "
                           f"{word(runs)} runs each, every answer checked automatically.")
        else:
            deck = (f"This report covers one pool of models: {E(r['title'])}, on the {nt} tasks behind {BRAND}'s {nr} roles. "
                    "<strong>Results are pending.</strong>")
            runs_dd = f"{nt} tasks per model, {E(r['reasoning'])} reasoning, {run_dates}"
            nav = (("pending", "Results pending"), ("method", "Method"))
            ledger = ""
            main = self.pending_sections(r, root)
            description = f"{BRAND} report {number}: {plain(r['label'])}. Results pending."
        body = f'''<header class="mast">
  <div class="wrap">
    <div class="strip">
      <a class="brand" href="{root}">{BRAND}</a>
      <nav aria-label="Site"><a href="{root}#reports">Reports</a><a href="{root}roles/">The roles</a><a href="{root}#how">How it works</a></nav>
    </div>
    <div class="report-bar">
      <span class="rb-no">Report {number}</span>
      <span class="rb-name">{E(r["label"])}</span>
      <span class="rb-date">{date}</span>
    </div>
    <div class="headline">
      <p class="kicker">{kicker}</p>
      <h1>{headline}</h1>
      <p class="deck">{deck}</p>
    </div>
    <dl class="scope">
      <div><dt>Model pool</dt><dd>{E(r["pool"])}</dd></div>
      <div><dt>Test setup</dt><dd>{E(r["harness"])}</dd></div>
      <div><dt>Runs</dt><dd>{runs_dd}</dd></div>
    </dl>
    <nav class="sec-nav" aria-label="In this report">{"".join(f'<a href="#{a}">{t}</a>' for a, t in nav)}</nav>
    {ledger}
  </div>
</header>
<main id="main" class="wrap">{main}
</main>
<footer class="foot"><div class="wrap">
  <p>Report {number} compares one pool of models, {E(r["title"])}, on {BRAND}'s fixed tasks. It is not a general intelligence ranking, and other model pools are covered in their own reports.</p>
  <p><a href="{root}">All reports</a> &middot; <a href="{root}roles/">The roles</a></p>{LICENCE_NOTICE}
</div></footer>'''
        return self.shell(title=f"Report {number}: {E(r['title'])} &middot; {BRAND}", description=description,
                          path=f"reports/{r['slug']}/", root=root, body=body, skip="Skip to results", og_type="article")

    @staticmethod
    def report_ledger(r: dict, s: dict) -> str:
        leader, tied, never, nt = s["models"][0], len(s["tied"]), len(s["never"]), len(s["task_ids"])
        if tied > 1:
            close = f'<div><dt>Too close to call</dt><dd><b>{tied} models</b> are too close to tell apart for first place</dd></div>'
        else:
            close = '<div><dt>Clear leader</dt><dd><b>No tie</b> for first: every other model is clearly behind</dd></div>'
        if never:
            unsolved = f'<div><dt>Still unsolved</dt><dd><b>{never} of {nt} tasks</b> were never passed by any model</dd></div>'
        else:
            unsolved = f'<div><dt>Nothing unsolved</dt><dd><b>All {nt} tasks</b> were passed by at least one model</dd></div>'
        return (f'<dl class="ledger"><div><dt>Best observed</dt><dd><b>{E(leader["name"])}</b>'
                f'{not_free_tag(leader["name"], r.get("not_free_as_of", {}))} passed '
                f'{pct(leader["rate"], 0)} of its runs in full</dd></div>{close}{unsolved}</dl>')

    def report_sections(self, r: dict, s: dict, root: str) -> str:
        nt, runs = len(s["task_ids"]), s["runs"]
        lot = f" With {nt} tasks, that is a lot." if nt <= 30 else ""
        never = s["never"]
        if never:
            beat = f"{cap(word(len(never)))} {plural(len(never), 'task beats', 'tasks beat')} every model on every run"
            whole = [x for x in self.roles if x["task_ids"] and set(x["task_ids"]) <= set(never)]
            if whole:
                beat += ", including " + join_and(
                    [f'{"both" if len(x["task_ids"]) == 2 else "every"} {E(x["name"])} {plural(len(x["task_ids"]), "task", "tasks")}' for x in whole])
            beat += "."
        else:
            beat = "Every task was passed at least once by some model."
        front = s["frontier"]
        names = [E(m["name"]) + not_free_tag(m["name"], r.get("not_free_as_of", {})) for m in front]
        if len(front) == 1:
            frontier = f"{names[0]} is alone on it: no model is both faster and better."
        elif len(front) == 2:
            frontier = f"Only two models are on it: {names[0]} for speed and {names[1]} for score."
        else:
            frontier = f"{cap(word(len(front)))} models are on it, from {names[0]}, the fastest, to {names[-1]}, the highest scoring."
        if self.tasks_per_role:
            per_role = (f"Each role has {word(self.tasks_per_role)} {plural(self.tasks_per_role, 'task', 'tasks')}, "
                        f"so each bar rests on just {word(self.tasks_per_role * runs)} runs.")
        else:
            per_role = "Each role has only a few tasks, so each bar rests on few runs."
        return f'''
  <section id="standings" class="sec">
    <div class="sec-h"><span class="no">01</span><div><h2>The standings</h2>
      <p>A full pass means the answer passed every automatic check with no serious mistake. The bars show how far each score could move with a different draw of tasks.{lot}</p></div></div>
    {charts.forest(s, r["catches"], r.get("not_free_as_of", {}))}
  </section>

  <section id="tasks" class="sec">
    <div class="sec-h"><span class="no">02</span><div><h2>Where they pass and where they fail</h2>
      <p>Each square is one model on one task. The dots are its {word(runs)} runs. Read across for a model, down for a task. {beat}</p></div></div>
    {charts.matrix(s, self.roles, {t: self.tasks[t]["title"] for t in self.tasks}, root)}
  </section>

  <section id="reliability" class="sec">
    <div class="sec-h"><span class="no">03</span><div><h2>Once is luck. Every time is reliable.</h2>
      <p>An agent runs the same kind of job again and again, so a model that passes only sometimes is hard to trust. This counts tasks a model passed on <em>every</em> run against tasks it passed at least once.</p></div></div>
    {charts.dumbbell(s)}
  </section>

  <section id="speed" class="sec">
    <div class="sec-h"><span class="no">04</span><div><h2>Speed against score</h2>
      <p>Up is better, left is faster. The line joins models that no faster model beats on score. {frontier}</p></div></div>
    {charts.scatter(s)}
  </section>

  <section id="roles" class="sec">
    <div class="sec-h"><span class="no">05</span><div><h2>The best model for each role</h2>
      <p>{per_role} Treat these as leads to check, not settings to apply. Every role has a full brief: what it gets, what it must return, and the traps it sets.</p></div></div>
    <div class="profs">{self.role_cards(r, s, root)}</div>
  </section>

  <section id="table" class="sec">
    <div class="sec-h"><span class="no">06</span><div><h2>Every number</h2>
      <p>Hard failure: a serious mistake, such as ignoring a hard limit or claiming an unsafe action was done. Valid JSON: answers in the required structured text format, with named fields. Provisional: at least one task has fewer scored runs than planned. Median time: the middle response time when sorted. Output tokens: the pieces of text in an answer.</p></div></div>
    {self.full_table(r, s)}
  </section>
{self.method(r, s)}'''

    def role_cards(self, r: dict, s: dict, root: str) -> str:
        cards = []
        for role in self.roles:
            rows = s["role_rows"][role["id"]]
            top = rows[0][1] / rows[0][2] if rows and rows[0][2] else 0.0
            bars = []
            for name, k, n in rows:
                rate = k / n if n else 0.0
                lead = top > 0 and rate == top
                bars.append(f'<li class="{"lead" if lead else ""}"><span class="pn">{E(name)}</span>'
                            f'<span class="pb"><span style="width:{rate * 100:.1f}%"></span></span><span class="pv">{k}/{n}</span></li>')
            if top == 0:
                verdict = '<p class="pverdict none">No model passed a single run.</p>'
            else:
                leads = [x[0] for x in rows if x[2] and x[1] / x[2] == top]
                verdict = (f'<p class="pverdict"><b>{E(leads[0])}</b>' + not_free_tag(leads[0], r.get("not_free_as_of", {}))
                           + (f" and {len(leads) - 1} more tied" if len(leads) > 1 else "")
                           + f" &middot; {pct(top, 0)}</p>")
            tasks = "".join(f'<li><code>{E(t)}</code> {E(self.tasks[t]["title"])}</li>' for t in role["task_ids"])
            cards.append(f'''
      <article class="prof" id="role-{E(role["id"])}">
        <header><h3>{E(role["name"])}</h3><p>{E(role["mission"])}</p></header>
        <ul class="ptasks">{tasks}</ul>
        {verdict}
        <ol class="pbars">{"".join(bars)}</ol>
        <a class="pmore" href="{root}roles/#{E(role["id"])}">Read the role brief &#8594;</a>
      </article>''')
        return "".join(cards)

    @staticmethod
    def full_table(r: dict, s: dict) -> str:
        def rate(v):
            return "&ndash;" if v is None else pct(v)

        total = len(s["task_ids"]) * s["runs"]
        rows = []
        for m in s["models"]:
            lat = "&ndash;" if m["latency_s"] is None else f'{m["latency_s"]:.1f}s'
            tok = "&ndash;" if m["tokens"] is None else f'{m["tokens"]:,.0f}'
            rows.append(f'<tr><td class="num">{m["rank"]}</td><th scope="row">{E(m["name"])}'
                        f'{not_free_tag(m["name"], r.get("not_free_as_of", {}))}</th>'
                        f'<td class="num"><b>{pct(m["rate"])}</b></td><td class="num">{span(*m["ci"])}%</td>'
                        f'<td>{"tied for first" if m["band"] == 1 else "behind"}</td><td>{E(m["status"])}</td>'
                        f'<td class="num">{m["scored"]}/{total}</td><td class="num">{rate(m["auto"])}</td>'
                        f'<td class="num">{rate(m["hard"])}</td><td class="num">{rate(m["json"])}</td>'
                        f'<td class="num">{lat}</td><td class="num">{tok}</td></tr>')
        notes = "".join(f"<li><b>{E(n)}</b> {E(t)}</li>" for n, t in r["notes"])
        left = "".join(f"<li><b>{E(n)}</b> - {E(reason)}</li>" for n, reason in s["left_out"])
        side = []
        if notes:
            side.append(f"<div><h3>Models with a catch</h3><ul>{notes}</ul></div>")
        if left:
            side.append(f"<div><h3>Left out</h3><ul>{left}</ul></div>")
        return f'''<div class="tbl-wrap" role="region" aria-label="Full results table" tabindex="0">
      <table class="tbl">
        <thead><tr><th scope="col">Rank</th><th scope="col">Model</th><th scope="col">Pass rate</th><th scope="col">95% range</th><th scope="col">Vs leader</th><th scope="col">Status</th><th scope="col">Scored runs</th><th scope="col">Checks passed</th><th scope="col">Hard failures</th><th scope="col">Valid JSON</th><th scope="col">Median time</th><th scope="col">Output tokens</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>
    <div class="notes">{"".join(side)}</div>'''

    def method(self, r: dict, s: dict | None) -> str:
        run_dates = "dates still to be set" if pending(r["run_dates"]) else E(r["run_dates"])
        intro = (f"Every model got the same tasks, the same test setup and the {E(r['reasoning'])} reasoning setting. "
                 f"Test setup: {E(r['harness'])}. Models ran on {run_dates}.")
        prov = [("Model pool", E(r["pool"]) + (f'. {len(s["models"])} ranked, {len(s["left_out"])} left out.' if s else "."))]
        prov.append(("Test setup", E(r["harness"])))
        funnel = ""
        if s:
            meta, agg = s["meta"], s["aggregate"]
            version = f'v{E(str(meta["benchmark_version"]))}'
            ledger_version = self.ledger.get("benchmark_version")
            if ledger_version and str(ledger_version) != str(meta["benchmark_version"]):
                version += f" (the roles page shows task text from v{E(str(ledger_version))})"
            prov += [("Task release", version),
                     ("Scoring policy", f'{E(str(meta["policy_id"]))} v{E(str(meta["policy_version"]))}'),
                     ("Pass rate", "Average of the pass rates for each task, as defined by the scoring rules"),
                     ("Ranges and ranks", 'Paired bootstrap over tasks: randomly draw a new set of tasks, allowing repeats. '
                                          f'Compare models on the same set each time. Repeat {s["draws"]:,} times to estimate 95% ranges. '
                                          'Computed for this page; not part of the '
                                          "scoring policy. Each comparison is assessed on its own, without adjusting for the number of comparisons. "
                                          "More models may therefore be tied than shown."),
                     ("Human quality scores", "Assigned" if s["human_scores_assigned"] else "Not assigned yet")]
            if meta.get("release_lock_fingerprint"):
                prov.append(("Release lock (fingerprint of the fixed benchmark files)", f'<code>{E(meta["release_lock_fingerprint"])}</code>'))
            nm, nt = len(s["models"]), len(s["task_ids"])
            attempted = agg.get("attempted_runs")
            product = f" ({nm} models &times; {nt} tasks &times; {s['runs']})" if attempted == nm * nt * s["runs"] else ""
            steps = [(attempted, f"runs attempted{product}", ""),
                     (agg.get("comparable_strict_json_valid_runs"), "answers in exactly the right format", ""),
                     (agg.get("comparable_format_recovered_runs"), "small format slips, scored on content", ""),
                     (agg.get("comparable_unrecoverable_output_runs"), "unreadable answers, counted as fails", ""),
                     (None if attempted is None or agg.get("comparable_resolved_runs") is None
                      else attempted - agg["comparable_resolved_runs"], "runs left out (no answer, or missing a checked part)", ""),
                     (agg.get("comparable_full_contract_pass_runs"), "runs that passed in full", ' class="hl"')]
            items = "".join(f"<li{cls}><b>{v}</b><span>{label}</span></li>" for v, label, cls in steps if v is not None)
            funnel = f'<ol class="funnel">{items}</ol>' if items else ""
        prov_html = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in prov)
        return f'''
  <section id="method" class="sec">
    <div class="sec-h"><span class="no">{"07" if s else "02"}</span><div><h2>How this was measured</h2>
      <p>{intro}</p></div></div>
    <div class="method{"" if funnel else " method-1"}">
      {funnel}
      <dl class="prov">{prov_html}</dl>
    </div>
  </section>'''

    def pending_sections(self, r: dict, root: str) -> str:
        return f'''
  <section id="pending" class="sec">
    <div class="sec-h"><span class="no">01</span><div><h2>Results pending</h2>
      <p>The runs for this report have not been published yet. Standings, per-task results and every number will appear here when they are.</p></div></div>
    <div class="pending-note">
      <p>The tasks are fixed, so you can read what each model will face now.</p>
      <p><a href="{root}roles/">Read the {len(self.roles)} role briefs and {len(self.tasks)} tasks &#8594;</a></p>
    </div>
  </section>
{self.method(r, None)}'''

    # ---------- 404 ----------

    def not_found(self) -> str:
        home = self.base_url or "./"
        roles = (self.base_url or "") + "roles/"
        body = f'''<header class="mast">
  <div class="wrap">
    <div class="strip">
      <a class="brand" href="{E(home)}">{BRAND}</a>
      <nav aria-label="Site"><a href="{E(home)}#reports">Reports</a><a href="{E(roles)}">The roles</a></nav>
    </div>
    <div class="headline">
      <p class="kicker">404 &middot; Page not found</p>
      <h1>Nothing <em>here.</em></h1>
      <p class="deck">The page you asked for does not exist. It may have moved when a report was published.</p>
    </div>
  </div>
</header>
<main id="main" class="wrap">
  <section class="sec">
    <div class="sec-h"><span class="no">&rarr;</span><div><h2>Try one of these</h2>
      <p><a href="{E(home)}">All reports</a> &middot; <a href="{E(roles)}">The roles and their tasks</a></p></div></div>
  </section>
</main>
<footer class="foot"><div class="wrap">{LICENCE_NOTICE}</div></footer>'''
        return self.shell(title=f"Page not found &middot; {BRAND}", description=f"This {BRAND} page does not exist.",
                          path=None, root=home, body=body, skip="Skip to links")


# ---------- output checks ----------

class _Refs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.refs: list[tuple[str, str, str]] = []
        self.styles: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k: "" if v is None else v for k, v in attrs}
        for key in ("id", "name"):
            if a.get(key) and (key == "id" or tag == "a"):
                self.ids.add(a[key])
        for key in ("href", "src", "srcset", "poster", "data", "action", "xlink:href"):
            if a.get(key) is not None:
                kind = "canonical" if tag == "link" and a.get("rel") == "canonical" else tag
                self.refs.append((kind, key, a[key]))
        if a.get("style"):
            self.styles.append(a["style"])
        self._in_style = tag == "style"

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.styles.append(data)


_REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//", re.I)
_CSS_REMOTE = re.compile(r"(?:url\(\s*['\"]?|@import\s+['\"]?)((?:[a-z][a-z0-9+.-]*:)?//[^'\")\s]+)", re.I)


def check_output(out: Path, base_url: str | None) -> list[str]:
    """Return every unresolved link, missing anchor or remote asset under out.

    Hyperlinks (<a href>) may point off-site; anything a browser would fetch to render
    the page (src, <link href>, CSS url() or @import) must be local or a data: URI.
    """
    pages = sorted(out.rglob("*.html"))
    parsed = {}
    for p in pages:
        parser = _Refs()
        parser.feed(p.read_text(encoding="utf-8"))
        parsed[p.resolve()] = parser
    problems = []
    for p in pages:
        rel = p.relative_to(out).as_posix()
        for css in parsed[p.resolve()].styles:
            for url in _CSS_REMOTE.findall(css):
                problems.append(f"{rel}: remote asset in CSS {url}")
        for tag, key, value in parsed[p.resolve()].refs:
            if value.startswith(("data:", "mailto:")):
                continue
            target = value
            if base_url and value.startswith(base_url):
                target = "/" + value[len(base_url):]
                local = out
            elif urlsplit(value).scheme or _REMOTE.match(value):
                if tag not in ("a", "canonical"):
                    problems.append(f"{rel}: remote asset {value}")
                continue
            else:
                local = p.parent
            parts = urlsplit(target)
            path = unquote(parts.path)
            if path.startswith("/"):
                dest = out / path.lstrip("/")
            elif path:
                dest = local / path
            else:
                dest = p
            dest = Path(dest)
            if path.endswith("/") or (dest.exists() and dest.is_dir()):
                dest = dest / "index.html"
            dest = dest.resolve()
            if out.resolve() not in dest.parents:
                problems.append(f"{rel}: link leaves the site: {value}")
                continue
            if not dest.is_file():
                problems.append(f"{rel}: broken link {value}")
                continue
            if parts.fragment:
                ids = parsed.get(dest)
                if ids is None or unquote(parts.fragment) not in ids.ids:
                    problems.append(f"{rel}: missing anchor {value}")
    return problems


def leak_check(out: Path) -> list[str]:
    hits = [f"{p.relative_to(out).as_posix()}: contains the leak-check allow marker"
            for p in sorted(out.rglob("*")) if p.is_file() and ALLOW_MARKER in p.read_text(encoding="utf-8")]
    if not LEAK_CHECK.is_file():
        raise BuildError(f"public-content check not found: {LEAK_CHECK}")
    result = subprocess.run([sys.executable, "-S", str(LEAK_CHECK), str(out)], capture_output=True, text=True)
    if result.returncode != 0:
        lines = result.stdout.strip().splitlines() or [result.stderr.strip() or "public-content check failed"]
        hits += [line.replace(str(out) + "/", "") for line in lines]
    return hits


# ---------- build ----------

def render(ledger: dict, reports: list[dict], base_url: str | None) -> dict[str, str]:
    site = Site(ledger, reports, (HERE / "assets" / "site.css").read_text(encoding="utf-8"), base_url)
    files = {"index.html": site.home(), "roles/index.html": site.roles_page(), "404.html": site.not_found()}
    for r in reports:
        files[f"reports/{r['slug']}/index.html"] = site.report_page(r)
    return files


def prepare_out(out: Path) -> None:
    if out.exists():
        if not out.is_dir():
            raise BuildError(f"--out exists and is not a directory: {out}")
        extra = sorted(p.name for p in out.iterdir() if p.name not in PAGE_NAMES)
        if extra:
            raise BuildError(f"--out {out} holds files this build did not make ({extra[:5]}); refusing to replace it")


def build(out: Path, ledger_path: Path, reports_dir: Path, overrides: dict[str, Path],
          base_url: str | None, results_dir: Path = REPO / "results") -> dict[str, str]:
    if base_url is not None:
        if not re.match(r"https://[^/\s]+(/[^\s]*)?$", base_url):
            raise BuildError("--base-url must be an https URL")
        base_url = base_url.rstrip("/") + "/"
    prepare_out(out)
    ledger = load_ledger(ledger_path)
    reports = load_reports(reports_dir)
    attach_results(reports, ledger, overrides, results_dir)
    try:
        files = render(ledger, reports, base_url)
    except charts.LayoutError as exc:
        raise BuildError(str(exc)) from None
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".site-build-", dir=out.parent))
    stage.chmod(0o755)  # GitHub Pages needs world-readable folders; mkdtemp makes 0700
    try:
        for rel in sorted(files):
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(files[rel])
        problems = check_output(stage, base_url)
        if problems:
            raise BuildError("link check failed:\n  " + "\n  ".join(problems))
        hits = leak_check(stage)
        if hits:
            raise BuildError("public-content check failed:\n  " + "\n  ".join(hits))
        if out.exists():
            shutil.rmtree(out)
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return files


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Build the StaffBench static site.")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ledger", type=Path, default=REPO / "data" / "task-ledger.json")
    ap.add_argument("--reports-dir", type=Path, default=REPO / "reports")
    ap.add_argument("--results-override", action="append", default=[], metavar="SLUG=PATH")
    ap.add_argument("--base-url", default=None, help="public https URL of the site root, for canonical links")
    args = ap.parse_args(argv)
    try:
        files = build(args.out.expanduser(), args.ledger.expanduser(), args.reports_dir.expanduser(),
                      parse_overrides(args.results_override), args.base_url)
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(files)} pages to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
