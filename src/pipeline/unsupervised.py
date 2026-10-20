"""Minería no supervisada (R7) y reducción de dimensionalidad.

1. PCA: varianza explicada y proyección 2D.
2. Clustering de "regímenes de mercado" con K-Means, Jerárquico (Ward) y DBSCAN,
   comparados con silhouette y Davies-Bouldin.
3. Reglas de asociación (Apriori y FP-Growth) sobre movimientos discretizados:
   ¿qué combinaciones de mercado anteceden a una TRM al alza o a la baja?

Uso:  python -m src.pipeline.unsupervised
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from mlxtend.frequent_patterns import apriori, association_rules, fpgrowth
from scipy.cluster.hierarchy import dendrogram, linkage
from sklearn.cluster import DBSCAN, AgglomerativeClustering, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import davies_bouldin_score, silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from src.config import DATA_PROCESSED, FIGURES, RANDOM_STATE, REPORTS
from src.pipeline.prepare import feature_columns

REGIME_FEATURES = ["trm_vol_20", "trm_ret_5d", "vix_ret_5", "dxy_ret_5", "brent_ret_5", "btc_ret_5",
                   "brent_vol_20", "btc_vol_20", "sp500_vol_20"]


def pca_analysis(X: np.ndarray, names: list[str]) -> dict:
    pca = PCA().fit(X)
    cum = np.cumsum(pca.explained_variance_ratio_)
    n90 = int(np.searchsorted(cum, 0.90) + 1)
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.bar(range(1, len(cum) + 1), pca.explained_variance_ratio_, alpha=0.6)
    ax.step(range(1, len(cum) + 1), cum, where="mid", color="k")
    ax.axhline(0.9, ls="--", color="tab:red"); ax.set_xlabel("Componente"); ax.set_ylabel("Varianza explicada")
    fig.tight_layout(); fig.savefig(FIGURES / "pca_variance.png", dpi=200); plt.close(fig)
    loadings = pd.DataFrame(pca.components_[:2].T, index=names, columns=["PC1", "PC2"])
    top = {pc: loadings[pc].abs().sort_values(ascending=False).head(5).index.tolist() for pc in loadings}
    return {"n_components_90pct": n90, "total_features": len(names), "top_loadings": top}


def clustering(df: pd.DataFrame) -> tuple[dict, np.ndarray]:
    raw = df[REGIME_FEATURES]
    X_raw = StandardScaler().fit_transform(raw)
    # Los días de crisis (p. ej. marzo de 2020) dominan las distancias: con k=2, K-Means solo
    # separa esos ~30 días. Para segmentar regímenes "normales" se recortan las colas al p1-p99;
    # DBSCAN se aplica sobre los datos sin recortar para detectar precisamente esos días anómalos.
    X = StandardScaler().fit_transform(raw.clip(raw.quantile(0.01), raw.quantile(0.99), axis=1))
    res = {}

    # K-Means: elegir k con silhouette (k = 3..8) y método del codo
    inertias, sils = {}, {}
    for k in range(3, 9):
        km = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE).fit(X)
        inertias[k], sils[k] = km.inertia_, silhouette_score(X, km.labels_)
    k_best = max(sils, key=sils.get)
    km = KMeans(n_clusters=k_best, n_init=10, random_state=RANDOM_STATE).fit(X)
    res["kmeans"] = {"k": k_best, "silhouette": round(sils[k_best], 4),
                     "davies_bouldin": round(davies_bouldin_score(X, km.labels_), 4),
                     "silhouette_by_k": {k: round(v, 4) for k, v in sils.items()}}
    fig, ax = plt.subplots(figsize=(5, 3.5))
    ax.plot(list(inertias), list(inertias.values()), marker="o"); ax.set_xlabel("k"); ax.set_ylabel("Inercia")
    fig.tight_layout(); fig.savefig(FIGURES / "kmeans_elbow.png", dpi=200); plt.close(fig)

    # Jerárquico (Ward) con dendrograma
    agg = AgglomerativeClustering(n_clusters=k_best, linkage="ward").fit(X)
    res["hierarchical"] = {"k": k_best, "silhouette": round(silhouette_score(X, agg.labels_), 4),
                           "davies_bouldin": round(davies_bouldin_score(X, agg.labels_), 4)}
    Z = linkage(X, method="ward")
    fig, ax = plt.subplots(figsize=(8, 3.5))
    dendrogram(Z, truncate_mode="lastp", p=30, ax=ax, no_labels=True)
    ax.set_ylabel("Distancia Ward")
    fig.tight_layout(); fig.savefig(FIGURES / "hier_dendrogram.png", dpi=200); plt.close(fig)

    # DBSCAN como detector de días anómalos: eps desde el codo de la curva de k-distancias
    min_samples = 2 * X_raw.shape[1]
    dist = np.sort(NearestNeighbors(n_neighbors=min_samples).fit(X_raw).kneighbors(X_raw)[0][:, -1])
    eps = float(np.quantile(dist, 0.95))
    db = DBSCAN(eps=eps, min_samples=min_samples).fit(X_raw)
    mask = db.labels_ != -1
    n_clusters = len(set(db.labels_[mask]))
    noise = df.loc[~mask]
    res["dbscan"] = {"eps": round(eps, 4), "min_samples": min_samples, "clusters": n_clusters,
                     "noise_days": int((~mask).sum()), "noise_share": round(float((~mask).mean()), 4),
                     "noise_by_year": noise.date.dt.year.value_counts().sort_index().to_dict(),
                     "next_ret_std_noise_vs_rest": [round(float(noise.y_ret.std()), 5),
                                                    round(float(df.loc[mask].y_ret.std()), 5)]}
    fig, ax = plt.subplots(figsize=(5, 3.5))
    ax.plot(dist); ax.axhline(eps, ls="--", color="tab:red"); ax.set_ylabel(f"{min_samples}-distancia")
    fig.tight_layout(); fig.savefig(FIGURES / "dbscan_kdist.png", dpi=200); plt.close(fig)

    # Perfil de cada régimen (K-Means) y comportamiento de la TRM siguiente
    prof = df.assign(regime=km.labels_).groupby("regime").agg(
        days=("y_up", "size"), share_up=("y_up", "mean"), next_ret_std=("y_ret", "std"),
        trm_vol_20=("trm_vol_20", "mean"), vix_ret_5=("vix_ret_5", "mean"))
    prof.round(5).to_csv(REPORTS / "regimes_profile.csv")

    xy = PCA(n_components=2).fit_transform(X)
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    ax[0].scatter(xy[:, 0], xy[:, 1], c=km.labels_, s=4, cmap="tab10"); ax[0].set_title(f"K-Means (k={k_best})")
    ax[1].scatter(xy[:, 0], xy[:, 1], c=np.where(mask, "tab:blue", "tab:red"), s=4)
    ax[1].set_title("DBSCAN: días anómalos en rojo")
    for a in ax:
        a.set_xlabel("PC1"); a.set_ylabel("PC2")
    fig.tight_layout(); fig.savefig(FIGURES / "clusters_pca.png", dpi=200); plt.close(fig)
    return res, km.labels_


def discretize(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte retornos en ítems: <mercado>_up / <mercado>_down (terciles), más el resultado de la TRM."""
    items = {}
    for m in ("brent", "dxy", "vix", "sp500", "us10y", "usdmxn", "usdbrl", "btc"):
        col = df[f"{m}_ret_1"]
        lo, hi = col.quantile([1 / 3, 2 / 3])
        items[f"{m}_up"] = col > hi
        items[f"{m}_down"] = col < lo
    vol_hi = df.trm_vol_20 > df.trm_vol_20.quantile(0.75)
    items["trm_vol_high"] = vol_hi
    items["TRM_next_up"] = df.y_up == 1
    items["TRM_next_down"] = df.y_up == 0
    return pd.DataFrame(items)


