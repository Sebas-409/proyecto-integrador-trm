"""Figuras con la paleta del póster: en inglés (póster) y en español (presentación).

Uso:  python -m scripts.poster_figures
Salida: docs/poster/fig/*.png (en) y docs/presentacion/fig/*.png (es)
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

OUTS = {"en": ROOT / "docs" / "poster" / "fig", "es": ROOT / "docs" / "presentacion" / "fig"}
EXP = ROOT / "reports" / "experiments"

NAVY, TEAL, AMBER, RED, GREY = "#0B2545", "#1B7F79", "#E0A100", "#C0392B", "#8A94A6"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 13, "axes.spines.top": False,
    "axes.spines.right": False, "axes.edgecolor": "#444", "axes.titleweight": "bold",
    "axes.titlesize": 14, "legend.frameon": False,
})

TX = {
    "en": {
        "trm": "TRM (COP per USD)", "vol": "Ann. vol. 20d (%)", "hv": "High-volatility regime",
        "test": "  test period", "dir": "Direction (up / down)", "risk": "Risk (large move)",
        "chance": "chance", "auc": "Test ROC-AUC (432 unseen days)",
        "models": ["Logistic", "KNN", "SVM", "AdaBoost", "Random Forest", "Gradient Boosting"],
        "shap": "Mean |SHAP| (percentage points of P(up))",
        "feat": {
            "spot_gap": "Spot USD/COP vs. TRM gap", "trm_ret_lag1": "Last TRM return",
            "dxy_ret_1": "Dollar index (DXY), 1d", "usdcop_ret_1": "Spot USD/COP, 1d",
            "brent_ret_5": "Brent oil, 5d", "sp500_ret_1": "S&P 500, 1d",
            "trm_vol_20": "TRM volatility, 20d", "brent_ret_1": "Brent oil, 1d",
            "usdcop_vol_20": "USD/COP volatility, 20d", "vix_ret_1": "VIX, 1d"},
        "model": "Model: wait one day if P(up) > 0.5", "random": "Random wait-or-sell (mean of 500)",
        "gain": "Cumulative gain vs. selling\nat once (thousand COP)",
        "runs": ["ASG min. = 1", "ASG min. = 2 (one per zone)"], "avail": "{}: availability {:.1f}%",
        "ok": "200 OK", "fail": "failed", "lat": "Latency (ms)",
        "t": "Time (s); amber line = instance terminated",
        "p95": "p95 latency", "p50": "p50 latency", "lat_s": "Latency (s)", "min": "Minutes under load",
        "desired": "Desired instances", "healthy": "Healthy instances", "inst": "Instances",
    },
    "es": {
        "trm": "TRM (COP por USD)", "vol": "Volat. anual 20d (%)", "hv": "Régimen de alta volatilidad",
        "test": "  periodo de prueba", "dir": "Dirección (sube / baja)", "risk": "Riesgo (movimiento fuerte)",
        "chance": "azar", "auc": "ROC-AUC de prueba (432 días no vistos)",
        "models": ["Logística", "KNN", "SVM", "AdaBoost", "Random Forest", "Gradient Boosting"],
        "shap": "|SHAP| medio (puntos porcentuales de P(sube))",
        "feat": {
            "spot_gap": "Brecha spot USD/COP vs. TRM", "trm_ret_lag1": "Último retorno de la TRM",
            "dxy_ret_1": "Índice dólar (DXY), 1d", "usdcop_ret_1": "Spot USD/COP, 1d",
            "brent_ret_5": "Petróleo Brent, 5d", "sp500_ret_1": "S&P 500, 1d",
            "trm_vol_20": "Volatilidad TRM, 20d", "brent_ret_1": "Petróleo Brent, 1d",
            "usdcop_vol_20": "Volatilidad USD/COP, 20d", "vix_ret_1": "VIX, 1d"},
        "model": "Modelo: esperar un día si P(sube) > 0,5", "random": "Esperar o vender al azar (media de 500)",
        "gain": "Ganancia acumulada vs. vender\nde inmediato (miles de COP)",
        "runs": ["Mínimo del ASG = 1", "Mínimo del ASG = 2 (uno por zona)"], "avail": "{}: disponibilidad {:.1f} %",
        "ok": "200 OK", "fail": "fallida", "lat": "Latencia (ms)",
        "t": "Tiempo (s); línea ámbar = instancia terminada",
        "p95": "Latencia p95", "p50": "Latencia p50", "lat_s": "Latencia (s)", "min": "Minutos de carga",
        "desired": "Instancias deseadas", "healthy": "Instancias sanas", "inst": "Instancias",
    },
}


def save(fig, out, name):
    fig.tight_layout()
    fig.savefig(out / name, dpi=250)
    plt.close(fig)


df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
cut = int(len(df) * (1 - TEST_FRACTION))
reg = pd.read_csv(DATA_PROCESSED / "regimes.csv", parse_dates=["date"])

# Backtest del hogar con remesas (se calcula una sola vez)
res = json.loads((REPORTS / "model_results.json").read_text(encoding="utf-8"))
feats = feature_columns(df)
tr, te = df.iloc[:cut], df.iloc[cut:]
model = best(res, "classification", res["selected"]["classifier"]).fit(tr[feats], tr.y_up)
p = model.predict_proba(te[feats])[:, 1]
ret = np.expm1(te.y_ret.values)
scale = 1000 * te.trm.mean()
rng = np.random.default_rng(42)
rand = np.mean([np.cumsum(ret * (rng.random(len(ret)) > 0.5)) for _ in range(500)], axis=0) * scale

auc = pd.DataFrame({
    "direction": [0.497, 0.474, 0.490, 0.541, 0.555, 0.566],
    "risk": [0.549, 0.571, 0.562, 0.557, 0.585, 0.586],
})
shap = pd.read_csv(REPORTS / "shap_importance.csv", index_col=0).iloc[:10, 0][::-1]
load = pd.read_csv(EXP / "load_timeline.csv")

for lang, tx in TX.items():
    out = OUTS[lang]
    out.mkdir(parents=True, exist_ok=True)

    # 1. TRM y volatilidad, con el periodo de prueba sombreado
    fig, ax = plt.subplots(2, 1, figsize=(10, 5.2), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    ax[0].plot(df.date, df.trm, lw=1.3, color=NAVY)
    ax[0].set_ylabel(tx["trm"])
    ax[1].plot(df.date, df.trm_vol_20 * np.sqrt(252) * 100, lw=1, color=TEAL)
    hv = reg[reg.regime == 1].merge(df[["date", "trm_vol_20"]], on="date")
    ax[1].scatter(hv.date, hv.trm_vol_20 * np.sqrt(252) * 100, s=6, color=RED, zorder=3, label=tx["hv"])
    ax[1].set_ylabel(tx["vol"])
    ax[1].legend(loc="upper right", fontsize=11)
    for a in ax:
        a.axvspan(df.date.iloc[cut], df.date.iloc[-1], color=AMBER, alpha=0.15, lw=0)
    ax[0].text(df.date.iloc[cut], ax[0].get_ylim()[1], tx["test"], va="top", fontsize=11, color="#7a5a00")
    save(fig, out, "trm_series.png")

    # 2. ROC-AUC de prueba por modelo
    y = np.arange(len(auc))
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(y + 0.2, auc.direction, 0.38, color=NAVY, label=tx["dir"])
    ax.barh(y - 0.2, auc.risk, 0.38, color=TEAL, label=tx["risk"])
    ax.axvline(0.5, color=RED, ls="--", lw=1.5)
    ax.text(0.501, len(auc) - 0.45, tx["chance"], color=RED, fontsize=11)
    ax.set_yticks(y, tx["models"])
    ax.set_xlim(0.44, 0.60)
    ax.set_xlabel(tx["auc"])
    ax.legend(loc="lower right", fontsize=11)
    save(fig, out, "model_auc.png")

    # 3. Importancia SHAP (top 10)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh([tx["feat"].get(k, k) for k in shap.index], shap.values * 100,
            color=[AMBER if k in ("spot_gap", "trm_ret_lag1") else NAVY for k in shap.index])
    ax.set_xlabel(tx["shap"])
    save(fig, out, "shap_top10.png")

    # 4. Backtest
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(te.date, np.cumsum(ret * (p > 0.5)) * scale / 1000, color=NAVY, lw=2, label=tx["model"])
    ax.plot(te.date, rand / 1000, color=GREY, lw=1.5, ls="--", label=tx["random"])
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel(tx["gain"])
    ax.legend(loc="lower left", fontsize=11)
    save(fig, out, "backtest.png")

    # 5. Alta disponibilidad
    fig, axes = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True)
    for ax, folder, label in zip(axes, ["run1_min1", "run2_min2"], tx["runs"]):
        pr = pd.read_csv(EXP / folder / "ha_probes.csv")
        ok, bad = pr[pr.status == 200], pr[pr.status != 200]
        ax.scatter(ok.t_s, ok.latency_ms, s=5, color=NAVY, label=tx["ok"])
        if len(bad):
            ax.scatter(bad.t_s, [0] * len(bad), s=12, color=RED, marker="x", label=tx["fail"])
        ax.axvline(30, color=AMBER, lw=2)
        ax.set_title(tx["avail"].format(label, 100 * len(ok) / len(pr)), loc="left")
        ax.set_ylabel(tx["lat"])
        ax.legend(loc="upper right", fontsize=11)
    axes[0].set_ylim(-60, 600)
    axes[-1].set_xlabel(tx["t"])
    save(fig, out, "ha_poster.png")

    # 6. Prueba de carga
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(load.t_min, load.p95_ms / 1000, marker="o", ms=4, color=NAVY, label=tx["p95"])
    ax.plot(load.t_min, load.p50_ms / 1000, marker="o", ms=4, color=TEAL, label=tx["p50"])
    ax.set_xlabel(tx["min"]); ax.set_ylabel(tx["lat_s"])
    ax2 = ax.twinx()
    ax2.spines["right"].set_visible(True)
    ax2.step(load.t_min, load.desired, where="post", color=GREY, ls="--", lw=2, label=tx["desired"])
    ax2.step(load.t_min, load.healthy, where="post", color=AMBER, lw=2.5, label=tx["healthy"])
    ax2.set_ylabel(tx["inst"]); ax2.set_ylim(0, 6.2)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=11, loc="upper right", ncol=2)
    ax.set_ylim(0, 23)
    save(fig, out, "load_poster.png")
    print("figuras en", out)
