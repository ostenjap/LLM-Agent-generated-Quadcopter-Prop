import sqlite3
import os
import json
import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "research.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def start_run(phase: int, config: dict) -> int:
    conn = get_db_connection()
    try:
        now = datetime.datetime.now().isoformat()
        config_json = json.dumps(config)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO runs (started_at, status, phase, last_gen, config_json)
            VALUES (?, 'running', ?, 0, ?)
        """, (now, phase, config_json))
        run_id = cursor.lastrowid
        conn.commit()
        return run_id
    finally:
        conn.close()

def get_latest_run():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT run_id, status, phase, last_gen, config_json FROM runs ORDER BY run_id DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            return {
                "run_id": row[0],
                "status": row[1],
                "phase": row[2],
                "last_gen": row[3],
                "config_json": json.loads(row[4]) if row[4] else {}
            }
        return None
    finally:
        conn.close()

def update_run_status(run_id: int, status: str, finished: bool = False, phase: int = None):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        now = datetime.datetime.now().isoformat()
        if finished:
            cursor.execute("UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?", (status, now, run_id))
        elif phase is not None:
            cursor.execute("UPDATE runs SET status = ?, phase = ? WHERE run_id = ?", (status, phase, run_id))
        else:
            cursor.execute("UPDATE runs SET status = ? WHERE run_id = ?", (status, run_id))
        conn.commit()
    finally:
        conn.close()

def log_event(run_id: int | None, level: str, role: str | None, message: str, payload: str | None = None):
    conn = get_db_connection()
    try:
        now = datetime.datetime.now().isoformat()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO events (run_id, ts, level, role, message, payload)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (run_id, now, level, role, message, payload))
        conn.commit()
    finally:
        conn.close()

def save_generation(run_id: int, gen: int, results: list, evaluator: str):
    conn = get_db_connection()
    try:
        conn.execute("BEGIN TRANSACTION;")
        cursor = conn.cursor()
        now = datetime.datetime.now().isoformat()
        
        design_ids = []
        for r in results:
            d = r["design"]
            # Dedup design insertion
            cursor.execute("""
                SELECT design_id FROM designs
                WHERE run_id = ? 
                  AND chord_root_m = ? AND chord_tip_m = ?
                  AND twist_root_deg = ? AND twist_tip_deg = ?
                  AND tubercle_amp_m = ? AND tubercle_wl_m = ?
                  AND n_blades = ?
            """, (run_id, d["chord_root_m"], d["chord_tip_m"], 
                  d["twist_root_deg"], d["twist_tip_deg"],
                  d["tubercle_amp_m"], d["tubercle_wl_m"], d["n_blades"]))
            row = cursor.fetchone()
            if row:
                design_id = row[0]
            else:
                cursor.execute("""
                    INSERT INTO designs (
                        run_id, generation, source, 
                        chord_root_m, chord_tip_m, 
                        twist_root_deg, twist_tip_deg, 
                        tubercle_amp_m, tubercle_wl_m, 
                        n_blades, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (run_id, gen, r.get("source", "unknown"),
                      d["chord_root_m"], d["chord_tip_m"], 
                      d["twist_root_deg"], d["twist_tip_deg"],
                      d["tubercle_amp_m"], d["tubercle_wl_m"], d["n_blades"], now))
                design_id = cursor.lastrowid
            
            design_ids.append(design_id)
            
            # Insert eval
            o = r["objectives"]
            m = r.get("metrics", {})
            constraints_ok = 1 if r.get("feasible", False) else 0
            watertight = 1 if m.get("watertight", True) else 0
            
            # Match plan objectives: figure_of_merit, noise_reduction_dB, thrust_N (or blade_mass_g)
            fm = o.get("figure_of_merit")
            noise_db = o.get("noise_reduction_dB")
            # The plan has thrust_N as objective, but optimization/evaluate.py currently uses blade_mass_g
            # We record thrust_N in thrust_n, and if objectives has thrust_n, use it.
            thrust_n = m.get("thrust_N") if "thrust_N" in m else o.get("thrust_N")
            
            cursor.execute("""
                INSERT INTO evals (
                    design_id, evaluator, fm, thrust_n, noise_db, 
                    tip_mach, von_mises_mpa, watertight, 
                    constraints_ok, artifact_path, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (design_id, evaluator, fm, thrust_n, noise_db,
                  m.get("tip_mach"), m.get("von_mises_MPa"), watertight,
                  constraints_ok, r.get("artifact_path"), now))
        
        # Update last_gen in runs
        cursor.execute("UPDATE runs SET last_gen = ? WHERE run_id = ?", (gen, run_id))
        
        # Save pareto front snapshot for this generation
        # Let's import pareto_front to get active pareto front for the run
        from optimization.pareto import pareto_front
        
        # Fetch all evals for this run to compute current pareto front
        cursor.execute("""
            SELECT e.fm, e.noise_db, e.thrust_n, d.design_id, e.constraints_ok
            FROM evals e
            JOIN designs d ON e.design_id = d.design_id
            WHERE d.run_id = ?
        """, (run_id,))
        
        all_evals = []
        for row in cursor.fetchall():
            all_evals.append({
                "feasible": bool(row[4]),
                "objectives": {
                    "figure_of_merit": row[0],
                    "noise_reduction_dB": row[1],
                    "thrust_N": row[2]
                },
                "design_id": row[3]
            })
            
        # Pareto front calculation (simplistic version since we need design_id)
        if all_evals:
            front = pareto_front(all_evals)
            for f in front:
                cursor.execute("""
                    INSERT OR IGNORE INTO pareto_snapshots (run_id, generation, design_id)
                    VALUES (?, ?, ?)
                """, (run_id, gen, f["design_id"]))
                
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def load_all_run_results(run_id: int) -> list:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT d.chord_root_m, d.chord_tip_m, d.twist_root_deg, d.twist_tip_deg,
                   d.tubercle_amp_m, d.tubercle_wl_m, d.n_blades, d.source,
                   e.fm, e.noise_db, e.thrust_n, e.tip_mach, e.von_mises_mpa, e.constraints_ok, e.artifact_path
            FROM evals e
            JOIN designs d ON e.design_id = d.design_id
            WHERE d.run_id = ?
        """, (run_id,))
        results = []
        for r in cursor.fetchall():
            results.append({
                "feasible": bool(r[13]),
                "source": r[7],
                "objectives": {
                    "figure_of_merit": r[8],
                    "noise_reduction_dB": r[9],
                    "thrust_N": r[10]
                },
                "metrics": {
                    "thrust_N": r[10],
                    "tip_mach": r[11],
                    "von_mises_MPa": r[12]
                },
                "design": {
                    "chord_root_m": r[0],
                    "chord_tip_m": r[1],
                    "twist_root_deg": r[2],
                    "twist_tip_deg": r[3],
                    "tubercle_amp_m": r[4],
                    "tubercle_wl_m": r[5],
                    "n_blades": r[6]
                },
                "artifact_path": r[14]
            })
        return results
    finally:
        conn.close()
