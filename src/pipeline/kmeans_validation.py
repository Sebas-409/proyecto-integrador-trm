"""Validación del K-Means de regímenes siguiendo la guía de fases (EDA, entrenamiento, validación y acciones).

Complementa elbow.py (que elige k) con lo que pide la guía:
  Fase 2: nulos, atípicos recortados y correlación entre las variables de régimen.
  Fase 4: k-means++ explícito y centroides devueltos a la escala original con inverse_transform.
  Fase 5: silhouette por clúster, estabilidad entre semillas (ARI), contraste contra datos sin
          estructura y cruce con variables NO usadas para agrupar (TRM del día siguiente, día de la
          semana, año, tasas de EE. UU., pares emergentes) más la persistencia temporal del régimen.
  Fase 6: tabla de perfiles con la acción de cobertura sugerida para cada régimen.

Uso:  python -m src.pipeline.kmeans_validation
"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, kruskal
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_samples, silhouette_score
from sklearn.preprocessing import StandardScaler

from src.config import DATA_PROCESSED, FIGURES, RANDOM_STATE, REPORTS
from src.pipeline.unsupervised import REGIME_FEATURES

# Variables que el K-Means no vio: sirven para comprobar si los regímenes significan algo afuera
EXTERNAS = ["y_ret", "us10y_ret_5", "usdmxn_ret_5", "usdbrl_ret_5", "eth_vol_20", "spot_gap"]

ACCIONES = {
    "Calma": "Cobertura base (forwards a 30-90 días por el flujo esperado); buen momento para "
             "comprar opciones porque la volatilidad implícita es barata.",
    "Apetito por riesgo (peso se aprecia)": "Importadores: adelantar compras de dólares y ampliar la "
             "cobertura. Exportadores: esperar para monetizar, el peso fuerte suele revertir.",
    "Aversión al riesgo (dólar sube)": "Exportadores: vender dólares a plazo y asegurar la tasa alta. "
             "Importadores: no perseguir el alza; escalonar compras.",
    "Crisis / alta volatilidad": "Reducir exposición abierta, subir la cobertura al máximo de la política "
             "y ampliar los límites de stop; el modelo predictivo es menos confiable aquí.",
}


def main():
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    raw = df[REGIME_FEATURES]
    regimen = pd.read_csv(REPORTS / "elbow_clusters_profile.csv").set_index("cluster").regimen
    k = len(regimen)

    # ---------- Fase 2: calidad de datos ----------
    lo, hi = raw.quantile(0.01), raw.quantile(0.99)
    atipicos = ((raw < lo) | (raw > hi)).sum()
    corr = raw.corr()
    pares = corr.where(np.triu(np.ones(corr.shape, dtype=bool), 1)).stack()
    fase2 = {
        "nulos": int(raw.isna().sum().sum()),
        "atipicos_recortados_p1_p99": atipicos.to_dict(),
        "max_corr_abs": {"par": list(pares.abs().idxmax()), "valor": round(float(pares.abs().max()), 3)},
        "pares_corr_mayor_0_7": int((pares.abs() > 0.7).sum()),
    }
    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(REGIME_FEATURES)), REGIME_FEATURES, rotation=45, ha="right")
    ax.set_yticks(range(len(REGIME_FEATURES)), REGIME_FEATURES)
    for i in range(len(REGIME_FEATURES)):
        for j in range(len(REGIME_FEATURES)):
            ax.text(j, i, f"{corr.iat[i, j]:.2f}", ha="center", va="center", fontsize=6)
    fig.colorbar(im, ax=ax); ax.set_title("Correlación entre variables de régimen")
    fig.tight_layout(); fig.savefig(FIGURES / "kmeans_corr_variables.png", dpi=200); plt.close(fig)

    # ---------- Fase 4: modelo final con k-means++ y centroides en escala original ----------
    scaler = StandardScaler()
    X = scaler.fit_transform(raw.clip(lo, hi, axis=1))
    km = KMeans(n_clusters=k, init="k-means++", n_init=10, random_state=RANDOM_STATE).fit(X)
    labels = km.labels_
    # Mismo modelo que elbow.py (misma semilla): los números de clúster coinciden con el perfil
    ref = pd.read_csv(DATA_PROCESSED / "clusters_codo.csv").cluster.to_numpy()
    assert adjusted_rand_score(ref, labels) > 0.99, "El modelo no coincide con elbow.py"
    cent = pd.DataFrame(scaler.inverse_transform(km.cluster_centers_), columns=REGIME_FEATURES)
    cent.insert(0, "regimen", regimen.values)
    cent.round(5).to_csv(REPORTS / "kmeans_centroides_originales.csv", index_label="cluster")

    # ---------- Fase 5: validación interna ----------
    sil = silhouette_samples(X, labels)
    sil_cluster = pd.Series(sil).groupby(labels).agg(["mean", lambda s: (s < 0).mean()])
    sil_cluster.columns = ["silhouette_media", "pct_negativos"]

    # Estabilidad: el mismo k con 20 semillas distintas debe dar casi la misma partición
    aris = [adjusted_rand_score(labels, KMeans(n_clusters=k, n_init=10, random_state=s).fit_predict(X))
            for s in range(1, 21)]
    # Bootstrap: entrenar con el 80 % de los días y comparar la asignación de todos los días
    rng = np.random.default_rng(RANDOM_STATE)
    boot = []
    for _ in range(20):
        idx = rng.choice(len(X), int(0.8 * len(X)), replace=False)
        boot.append(adjusted_rand_score(labels, KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE)
                                        .fit(X[idx]).predict(X)))
    # Línea base: mismas variables pero barajadas columna por columna (sin estructura conjunta)
    X_null = np.column_stack([rng.permutation(c) for c in X.T])
    sil_null = silhouette_score(X_null, KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE).fit_predict(X_null))

    # ---------- Fase 5: análisis complementario con variables no usadas ----------
    d = df.assign(cluster=labels, regimen=regimen.values[labels], anio=df.date.dt.year)
    tab_up = pd.crosstab(d.regimen, d.y_up)
    chi_up = chi2_contingency(tab_up)
    tab_dow = pd.crosstab(d.regimen, d.dow)
    chi_dow = chi2_contingency(tab_dow)
    kw_absret = kruskal(*[g.y_ret.abs() for _, g in d.groupby("cluster")])
    externas = d.groupby("regimen")[EXTERNAS].mean()
    externas["abs_ret_siguiente"] = d.groupby("regimen").y_ret.apply(lambda s: s.abs().mean())
    externas.round(5).to_csv(REPORTS / "kmeans_variables_externas.csv")
    # Columnas en orden de clúster para que el color C{i} sea el mismo en todas las figuras
    anio = pd.crosstab(d.anio, d.regimen, normalize="index").round(3)[list(regimen.values)]
    anio.to_csv(REPORTS / "kmeans_regimen_por_anio.csv")

    # Persistencia: un régimen de mercado útil dura varios días, no salta al azar
    runs = (d.cluster != d.cluster.shift()).cumsum()
    duracion = d.groupby(runs).agg(c=("cluster", "first"), dias=("cluster", "size")) \
        .groupby("c").dias.mean()
    trans = pd.crosstab(d.cluster, d.cluster.shift(-1), normalize="index")
    permanencia = pd.Series(np.diag(trans), index=trans.index)

    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    order = np.argsort(labels, kind="stable")
    ax[0].bar(range(len(sil)), sil[order], color=[f"C{c}" for c in labels[order]], width=1.0)
    ax[0].axhline(sil.mean(), ls="--", color="k", lw=1, label=f"Media {sil.mean():.3f}")
    ax[0].set_title("Silhouette por día, agrupado por régimen"); ax[0].set_xticks([]); ax[0].legend()
    anio.plot.bar(stacked=True, ax=ax[1], width=0.8, legend=False, color=[f"C{c}" for c in range(k)])
    ax[1].set_title("Proporción de días por régimen y año"); ax[1].set_xlabel("")
    fig.legend(*ax[1].get_legend_handles_labels(), loc="lower center", ncol=4, fontsize=8)
    fig.tight_layout(rect=(0, 0.08, 1, 1)); fig.savefig(FIGURES / "kmeans_validacion.png", dpi=200); plt.close(fig)

    # ---------- Fase 6: perfiles + acciones ----------
    perfil = pd.read_csv(REPORTS / "elbow_clusters_profile.csv").set_index("cluster")
    perfil["silhouette_media"] = sil_cluster.silhouette_media
    perfil["duracion_media_dias"] = duracion
    perfil["prob_seguir_manana"] = permanencia
    perfil["accion"] = perfil.regimen.map(ACCIONES)
    perfil.round(5).to_csv(REPORTS / "kmeans_perfiles_acciones.csv")

    result = {
        "fase2": fase2,
        "fase4": {"init": "k-means++", "n_init": 10, "k": k,
                  "centroides_originales": cent.round(5).to_dict(orient="records")},
        "fase5": {
            "silhouette_global": round(float(sil.mean()), 4),
            "silhouette_datos_barajados": round(float(sil_null), 4),
            "silhouette_por_cluster": sil_cluster.round(4).reset_index(names="cluster").to_dict(orient="records"),
            "ari_semillas": {"media": round(float(np.mean(aris)), 4), "min": round(float(np.min(aris)), 4)},
            "ari_bootstrap_80": {"media": round(float(np.mean(boot)), 4), "min": round(float(np.min(boot)), 4)},
            "chi2_alza_siguiente": {"chi2": round(float(chi_up.statistic), 3), "p": round(float(chi_up.pvalue), 5)},
            "chi2_dia_semana": {"chi2": round(float(chi_dow.statistic), 3), "p": round(float(chi_dow.pvalue), 5)},
            "kruskal_abs_ret_siguiente": {"H": round(float(kw_absret.statistic), 3), "p": float(f"{kw_absret.pvalue:.3g}")},
            "duracion_media_dias": duracion.round(2).to_dict(),
            "prob_seguir_manana": permanencia.round(3).to_dict(),
        },
    }
    (REPORTS / "kmeans_validation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["fase2"], indent=1, ensure_ascii=False))
    print(json.dumps(result["fase5"], indent=1, ensure_ascii=False))
    print(externas.round(4).to_string()); print(anio.to_string())


if __name__ == "__main__":
    main()
