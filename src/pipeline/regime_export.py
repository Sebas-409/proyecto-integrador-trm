"""Exporta el K-Means de regímenes (k del codo) a un JSON liviano para que la API clasifique el día en vivo.

Repite el ajuste de elbow.py (mismas variables, recorte p1-p99, estandarización, random_state) y guarda
límites de recorte, media y escala del estandarizador, centroides y nombre de cada régimen.
La API asigna el régimen por centroide más cercano, sin depender de scikit-learn ni de pickles.

Uso:  python -m src.pipeline.regime_export
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from src.config import DATA_PROCESSED, RANDOM_STATE, REPORTS
from src.pipeline.unsupervised import REGIME_FEATURES

OUT = Path(__file__).resolve().parents[2] / "api" / "regimes.json"


def main():
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    raw = df[REGIME_FEATURES]
    lo, hi = raw.quantile(0.01), raw.quantile(0.99)
    scaler = StandardScaler().fit(raw.clip(lo, hi, axis=1))
    X = scaler.transform(raw.clip(lo, hi, axis=1))
    k = json.loads((REPORTS / "elbow.json").read_text(encoding="utf-8"))["k_codo"]
    km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE).fit(X)

    prev = pd.read_csv(DATA_PROCESSED / "clusters_codo.csv")
    agree = float((prev.cluster.to_numpy() == km.labels_).mean())
    if agree < 0.99:
        raise SystemExit(f"Los clústeres no coinciden con clusters_codo.csv ({agree:.1%}); corre elbow.py primero")

    prof = pd.read_csv(REPORTS / "elbow_clusters_profile.csv", index_col="cluster")
    out = {"features": REGIME_FEATURES, "clip_lo": lo.tolist(), "clip_hi": hi.tolist(),
           "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
           "centroids": np.round(km.cluster_centers_, 6).tolist(),
           "names": [prof.regimen[c] for c in range(k)], "data_until": str(df.date.max().date())}
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{OUT} · k={k} · coincidencia con elbow.py {agree:.1%} · {out['names']}")


if __name__ == "__main__":
    main()
