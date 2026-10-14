# StaffBench

An open benchmark for personal AI agents.

A personal agent is not one job.
StaffBench tests models on nine roles a personal agent plays, from chief of staff to travel guide, with two fixed tasks per role.
Every task uses invented data, runs offline, and is checked automatically.
Each report compares one pool of models on the same tasks.

This repository is under construction and not yet public.

## The nine roles

- **Chief of Staff** (`cos`): Your daily driver. Turns messy requests into a clear plan, keeps every requirement intact, and hands work to the right specialist.
- **Personal CFO** (`fin`): Models money decisions with stated assumptions, honest risk and no false certainty. Analysis, never advice or action.
- **Principal Engineer** (`eng`): Ships and reviews code to a senior standard: correct, secure, tightly scoped and honestly verified.
- **Travel & Adventure Guide** (`trv`): Plans trips and expeditions end to end: gear, routes, transfers, permits, weather, and when to turn back.
- **Performance Coach** (`fit`): Builds training that fits real life and adapts it when sleep, soreness or schedule changes. Coaching, not diagnosis.
- **Research Lead** (`res`): Answers questions from sources you can check, with every claim traced and the uncertainty stated.
- **Systems Guardian** (`ops`): Watches the automations and services you rely on, tells stale from broken, and fixes things in a safe order.
- **Product Strategist** (`prd`): Turns rough ideas into a focused, measurable product and launch, without scope creep.
- **Design Director** (`des`): Critiques and specifies the experiences you ship: hierarchy, states, accessibility and clarity.

| Role | Task 1 | Task 2 |
|---|---|---|
| Chief of Staff | Turn a messy request into a dependency-aware plan | Reconcile three conflicting agent reports into one handoff |
| Personal CFO | Review a portfolio against fixed goals and limits | Compare two investment strategies under fixed scenarios |
| Principal Engineer | Fix an authentication bug in a small repository | Security-review an extension install manifest |
| Travel & Adventure Guide | Pick a backpacking cook kit from a fixed catalogue | Build a multi-day route and logistics plan |
| Performance Coach | Write a training plan from a baseline and goal date | Adapt a training plan after a weekly check-in |
| Research Lead | Extract trip rules from a bundle of official sources | Choose a local AI model build that fits a 24 GB memory budget |
| Systems Guardian | Classify a data-collector incident from logs and health checks | Diagnose source freshness and workflow drift in a scheduled report run |
| Product Strategist | Turn a product brief into a measurable MVP | Set launch and landing-page direction from a positioning brief |
| Design Director | Critique an HTML report with seeded design defects | Specify interactions for a dashboard's loading, empty, stale and error states |

Task IDs are the role code plus a number, for example `COS-01`.
[`docs/task-ledger.md`](docs/task-ledger.md) indexes all 18 tasks; [`data/task-ledger.json`](data/task-ledger.json) is the canonical contract.

## How tasks are scored

Each task gives the model a fixed prompt and a fixed synthetic input, with no tools, no memory and no web, and asks for one JSON object.
The answer is scored in order:

1. **Hard failures** first: fabricated evidence, unauthorised action, unsafe advice, dropped hard constraints and other task-specific disqualifiers. One hard failure fails the task.
2. **Automatic checks**: deterministic checks such as arithmetic, required fields, dependency graphs, hidden tests and defect recall. A check that cannot run is `blocked`, never a pass.
3. **Human review** on a 0-4 anchored scale, which can never rescue a hard failure.

The leaderboard ranks by full contract pass rate, then automatic-check pass rate, then human quality, hard-failure rate, invalid-output rate and median latency.
See [`docs/evaluation-contract.md`](docs/evaluation-contract.md) for the full rules.

## Check the benchmark

Everything below uses only the Python 3.11+ standard library and makes no model calls.

```bash
python3 -S -m unittest discover -s tests -v
python3 -S scripts/validate_benchmark.py        # ledger contract
python3 -S scripts/validate_benchmark_ready.py  # every task package and both controls
python3 -S scripts/check_public_content.py      # no private or credential-shaped text
```

## Run a matrix with your own Hermes home

Models run through [Hermes Agent](https://hermes-agent.nousresearch.com/docs) with tools, memory, session storage and fallback routing disabled.
The harness reads provider credentials from one Hermes home that you choose; nothing about that home is written to run records.

```bash
export STAFFBENCH_HERMES_HOME=/path/to/your/hermes-home

# 1. Write a roster snapshot that conforms to schemas/model-roster.schema.json.
# 2. Run every eligible model on all 18 tasks, three replicates, paced for rate limits:
python3 scripts/run_matrix_paced.py \
  --roster .model-evidence/my-roster.json \
  --snapshot-id my-run \
  --output-prefix .model-evidence/my-run \
  --replicates r1 r2 r3

# 3. Build the leaderboard from a replicate's input manifest:
python3 scripts/build_leaderboard.py \
  --input .model-evidence/my-run-r1/leaderboard-input.json \
  --output .model-evidence/my-run/leaderboard.json
```

`scripts/run_leaderboard_matrix.py` runs the same cells without pacing.
Evidence under `.model-evidence/` is git-ignored and never committed.

## Reports

A report compares one pool of models on the fixed tasks.
Its configuration lives in `reports/<slug>/report.json` and its results in `results/<slug>/leaderboard.json`.
Publish results with:

```bash
python3 scripts/publish_results.py .model-evidence/my-run/leaderboard.json --slug 01-my-pool
```

The published copy drops run IDs, record paths, raw output references and the input manifest, and must pass the public-content check.
[`docs/contracts.md`](docs/contracts.md) defines the shared names and file formats.
