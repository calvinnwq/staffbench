# Evaluation contract

StaffBench compares models under matched conditions.
The model is the independent variable.

Task wording, fixture bytes, allowed tools, output format, evaluator version, reasoning effort, timeout and verification opportunity stay constant within a comparison.
Every task gives the model the same empty tool surface: no tools, no memory, no web.

## How a task is scored

### 1. Hard-failure gate

Hard failures are checked first.
A hard failure means the result cannot count as a successful outcome, however well it scores elsewhere.

Examples: a fabricated source, unsafe health guidance, an unauthorised action, a forbidden change, a failed hidden test, an impossible recommendation, or a claim that verification happened when it did not.

Hard-failure identifiers are task-specific and live in `data/task-ledger.json`.

### 2. Automatic checks

Automatic checks are deterministic checks over structured output, a repository tree or a fixed artifact.
Each check produces `pass`, `fail` or `blocked` with an evidence reference.
A missing, malformed or unsupported check result is `blocked`, never a pass.

Examples: arithmetic reconciliation, required-field coverage, exact state labels, source-identifier integrity, dependency-graph validity, hidden tests, changed-file scope, defect recall and defect precision.
Checks test observable behaviour and boundaries, not similarity to one reference answer.

### 3. Anchored human review

Human dimensions use a 0-4 scale with shared anchors:

| Score | Meaning |
|---:|---|
| 0 | Misses the requirement or introduces a material error. |
| 1 | Shows limited progress but remains materially incomplete or unsafe. |
| 2 | Partly correct but incomplete, weakly justified, or unevenly constrained. |
| 3 | Strong and mostly complete with minor omissions or weaknesses. |
| 4 | Fully satisfies the contract with accurate evidence, clear assumptions, and no material omission. |

Each task defines its own weighted dimensions and task-specific 0, 2 and 4 anchors.
Reviewers score anonymised outputs where practical and never reward matching one reference solution when another answer meets the contract better.
Human quality cannot rescue a hard failure.

## Task status

- `contract-draft`: the task shape is defined, but prompt, fixture and oracle are not frozen.
- `fixture-ready`: prompt and fixture are frozen, but evaluator validation is incomplete.
- `benchmark-ready`: prompt, fixture, evaluator oracle and both controls have passed validation.

For benchmark version `0.5.0`, all 18 tasks are `benchmark-ready`.
`python3 -S scripts/validate_benchmark.py` validates the ledger contract.
`python3 -S scripts/validate_benchmark_ready.py` validates artifact bindings and both controls for all 18 tasks: every known-good control must pass and every known-bad control must trigger every declared hard failure.
The sealed release-artifact lock (`data/release-artifact-lock.json`) pins every package file and shared runtime script, so changing an artifact and repinning its manifest cannot quietly redefine a task.

## Task packages

Every task package under `fixtures/<task-id>/` has:

- a stable fixture identifier and version
- exact input bytes
- an exact prompt
- an empty allowed-tool list
- an evaluator-held oracle under `oracles/`
- a candidate output schema under `schemas/`
- a known-good and a known-bad control
- a `public_synthetic` privacy class and `synthetic` provenance
- a fingerprint recorded with every run

All fixtures are purpose-built and synthetic: the people, places, systems, prices and records in them are invented.

One evaluator, `scripts/evaluate_task.py` (version `task-oracle-v3`), scores every task from its oracle.
For example, the COS-01 planning oracle declares checks for required fields, hard-constraint coverage, owner membership, an acyclic dependency graph and labelled ambiguities, plus a hard rule that external work stays unstarted and pending approval.

## Replaying controls

Controls replay without any model call:

```bash
python3 -S scripts/replay_task.py \
  --task COS-01 \
  --fixture fixtures/cos-01/request-packet.json \
  --prompt fixtures/cos-01/prompt.txt \
  --candidate fixtures/cos-01/controls/known-good.json \
  --condition known-good-control \
  --run-id cos-01-control-good-local \
  --model-requested control-known-good \
  --model-resolved control-known-good \
  --output .model-evidence/cos-01/control-good.run.json
```

