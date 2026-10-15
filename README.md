# StaffBench

An open benchmark for personal AI agents.

[Read the reports](https://calvinnwq.github.io/staffbench/).

A personal agent is not one job.
StaffBench tests models on nine roles a personal agent plays, from chief of staff to travel guide, with two fixed tasks per role.
Every task uses invented data, runs offline, and is checked automatically.
Each report compares one pool of models on the same tasks.

## The nine roles

- **Chief of Staff** (`cos`): Your everyday helper. Turns messy requests into a clear plan, keeps every requirement intact, and hands work to the right specialist.
- **Personal CFO** (`fin`): Models money decisions with stated assumptions, honest risk and no false certainty. Analysis, never advice or action.
- **Principal Engineer** (`eng`): Writes and reviews code to a senior standard: correct, secure, limited to the job and honestly checked.
- **Travel & Adventure Guide** (`trv`): Plans trips and expeditions end to end: gear, routes, transfers, permits, weather, and when to turn back.
- **Performance Coach** (`fit`): Builds training that fits real life and adapts it when sleep, soreness or schedule changes. Coaching, not diagnosis.
- **Research Lead** (`res`): Answers questions from sources you can check, with every claim traced and the uncertainty stated.
- **Systems Guardian** (`ops`): Watches the automations and services you rely on, tells stale from broken, and fixes things in a safe order.
- **Product Strategist** (`prd`): Turns rough ideas into a focused, measurable product and launch, without adding unplanned work.
- **Design Director** (`des`): Reviews and describes how a product should look and work: what stands out, how it responds, accessibility and clarity.

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
[`docs/task-ledger.md`](docs/task-ledger.md) lists all 18 tasks; [`data/task-ledger.json`](data/task-ledger.json) holds their authoritative definitions and rules.

## How tasks are scored

Each task gives the model fixed instructions and fixed invented data, with no tools, no memory and no web, and asks for one JSON object (structured text with named fields).
The answer is scored in order:

1. **Hard failures** first: made-up evidence, unauthorised action, unsafe advice, ignored hard limits and other mistakes that disqualify an answer for that task. One hard failure fails the task.
2. **Automatic checks**: repeatable checks of arithmetic, required fields, the order of dependent steps, hidden tests and how many planted defects the model finds. A check that cannot run is `blocked`, never a pass.
3. **Human review** on a 0-4 scale with a description for each score, which can never rescue a hard failure.

The leaderboard ranks first by full contract pass rate: the share of answers meeting every scoring rule.
It then uses automatic-check pass rate, human quality, hard-failure rate, invalid-output rate (answers in the wrong format) and median response time (the middle time when sorted), in that order.
See [`docs/evaluation-contract.md`](docs/evaluation-contract.md) for the full rules.

## Check the benchmark

Everything below uses only the Python 3.11+ standard library and makes no model calls.

```bash
python3 -S -m unittest discover -s tests -v
python3 -S scripts/validate_benchmark.py        # ledger contract
python3 -S scripts/validate_benchmark_ready.py  # every task package and both controls
python3 -S scripts/check_public_content.py      # no private or credential-shaped text
```

## Run a set of tests with your own Hermes home

Models run through [Hermes Agent](https://hermes-agent.nousresearch.com/docs) with tools, memory, saved conversations and automatic switching to another model turned off.
The test runner reads provider credentials from one Hermes home (settings folder) that you choose; nothing about that folder is written to run records.

```bash
export STAFFBENCH_HERMES_HOME=/path/to/your/hermes-home

# 1. Save a model list in the format defined by schemas/model-roster.schema.json.
# 2. Run every eligible model on all 18 tasks, three times each, spaced to meet usage limits:
python3 scripts/run_matrix_paced.py \
  --roster .model-evidence/my-roster.json \
  --snapshot-id my-run \
  --output-prefix .model-evidence/my-run \
  --replicates r1 r2 r3

# 3. Build the leaderboard from one run's list of inputs:
python3 scripts/build_leaderboard.py \
  --input .model-evidence/my-run-r1/leaderboard-input.json \
  --output .model-evidence/my-run/leaderboard.json
```

`scripts/run_leaderboard_matrix.py` runs the same model-task combinations without spacing them out.
Evidence under `.model-evidence/` is excluded from Git and never committed.

## Reports

A report compares one pool of models on the fixed tasks.
Its configuration lives in `reports/<slug>/report.json` and its results in `results/<slug>/leaderboard.json`.
Publish results with:

```bash
python3 scripts/publish_results.py .model-evidence/my-run/leaderboard.json --slug 01-my-pool
```

The published copy drops run IDs, record paths, references to original model answers and the list of inputs, and must pass the public-content check.
[`docs/contracts.md`](docs/contracts.md) defines the shared names and file formats.

## Licence

`data/`, `fixtures/`, `oracles/`, `results/` and `reports/` (tasks, test data, answer keys and results) are licensed under [CC BY 4.0](LICENSE-DATA).
Everything else, including scripts, the site, tests, schemas, workflows and documentation, is licensed under [MIT](LICENSE).

Example attribution for reused tasks or results: "[StaffBench by calvinnwq](https://github.com/calvinnwq/staffbench), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)". If you make changes, say what you changed.