def association(df: pd.DataFrame) -> dict:
    basket = discretize(df)
    out = {}
    for name, algo in (("apriori", apriori), ("fpgrowth", fpgrowth)):
        freq = algo(basket, min_support=0.05, use_colnames=True)
        rules = association_rules(freq, metric="lift", min_threshold=1.05)
        rules = rules[rules.consequents.apply(lambda c: c <= {"TRM_next_up", "TRM_next_down"} and len(c) == 1)]
        rules = rules.sort_values(["lift", "confidence"], ascending=False)
        out[name] = {"frequent_itemsets": len(freq), "rules_to_trm": len(rules)}
        if name == "fpgrowth":
            top = rules.head(10)[["antecedents", "consequents", "support", "confidence", "lift"]].copy()
            top["antecedents"] = top.antecedents.apply(lambda s: " & ".join(sorted(s)))
            top["consequents"] = top.consequents.apply(lambda s: next(iter(s)))
            top.round(4).to_csv(REPORTS / "association_rules_top.csv", index=False)
            out["top_rules"] = top.round(4).to_dict(orient="records")
    return out


def main():
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    feats = feature_columns(df)
    X = StandardScaler().fit_transform(df[feats])
    result = {"pca": pca_analysis(X, feats)}
    result["clustering"], labels = clustering(df)
    result["association"] = association(df)
    pd.DataFrame({"date": df.date, "regime": labels}).to_csv(DATA_PROCESSED / "regimes.csv", index=False)
    (REPORTS / "unsupervised.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
