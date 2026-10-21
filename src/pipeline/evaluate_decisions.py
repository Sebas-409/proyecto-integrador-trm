"""Evaluación orientada a decisiones (impacto para el usuario, ODS 10.c).

1. Calibración (Brier) del clasificador de dirección en el periodo de prueba.
2. Simulación "hogar con remesas": cada día recibe USD 1.000 y decide si los vende hoy a la TRM vigente
   o espera a la siguiente. Regla: esperar si P(sube) > umbral. Se compara contra vender siempre hoy,
   una decisión aleatoria y un oráculo (cota superior).
3. Alerta de riesgo: umbral de probabilidad elegido en entrenamiento (último pliegue) para que la tasa
   de alertas coincida con la frecuencia histórica de movimientos fuertes; precisión y exhaustividad en prueba.

Usa los hiperparámetros elegidos por train.py, reentrenando solo con el periodo de entrenamiento.
Uso:  python -m src.pipeline.evaluate_decisions
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import brier_score_loss, precision_score, recall_score

from src.config import DATA_PROCESSED, FIGURES, RANDOM_STATE, REPORTS, TEST_FRACTION
from src.pipeline.prepare import feature_columns

ESTIMATORS = {"random_forest": RandomForestClassifier, "gradient_boosting": GradientBoostingClassifier}


def best(results: dict, task: str, name: str):
    params = next(r for r in results[task] if r["model"] == name)["best_params"]
    extra = {"n_jobs": -1} if name == "random_forest" else {}
    return ESTIMATORS[name](random_state=RANDOM_STATE, **params, **extra)


def main():
    res = json.loads((REPORTS / "model_results.json").read_text(encoding="utf-8"))
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    feats = feature_columns(df)
    cut = int(len(df) * (1 - TEST_FRACTION))
    tr, te = df.iloc[:cut], df.iloc[cut:]
    thr_big = res["baseline"]["risk_threshold"]
    y_big = (df.y_ret.abs() > thr_big).astype(int)
    out = {}

    # ---------- 1 y 2: dirección ----------
    for name in ("random_forest", "gradient_boosting"):
        clf = best(res, "classification", name).fit(tr[feats], tr.y_up)
        p = clf.predict_proba(te[feats])[:, 1]
        ret = np.expm1(te.y_ret.values)  # variación porcentual de la TRM si se espera un día
        rng = np.random.default_rng(RANDOM_STATE)
        sims = {}
        for margin in (0.0, 0.05, 0.10):
            wait = p > 0.5 + margin
            sims[f"modelo_p>{0.5 + margin:.2f}"] = {"dias_espera": int(wait.sum()),
                                                    "ganancia_cop_por_1000usd_dia": float((ret * wait).mean() * te.trm.mean() * 1000 / 1)}
        rand = np.mean([(ret * (rng.random(len(ret)) > 0.5)).mean() for _ in range(1000)])
        sims["aleatorio_media_1000_sim"] = {"ganancia_cop_por_1000usd_dia": float(rand * te.trm.mean() * 1000)}
        sims["oraculo"] = {"ganancia_cop_por_1000usd_dia": float((ret * (ret > 0)).mean() * te.trm.mean() * 1000)}
        out[name] = {"brier": float(brier_score_loss(te.y_up, p)),
                     "brier_baseline_tasa_base": float(brier_score_loss(te.y_up, np.full(len(te), tr.y_up.mean()))),
                     "simulacion_remesas": sims}
        if name == res["selected"]["classifier"]:
            cum = pd.DataFrame({"date": te.date.values,
                                "Modelo (p>0.5)": np.cumsum(ret * (p > 0.5)) * 1000 * te.trm.mean() / 1,
                                "Oráculo": np.cumsum(ret * (ret > 0)) * 1000 * te.trm.mean()})

    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.plot(cum.date, cum["Modelo (p>0.5)"], label="Modelo (esperar si P(sube) > 0,5)")
    ax.axhline(0, color="k", lw=0.8, label="Vender siempre hoy (referencia)")
    ax.set_ylabel("COP acumulados por USD 1.000/día"); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(FIGURES / "decision_backtest.png", dpi=200); plt.close(fig)

    # ---------- 3: alerta de riesgo ----------
    name = res["selected"]["risk"]
    inner = int(cut * 0.8)  # umbral elegido sin mirar el periodo de prueba
    m = best(res, "risk", name).fit(df[feats].iloc[:inner], y_big.iloc[:inner])
    p_val = m.predict_proba(df[feats].iloc[inner:cut])[:, 1]
    alert_thr = float(np.quantile(p_val, 1 - y_big.iloc[:cut].mean()))
    m = best(res, "risk", name).fit(tr[feats], y_big.iloc[:cut])
    p_te = m.predict_proba(te[feats])[:, 1]
    alert = p_te > alert_thr
    yt = y_big.iloc[cut:].values
    out["risk_alert"] = {"model": name, "prob_threshold": round(alert_thr, 4), "alert_rate": float(alert.mean()),
                         "base_rate": float(yt.mean()), "precision": float(precision_score(yt, alert)),
                         "recall": float(recall_score(yt, alert)),
                         "lift_precision_vs_base": float(precision_score(yt, alert) / yt.mean())}

    (REPORTS / "decision_eval.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
