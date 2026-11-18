"""API REST de predicción de la TRM + tablero web.

- Modelo: s3://$DATA_BUCKET/models/model.joblib (en AWS) o models/model.joblib (local).
- Datos: PostgreSQL (RDS) si DATABASE_URL está definido; si no, data/raw/ (local).

Ejecutar en local:  uvicorn api.app:app --reload --port 8000
"""
import io
import json
import math
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import joblib
import numpy as np
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


# ---------------- Importadores: ¿compro hoy o mañana? ----------------
# Para quien trae mercancía de EE. UU. con casillero y la vende en pesos. Sin impuestos por ahora.

CASILLERO_USD_KG = 10.0  # tarifa fija por kg; se cobra el kg completo (0,3 kg = 1 kg)
REGIMES = json.loads((Path(__file__).parent / "regimes.json").read_text(encoding="utf-8"))
REGLA = json.loads((Path(__file__).parent / "importador_regla.json").read_text(encoding="utf-8"))


def regime_of(row: pd.Series) -> str | None:
    """Régimen K-Means del día por centroide más cercano (mismo recorte y escala que elbow.py)."""
    x = row[REGIMES["features"]].to_numpy(dtype=float)
    if np.isnan(x).any():
        return None
    z = (np.clip(x, REGIMES["clip_lo"], REGIMES["clip_hi"]) - REGIMES["mean"]) / REGIMES["scale"]
    return REGIMES["names"][int(np.argmin(((np.array(REGIMES["centroids"]) - z) ** 2).sum(axis=1)))]


def num(x: float, d: int = 2) -> str:
    """Formato colombiano: 3.156,32"""
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def cop(x: float) -> str:
    return "$" + num(x, 0)


def usd_total(precio_usd: float, peso_kg: float) -> tuple[float, int]:
    kg = max(1, math.ceil(peso_kg - 1e-9))
    return precio_usd + CASILLERO_USD_KG * kg, kg


def decide(usd: float, venta_cop: float, margen_min: float, recargo: float,
           trm_hoy: float, trm_exp: float, mae: float, prob_up: float, regimen: str | None) -> dict:
    """Regla de decisión (validada en src/pipeline/evaluate_importador.py): comprar hoy, esperar a mañana o no es viable.

    Esperar solo si P(baja) >= umbral y el mercado no está en Crisis. La TRM esperada del regresor y su
    banda de error (MAE) dan el rango de costo de mañana; el ahorro típico sale del backtest.
    """
    def esc(trm):
        costo = usd * trm * (1 + recargo)
        return {"trm": round(trm, 2), "costo_cop": round(costo), "margen_cop": round(venta_cop - costo),
                "margen_pct": round((venta_cop - costo) / venta_cop * 100, 2)}

    hoy, man = esc(trm_hoy), esc(trm_exp)
    rango = {"optimista": esc(trm_exp * (1 - mae)), "pesimista": esc(trm_exp * (1 + mae))}
    p_baja = 1 - prob_up
    crisis = bool(regimen and regimen.startswith("Crisis"))
    esperar = p_baja >= REGLA["umbral_p_baja"] and not crisis
    ahorro_tipico = round(usd / 1000 * REGLA["ahorro_cop_por_1000usd_cuando_espera"] * (1 + recargo))
    ok_hoy = hoy["margen_pct"] >= margen_min * 100
    ok_optimista = rango["optimista"]["margen_pct"] >= margen_min * 100
    venta_min = round(hoy["costo_cop"] / (1 - margen_min))
    acierto = f"{REGLA['acierto_al_esperar']*100:.0f} %"

    if not ok_hoy and not (esperar and ok_optimista):
        d = "no_viable"
        why = (f"Con la TRM de hoy el margen es {num(hoy['margen_pct'], 1)} % y pides al menos {margen_min*100:.0f} %. "
               f"Para que sea viable tendrías que vender en {cop(venta_min)} o conseguir el producto más barato.")
    elif esperar:
        d = "esperar"
        why = (f"El modelo da {num(p_baja*100, 0)} % de probabilidad de que la TRM baje mañana. "
               f"En las pruebas, cuando dio esta señal acertó el {acierto} de las veces y el ahorro típico "
               f"para esta compra sería de unos {cop(ahorro_tipico)}.")
        if not ok_hoy:
            why += " Hoy no da el margen mínimo; mañana posiblemente sí."
    else:
        d = "comprar_hoy"
        if crisis:
            why = "El mercado está en régimen de Crisis / alta volatilidad: esperar es arriesgado aunque el modelo vea una baja."
        elif prob_up >= 0.5:
            why = (f"El modelo da {num(prob_up*100, 0)} % de probabilidad de que la TRM suba mañana: "
                   f"esperar puede encarecer la compra.")
        else:
            why = (f"La probabilidad de baja ({num(p_baja*100, 0)} %) no es suficiente para arriesgar la venta "
                   f"esperando; la señal pide al menos {REGLA['umbral_p_baja']*100:.0f} %.")
    return {"decision": d, "explicacion": why, "hoy": hoy, "manana": man, "manana_rango": rango,
            "prob_baja": round(p_baja, 4), "ahorro_tipico_esperar_cop": ahorro_tipico,
            "venta_minima_cop": venta_min, "regimen": regimen}


