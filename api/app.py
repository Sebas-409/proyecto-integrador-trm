"""API REST de predicción de la TRM + tablero web.

- Modelo: s3://$DATA_BUCKET/models/model.joblib (en AWS) o models/model.joblib (local).
- Datos: PostgreSQL (RDS) si DATABASE_URL está definido; si no, data/raw/ (local).

Ejecutar en local:  uvicorn api.app:app --reload --port 8000
"""
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.config import BINANCE_SYMBOLS, MODELS, YAHOO_SYMBOLS
from src.pipeline.prepare import load_market, load_trm, make_features

BUCKET = os.getenv("DATA_BUCKET", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
STATIC = Path(__file__).parent / "static"
MARKETS = list(YAHOO_SYMBOLS) + list(BINANCE_SYMBOLS)
CACHE_SECONDS = 600

app = FastAPI(title="TRM Predict API", version="1.0.0",
              description="Pronóstico de la dirección y el valor de la TRM (COP/USD) del siguiente día hábil.")
_state = {"bundle": None, "features": None, "loaded_at": 0.0}


# ---------------- Carga de modelo y datos ----------------

def load_bundle():
    if BUCKET:
        import boto3

        body = boto3.client("s3").get_object(Bucket=BUCKET, Key="models/model.joblib")["Body"].read()
        return joblib.load(io.BytesIO(body))
    return joblib.load(MODELS / "model.joblib")


def _db():
    import pg8000.native

    u = urlparse(DATABASE_URL)
    return pg8000.native.Connection(host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"),
                                    user=u.username, password=u.password, timeout=10)


def load_data() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    if not DATABASE_URL:
        return load_trm(), {m: load_market(m) for m in MARKETS}
    con = _db()
    trm = pd.DataFrame(con.run("SELECT date, value::float FROM trm WHERE date >= now() - interval '400 days' ORDER BY date"),
                       columns=["date", "trm"])
    mk = pd.DataFrame(con.run("SELECT date, name, value FROM market WHERE date >= now() - interval '400 days'"),
                      columns=["date", "name", "value"])
    con.close()
    trm["date"] = pd.to_datetime(trm.date)
    mk["date"] = pd.to_datetime(mk.date)
    markets = {m: mk[mk.name == m][["date", "value"]].rename(columns={"value": m}).sort_values("date") for m in MARKETS}
    return trm, markets


def state():
    if _state["bundle"] is None:
        _state["bundle"] = load_bundle()
    if _state["features"] is None or time.time() - _state["loaded_at"] > CACHE_SECONDS:
        trm, markets = load_data()
        _state["features"] = make_features(trm, markets)
        _state["loaded_at"] = time.time()
    return _state["bundle"], _state["features"]


def predict_row(row: pd.DataFrame, bundle) -> dict:
    feats = bundle["meta"]["features"]
    X = row[feats]
    if X.isna().any(axis=None):
        missing = X.columns[X.isna().any()].tolist()
        raise HTTPException(503, f"Faltan datos recientes para: {missing}")
    prob_up = float(bundle["classifier"].predict_proba(X)[0, 1])
    exp_ret = float(bundle["regressor"].predict(X)[0])
    prob_big = float(bundle["risk"].predict_proba(X)[0, 1])
    trm = float(row["trm"].iloc[0])
    return {"base_date": str(row["date"].iloc[0].date()), "trm_base": round(trm, 2),
            "prob_up": round(prob_up, 4), "direction": "sube" if prob_up >= 0.5 else "baja",
            "expected_return_pct": round(exp_ret * 100, 4),
            "trm_expected": round(trm * (1 + exp_ret), 2),
            "prob_big_move": round(prob_big, 4),
            "big_move_threshold_pct": round(bundle["meta"]["risk_threshold"] * 100, 3),
            "models": {k: bundle["meta"][k] for k in ("classifier", "regressor", "risk")}}


def save_prediction(p: dict):
    if not DATABASE_URL:
        return
    try:
        con = _db()
        con.run("INSERT INTO predictions(base_date, trm_base, prob_up, expected_ret, trm_expected, model) "
                "VALUES (:d, :t, :p, :r, :e, :m) ON CONFLICT (base_date, model) DO NOTHING",
                d=p["base_date"], t=p["trm_base"], p=p["prob_up"], r=p["expected_return_pct"] / 100,
                e=p["trm_expected"], m=p["models"]["classifier"])
        con.close()
    except Exception as err:  # registrar la predicción no debe romper la respuesta
        print(f"No se pudo guardar la predicción: {err}")


# ---------------- Endpoints ----------------

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "instance": os.getenv("HOSTNAME", "local")}


@app.get("/api/predict")
def predict():
    bundle, feats = state()
    p = predict_row(feats.tail(1), bundle)
    save_prediction(p)
    return p


class Scenario(BaseModel):
    """Choques hipotéticos sobre las variables del último día, p. ej. {"brent_ret_1": -0.03}."""
    shocks: dict[str, float]


@app.post("/api/scenario")
def scenario(body: Scenario):
    bundle, feats = state()
    row = feats.tail(1).copy()
    unknown = [k for k in body.shocks if k not in bundle["meta"]["features"]]
    if unknown:
        raise HTTPException(400, f"Variables desconocidas: {unknown}")
    for k, v in body.shocks.items():
        row[k] = v
    return {"base": predict_row(feats.tail(1), bundle), "scenario": predict_row(row, bundle), "shocks": body.shocks}


@app.get("/api/history")
def history(days: int = 180):
    _, feats = state()
    h = feats.tail(days)[["date", "trm"]]
    return {"dates": h.date.dt.strftime("%Y-%m-%d").tolist(), "trm": h.trm.round(2).tolist()}


@app.get("/api/model")
def model_info():
    bundle, _ = state()
    return bundle["meta"]


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "index.html")
