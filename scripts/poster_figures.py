"""Figuras en inglés, con la paleta del póster A1.

Uso:  python -m scripts.poster_figures
Salida: docs/poster/fig/*.png
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.config import DATA_PROCESSED, REPORTS, ROOT, TEST_FRACTION
from src.pipeline.evaluate_decisions import best
from src.pipeline.prepare import feature_columns

OUT = ROOT / "docs" / "poster" / "fig"
OUT.mkdir(parents=True, exist_ok=True)

NAVY, TEAL, AMBER, RED, GREY = "#0B2545", "#1B7F79", "#E0A100", "#C0392B", "#8A94A6"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 13, "axes.spines.top": False,
    "axes.spines.right": False, "axes.edgecolor": "#444", "axes.titleweight": "bold",
    "axes.titlesize": 14, "legend.frameon": False,
})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=250)
    plt.close(fig)


df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
cut = int(len(df) * (1 - TEST_FRACTION))

# 1. TRM y volatilidad, con el periodo de prueba sombreado
reg = pd.read_csv(DATA_PROCESSED / "regimes.csv", parse_dates=["date"])
fig, ax = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
ax[0].plot(df.date, df.trm, lw=1.3, color=NAVY)
ax[0].set_ylabel("TRM (COP per USD)")
ax[1].plot(df.date, df.trm_vol_20 * np.sqrt(252) * 100, lw=1, color=TEAL)
hv = reg[reg.regime == 1].merge(df[["date", "trm_vol_20"]], on="date")
ax[1].scatter(hv.date, hv.trm_vol_20 * np.sqrt(252) * 100, s=6, color=RED, zorder=3, label="High-volatility regime")
ax[1].set_ylabel("Ann. vol. 20d (%)")
ax[1].legend(loc="upper right", fontsize=11)
for a in ax:
    a.axvspan(df.date.iloc[cut], df.date.iloc[-1], color=AMBER, alpha=0.15, lw=0)
ax[0].text(df.date.iloc[cut], ax[0].get_ylim()[1], "  test period", va="top", fontsize=11, color="#7a5a00")
save(fig, "trm_series.png")

# 2. ROC-AUC de prueba por modelo (dirección y riesgo)
clf = pd.DataFrame({
    "model": ["Logistic", "KNN", "SVM", "AdaBoost", "Random Forest", "Gradient Boosting"],
    "direction": [0.497, 0.474, 0.490, 0.541, 0.555, 0.566],
    "risk": [0.549, 0.571, 0.562, 0.557, 0.585, 0.586],
})
y = np.arange(len(clf))
fig, ax = plt.subplots(figsize=(8, 5))
ax.barh(y + 0.2, clf.direction, 0.38, color=NAVY, label="Direction (up / down)")
ax.barh(y - 0.2, clf.risk, 0.38, color=TEAL, label="Risk (large move)")
ax.axvline(0.5, color=RED, ls="--", lw=1.5)
ax.text(0.501, len(clf) - 0.45, "chance", color=RED, fontsize=11)
ax.set_yticks(y, clf.model)
ax.set_xlim(0.44, 0.60)
ax.set_xlabel("Test ROC-AUC (432 unseen days)")
ax.legend(loc="lower right", fontsize=11)
save(fig, "model_auc.png")

# 3. Importancia SHAP (top 10) con nombres legibles
names = {
    "spot_gap": "Spot USD/COP vs. TRM gap", "trm_ret_lag1": "Last TRM return",
    "dxy_ret_1": "Dollar index (DXY), 1d", "usdcop_ret_1": "Spot USD/COP, 1d",
    "brent_ret_5": "Brent oil, 5d", "sp500_ret_1": "S&P 500, 1d",
    "trm_vol_20": "TRM volatility, 20d", "brent_ret_1": "Brent oil, 1d",
    "usdcop_vol_20": "USD/COP volatility, 20d", "vix_ret_1": "VIX, 1d",
}
shap = pd.read_csv(REPORTS / "shap_importance.csv", index_col=0).iloc[:10, 0][::-1]
fig, ax = plt.subplots(figsize=(8, 5))
ax.barh([names.get(k, k) for k in shap.index], shap.values * 100,
        color=[AMBER if k in ("spot_gap", "trm_ret_lag1") else NAVY for k in shap.index])
ax.set_xlabel("Mean |SHAP| (percentage points of P(up))")
save(fig, "shap_top10.png")

# 4. Backtest del hogar con remesas
res = json.loads((REPORTS / "model_results.json").read_text(encoding="utf-8"))
feats = feature_columns(df)
tr, te = df.iloc[:cut], df.iloc[cut:]
model = best(res, "classification", res["selected"]["classifier"]).fit(tr[feats], tr.y_up)
p = model.predict_proba(te[feats])[:, 1]
ret = np.expm1(te.y_ret.values)
scale = 1000 * te.trm.mean()
rng = np.random.default_rng(42)
rand = np.mean([np.cumsum(ret * (rng.random(len(ret)) > 0.5)) for _ in range(500)], axis=0) * scale
fig, ax = plt.subplots(figsize=(10, 4.2))
ax.plot(te.date, np.cumsum(ret * (p > 0.5)) * scale / 1000, color=NAVY, lw=2, label="Model: wait one day if P(up) > 0.5")
ax.plot(te.date, rand / 1000, color=GREY, lw=1.5, ls="--", label="Random wait-or-sell (mean of 500)")
ax.axhline(0, color="k", lw=0.8)
ax.set_ylabel("Cumulative gain vs. selling\nat once (thousand COP)")
ax.legend(loc="lower left", fontsize=11)
save(fig, "backtest.png")

# 5. Experimentos de nube (alta disponibilidad y carga)
EXP = ROOT / "reports" / "experiments"
fig, axes = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True)
for ax, (folder, label) in zip(axes, [("run1_min1", "ASG min. = 1"), ("run2_min2", "ASG min. = 2 (one per zone)")]):
    pr = pd.read_csv(EXP / folder / "ha_probes.csv")
    ok, bad = pr[pr.status == 200], pr[pr.status != 200]
    ax.scatter(ok.t_s, ok.latency_ms, s=5, color=NAVY, label="200 OK")
    if len(bad):
        ax.scatter(bad.t_s, [0] * len(bad), s=12, color=RED, marker="x", label="failed")
    ax.axvline(30, color=AMBER, lw=2)
    ax.set_title(f"{label}: availability {100 * len(ok) / len(pr):.1f}%", loc="left")
    ax.set_ylabel("Latency (ms)")
    ax.legend(loc="upper right", fontsize=11)
axes[0].set_ylim(-60, 600)
axes[-1].set_xlabel("Time (s); amber line = instance terminated")
save(fig, "ha_poster.png")

t = pd.read_csv(EXP / "load_timeline.csv")
fig, ax = plt.subplots(figsize=(8, 4.2))
ax.plot(t.t_min, t.p95_ms / 1000, marker="o", ms=4, color=NAVY, label="p95 latency")
ax.plot(t.t_min, t.p50_ms / 1000, marker="o", ms=4, color=TEAL, label="p50 latency")
ax.set_xlabel("Minutes under load"); ax.set_ylabel("Latency (s)")
ax2 = ax.twinx()
ax2.spines["right"].set_visible(True)
ax2.step(t.t_min, t.desired, where="post", color=GREY, ls="--", lw=2, label="Desired instances")
ax2.step(t.t_min, t.healthy, where="post", color=AMBER, lw=2.5, label="Healthy instances")
ax2.set_ylabel("Instances"); ax2.set_ylim(0, 6.2)
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, fontsize=11, loc="upper right", ncol=2)
ax.set_ylim(0, 23)
save(fig, "load_poster.png")
print("figuras en", OUT)
