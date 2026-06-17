---
name: run-propeller-optimizer
description: >
  Run the quadcopter-propeller AutoResearch optimization loop (the multi-agent
  search for the quietest, most efficient, highest-thrust propeller). Use when
  the user says "run the optimizer", "run the propeller loop", "start the
  autoresearch", "go to work on the propeller", "resume the run", or "kick off
  the overnight run". Handles fresh runs, crash-resume, the local-LLM swarm, and
  the unattended overnight runner.
---

# Run the Propeller Optimizer

This skill launches the AutoResearch loop in [`src/`](../../src). The loop
proposes propeller designs, scores them with trusted physics (Figure of Merit,
thrust, noise), keeps the Pareto-best, and writes everything to the SQLite store
of record at `data/research.db`. Full design: [`implementation_plan.md`](../../implementation_plan.md).

## TL;DR — just run it

```bash
cd src
python -m autoresearch.researcher --no-llm --budget 60
```

That runs a fast, dependency-light optimization (no Ollama needed) for 60
seconds and prints the best designs. Outputs land in `data/` and `docs/`.

## Pick the mode

| Goal | Command (from `src/`) |
|------|-----------------------|
| **Fast deterministic run** (no AI, just the GA — best first try) | `python -m autoresearch.researcher --no-llm --budget 60` |
| **Full swarm run** (local LLMs propose designs — needs Ollama) | `python -m autoresearch.researcher --budget 1800` |
| **Resume** the last run after a crash/stop | `python -m autoresearch.researcher --resume --budget 600` |
| **Unattended overnight** (auto-restart, caps, logging) | `powershell -ExecutionPolicy Bypass -File ..\run_overnight.ps1` |

`--budget` is wall-clock **seconds**. Increase it for a longer search.

## Before a full swarm run (only `--no-llm` needs nothing)

- **Ollama** running with: `ollama pull qwen2.5-coder:7b` and `ollama pull phi4-mini`.
  Check with `python -m autoresearch.local_llm --check`. If Ollama is down the
  loop automatically falls back to the deterministic GA, so it never hard-fails.
- Python deps: `pip install -r ../requirements.txt`.

## What you get after a run

- `data/research.db` — every design + score (the source of truth; query it with any SQLite tool).
- `data/journal.md` — one human-readable line per generation.
- `docs/pareto.png` — the trade-off plot (FM vs noise, colored by thrust).
- `data/optimization/pareto_front.json` + `all_candidates.csv` — exports.

## Monitor / stop

- **Watch progress live:** open `data/journal.md`, or query `data/research.db`
  (WAL mode lets you read it while the loop runs).
- **Stop an overnight run cleanly:** drop a file named `STOP` in the project root.
- **Morning verdict** of an overnight run: `data/RUN_STATUS.txt`.

## How it resumes (why a crash is safe)

Each generation is committed to `data/research.db` in one transaction, and the
`runs` table tracks `last_gen`. `--resume` adopts the latest unfinished run,
reloads its designs, and continues from the next generation — it does **not**
start over. The overnight runner passes `--resume` automatically on every restart.
