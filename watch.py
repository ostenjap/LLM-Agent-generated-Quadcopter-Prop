"""
watch.py  —  dumb live dashboard for the propeller run
======================================================
No dependencies (stdlib only). Reads data/research.db read-only and serves an
auto-refreshing web page so you can watch the optimization from your browser.

    python watch.py            # then open http://127.0.0.1:8765

Works while the loop is running (SQLite WAL + read-only connection, so it never
blocks or corrupts the writer). The page refreshes itself every 4 seconds.
"""

from __future__ import annotations

import datetime
import html
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "research.db"
HOST, PORT = "127.0.0.1", 8765
REFRESH_S = 4


START_OVERRIDE = None


def q(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def _elapsed(started: str) -> str:
    global START_OVERRIDE
    try:
        t0 = START_OVERRIDE if START_OVERRIDE is not None else datetime.datetime.fromisoformat(started)
        secs = int((datetime.datetime.now() - t0).total_seconds())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
    except Exception:
        return "?"


def _format_ts(ts_str: str | None) -> str:
    if not ts_str:
        return "—"
    try:
        t = ts_str.replace("T", " ")
        return t.split(".")[0]
    except Exception:
        return ts_str


def _sparkline(points, color, w=320, h=48):
    """Tiny inline SVG line for a per-generation series."""
    vals = [p for p in points if p is not None]
    if len(vals) < 2:
        return '<span class="dim">(need 2+ generations)</span>'
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(points)
    pts = []
    for i, v in enumerate(points):
        if v is None:
            continue
        x = w * i / (n - 1)
        y = h - (h - 6) * (v - lo) / span - 3
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline fill="none" stroke="{color}" stroke-width="2" '
            f'points="{" ".join(pts)}"/></svg>')