Replay the known-bad control with `--condition known-bad-control`; its failed run record is kept as visible evidence.
A control result is evaluator evidence, not a model score.

## Model harness

`scripts/run_task_model.py` runs one model on one task with the frozen prompt and fixture.
By default it runs `scripts/hermes_no_tools.py`, a narrow Hermes Agent adapter that:

- reads provider credentials from one Hermes home you supply with `--hermes-home` or `STAFFBENCH_HERMES_HOME`;
- ignores that home's configuration and rules;
- disables tools, memory, the session database and fallback routing;
- pins the requested reasoning effort.

There is no per-role agent or per-role home: every task runs through the same adapter.
The Hermes home path is never written to run records or trial metadata; the stored command shows `<hermes-home>`.

The runner keeps the exact composed input, stdout, stderr, usage report, process status, model and provider resolution, trial metadata and pre-run Git state under `.model-evidence/<run-id>/`.
A custom `--agent-command` is allowed for test doubles, but its isolation is unverified, so the cell is recorded as `blocked` (`unverified-isolation`) and never scored.
Malformed output, non-zero exits and timeouts stay visible as failed evidence.
Missing or contradictory model or provider resolution stays visible as blocked evidence.
Model-authored Python in ENG-01 is never executed; the evaluator uses static checks.
The runner verifies the sealed release lock before launching a model process.

```bash
export STAFFBENCH_HERMES_HOME=/path/to/your/hermes-home
python3 scripts/run_task_model.py \
  --task COS-02 \
  --fixture fixtures/cos-02/input.json \
  --prompt fixtures/cos-02/prompt.txt \
  --output-root .model-evidence/single \
  --run-id cos-02-model-001 \
  --model-requested <model-id> \
  --provider <provider-id>
```

## Running a matrix

A roster snapshot conforms to `schemas/model-roster.schema.json`.
Each model keeps its requested ID, resolved ID, requested and resolved provider, availability and any exclusion reason.
An excluded model stays visible but never enters a ranking.

`scripts/run_leaderboard_matrix.py` validates the roster and task manifests, runs `validate_benchmark_ready.py` before the first model call, plans every eligible model against all 18 tasks, and runs the cells one at a time with the same reasoning and timeout.
It writes a new evidence root and refuses a non-empty one.

```bash
python3 scripts/run_leaderboard_matrix.py \
  --roster .model-evidence/<roster>/roster.json \
  --output-root .model-evidence/<matrix> \
  --snapshot-id <matrix-id> \
  --hermes-home /path/to/your/hermes-home \
  --reasoning medium \
  --timeout-seconds 600
```

For rate-limited providers, `scripts/run_matrix_paced.py` runs the same cells with the same per-cell arguments and adds scheduling only:

- a fixed gap between launches (`--gap-seconds`);
- an optional probe (`--probe-command`) run before each launch; on HTTP 429 it waits for the probe's `retry_after` plus a margin, and on a low fair-share budget it waits for top-up;
- bounded recovery reruns (`--max-retries`) of failed or unresolved cells, with new run IDs under `<prefix>-<replicate>-recovery`; the original failed cells are always kept;
- a deadline (`--deadline-hours`) after which it stops cleanly and marks the run incomplete.

```bash
python3 scripts/run_matrix_paced.py \
  --roster .model-evidence/<roster>/roster.json \
  --snapshot-id <matrix-id> \
  --output-prefix .model-evidence/<matrix> \
  --replicates r1 r2 r3 \
  --hermes-home /path/to/your/hermes-home \
  --probe-command "python3 /path/to/your/probe.py"
```

A probe takes the model ID as its last argument and prints one JSON object on its last line: `{"status": 200 | 429 | null, "retry_after": seconds, "headers": {...}}`.

Each root gets a `leaderboard-input.json` (`schemas/leaderboard-input.schema.json`) that lists only run records that were written and identity-checked.
A launch failure is recorded and never becomes a fabricated run record.

## Building and publishing a leaderboard

```bash
python3 scripts/build_leaderboard.py \
  --input .model-evidence/<matrix>-r1/leaderboard-input.json \
  --output .model-evidence/<matrix>/leaderboard.json
```

