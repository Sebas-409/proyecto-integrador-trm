"""Modelado predictivo (Machine Learning).

- División cronológica: 80 % entrenamiento / 20 % prueba (nunca se mezcla el futuro con el pasado).
- Validación cruzada de series de tiempo (TimeSeriesSplit, 5 pliegues) dentro de entrenamiento.
- Regresión del retorno siguiente: Lineal, Polinomial+Ridge, Ridge, Lasso, ElasticNet, RF, GB.
- Clasificación de la dirección (sube/baja): Logística, KNN, SVM, RF, AdaBoost, GB.
- Clasificación de riesgo: ¿el siguiente movimiento será fuerte (|retorno| > percentil 75 de entrenamiento)?
- Optimización: GridSearchCV (modelos lineales, KNN, SVM, AdaBoost) y RandomizedSearchCV (RF, GB).
- Línea base: paseo aleatorio (retorno 0) y clase mayoritaria.
- Interpretabilidad: importancia de variables, SHAP y LIME.

Uso:  python -m src.pipeline.train
"""
import json
import time
from datetime import datetime, timezone

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from lime.lime_tabular import LimeTabularExplainer
from scipy.stats import loguniform, randint, uniform
from sklearn.ensemble import (AdaBoostClassifier, GradientBoostingClassifier, GradientBoostingRegressor,
                              RandomForestClassifier, RandomForestRegressor)
from sklearn.feature_selection import SelectKBest, f_regression
from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import (accuracy_score, f1_score, mean_absolute_error, mean_squared_error, r2_score,
                             roc_auc_score)
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV, TimeSeriesSplit
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler
from sklearn.svm import SVC

from src.config import DATA_PROCESSED, FIGURES, MODELS, RANDOM_STATE, REPORTS, TEST_FRACTION
from src.pipeline.prepare import feature_columns
from src.storage import save

CV = TimeSeriesSplit(n_splits=5)
RS = RANDOM_STATE


def reg_candidates():
    return {
        "linear": (Pipeline([("sc", StandardScaler()), ("m", LinearRegression())]), None, "grid"),
        "poly2_ridge": (Pipeline([("sc", StandardScaler()), ("kb", SelectKBest(f_regression)),
                                  ("poly", PolynomialFeatures(2, include_bias=False)), ("m", Ridge())]),
                        {"kb__k": [5, 10], "m__alpha": [10, 100, 1000]}, "grid"),
        "ridge": (Pipeline([("sc", StandardScaler()), ("m", Ridge())]), {"m__alpha": np.logspace(-2, 4, 13)}, "grid"),
        "lasso": (Pipeline([("sc", StandardScaler()), ("m", Lasso(max_iter=20000))]),
                  {"m__alpha": np.logspace(-6, -2, 13)}, "grid"),
        "elasticnet": (Pipeline([("sc", StandardScaler()), ("m", ElasticNet(max_iter=20000))]),
                       {"m__alpha": np.logspace(-6, -2, 9), "m__l1_ratio": [0.2, 0.5, 0.8]}, "grid"),
        "random_forest": (RandomForestRegressor(random_state=RS, n_jobs=-1),
                          {"n_estimators": randint(200, 600), "max_depth": randint(3, 12),
                           "min_samples_leaf": randint(5, 50), "max_features": uniform(0.2, 0.6)}, "random"),
        "gradient_boosting": (GradientBoostingRegressor(random_state=RS),
                              {"n_estimators": randint(100, 500), "learning_rate": loguniform(0.005, 0.1),
                               "max_depth": randint(2, 5), "subsample": uniform(0.6, 0.4),
                               "min_samples_leaf": randint(10, 60)}, "random"),
    }


def clf_candidates():
    return {
        "logistic": (Pipeline([("sc", StandardScaler()), ("m", LogisticRegression(max_iter=5000))]),
                     {"m__C": np.logspace(-3, 2, 11)}, "grid"),
        "knn": (Pipeline([("sc", StandardScaler()), ("m", KNeighborsClassifier())]),
                {"m__n_neighbors": [15, 31, 51, 101, 151], "m__weights": ["uniform", "distance"]}, "grid"),
        "svm": (Pipeline([("sc", StandardScaler()), ("m", SVC(probability=True, random_state=RS))]),
                {"m__C": [0.1, 1, 10], "m__gamma": ["scale", 0.01, 0.001]}, "grid"),
        "random_forest": (RandomForestClassifier(random_state=RS, n_jobs=-1),
                          {"n_estimators": randint(200, 600), "max_depth": randint(3, 12),
                           "min_samples_leaf": randint(5, 50), "max_features": uniform(0.2, 0.6)}, "random"),
        "adaboost": (AdaBoostClassifier(random_state=RS),
                     {"n_estimators": [100, 300, 500], "learning_rate": [0.01, 0.05, 0.1, 0.5]}, "grid"),
        "gradient_boosting": (GradientBoostingClassifier(random_state=RS),
                              {"n_estimators": randint(100, 500), "learning_rate": loguniform(0.005, 0.1),
                               "max_depth": randint(2, 5), "subsample": uniform(0.6, 0.4),
                               "min_samples_leaf": randint(10, 60)}, "random"),
    }