class Compra(BaseModel):
    precio_usd: float = Field(gt=0, le=100_000, description="Precio del producto en la tienda de EE. UU.")
    peso_kg: float = Field(gt=0, le=500)
    venta_cop: float = Field(gt=0, description="Precio al que lo vendes en Colombia")
    margen_min_pct: float = Field(10, ge=0, lt=100)
    recargo_banco_pct: float = Field(3, ge=0, le=20, description="Lo que el banco cobra sobre la TRM oficial")


@app.post("/api/importador/decision")
def importador_decision(c: Compra):
    bundle, feats = state()
    row = feats.tail(1)
    p = predict_row(row, bundle)
    usd, kg = usd_total(c.precio_usd, c.peso_kg)
    mae = bundle["meta"]["test_metrics"]["regressor"]["test_mae"]
    d = decide(usd, c.venta_cop, c.margen_min_pct / 100, c.recargo_banco_pct / 100,
               p["trm_base"], p["trm_expected"], mae, p["prob_up"], regime_of(row.iloc[0]))
    return {**d, "regla": REGLA, "usd_total": usd, "kg_cobrados": kg, "casillero_usd": CASILLERO_USD_KG * kg,
            "base_date": p["base_date"], "prob_up": p["prob_up"]}


class Pedido(BaseModel):
    producto: str = Field("", max_length=120)
    precio_usd: float = Field(gt=0, le=100_000)
    peso_kg: float = Field(gt=0, le=500)
    venta_cop: float = Field(gt=0)
    fecha_venta: date = Field(description="Día en que le cotizaste o vendiste al cliente en pesos")
    fecha_compra: date = Field(description="Día en que pagaste en la tienda de EE. UU.")
    trm_banco: float | None = Field(None, gt=1000, lt=20_000, description="TRM que te liquidó el banco (opcional)")


class Historial(BaseModel):
    pedidos: list[Pedido] = Field(max_length=500)
    recargo_banco_pct: float = Field(3, ge=0, le=20)
    margen_min_pct: float = Field(10, ge=0, lt=100)


