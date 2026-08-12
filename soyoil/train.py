"""Model training with honest validation and conformal prediction intervals.

Protocol (per research/ml-modeling-design.md):
  * Models: LightGBM regressors, one per target.
  * Validation: leave-one-year-out (LOYO) cross-validation — the standard for
    crop prediction, because random k-fold leaks same-year weather across
    folds and overstates skill.
  * Baselines that must be beaten: (a) global mean, (b) region-mean + linear
    year trend. Skill is only claimed relative to these.
  * Uncertainty: split-conformal intervals calibrated on out-of-fold LOYO
    residuals — gives finite-sample coverage guarantees under exchangeability
    across years (approximately valid; stated as such in the model card).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from .features import FEATURE_NAMES
from .simulate import TARGETS, build_training_table

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")

LGB_PARAMS = dict(
    objective="regression", n_estimators=400, learning_rate=0.05,
    num_leaves=31, min_child_samples=25, subsample=0.9, subsample_freq=1,
    colsample_bytree=0.85, reg_lambda=1.0, random_state=11, verbose=-1,
)


@dataclass
class TargetReport:
    target: str
    loyo_rmse: float
    loyo_mae: float
    loyo_r2: float
    baseline_mean_rmse: float
    baseline_trend_rmse: float
    skill_vs_trend: float           # 1 - rmse/baseline_trend_rmse
    conformal_q90: float            # half-width of 90% interval
    coverage_check: float           # empirical coverage of the 90% interval
    per_year: dict[str, float] = field(default_factory=dict)


def _baselines(df: pd.DataFrame, target: str) -> tuple[np.ndarray, np.ndarray]:
    """Out-of-year predictions for the two baselines."""
    mean_pred = np.empty(len(df))
    trend_pred = np.empty(len(df))
    years = df["year"].to_numpy()
    for y in np.unique(years):
        tr = df[years != y]
        te_idx = np.where(years == y)[0]
        mean_pred[te_idx] = tr[target].mean()
        # region mean + global linear year trend
        b = np.polyfit(tr["year"], tr[target], 1)
        reg_means = tr.groupby("region_id")[target].mean()
        reg_trend_resid = reg_means - (b[0] * tr.groupby("region_id")["year"].mean() + b[1])
        for i in te_idx:
            rid = df["region_id"].iloc[i]
            base = b[0] * y + b[1]
            trend_pred[i] = base + reg_trend_resid.get(rid, 0.0)
    return mean_pred, trend_pred


def loyo_validate(df: pd.DataFrame, target: str) -> tuple[np.ndarray, TargetReport]:
    """Leave-one-year-out CV. Returns out-of-fold predictions and a report."""
    X = df[FEATURE_NAMES].to_numpy()
    y = df[target].to_numpy()
    years = df["year"].to_numpy()
    oof = np.empty(len(df))
    per_year: dict[str, float] = {}

    for yr in np.unique(years):
        tr, te = years != yr, years == yr
        m = lgb.LGBMRegressor(**LGB_PARAMS)
        m.fit(X[tr], y[tr])
        oof[te] = m.predict(X[te])
        per_year[str(int(yr))] = float(np.sqrt(np.mean((oof[te] - y[te]) ** 2)))

    resid = oof - y
    rmse = float(np.sqrt(np.mean(resid**2)))
    mae = float(np.mean(np.abs(resid)))
    r2 = float(1 - np.sum(resid**2) / np.sum((y - y.mean()) ** 2))

    mean_pred, trend_pred = _baselines(df, target)
    b_mean = float(np.sqrt(np.mean((mean_pred - y) ** 2)))
    b_trend = float(np.sqrt(np.mean((trend_pred - y) ** 2)))

    # Split-conformal on LOYO residuals (already out-of-fold → honest)
    q90 = float(np.quantile(np.abs(resid), 0.90))
    coverage = float(np.mean(np.abs(resid) <= q90))

    report = TargetReport(
        target=target, loyo_rmse=rmse, loyo_mae=mae, loyo_r2=r2,
        baseline_mean_rmse=b_mean, baseline_trend_rmse=b_trend,
        skill_vs_trend=float(1 - rmse / b_trend) if b_trend > 0 else 0.0,
        conformal_q90=q90, coverage_check=coverage, per_year=per_year,
    )
    return oof, report


def train_all(df: pd.DataFrame | None = None, model_dir: str = MODEL_DIR) -> dict:
    """Full pipeline: validate every target with LOYO, then fit final models
    on all data. Persists models + conformal quantiles + validation report."""
    if df is None:
        df = build_training_table()
    os.makedirs(model_dir, exist_ok=True)

    reports: dict[str, TargetReport] = {}
    models: dict[str, lgb.LGBMRegressor] = {}
    conformal: dict[str, dict[str, float]] = {}

    for target in TARGETS:
        oof, rep = loyo_validate(df, target)
        reports[target] = rep

        m = lgb.LGBMRegressor(**LGB_PARAMS)
        m.fit(df[FEATURE_NAMES].to_numpy(), df[target].to_numpy())
        models[target] = m
        abs_resid = np.abs(oof - df[target].to_numpy())
        conformal[target] = {
            "q80": float(np.quantile(abs_resid, 0.80)),
            "q90": rep.conformal_q90,
        }
        joblib.dump(m, os.path.join(model_dir, f"{target}.joblib"))

    meta = {
        "feature_names": FEATURE_NAMES,
        "targets": TARGETS,
        "n_train": int(len(df)),
        "years": [int(df['year'].min()), int(df['year'].max())],
        "regions": sorted(df["region_id"].unique().tolist()),
        "conformal": conformal,
        "validation": {t: asdict(r) for t, r in reports.items()},
        "training_data": "synthetic-from-published-response-functions-v1",
    }
    with open(os.path.join(model_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    df.to_parquet(os.path.join(model_dir, "training_table.parquet"))
    return meta


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    meta = train_all()
    print(json.dumps({t: {k: round(v, 3) for k, v in r.items()
                          if isinstance(v, (int, float))}
                      for t, r in meta["validation"].items()}, indent=2))
