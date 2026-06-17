"""
store.py  —  SQLite store of record
====================================
The single source of truth for a run: every design, every score, the run's
progress, and a debug event trail.  One file, ``data/research.db``.

Why SQLite (not JSON files): it is atomic/ACID, so a process killed mid-write
can never corrupt a row.  That is what makes the unattended overnight runner
safe to leave alone — each generation is committed in one transaction, so on a
crash we resume cleanly from the last completed generation instead of starting
over.  It runs in WAL mode, so you can open the DB and inspect progress while
the loop is still running.

Schema mirrors implementation_plan.md:
    runs · designs · evals · events · pareto_snapshots

Large artifacts (STEP/STL, CFD cases) are NOT stored here — only their paths.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,            -- running | done | crashed | stopped
    phase       INTEGER NOT NULL DEFAULT 1,
    last_gen    INTEGER NOT NULL DEFAULT 0,
    config_json TEXT
);
CREATE TABLE IF NOT EXISTS designs (
    design_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES runs(run_id),
    generation     INTEGER NOT NULL,
    source         TEXT,
    chord_root_m   REAL, chord_tip_m   REAL,
    twist_root_deg REAL, twist_tip_deg REAL,
    tubercle_amp_m REAL, tubercle_wl_m REAL,
    n_blades       INTEGER,
    created_at     TEXT NOT NULL,
    UNIQUE(run_id, chord_root_m, chord_tip_m, twist_root_deg,
           twist_tip_deg, tubercle_amp_m, tubercle_wl_m, n_blades)
);
CREATE TABLE IF NOT EXISTS evals (
    eval_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id      INTEGER NOT NULL REFERENCES designs(design_id),
    evaluator      TEXT NOT NULL,
    fm             REAL,
    thrust_n       REAL,
    noise_db       REAL,
    tip_mach       REAL,
    von_mises_mpa  REAL,
    watertight     INTEGER,
    constraints_ok INTEGER NOT NULL,
    artifact_path  TEXT,
    evaluated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER REFERENCES runs(run_id),
    ts         TEXT NOT NULL,
    level      TEXT NOT NULL,
    role       TEXT,
    message    TEXT,
    payload    TEXT
);
CREATE TABLE IF NOT EXISTS pareto_snapshots (
    run_id     INTEGER NOT NULL REFERENCES runs(run_id),
    generation INTEGER NOT NULL,
    design_id  INTEGER NOT NULL REFERENCES designs(design_id),
    PRIMARY KEY (run_id, generation, design_id)
);
"""