def search(est, grid, kind, scoring):
    if grid is None:
        return est
    if kind == "grid":
        return GridSearchCV(est, grid, cv=CV, scoring=scoring, n_jobs=-1)
    return RandomizedSearchCV(est, grid, n_iter=15, cv=CV, scoring=scoring, n_jobs=-1, random_state=RS)


def fit_all(cands, X_tr, y_tr, scoring):
    fitted, rows = {}, []
    for name, (est, grid, kind) in cands.items():
        t0 = time.time()
        s = search(est, grid, kind, scoring).fit(X_tr, y_tr)
        model = s.best_estimator_ if hasattr(s, "best_estimator_") else s
        cv_score = float(s.best_score_) if hasattr(s, "best_score_") else None
        fitted[name] = model
        rows.append({"model": name, "search": kind if grid is not None else "none", "cv_score": cv_score,
                     "best_params": {k: (float(v) if isinstance(v, np.floating) else v)
                                     for k, v in getattr(s, "best_params_", {}).items()},
                     "fit_seconds": round(time.time() - t0, 2)})
        print(f"  {name:<18} cv={cv_score}  ({rows[-1]['fit_seconds']} s)")
    return fitted, rows


def evaluate_classifiers(X_tr, y_tr, X_te, y_te):
    fitted, rows = fit_all(clf_candidates(), X_tr, y_tr, "roc_auc")
    for r in rows:
        proba = fitted[r["model"]].predict_proba(X_te)[:, 1]
        pred = (proba >= 0.5).astype(int)
        r.update(test_auc=float(roc_auc_score(y_te, proba)), test_acc=float(accuracy_score(y_te, pred)),
                 test_f1=float(f1_score(y_te, pred, zero_division=0)))
    return fitted, rows


