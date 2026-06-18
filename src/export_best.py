"""
export_best.py  —  turn a winning design into CAD (STEP + STL)
==============================================================
Pulls a design from data/research.db (default: the best feasible by Figure of
Merit), builds the CadQuery solid via generate_propeller.build_propeller, exports
STEP + STL into ../cad/, and runs the watertightness/manifold check the plan
requires before a design is trusted.

    cd src
    python export_best.py                 # best feasible by FM
    python export_best.py --by thrust_n   # best feasible by thrust
    python export_best.py --run 5         # restrict to one run

Outputs: cad/best_<objective>.step, .stl   (+ a printed validity report)
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

sys.path.insert(0, ".")  # so sibling modules import when run from src/

from optimization.design import Design
from generate_propeller import build_propeller
import cadquery as cq

DB = pathlib.Path("../data/research.db")
CAD = pathlib.Path("../cad")
SEARCH_VARS = ("chord_root_m", "chord_tip_m", "twist_root_deg", "twist_tip_deg",
               "tubercle_amp_m", "tubercle_wl_m", "n_blades")


def pick_design(objective: str, run_id: int | None):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    where = "e.constraints_ok=1"
    args = []
    if run_id is not None:
        where += " AND d.run_id=?"
        args.append(run_id)
    row = conn.execute(
        f"SELECT d.*, e.fm, e.thrust_n, e.noise_db FROM evals e "
        f"JOIN designs d ON d.design_id=e.design_id WHERE {where} "
        f"ORDER BY e.{objective} DESC LIMIT 1", args).fetchone()
    conn.close()
    if not row:
        sys.exit("No feasible design found in research.db for that filter.")
    kw = {v: row[v] for v in SEARCH_VARS}
    kw["n_blades"] = int(kw["n_blades"])
    return Design(**kw), dict(fm=row["fm"], thrust=row["thrust_n"], noise=row["noise_db"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", default="fm",
                    help="objective column to maximize: fm | thrust_n | noise_db")
    ap.add_argument("--run", type=int, default=None, help="restrict to one run_id")
    ap.add_argument("--sections", type=int, default=40, help="loft sections (smoothness)")
    args = ap.parse_args()

    design, perf = pick_design(args.by, args.run)
    print(f"Selected (best by {args.by}): FM={perf['fm']:.3f} "
          f"thrust={perf['thrust']:.1f}N noise={perf['noise']:.2f}dB")
    print(f"  B={design.n_blades} c_root={design.chord_root_m:.3f} "
          f"c_tip={design.chord_tip_m:.3f} tw={design.twist_root_deg:.0f}/"
          f"{design.twist_tip_deg:.0f} amp={design.tubercle_amp_m:.4f} "
          f"wl={design.tubercle_wl_m:.3f}")

    # tubercle sanity (per skill guidance: want ~2-4 gentle bumps)
    span = design.prop_radius_m - design.hub_radius_m
    bumps = span / design.tubercle_wl_m if design.tubercle_wl_m else 0
    print(f"  tubercle bumps across span ~ {bumps:.1f}  (sane range 2-4)")

    print(f"Building solid ({args.sections} sections)...")
    prop = build_propeller(design=design, n_sections=args.sections)

    CAD.mkdir(parents=True, exist_ok=True)
    stem = f"best_{args.by}"
    step_p = CAD / f"{stem}.step"
    stl_p = CAD / f"{stem}.stl"
    cq.exporters.export(prop, str(step_p))
    cq.exporters.export(prop, str(stl_p), exportType="STL",
                        tolerance=0.0005, angularTolerance=0.1)

    # ---- watertight / manifold check ----
    solid = prop.val()
    vol = solid.Volume()
    valid = solid.isValid()
    bb = solid.BoundingBox()
    print("\n--- CAD validity report ---")
    print(f"  exported: {step_p}")
    print(f"            {stl_p}")
    print(f"  isValid (manifold/watertight): {valid}")
    print(f"  volume: {vol:.3e} mm^3  ({'OK >0' if vol > 0 else 'BAD <=0'})")
    print(f"  bounding box (mm): "
          f"x[{bb.xmin:.1f},{bb.xmax:.1f}] "
          f"y[{bb.ymin:.1f},{bb.ymax:.1f}] z[{bb.zmin:.1f},{bb.zmax:.1f}]")
    diam = max(bb.xlen, bb.ylen)
    print(f"  outer diameter ~ {diam:.1f} mm  (target ~ {2000*design.prop_radius_m:.0f} mm)")
    ok = valid and vol > 0
    print(f"\n  RESULT: {'PASS - eyeball the STL for snaking/tubercles next' if ok else 'FAIL - not watertight, do not use'}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