def render() -> str:
    if not DB.exists():
        return ("<h1>No run yet</h1><p>data/research.db does not exist. "
                "Start a run, then refresh.</p>")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # prefer a live run, else the most recent one
    run = q(conn, "SELECT * FROM runs "
                  "ORDER BY (status='running') DESC, run_id DESC LIMIT 1")
    if not run:
        return "<h1>No runs recorded yet.</h1>"
    run = run[0]
    rid = run["run_id"]
    status = run["status"]

    kpi = q(conn,
            "SELECT MAX(e.fm) fm, MAX(e.thrust_n) th, MAX(e.noise_db) nz, "
            "COUNT(*) n, SUM(e.constraints_ok) feas "
            "FROM evals e JOIN designs d ON d.design_id=e.design_id "
            "WHERE d.run_id=?", (rid,))[0]

    series = q(conn,
               "SELECT d.generation g, MAX(e.fm) fm, MAX(e.thrust_n) th "
               "FROM designs d JOIN evals e ON e.design_id=d.design_id "
               "WHERE d.run_id=? GROUP BY d.generation ORDER BY d.generation", (rid,))
    fm_series = [r["fm"] for r in series]
    th_series = [r["th"] for r in series]

    top = q(conn,
            "SELECT e.fm, e.thrust_n, e.noise_db, d.n_blades, d.chord_root_m, "
            "d.chord_tip_m, d.twist_root_deg, d.twist_tip_deg, d.tubercle_amp_m, "
            "d.tubercle_wl_m FROM evals e JOIN designs d ON d.design_id=e.design_id "
            "WHERE d.run_id=? AND e.constraints_ok=1 ORDER BY e.fm DESC LIMIT 12", (rid,))

    events = q(conn, "SELECT ts, level, role, message FROM events "
                     "WHERE run_id=? ORDER BY event_id DESC LIMIT 12", (rid,))
    
    last_data_res = q(conn,
                      "SELECT MAX(ts) AS last_ts FROM ("
                      "  SELECT MAX(e.evaluated_at) AS ts FROM evals e JOIN designs d ON d.design_id=e.design_id WHERE d.run_id=? "
                      "  UNION "
                      "  SELECT MAX(created_at) AS ts FROM designs WHERE run_id=? "
                      "  UNION "
                      "  SELECT MAX(ts) AS ts FROM events WHERE run_id=? "
                      ")", (rid, rid, rid))
    last_data_ts = last_data_res[0]["last_ts"] if last_data_res else None

    conn.close()

    dot = {"running": "#3fb950", "done": "#58a6ff",
           "crashed": "#f85149", "stopped": "#d29922"}.get(status, "#8b949e")

    def cell(x, f="{:.3f}"):
        return f.format(x) if x is not None else "—"

    rows = "".join(
        f"<tr><td>{cell(r['fm'])}</td><td>{cell(r['thrust_n'],'{:.1f}')}</td>"
        f"<td>{cell(r['noise_db'],'{:.2f}')}</td><td>{r['n_blades']}</td>"
        f"<td>{cell(r['chord_root_m'])}</td><td>{cell(r['chord_tip_m'])}</td>"
        f"<td>{cell(r['twist_root_deg'],'{:.0f}')}/{cell(r['twist_tip_deg'],'{:.0f}')}</td>"
        f"<td>{cell(r['tubercle_amp_m'],'{:.4f}')}</td>"
        f"<td>{cell(r['tubercle_wl_m'],'{:.3f}')}</td></tr>"
        for r in top) or '<tr><td colspan="9" class="dim">no feasible designs yet</td></tr>'

    evs = "".join(
        f'<li><span class="dim">{html.escape(e["ts"][11:19])}</span> '
        f'<b>{html.escape(e["role"] or "")}</b> '
        f'{html.escape((e["message"] or "")[:160])}</li>'
        for e in events) or '<li class="dim">no events yet</li>'

    return f"""
    <div class="head">
      <h1>Propeller AutoResearch <span class="dim">· run {rid}</span></h1>
      <div class="status"><span class="dot" style="background:{dot}"></span>{status}
        &nbsp;·&nbsp; gen <b>{run['last_gen']}</b>
        &nbsp;·&nbsp; phase {run['phase']}
        &nbsp;·&nbsp; elapsed {_elapsed(run['started_at'])} <a href="/restart" class="btn">Restart</a>
        &nbsp;·&nbsp; last time updated <b>{_format_ts(last_data_ts)}</b></div>
    </div>

    <div class="cards">
      <div class="card"><div class="lbl">Best Figure of Merit</div><div class="big">{cell(kpi['fm'])}</div></div>
      <div class="card"><div class="lbl">Best Thrust</div><div class="big">{cell(kpi['th'],'{:.1f}')} <small>N</small></div></div>
      <div class="card"><div class="lbl">Best Noise reduction</div><div class="big">{cell(kpi['nz'],'{:.2f}')} <small>dB</small></div></div>
      <div class="card"><div class="lbl">Evaluated</div><div class="big">{kpi['n'] or 0} <small>({kpi['feas'] or 0} feasible)</small></div></div>
    </div>

    <div class="grid">
      <div class="panel">
        <h2>Best Figure of Merit / generation</h2>{_sparkline(fm_series, "#58a6ff")}
        <h2>Best Thrust / generation</h2>{_sparkline(th_series, "#3fb950")}
      </div>
      <div class="panel">
        <h2>Recent activity</h2><ul class="events">{evs}</ul>
      </div>
    </div>

    <div class="panel">
      <h2>Top designs by Figure of Merit</h2>
      <table>
        <tr><th>FM</th><th>thrust N</th><th>noise dB</th><th>B</th><th>c_root</th>
            <th>c_tip</th><th>twist r/t</th><th>tub amp</th><th>tub wl</th></tr>
        {rows}
      </table>
    </div>
    """


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<title>Propeller run</title><style>
  body{{background:#0d1117;color:#c9d1d9;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}}
  h1{{font-size:20px;margin:0}} h2{{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#8b949e;margin:18px 0 8px}}
  .dim{{color:#8b949e}} small{{color:#8b949e;font-size:60%}}
  .head{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;border-bottom:1px solid #21262d;padding-bottom:14px}}
  .btn{{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:3px 8px;font-size:11px;border-radius:6px;cursor:pointer;transition:all .2s ease;text-decoration:none;margin-left:6px;display:inline-block}}
  .btn:hover{{background:#30363d;border-color:#8b949e}}
  .btn:active{{background:#282e38}}
  .status{{font-size:14px}} .dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0}}
  .card{{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px}}
  .lbl{{color:#8b949e;font-size:12px}} .big{{font-size:26px;font-weight:600;margin-top:4px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  @media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}
  .panel{{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:16px;margin-bottom:16px}}
  table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
  th,td{{text-align:right;padding:5px 8px;border-bottom:1px solid #21262d;font-size:13px}}
  th{{color:#8b949e;font-weight:500}}
  .events{{list-style:none;margin:0;padding:0}} .events li{{padding:4px 0;border-bottom:1px solid #21262d;font-size:13px}}
</style></head><body>{body}
<p class="dim" style="margin-top:20px">auto-refreshing every {refresh}s · source: data/research.db</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global START_OVERRIDE
        if self.path == "/restart":
            START_OVERRIDE = datetime.datetime.now()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return
        try:
            body = render()
        except Exception as e:
            body = f"<h1>read error</h1><pre>{html.escape(str(e))}</pre>"
        out = PAGE.format(refresh=REFRESH_S, body=body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    print(f"Propeller watcher: open http://{HOST}:{PORT}  (Ctrl+C to stop)")
    HTTPServer((HOST, PORT), Handler).serve_forever()
