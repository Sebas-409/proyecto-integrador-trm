"""Backtest de la señal para importadores (compran USD para pagar mercancía en EE. UU.).

Espejo de la simulación de remesas de evaluate_decisions.py: cada día del periodo de prueba el importador
debe pagar USD 1.000 y decide si paga hoy a la TRM vigente o espera al día siguiente.
Regla: esperar si P(baja) >= umbral, sin alerta de movimiento fuerte y fuera del régimen de Crisis.
Ganancia = lo que ahorra en pesos cuando la TRM efectivamente baja (pierde si sube).

Usa los hiperparámetros elegidos por train.py, reentrenando solo con el periodo de entrenamiento.
Uso:  python -m src.pipeline.evaluate_importador
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import DATA_PROCESSED, RANDOM_STATE, REPORTS, TEST_FRACTION
from src.pipeline.evaluate_decisions import best
from src.pipeline.prepare import feature_columns

UMBRAL = 0.55  # regla que usa la API (la de mejor acierto con suficientes días de espera)
REGLA_API = Path(__file__).resolve().parents[2] / "api" / "importador_regla.json"


def main():
    res = json.loads((REPORTS / "model_results.json").read_text(encoding="utf-8"))
    risk_thr = json.loads((REPORTS / "decision_eval.json").read_text(encoding="utf-8"))["risk_alert"]["prob_threshold"]
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    reg = pd.read_csv(DATA_PROCESSED / "clusters_codo.csv", parse_dates=["date"])
    prof = pd.read_csv(REPORTS / "elbow_clusters_profile.csv", index_col="cluster")
    crisis_id = int(prof.index[prof.regimen.str.startswith("Crisis")][0])
    df = df.merge(reg, on="date", how="left")
    feats = feature_columns(df.drop(columns="cluster"))
    cut = int(len(df) * (1 - TEST_FRACTION))
    tr, te = df.iloc[:cut], df.iloc[cut:]

    clf = best(res, "classification", res["selected"]["classifier"]).fit(tr[feats], tr.y_up)
    p_down = 1 - clf.predict_proba(te[feats])[:, 1]
    y_big = (df.y_ret.abs() > res["baseline"]["risk_threshold"]).astype(int)
    risk = best(res, "risk", res["selected"]["risk"]).fit(tr[feats], y_big.iloc[:cut])
    alert = risk.predict_proba(te[feats])[:, 1] > risk_thr
    crisis = (te.cluster == crisis_id).to_numpy()

    ret = np.expm1(te.y_ret.values)  # variación de la TRM si espera un día
    per_1000 = te.trm.mean() * 1000  # COP por cada USD 1.000 y punto de variación
    out = {"periodo_prueba": [str(te.date.min().date()), str(te.date.max().date())], "dias": len(te),
           "reglas": {}}
    for thr in (0.50, 0.55, 0.60):
        for filtro in (False, True):
            wait = p_down >= thr
            if filtro:
                wait &= ~alert & ~crisis
            ahorro = -ret * wait  # positivo si la TRM bajó mientras esperaba
            key = f"P(baja)>={thr:.2f}" + (" sin alerta ni crisis" if filtro else "")
            out["reglas"][key] = {
                "dias_espera": int(wait.sum()),
                "acierto_al_esperar": float((ret[wait] < 0).mean()) if wait.any() else None,
                "ahorro_cop_por_1000usd_dia": round(float(ahorro.mean() * per_1000), 1),
                "ahorro_cop_por_1000usd_cuando_espera": round(float(-ret[wait].mean() * per_1000), 1) if wait.any() else None}
    rng = np.random.default_rng(RANDOM_STATE)
    out["aleatorio_media_1000_sim"] = round(float(np.mean(
        [(-ret * (rng.random(len(ret)) > 0.5)).mean() for _ in range(1000)]) * per_1000), 1)
    out["oraculo"] = round(float((-ret * (ret < 0)).mean() * per_1000), 1)

    (REPORTS / "importador_eval.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    elegida = out["reglas"][f"P(baja)>={UMBRAL:.2f}"]
    REGLA_API.write_text(json.dumps({
        "umbral_p_baja": UMBRAL, "periodo_prueba": out["periodo_prueba"],
        "acierto_al_esperar": round(elegida["acierto_al_esperar"], 4), "dias_espera": elegida["dias_espera"],
        "ahorro_cop_por_1000usd_cuando_espera": elegida["ahorro_cop_por_1000usd_cuando_espera"]}, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