def main():
    df = pd.read_csv(DATA_PROCESSED / "dataset.csv", parse_dates=["date"])
    feats = feature_columns(df)
    cut = int(len(df) * (1 - TEST_FRACTION))
    tr, te = df.iloc[:cut], df.iloc[cut:]
    X_tr, X_te = tr[feats], te[feats]
    print(f"Entrenamiento: {tr.date.min().date()} -> {tr.date.max().date()} ({len(tr)}) | "
          f"Prueba: {te.date.min().date()} -> {te.date.max().date()} ({len(te)})")

    # ---------- Regresión ----------
    print("Regresión (scoring = -RMSE):")
    regs, reg_rows = fit_all(reg_candidates(), X_tr, tr.y_ret, "neg_root_mean_squared_error")
    base_rmse = float(np.sqrt(mean_squared_error(te.y_ret, np.zeros(len(te)))))
    for r in reg_rows:
        p = regs[r["model"]].predict(X_te)
        r.update(test_rmse=float(np.sqrt(mean_squared_error(te.y_ret, p))), test_mae=float(mean_absolute_error(te.y_ret, p)),
                 test_r2=float(r2_score(te.y_ret, p)), test_dir_acc=float(accuracy_score(te.y_up, (p > 0).astype(int))))
        r["rmse_vs_random_walk"] = r["test_rmse"] / base_rmse

    # ---------- Clasificación de dirección ----------
    print("Clasificación de dirección (scoring = ROC-AUC):")
    clfs, clf_rows = evaluate_classifiers(X_tr, tr.y_up, X_te, te.y_up)
    majority = int(tr.y_up.mean() >= 0.5)
    base_acc = float(accuracy_score(te.y_up, np.full(len(te), majority)))

    # ---------- Clasificación de riesgo (movimiento fuerte) ----------
    big_thr = float(tr.y_ret.abs().quantile(0.75))  # umbral fijado solo con entrenamiento
    df["y_big"] = (df.y_ret.abs() > big_thr).astype(int)
    tr, te = df.iloc[:cut], df.iloc[cut:]
    print(f"Clasificación de riesgo |ret| > {big_thr:.4%} (scoring = ROC-AUC):")
    risk_clfs, risk_rows = evaluate_classifiers(X_tr, tr.y_big, X_te, te.y_big)

    # Selección del modelo final: mejor desempeño en validación cruzada (no en prueba, para no sobreajustar a ella).
    best_clf = max(clf_rows, key=lambda r: r["cv_score"])["model"]
    best_risk = max(risk_rows, key=lambda r: r["cv_score"])["model"]
    best_reg = max((r for r in reg_rows if r["cv_score"] is not None), key=lambda r: r["cv_score"])["model"]

    results = {"train_rows": len(tr), "test_rows": len(te), "test_period": [str(te.date.min().date()), str(te.date.max().date())],
               "baseline": {"random_walk_rmse": base_rmse, "majority_class": majority, "majority_acc": base_acc,
                            "risk_threshold": big_thr, "risk_positive_share_test": float(te.y_big.mean())},
               "regression": reg_rows, "classification": clf_rows, "risk": risk_rows,
               "selected": {"classifier": best_clf, "regressor": best_reg, "risk": best_risk}}
    (REPORTS / "model_results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(clf_rows).drop(columns="best_params").round(4).to_csv(REPORTS / "classification_table.csv", index=False)
    pd.DataFrame(risk_rows).drop(columns="best_params").round(4).to_csv(REPORTS / "risk_table.csv", index=False)
    pd.DataFrame(reg_rows).drop(columns="best_params").to_csv(REPORTS / "regression_table.csv", index=False)

    # ---------- Interpretabilidad ----------
    # SHAP sobre el mejor ensemble (árboles) para explicar qué variables mueven la probabilidad de alza.
    ens_name = max((r for r in clf_rows if r["model"] in ("random_forest", "adaboost", "gradient_boosting")),
                   key=lambda r: r["cv_score"])["model"]
    ens = clfs[ens_name]
    sv = shap.TreeExplainer(ens)(X_te)
    if sv.values.ndim == 3:  # RandomForest devuelve una matriz por clase: se toma la clase "sube"
        sv = sv[:, :, 1]
    plt.figure()
    shap.plots.beeswarm(sv, max_display=15, show=False)
    plt.title(f"SHAP – {ens_name}"); plt.tight_layout(); plt.savefig(FIGURES / "shap_beeswarm.png", dpi=200); plt.close()
    shap_rank = pd.Series(np.abs(sv.values).mean(0), index=feats).sort_values(ascending=False)
    shap_rank.to_csv(REPORTS / "shap_importance.csv", header=["mean_abs_shap"])

    if hasattr(ens, "feature_importances_"):
        imp = pd.Series(ens.feature_importances_, index=feats).sort_values().tail(15)
        fig, ax = plt.subplots(figsize=(6, 5)); imp.plot.barh(ax=ax); ax.set_title(f"Feature importance – {ens_name}")
        fig.tight_layout(); fig.savefig(FIGURES / "feature_importance.png", dpi=200); plt.close(fig)

    # LIME: explicación local de la predicción más reciente del modelo final.
    final_clf = clfs[best_clf]
    lime_exp = LimeTabularExplainer(X_tr.values, feature_names=feats, class_names=["baja", "sube"],
                                    mode="classification", discretize_continuous=True, random_state=RS)
    exp = lime_exp.explain_instance(X_te.values[-1], lambda x: final_clf.predict_proba(pd.DataFrame(x, columns=feats)),
                                    num_features=10)
    exp.save_to_file(str(REPORTS / "lime_last_prediction.html"))
    fig = exp.as_pyplot_figure(); fig.set_size_inches(7, 4); fig.tight_layout()
    fig.savefig(FIGURES / "lime_last_prediction.png", dpi=200); plt.close(fig)

    # ---------- Persistencia ----------
    # Se reentrenan los modelos elegidos con todos los datos disponibles antes de desplegarlos.
    final_clf.fit(df[feats], df.y_up)
    final_reg = regs[best_reg].fit(df[feats], df.y_ret)
    final_risk = risk_clfs[best_risk].fit(df[feats], df.y_big)
    meta = {"trained_at": datetime.now(timezone.utc).isoformat(), "features": feats,
            "classifier": best_clf, "regressor": best_reg, "risk": best_risk, "risk_threshold": big_thr,
            "data_until": str(df.date.max().date()),
            "test_metrics": {"classifier": next(r for r in clf_rows if r["model"] == best_clf),
                             "regressor": next(r for r in reg_rows if r["model"] == best_reg),
                             "risk": next(r for r in risk_rows if r["model"] == best_risk)},
            "shap_top10": shap_rank.head(10).round(6).to_dict()}
    joblib.dump({"classifier": final_clf, "regressor": final_reg, "risk": final_risk, "meta": meta}, MODELS / "model.joblib")
    save("models/model.joblib", (MODELS / "model.joblib").read_bytes())
    save("models/metadata.json", json.dumps(meta, indent=2, default=str).encode("utf-8"))

    print(pd.DataFrame(clf_rows)[["model", "cv_score", "test_auc", "test_acc", "test_f1"]].round(4).to_string(index=False))
    print(f"Línea base clase mayoritaria: acc={base_acc:.4f}")
    print(pd.DataFrame(risk_rows)[["model", "cv_score", "test_auc", "test_acc", "test_f1"]].round(4).to_string(index=False))
    print(pd.DataFrame(reg_rows)[["model", "cv_score", "test_rmse", "rmse_vs_random_walk", "test_dir_acc"]].round(6).to_string(index=False))
    print(f"Seleccionados -> dirección: {best_clf} | riesgo: {best_risk} | regresor: {best_reg}")


if __name__ == "__main__":
    main()
