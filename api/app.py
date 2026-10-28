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
from typing import Literal
from urllib.parse import urlparse

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

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


# ---------------- Asistente (Claude) ----------------

CHAT_MODEL = "claude-opus-5-5"
CHAT_LIMIT_PER_HOUR = 30  # por IP y por instancia: la clave paga cada pregunta
_chat = {"client": None, "hits": {}}

CHAT_SYSTEM = """Eres el asistente de TRM Predict, un proyecto académico de la Universidad Santo Tomás Tunja \
que pronostica la TRM (tasa representativa del mercado COP/USD) del siguiente día hábil.

Responde en español, claro y breve (máximo 3 párrafos cortos), en texto plano: la página no interpreta \
Markdown, así que no uses asteriscos, almohadillas ni tablas. Usa solo los datos del contexto que te dan; \
si algo no está ahí, dilo en lugar de inventarlo. No des recomendaciones de inversión personalizadas: \
puedes explicar qué indica el modelo y qué suelen hacer importadores y exportadores, aclarando que es un \
sistema académico de apoyo a decisiones.

Conceptos que debes tener claros:
- La TRM es la tasa oficial que certifica la Superintendencia Financiera para cada día (fuente: datos.gov.co). \
Es un solo valor por día y no es el dólar spot que se ve en Google o en casas de cambio, que cambia todo el día.
- Modelos: clasificador (probabilidad de que la TRM suba), regresor (retorno esperado) y modelo de riesgo \
(probabilidad de un movimiento fuerte). Usan retornos y volatilidades de la TRM, petróleo Brent, índice \
dólar DXY, Tesoro de EE. UU. a 10 años, VIX, S&P 500, peso mexicano, real brasileño, Bitcoin y Ether.
- Regímenes de mercado (K-Means, k=4 por el método del codo, 2.159 días de 2017 a 2026): Calma (35 % de los días), \
Apetito por riesgo con peso que se aprecia (35 %), Aversión al riesgo con dólar que sube (28 %) y Crisis / alta \
volatilidad (2 %, marzo a mayo de 2020 por el COVID-19)."""


def chat_client():
    if _chat["client"] is None:
        import anthropic

        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key and BUCKET:  # en AWS la clave vive cifrada en SSM, no en la imagen
            import boto3

            try:
                key = boto3.client("ssm").get_parameter(
                    Name=os.getenv("ANTHROPIC_KEY_PARAM", "/trm-predict/dev/anthropic_api_key"),
                    WithDecryption=True)["Parameter"]["Value"]
            except Exception as err:
                print(f"No se pudo leer la clave del asistente: {err}")
        if not key:
            raise HTTPException(503, "El asistente no está configurado")
        _chat["client"] = anthropic.Anthropic(api_key=key, timeout=60.0)
    return _chat["client"]


def chat_context() -> str:
    bundle, feats = state()
    p = predict_row(feats.tail(1), bundle)
    meta = bundle["meta"]
    h = feats.tail(20)[["date", "trm"]]
    serie = ", ".join(f"{d:%Y-%m-%d}: {v:.2f}" for d, v in zip(h.date, h.trm))
    return (f"Pronóstico actual: {p}\n"
            f"Métricas de prueba: {meta.get('test_metrics')}\n"
            f"Variables más influyentes (SHAP): {meta.get('shap_top10')}\n"
            f"Datos de entrenamiento hasta: {meta.get('data_until')}\n"
            f"TRM oficial de los últimos 20 días hábiles: {serie}")


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatBody(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1, max_length=12)


@app.post("/api/chat")
def chat(body: ChatBody, request: Request):
    import anthropic

    if body.messages[-1].role != "user":
        raise HTTPException(400, "El último mensaje debe ser del usuario")
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0]
    now = time.time()
    hits = [t for t in _chat["hits"].get(ip, []) if now - t < 3600]
    if len(hits) >= CHAT_LIMIT_PER_HOUR:
        raise HTTPException(429, "Límite de preguntas por hora alcanzado; intenta más tarde")
    _chat["hits"][ip] = hits + [now]

    msgs = [m.model_dump() for m in body.messages]
    msgs[0]["content"] = f"<contexto>\n{chat_context()}\n</contexto>\n\n{msgs[0]['content']}"
    try:
        r = chat_client().beta.messages.create(
            model=CHAT_MODEL, max_tokens=2000, system=CHAT_SYSTEM, messages=msgs,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.RateLimitError:
        raise HTTPException(429, "El asistente está ocupado; intenta en un minuto")
    except anthropic.APIStatusError as err:
        print(f"Error de Claude {err.status_code}: {err.message}")
        raise HTTPException(502, "El asistente no respondió")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "No se pudo contactar al asistente")
    if r.stop_reason == "refusal":
        return {"reply": "No puedo responder esa pregunta. Pregúntame sobre la TRM o el modelo."}
    return {"reply": "".join(b.text for b in r.content if b.type == "text").strip()}


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "index.html")
