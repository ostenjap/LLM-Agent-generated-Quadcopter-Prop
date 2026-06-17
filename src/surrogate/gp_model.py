"""
gp_model.py  —  Gaussian-Process surrogate (Phase 2)
=====================================================
A cheap stand-in for the expensive evaluator. Train it on designs already scored
by the trusted physics/CFD, then let it *predict* objectives for new designs so
the loop can skip slow evaluations and spend them only where the model is unsure
(Bayesian infill: Expected Improvement / UCB).

This is a working scaffold: it trains and predicts today using scikit-learn. The
infill-criterion wiring into researcher.py is the remaining Phase-2 task.

Requires scikit-learn:  pip install scikit-learn
"""

from __future__ import annotations

from typing import List, Sequence

import numpy as np

SEARCH_VARS = ("chord_root_m", "chord_tip_m", "twist_root_deg", "twist_tip_deg",
               "tubercle_amp_m", "tubercle_wl_m", "n_blades")
OBJECTIVE_KEYS = ("figure_of_merit", "thrust_N", "noise_reduction_dB")


class GPSurrogate:
    """One GaussianProcessRegressor per objective, with normalized inputs."""

    def __init__(self):
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import Matern, ConstantKernel
        self._make = lambda: GaussianProcessRegressor(
            kernel=ConstantKernel(1.0) * Matern(length_scale=1.0, nu=2.5),
            normalize_y=True, n_restarts_optimizer=2, alpha=1e-6)
        self.models = {}
        self._mu = None
        self._sd = None

    @staticmethod
    def _vec(design: dict) -> List[float]:
        return [float(design[v]) for v in SEARCH_VARS]

    def fit(self, results: Sequence[dict]) -> "GPSurrogate":
        X = np.array([self._vec(r["design"]) for r in results], dtype=float)
        self._mu, self._sd = X.mean(0), X.std(0) + 1e-9
        Xn = (X - self._mu) / self._sd
        for key in OBJECTIVE_KEYS:
            y = np.array([r["objectives"][key] for r in results], dtype=float)
            m = self._make()
            m.fit(Xn, y)
            self.models[key] = m
        return self

    def predict(self, design: dict):
        """Return {objective: (mean, std)} for one design."""
        xn = (np.array(self._vec(design)) - self._mu) / self._sd
        out = {}
        for key, m in self.models.items():
            mean, std = m.predict(xn.reshape(1, -1), return_std=True)
            out[key] = (float(mean[0]), float(std[0]))
        return out

    def cross_val_rmse(self, results: Sequence[dict]) -> dict:
        """Leave-one-out RMSE per objective — the Phase-2 verification metric."""
        from sklearn.model_selection import LeaveOneOut
        X = np.array([self._vec(r["design"]) for r in results], dtype=float)
        mu, sd = X.mean(0), X.std(0) + 1e-9
        Xn = (X - mu) / sd
        rmse = {}
        for key in OBJECTIVE_KEYS:
            y = np.array([r["objectives"][key] for r in results], dtype=float)
            errs = []
            for tr, te in LeaveOneOut().split(Xn):
                m = self._make().fit(Xn[tr], y[tr])
                errs.append((m.predict(Xn[te])[0] - y[te][0]) ** 2)
            rmse[key] = float(np.sqrt(np.mean(errs)))
        return rmse


if __name__ == "__main__":
    print("GPSurrogate scaffold. Import and call .fit(results) with scored designs "
          "from data/research.db, then .predict(design) or .cross_val_rmse(results).")
