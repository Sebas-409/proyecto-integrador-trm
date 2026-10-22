"""Clusterización de regímenes de mercado con K-Means, eligiendo k por el método del codo.

1. Mismas variables de régimen que unsupervised.py (volatilidad y retornos a 5 días de la TRM y
   de sus mercados de referencia), con colas recortadas al p1-p99 y estandarizadas.
2. K-Means para k = 1..10; se guarda la inercia (WCSS) de cada k.
3. El codo se ubica de forma objetiva con el criterio de máxima distancia a la cuerda
   (idea del algoritmo Kneedle): el k cuya inercia queda más lejos de la recta que une k=1 y k=10.
   Se reportan también la caída porcentual de inercia y el silhouette de cada k como contraste.
4. Con el k del codo se entrena el modelo final, se perfila cada clúster y se le da un nombre.

Uso:  python -m src.pipeline.elbow
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from src.config import DATA_PROCESSED, FIGURES, RANDOM_STATE, REPORTS
from src.pipeline.unsupervised import REGIME_FEATURES

K_RANGE = range(1, 11)


def elbow_k(ks: list[int], inertias: list[float]) -> int:
    """k con la mayor distancia perpendicular a la recta entre el primer y el último punto."""
    x = (np.array(ks) - ks[0]) / (ks[-1] - ks[0])
    y = (np.array(inertias) - inertias[-1]) / (inertias[0] - inertias[-1])
    # Ambos ejes normalizados a [0, 1]; la cuerda va de (0, 1) a (1, 0): distancia ∝ |x + y - 1|
    dist = np.abs(x + y - 1) / np.sqrt(2)
    return int(ks[int(np.argmax(dist))])


def main():
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    raw = df[REGIME_FEATURES]
    X = StandardScaler().fit_transform(raw.clip(raw.quantile(0.01), raw.quantile(0.99), axis=1))

    rows = []
    for k in K_RANGE:
        km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE).fit(X)
        row = {"k": k, "inercia": km.inertia_}
        if k > 1:
            row["silhouette"] = silhouette_score(X, km.labels_)
            row["davies_bouldin"] = davies_bouldin_score(X, km.labels_)
            row["calinski_harabasz"] = calinski_harabasz_score(X, km.labels_)
        rows.append(row)
    curve = pd.DataFrame(rows).set_index("k")
    curve["caida_pct"] = -curve.inercia.pct_change() * 100
    curve.round(4).to_csv(REPORTS / "elbow_curve.csv")

    k_best = elbow_k(list(curve.index), curve.inercia.tolist())

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(curve.index, curve.inercia, marker="o", color="tab:blue")
    ax.plot([curve.index[0], curve.index[-1]], [curve.inercia.iloc[0], curve.inercia.iloc[-1]],
            ls=":", color="grey", label="Cuerda k=1 a k=10")
    ax.scatter([k_best], [curve.inercia[k_best]], s=160, facecolors="none", edgecolors="tab:red", lw=2,
               label=f"Codo: k={k_best}", zorder=3)
    ax.set_xlabel("Número de clústeres (k)"); ax.set_ylabel("Inercia (WCSS)")
    ax.set_title("Método del codo: K-Means sobre regímenes de mercado")
    ax2 = ax.twinx()
    ax2.plot(curve.index[1:], curve.silhouette.iloc[1:], marker="s", ls="--", color="tab:green", alpha=0.7)
    ax2.set_ylabel("Silhouette", color="tab:green")
    ax.set_xticks(list(K_RANGE)); ax.legend(loc="upper right")
    fig.tight_layout(); fig.savefig(FIGURES / "codo_kmeans.png", dpi=200); plt.close(fig)

    km = KMeans(n_clusters=k_best, n_init=10, random_state=RANDOM_STATE).fit(X)
    labels = km.labels_

    # Perfil: media de cada variable original por clúster y comportamiento de la TRM al día siguiente
    prof = df.assign(cluster=labels).groupby("cluster").agg(
        dias=("y_up", "size"), pct_alza_siguiente=("y_up", "mean"), std_ret_siguiente=("y_ret", "std"),
        **{f: (f, "mean") for f in REGIME_FEATURES})
    prof["pct_dias"] = prof.dias / prof.dias.sum()
    # Nombre del régimen según volatilidad de la TRM y tendencia de 5 días
    vol_rank = prof.trm_vol_20.rank()
    prof["regimen"] = [
        "Crisis / alta volatilidad" if vol_rank[c] == len(prof) else
        "Calma" if vol_rank[c] == 1 else
        ("Aversión al riesgo (dólar sube)" if prof.trm_ret_5d[c] > 0 else "Apetito por riesgo (peso se aprecia)")
        for c in prof.index]
    prof.round(5).to_csv(REPORTS / "elbow_clusters_profile.csv")

    # Centroides en unidades estandarizadas (z) para leer qué distingue a cada clúster
    cent = pd.DataFrame(km.cluster_centers_, columns=REGIME_FEATURES)
    fig, ax = plt.subplots(figsize=(8, 0.6 * k_best + 1.5))
    im = ax.imshow(cent, cmap="RdBu_r", vmin=-2, vmax=2, aspect="auto")
    ax.set_xticks(range(len(REGIME_FEATURES)), REGIME_FEATURES, rotation=45, ha="right")
    ax.set_yticks(range(k_best), [f"{c}: {prof.regimen[c]}" for c in range(k_best)])
    for i in range(k_best):
        for j in range(len(REGIME_FEATURES)):
            ax.text(j, i, f"{cent.iat[i, j]:.1f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, label="Centroide (z)")
    ax.set_title(f"Centroides K-Means (k={k_best})")
    fig.tight_layout(); fig.savefig(FIGURES / "codo_centroides.png", dpi=200); plt.close(fig)

    xy = PCA(n_components=2, random_state=RANDOM_STATE).fit_transform(X)
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    for c in range(k_best):
        m = labels == c
        ax[0].scatter(xy[m, 0], xy[m, 1], s=4, label=f"{c}: {prof.regimen[c]}")
        ax[1].scatter(df.date[m], df.trm[m], s=3)
    ax[0].set_xlabel("PC1"); ax[0].set_ylabel("PC2"); ax[0].set_title("Clústeres en el plano PCA")
    ax[0].legend(markerscale=3, fontsize=8)
    ax[1].set_title("TRM coloreada por régimen"); ax[1].set_ylabel("TRM (COP/USD)")
    fig.tight_layout(); fig.savefig(FIGURES / "codo_clusters.png", dpi=200); plt.close(fig)

    pd.DataFrame({"date": df.date, "cluster": labels}).to_csv(DATA_PROCESSED / "clusters_codo.csv", index=False)
    result = {
        "variables": REGIME_FEATURES, "observaciones": len(df),
        "k_codo": k_best,
        "metricas_k_codo": {m: round(float(curve.loc[k_best, m]), 4)
                            for m in ("inercia", "silhouette", "davies_bouldin", "calinski_harabasz")},
        "curva": curve.round(4).reset_index().to_dict(orient="records"),
        "perfil": prof.round(5).reset_index().to_dict(orient="records"),
    }
    (REPORTS / "elbow.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(curve.round(4).to_string()); print(f"\nk por el codo = {k_best}\n"); print(prof.round(4).to_string())


if __name__ == "__main__":
    main()
