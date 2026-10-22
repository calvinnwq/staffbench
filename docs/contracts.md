# Shared contracts

This file fixes the identifiers, data shapes and file ownership that the benchmark engine and the site build share.
Change it only in a pull request that updates both sides.

## Roles and task IDs

| Role id | Code | Public name | Tasks |
|---|---|---|---|
| `cos` | COS | Chief of Staff | COS-01, COS-02 |
| `fin` | FIN | Personal CFO | FIN-01, FIN-02 |
| `eng` | ENG | Principal Engineer | ENG-01, ENG-02 |
| `trv` | TRV | Travel & Adventure Guide | TRV-01, TRV-02 |
| `fit` | FIT | Performance Coach | FIT-01, FIT-02 |
| `res` | RES | Research Lead | RES-01, RES-02 |
| `ops` | OPS | Systems Guardian | OPS-01, OPS-02 |
| `prd` | PRD | Product Strategist | PRD-01, PRD-02 |
| `des` | DES | Design Director | DES-01, DES-02 |

Role order on every page follows the table.
Fixture, oracle and schema files use the lowercase task ID, for example `fixtures/cos-01/` and `schemas/cos-01-output.schema.json`.

## Task ledger: `data/task-ledger.json`

- Top-level keys: `benchmark_id` (`staffbench`), `benchmark_version`, `evaluation_policy`, `roles`, `tasks`.
- Each role: `id`, `code`, `name`, `tag` (short label), `mission`, `task_ids`, `primary_dimensions`.
- Each task: `id`, `role_id`, `title`, `prompt_contract`, `measurement`, `known_failure_modes`, plus the existing status and fixture fields.
- No field names a person, a private system, or the history a task was modelled on.

## Leaderboard JSON

The leaderboard builder keeps the existing schema with role naming:

- `profiles` becomes `roles`, `profile_id` becomes `role_id`, `profiles_covered` and `profiles_total` become `roles_covered` and `roles_total`.
- Run records keep `run_id` and `record_path`, but a published copy under `results/` drops them and the input manifest.
- Every other field name is unchanged.
- `scripts/combine_leaderboard_inputs.py` keeps single-snapshot inputs unchanged by default. Opting in with `--combined-snapshot-id <new-id>` writes a union roster and manifest under that new ID. A sibling `<output-stem>.sources.json` records source snapshot IDs and input paths; provenance stays outside the frozen input and roster schemas. Conflicting model identities, benchmark identities, duplicate run IDs and duplicate record paths are refused.

## Reports

A report compares one pool of models on the fixed tasks.

- Config: `reports/<slug>/report.json` with `slug`, `number`, `title`, `label`, `published`, `pool`, `harness`, `run_dates`, `reasoning`, `models` (model ID to display name), `catches` (display name to short tag) and `notes` (list of `[display name, sentence]`).
- `run_dates` may describe several run dates on the unchanged benchmark; `published` remains the original report publication date.
- Optional `not_free_as_of`: model display name to the date of a benchmark run or free-list check that found the model no longer on the Nous Portal free list, in the same style as `published` (for example `"21 Oct 2026"`). This is an observation date, not a removal date. Every name must be in `models` and every value must be a real calendar date; invalid entries fail the site build. Omit it when no models are marked.
- Results: `results/<slug>/leaderboard.json`, the published copy described above.
- Raw run evidence is never committed; it lives in a private store outside the repository.

## Public-content check

`scripts/check_public_content.py [paths...]` scans the given paths, or every tracked file when none are given.
It always applies built-in rules: absolute home or volume paths, email addresses other than GitHub noreply addresses, and credential-shaped strings.
When the environment variable `STAFFBENCH_PRIVATE_TERMS` names a file, it also applies that file's patterns: one Python regular expression per line, `#` starts a comment, and `(?i)` makes a line case-insensitive.
It prints `path:line: matched-term` for every hit and exits non-zero when there is any hit.
The private patterns file is never committed.

## Ownership while the first two pull requests are open

- Engine pull request: `data/`, `docs/` (except this file), `fixtures/`, `oracles/`, `schemas/`, `scripts/`, `tests/`, `.github/workflows/benchmark.yml`, `README.md`.
- Site pull request: `site/` (including its tests), `reports/`, `.github/workflows/site.yml`.
- Merge order: engine first, then site rebased on it.
