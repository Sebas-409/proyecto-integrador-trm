"""Análisis exploratorio de datos (EDA): estadísticas descriptivas y visualizaciones.

Uso:  python -m src.pipeline.eda
Salidas: reports/eda_describe.csv, reports/eda_summary.json, reports/figures/eda_*.png
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats

from src.config import DATA_PROCESSED, FIGURES, REPORTS
from src.pipeline.prepare import feature_columns

sns.set_theme(style="whitegrid", context="paper")


def main():
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    feats = feature_columns(df)
    df[feats + ["trm", "y_ret"]].describe().T.to_csv(REPORTS / "eda_describe.csv")

    r = df.y_ret
    adf_like = stats.pearsonr(r[:-1], r[1:])  # autocorrelación de primer orden de los retornos
    summary = {
        "rows": len(df),
        "period": [str(df.date.min().date()), str(df.date.max().date())],
        "share_up_days": round(float(df.y_up.mean()), 4),
        "ret_mean": float(r.mean()), "ret_std": float(r.std()),
        "ret_skew": float(stats.skew(r)), "ret_kurtosis": float(stats.kurtosis(r)),
        "jarque_bera_p": float(stats.jarque_bera(r).pvalue),
        "ret_autocorr_lag1": round(float(adf_like.statistic), 4),
        "top_corr_with_y_ret": df[feats].corrwith(df.y_ret).abs().sort_values(ascending=False).head(8).round(4).to_dict(),
    }
    (REPORTS / "eda_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # 1. Serie de la TRM con volatilidad
    fig, ax = plt.subplots(2, 1, figsize=(9, 5), sharex=True)
    ax[0].plot(df.date, df.trm, lw=1)
    ax[0].set_ylabel("TRM (COP/USD)")
    ax[1].plot(df.date, df.trm_vol_20 * np.sqrt(252) * 100, lw=1, color="tab:red")
    ax[1].set_ylabel("Volatilidad anual. 20d (%)")
    fig.tight_layout(); fig.savefig(FIGURES / "eda_trm_series.png", dpi=200); plt.close(fig)

    # 2. Distribución de retornos frente a la normal
    fig, ax = plt.subplots(figsize=(6, 4))
    sns.histplot(r * 100, bins=80, stat="density", ax=ax)
    x = np.linspace(r.min(), r.max(), 200)
    ax.plot(x * 100, stats.norm.pdf(x, r.mean(), r.std()) / 100, color="k", lw=1, label="Normal")
    ax.set_xlabel("Retorno diario TRM (%)"); ax.legend()
    fig.tight_layout(); fig.savefig(FIGURES / "eda_returns_hist.png", dpi=200); plt.close(fig)

    # 3. Mapa de correlaciones de retornos de 1 día
    cols = [c for c in feats if c.endswith("_ret_1")] + ["trm_ret_lag1", "y_ret"]
    fig, ax = plt.subplots(figsize=(7, 6))
    sns.heatmap(df[cols].corr(), cmap="RdBu_r", center=0, annot=True, fmt=".2f", annot_kws={"size": 6}, ax=ax)
    fig.tight_layout(); fig.savefig(FIGURES / "eda_corr.png", dpi=200); plt.close(fig)

    # 4. Retorno por día de la semana
    fig, ax = plt.subplots(figsize=(6, 4))
    sns.boxplot(x=df.dow, y=r * 100, ax=ax, showfliers=False)
    ax.set_xlabel("Día de la semana (0 = lunes)"); ax.set_ylabel("Retorno TRM (%)")
    fig.tight_layout(); fig.savefig(FIGURES / "eda_dow_box.png", dpi=200); plt.close(fig)

    # 5. Correlación móvil TRM vs Brent y DXY (relación que cambia en el tiempo)
    fig, ax = plt.subplots(figsize=(9, 3.5))
    for m in ("brent", "dxy", "btc"):
        ax.plot(df.date, df.trm_ret_lag1.rolling(120).corr(df[f"{m}_ret_1"]), lw=1, label=m)
    ax.axhline(0, color="k", lw=0.5); ax.set_ylabel("Corr. móvil 120d"); ax.legend()
    fig.tight_layout(); fig.savefig(FIGURES / "eda_rolling_corr.png", dpi=200); plt.close(fig)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
