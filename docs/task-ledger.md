# Task ledger

The canonical task data lives in [`data/task-ledger.json`](../data/task-ledger.json).
This page is the human-readable index of the 18 tasks: two per role, nine roles.
The Primary evaluation column is an editorial summary; the measurement contract in the JSON ledger is authoritative.

All 18 tasks are `benchmark-ready` in benchmark version `0.5.0`.
Every task is a purpose-built synthetic fixture: invented people, places, systems and numbers.
Each task has a frozen prompt, fixture, evaluator oracle, output schema, known-good control and known-bad control under `fixtures/`, `oracles/` and `schemas/`.

| ID | Role | Task | Provenance | Primary evaluation (editorial summary) |
|---|---|---|---|---|
| `COS-01` | Chief of Staff | Turn a messy request into a dependency-aware plan | Synthetic | Hybrid: required fields, constraints, owner set, dependency DAG, uncertainty labels |
| `COS-02` | Chief of Staff | Reconcile three conflicting agent reports into one handoff | Synthetic | Hybrid: fact retention, conflict surfacing, evidence links, action completeness |
| `FIN-01` | Personal CFO | Review a portfolio against fixed goals and limits | Synthetic | Hybrid: arithmetic, reserve and risk limits, assumption provenance |
| `FIN-02` | Personal CFO | Compare two investment strategies under fixed scenarios | Synthetic | Hybrid: scenario formulas, comparison coverage, liquidity, recommendation consistency |
| `ENG-01` | Principal Engineer | Fix an authentication bug in a small repository | Synthetic | Objective: hidden tests, focused tests, changed-file scope, security boundary |
| `ENG-02` | Principal Engineer | Security-review an extension install manifest | Synthetic | Hybrid: defect recall, precision, severity mapping, path evidence |
| `TRV-01` | Travel & Adventure Guide | Pick a backpacking cook kit from a fixed catalogue | Synthetic | Hybrid: catalogue membership, weight, cost, fuel, availability |
| `TRV-02` | Travel & Adventure Guide | Build a multi-day route and logistics plan | Synthetic | Hybrid: timeline, transport, required facts, contingencies |
| `FIT-01` | Performance Coach | Write a training plan from a baseline and goal date | Synthetic | Hybrid: sessions, progression, volume, safety boundary |
| `FIT-02` | Performance Coach | Adapt a training plan after a weekly check-in | Synthetic | Hybrid: availability, load response, recovery hold, goal preservation |
| `RES-01` | Research Lead | Extract trip rules from a bundle of official sources | Synthetic | Hybrid: claim-source mapping, quote fidelity, dates, unsupported claims |
| `RES-02` | Research Lead | Choose a local AI model build that fits a 24 GB memory budget | Synthetic | Hybrid: memory arithmetic, capacity, requirements, estimate labelling |
| `OPS-01` | Systems Guardian | Classify a data-collector incident from logs and health checks | Synthetic | Objective: state, evidence, severity, action order |
| `OPS-02` | Systems Guardian | Diagnose source freshness and workflow drift in a scheduled report run | Synthetic | Objective: source states, stale versus failed, causal chain, read-only boundary |
| `PRD-01` | Product Strategist | Turn a product brief into a measurable MVP | Synthetic | Hybrid: field coverage, priority consistency, metric measurability, contradictions |
| `PRD-02` | Product Strategist | Set launch and landing-page direction from a positioning brief | Synthetic | Hybrid: positioning, claim grounding, call to action, section coverage |
| `DES-01` | Design Director | Critique an HTML report with seeded design defects | Synthetic | Hybrid: defect recall, precision, location integrity, severity order |
| `DES-02` | Design Director | Specify interactions for a dashboard's loading, empty, stale and error states | Synthetic | Hybrid: state coverage, transitions, keyboard behaviour, read-only boundary |

## Shared hard gates

Every task has task-specific hard failures in the JSON ledger.

The shared gate rejects fabricated evidence, unsupported certainty, unauthorised external action, privacy leakage, and claims of verification that did not occur.

Coding tasks also reject failed hidden tests and unrelated repository changes.

Health-related tasks reject diagnostic overreach and progression through fixture-defined red flags.

## Contract review checklist

Before a task is `benchmark-ready`, the release gate verifies that:

- the input replays without live web access
- the output format parses consistently
- at least one automatic check has an executable oracle
- human dimensions have concrete 0, 2 and 4 anchors
- a known-good and a known-bad output are distinguished
- provenance and privacy classification are accurate
- failure and blocked states remain visible

Run `python3 -S scripts/validate_benchmark_ready.py` to apply this checklist to every task package.
