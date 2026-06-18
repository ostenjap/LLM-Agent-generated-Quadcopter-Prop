"""
plot_results.py  —  V1 results figure from research.db
=======================================================
Reads the feasible evaluations of one run and draws the three-objective
trade-offs (Figure of Merit, thrust, noise) with the Pareto front highlighted,
plus a convergence panel. Saves docs/v1_results.png.

    cd src
    python plot_results.py            # uses run 5 (richest thrust-objective run)
    python plot_results.py --run 6
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def pareto_mask(rows):
    """Non-dominated set over (fm, thrust, noise), all maximized."""
    keep = []
    for i, a in enumerate(rows):
        dominated = False
        for j, b in enumerate(rows):
            if i == j:
                continue
            if (b["fm"] >= a["fm"] and b["thrust_n"] >= a["thrust_n"]
                    and b["noise_db"] >= a["noise_db"]
                    and (b["fm"] > a["fm"] or b["thrust_n"] > a["thrust_n"]
                         or b["noise_db"] > a["noise_db"])):
                dominated = True
                break
        keep.append(not dominated)
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=5)
    args = ap.parse_args()

    db = sqlite3.connect("file:../data/research.db?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT d.generation g, e.fm, e.thrust_n, e.noise_db "
        "FROM evals e JOIN designs d ON d.design_id=e.design_id "
        "WHERE d.run_id=? AND e.constraints_ok=1 AND e.fm IS NOT NULL",
        (args.run,)).fetchall()
    db.close()
    if not rows:
        raise SystemExit(f"No feasible data for run {args.run}")

    fm = [r["fm"] for r in rows]
    th = [r["thrust_n"] for r in rows]
    nz = [r["noise_db"] for r in rows]
    pf = pareto_mask(rows)
    pf_idx = [i for i, k in enumerate(pf) if k]

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    fig.suptitle(f"Propeller AutoResearch — V1 results (run {args.run}, "
                 f"{len(rows)} feasible designs, {len(pf_idx)} on Pareto front)",
                 fontsize=13, fontweight="bold")

    def trade(ax, x, y, c, xl, yl, cl):
        ax.scatter(x, y, c=c, cmap="viridis", s=18, alpha=0.35, edgecolor="none")
        ax.scatter([x[i] for i in pf_idx], [y[i] for i in pf_idx],
                   facecolor="none", edgecolor="crimson", s=70, linewidth=1.4,
                   label="Pareto front")
        ax.set_xlabel(xl); ax.set_ylabel(yl)
        cb = fig.colorbar(ax.collections[0], ax=ax); cb.set_label(cl)
        ax.legend(loc="best", fontsize=8)

    trade(axes[0, 0], fm, th, nz, "Figure of Merit", "Thrust (N)", "noise (dB)")
    trade(axes[0, 1], fm, nz, th, "Figure of Merit", "Noise reduction (dB)", "thrust (N)")
    trade(axes[1, 0], th, nz, fm, "Thrust (N)", "Noise reduction (dB)", "Figure of Merit")

    # convergence: best per generation
    gens = sorted({r["g"] for r in rows})
    best_fm = [max(r["fm"] for r in rows if r["g"] <= gg) for gg in gens]
    best_th = [max(r["thrust_n"] for r in rows if r["g"] <= gg) for gg in gens]
    ax = axes[1, 1]
    l1 = ax.plot(gens, best_fm, "-o", color="#1f77b4", ms=3, label="best FM")
    ax.set_xlabel("generation"); ax.set_ylabel("best Figure of Merit", color="#1f77b4")
    ax2 = ax.twinx()
    l2 = ax2.plot(gens, best_th, "-s", color="#2ca02c", ms=3, label="best thrust")
    ax2.set_ylabel("best thrust (N)", color="#2ca02c")
    ax.set_title("Convergence (best-so-far)")
    ax.legend(l1 + l2, [ln.get_label() for ln in l1 + l2], loc="lower right", fontsize=8)

    out = pathlib.Path("../docs/v1_results.png")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(out, dpi=120)
    print("wrote", out.resolve())


if __name__ == "__main__":
    main()
