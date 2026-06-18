import sqlite3
import os

db_path = os.path.join("data", "research.db")

def init_db():
    conn = sqlite3.connect(db_path)
    # Enable WAL mode for live concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    
    cursor = conn.cursor()
    
    # Create runs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS runs (
      run_id      INTEGER PRIMARY KEY,
      started_at  TEXT NOT NULL,
      finished_at TEXT,
      status      TEXT NOT NULL,            -- running | done | crashed | stopped
      phase       INTEGER NOT NULL,         -- 0..4, last phase entered
      last_gen    INTEGER DEFAULT 0,        -- last completed generation (resume point)
      config_json TEXT                      -- budget, bounds, model routing snapshot
    );
    """)
    
    # Create designs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS designs (
      design_id      INTEGER PRIMARY KEY,
      run_id         INTEGER NOT NULL REFERENCES runs(run_id),
      generation     INTEGER NOT NULL,
      source         TEXT,                  -- proposer | mutator | coder | surrogate | baseline
      chord_root_m   REAL, chord_tip_m   REAL,
      twist_root_deg REAL, twist_tip_deg REAL,
      tubercle_amp_m REAL, tubercle_wl_m REAL,
      n_blades       INTEGER,
      created_at     TEXT NOT NULL,
      UNIQUE(run_id, chord_root_m, chord_tip_m, twist_root_deg,
             twist_tip_deg, tubercle_amp_m, tubercle_wl_m, n_blades)  -- dedup
    );
    """)
    
    # Create evals table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS evals (
      eval_id        INTEGER PRIMARY KEY,
      design_id      INTEGER NOT NULL REFERENCES designs(design_id),
      evaluator      TEXT NOT NULL,         -- analytical | surrogate | cfd
      fm             REAL,                  -- Figure of Merit (objective)
      thrust_n       REAL,                  -- thrust, N (objective)
      noise_db       REAL,                  -- noise reduction vs baseline, dB (objective)
      tip_mach       REAL,
      von_mises_mpa  REAL,
      watertight     INTEGER,               -- 0/1
      constraints_ok INTEGER NOT NULL,      -- 0/1, all constraints satisfied
      artifact_path  TEXT,                  -- path to STEP/CFD case if any
      evaluated_at   TEXT NOT NULL
    );
    """)
    
    # Create events table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS events (
      event_id   INTEGER PRIMARY KEY,
      run_id     INTEGER REFERENCES runs(run_id),
      ts         TEXT NOT NULL,
      level      TEXT NOT NULL,             -- info | warn | error
      role       TEXT,                      -- proposer | mutator | cfd_analyst | researcher | ...
      message    TEXT,
      payload    TEXT                       -- raw LLM output / log tail / stack trace
    );
    """)
    
    # Create pareto_snapshots table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pareto_snapshots (
      run_id     INTEGER NOT NULL REFERENCES runs(run_id),
      generation INTEGER NOT NULL,
      design_id  INTEGER NOT NULL REFERENCES designs(design_id),
      PRIMARY KEY (run_id, generation, design_id)
    );
    """)
    
    conn.commit()
    conn.close()
    print("Database initialized successfully at", db_path)

if __name__ == "__main__":
    init_db()