_SEARCH_VARS = ("chord_root_m", "chord_tip_m", "twist_root_deg", "twist_tip_deg",
                "tubercle_amp_m", "tubercle_wl_m", "n_blades")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class Store:
    """Thin wrapper over research.db.  All writes are committed immediately."""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL;")
        self.db.execute("PRAGMA foreign_keys=ON;")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.run_id: Optional[int] = None

    # -- run lifecycle -----------------------------------------------------
    def start_run(self, config: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (started_at, status, phase, last_gen, config_json) "
            "VALUES (?, 'running', ?, 0, ?)",
            (_now(), int(config.get("phase", 1)), json.dumps(config)),
        )
        self.db.commit()
        self.run_id = int(cur.lastrowid)
        return self.run_id

    def resume_latest(self) -> Optional[int]:
        """Adopt the most recent unfinished run, if any. Returns its run_id."""
        row = self.db.execute(
            "SELECT run_id, last_gen FROM runs "
            "WHERE status IN ('running','crashed') ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        self.run_id = int(row["run_id"])
        self.db.execute("UPDATE runs SET status='running' WHERE run_id=?",
                        (self.run_id,))
        self.db.commit()
        return self.run_id

    def last_gen(self) -> int:
        if self.run_id is None:
            return 0
        row = self.db.execute("SELECT last_gen FROM runs WHERE run_id=?",
                              (self.run_id,)).fetchone()
        return int(row["last_gen"]) if row else 0

    def finish_run(self, status: str = "done"):
        if self.run_id is None:
            return
        self.db.execute("UPDATE runs SET status=?, finished_at=? WHERE run_id=?",
                        (status, _now(), self.run_id))
        self.db.commit()

    # -- per-generation write (one transaction = crash-safe) ---------------
    def record_generation(self, generation: int, results: List[dict],
                          front: List[dict], phase: int = 1):
        """Persist a generation's evaluated designs + evals, snapshot the Pareto
        front, and advance last_gen — all atomically."""
        if self.run_id is None:
            raise RuntimeError("call start_run()/resume_latest() first")
        ts = _now()
        front_keys = {self._design_key(r["design"]) for r in front}
        try:
            self.db.execute("BEGIN")
            front_design_ids = []
            for r in results:
                d, o, m = r["design"], r["objectives"], r.get("metrics", {})
                design_id = self._upsert_design(generation, d,
                                                r.get("source", "ga"), ts)
                self.db.execute(
                    "INSERT INTO evals (design_id, evaluator, fm, thrust_n, "
                    "noise_db, tip_mach, von_mises_mpa, watertight, "
                    "constraints_ok, artifact_path, evaluated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (design_id, r.get("evaluator", "analytical"),
                     o.get("figure_of_merit"), o.get("thrust_N"),
                     o.get("noise_reduction_dB"), m.get("tip_mach"),
                     m.get("von_mises_MPa"), m.get("watertight"),
                     1 if r.get("feasible") else 0, r.get("artifact_path"), ts),
                )
                if self._design_key(d) in front_keys:
                    front_design_ids.append(design_id)
            # snapshot the current front
            for design_id in front_design_ids:
                self.db.execute(
                    "INSERT OR IGNORE INTO pareto_snapshots "
                    "(run_id, generation, design_id) VALUES (?,?,?)",
                    (self.run_id, generation, design_id))
            self.db.execute(
                "UPDATE runs SET last_gen=?, phase=? WHERE run_id=?",
                (generation, phase, self.run_id))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _design_key(self, d: dict):
        return tuple(round(float(d[v]), 6) if v != "n_blades" else int(d[v])
                     for v in _SEARCH_VARS)

    def _upsert_design(self, generation: int, d: dict, source: str, ts: str) -> int:
        vals = [d[v] for v in _SEARCH_VARS]
        self.db.execute(
            "INSERT OR IGNORE INTO designs (run_id, generation, source, "
            "chord_root_m, chord_tip_m, twist_root_deg, twist_tip_deg, "
            "tubercle_amp_m, tubercle_wl_m, n_blades, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, generation, source, *vals, ts))
        row = self.db.execute(
            "SELECT design_id FROM designs WHERE run_id=? AND chord_root_m=? AND "
            "chord_tip_m=? AND twist_root_deg=? AND twist_tip_deg=? AND "
            "tubercle_amp_m=? AND tubercle_wl_m=? AND n_blades=?",
            (self.run_id, *vals)).fetchone()
        return int(row["design_id"])

    # -- debug / diagnostics ----------------------------------------------
    def log_event(self, level: str, message: str, role: str = None,
                  payload: str = None):
        self.db.execute(
            "INSERT INTO events (run_id, ts, level, role, message, payload) "
            "VALUES (?,?,?,?,?,?)",
            (self.run_id, _now(), level, role, message, payload))
        self.db.commit()

    def load_results(self, run_id: int) -> List[dict]:
        """Rebuild evaluate()-shaped dicts for a run, to reseed a resumed loop."""
        rows = self.db.execute(
            "SELECT d.*, e.fm, e.thrust_n, e.noise_db, e.constraints_ok "
            "FROM designs d JOIN evals e ON e.design_id = d.design_id "
            "WHERE d.run_id=?", (run_id,)).fetchall()
        out = []
        for r in rows:
            out.append({
                "objectives": {
                    "figure_of_merit": r["fm"],
                    "thrust_N": r["thrust_n"],
                    "noise_reduction_dB": r["noise_db"],
                },
                "feasible": bool(r["constraints_ok"]),
                "design": {v: r[v] for v in _SEARCH_VARS},
            })
        return out

    def close(self):
        try:
            self.db.commit()
            self.db.close()
        except Exception:
            pass