The output conforms to `schemas/leaderboard-output.schema.json`.
It has overall and per-role views (`roles`, keyed by role ID, with `roles_covered` and `roles_total`), explicit attempted, comparable and excluded counts, coverage, objective metrics, status and run references.

- Overall ranking needs at least one comparable run for every task.
- A complete but unrepeated model is `provisional`; three comparable replicates for every task make it `confirmed`.
- Incomplete coverage keeps a model `unranked`; blocked and unverified-isolation evidence never counts toward coverage.
- The primary metric is the full contract pass rate, then automatic-check pass rate, normalised human quality, hard-failure rate, invalid-output rate and median latency.

For a fixed policy, ledger, roster and run set, the output is byte-deterministic.

`scripts/publish_results.py` copies a built leaderboard to `results/<slug>/leaderboard.json` for a report.
The published copy drops every `run_id`, `record_path` and `raw_output_reference` and the `input` block, then passes the public-content check; on any hit nothing is published.

```bash
python3 scripts/publish_results.py .model-evidence/<matrix>/leaderboard.json --slug 01-example-pool
```

Raw run evidence is never committed.

## Output scoring

`data/leaderboard-policy.json` is the `leaderboard-v2` policy (version `2.0.0`).
It defines a benchmark-specific comparison, not a universal intelligence ranking.

| Raw response (`tolerant-json-v1` classification) | Execution | Treatment | `scoring_basis` |
|---|---|---|---|
| `strict-json-object` | completed | frozen evaluation, unchanged | `strict-json` |
| `markdown-fenced-json`, `surrounded-json` | completed | recovered object re-evaluated with the frozen evaluator | `format-recovered` |
| any other classification | completed | failed run with `invalid-output`; every blocked check becomes a fail | `unrecoverable-output` |
| any other classification | failed or timed out | excluded as infrastructure evidence | `excluded-infrastructure` |
| any | blocked, unverified isolation, or unresolved provider | excluded | `excluded-infrastructure` |
| any | unresolved or excluded identity | excluded | `excluded-provider-or-identity` |

A recovered object is only ever evaluated; it is never repaired, merged or chosen from several candidates.
If the evaluator still reports a blocked check for a recovered object, the run is excluded as `excluded-evaluator-blocked`.
`strict_json_valid_rate`, the share of comparable runs that returned one bare JSON object, is reported as format compliance and does not enter the ranking key.

`scripts/analyze_tolerant_outputs.py` and `scripts/compare_tolerant_analyses.py` produce separately labelled diagnostics from preserved raw responses without new model calls; they never change run records or leaderboards.

## Run records

Every run record (`schemas/task-run-record.schema.json`) keeps:

```text
run_id, benchmark_id, benchmark_version, release_lock_fingerprint, ledger_fingerprint,
task_id, role_id, harness, condition, evaluator_version,
model_requested, model_resolved, provider_requested, provider_resolved, resolution_status,
task_manifest_fingerprint, oracle_fingerprint, output_schema_fingerprint, evaluator_fingerprint,
run_record_schema_fingerprint, harness_fingerprint, prompt_fingerprint, fixture_fingerprint,
input_fingerprint, input_composition_version, output_parse_status,
started_at, completed_at, status, execution_status, failure_class,
raw_output_reference, raw_output_fingerprint, automatic_checks, hard_failures, human_scores,
latency_ms, usage, notes
```

A scoreable cell carries the full resolved model and provider IDs from the usage report.
If either cannot be resolved, the record uses `unresolved` and a `blocked` status rather than copying the request.
Retries use new run IDs, so failed attempts stay immutable and visible.

## Reproducibility rules

- Use the same fixture and evaluator versions for every condition.
- Keep live browsing disabled for scored tasks.
- Record run order and pacing.
- Treat latency and usage as secondary to correctness, scope, safety and evidence quality.
- Do not present one average as a universal measure of intelligence.
- A task-suite change needs a new benchmark version and a new, non-comparable output lineage.
- To add a model later, capture a new roster snapshot, run the unchanged suite, and build a new leaderboard; never edit an earlier snapshot.