@app.post("/api/importador/historial")
def importador_historial(h: Historial):
    """Analiza los pedidos del importador contra la TRM oficial y la señal que daba el modelo ese día."""
    bundle, feats = state()
    f = feats.set_index("date").sort_index()
    mae = bundle["meta"]["test_metrics"]["regressor"]["test_mae"]
    recargo = h.recargo_banco_pct / 100

    def trm_on(d):  # TRM vigente ese día (la última certificada hasta esa fecha)
        s = f.trm.loc[:pd.Timestamp(d)]
        return float(s.iloc[-1]) if len(s) else None

    # Señales de todos los días de compra en un solo lote (predecir pedido por pedido es lento)
    base_of = {}
    for d in {p.fecha_compra for p in h.pedidos}:
        idx = f.index[f.index <= pd.Timestamp(d)]
        if len(idx) and (pd.Timestamp(d) - idx[-1]).days <= 4:
            base_of[d] = idx[-1]
    senales = {}
    if base_of:
        rows = f.loc[sorted(set(base_of.values()))]
        rows = rows[rows[bundle["meta"]["features"]].notna().all(axis=1)]
        if len(rows):
            X = rows[bundle["meta"]["features"]]
            probs = bundle["classifier"].predict_proba(X)[:, 1]
            rets = bundle["regressor"].predict(X)
            for (d, row), pu, r_ in zip(rows.iterrows(), probs, rets):
                senales[d] = (float(pu), float(row.trm) * (1 + float(r_)), regime_of(row))

    out = []
    for i, pd_ in enumerate(h.pedidos):
        usd, kg = usd_total(pd_.precio_usd, pd_.peso_kg)
        trm_v, trm_c = trm_on(pd_.fecha_venta), trm_on(pd_.fecha_compra)
        r = {"i": i, "producto": pd_.producto, "usd_total": usd, "kg_cobrados": kg,
             "fecha_venta": str(pd_.fecha_venta), "fecha_compra": str(pd_.fecha_compra)}
        if trm_v is None or trm_c is None:
            out.append({**r, "error": "No hay TRM para esas fechas"}); continue
        trm_pag = pd_.trm_banco or trm_c * (1 + recargo)
        costo = usd * trm_pag
        r.update({"trm_venta": round(trm_v, 2), "trm_compra": round(trm_c, 2), "trm_pagada": round(trm_pag, 2),
                  "trm_pagada_estimada": pd_.trm_banco is None,
                  "costo_cop": round(costo), "margen_cop": round(pd_.venta_cop - costo),
                  "margen_pct": round((pd_.venta_cop - costo) / pd_.venta_cop * 100, 2),
                  "trm_se_comio_cop": round(usd * (trm_c - trm_v) * (1 + recargo)),
                  "recargo_banco_pct": round((trm_pag / trm_c - 1) * 100, 2)})
        # Señal del modelo el día de la compra (recalculada con el modelo actual)
        sig = None
        base = base_of.get(pd_.fecha_compra)
        if base is not None and base in senales:
            pu, te, rg = senales[base]
            sig = decide(usd, pd_.venta_cop, h.margen_min_pct / 100, recargo, trm_c, te, mae, pu, rg)
        sig_d = sig["decision"] if sig else None
        sig_d = "comprar_hoy" if sig_d == "no_viable" else sig_d  # ya estaba vendido: la pregunta es solo cuándo pagar
        siguiente = f.trm.loc[pd.Timestamp(pd_.fecha_compra) + pd.Timedelta(days=1):]
        trm_sig = float(siguiente.iloc[0]) if len(siguiente) else None
        # Positivo = seguir la señal habría ahorrado plata; negativo = la señal se equivocó
        impacto = None
        if sig_d == "esperar" and trm_sig is not None:
            impacto = round(usd * (trm_c - trm_sig) * (1 + recargo))
        elif sig_d == "comprar_hoy":
            impacto = 0
        r.update({"senal": sig_d, "senal_trm_esperada": sig["manana"]["trm"] if sig else None,
                  "senal_regimen": sig["regimen"] if sig else None,
                  "trm_dia_siguiente": round(trm_sig, 2) if trm_sig else None, "impacto_senal_cop": impacto})
        out.append(r)

    ok = [r for r in out if "error" not in r]
    resumen = {"pedidos": len(ok), "venta_cop": round(sum(h.pedidos[r["i"]].venta_cop for r in ok)),
               "margen_cop": sum(r["margen_cop"] for r in ok),
               "trm_se_comio_cop": sum(r["trm_se_comio_cop"] for r in ok),
               "recargo_banco_cop": round(sum(r["usd_total"] * (r["trm_pagada"] - r["trm_compra"]) for r in ok)),
               "impacto_senal_cop": sum(r["impacto_senal_cop"] or 0 for r in ok),
               "datos_hasta": str(f.index.max().date())}
    return {"resumen": resumen, "pedidos": out}


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
