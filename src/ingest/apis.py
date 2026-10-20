"""Adquisición de datos desde APIs (JSON).

Fuentes:
- TRM oficial: datos.gov.co (Superintendencia Financiera), API Socrata.
- Mercados globales: Yahoo Finance chart API.
- Criptoactivos: Binance API pública (velas diarias).

Cada función devuelve una lista de dicts {"date": "YYYY-MM-DD", "value": float}
para que sirva igual en local (pandas) y en Lambda (sin pandas).
"""
from datetime import datetime, timezone
from urllib.parse import urlencode

from .http import get_json

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"


def fetch_trm(since: str | None = None) -> list[dict]:
    """TRM diaria. Cada registro trae vigenciadesde/vigenciahasta; se toma la fecha de inicio."""
    params = {"$select": "valor,vigenciadesde", "$order": "vigenciadesde ASC", "$limit": 50000}
    if since:
        params["$where"] = f"vigenciadesde >= '{since}T00:00:00'"
    rows = get_json(f"{TRM_URL}?{urlencode(params)}")
    return [
        {"date": r["vigenciadesde"][:10], "value": float(r["valor"])}
        for r in rows
        if r.get("valor")
    ]


def fetch_yahoo(symbol: str, start: str = "2007-01-01") -> list[dict]:
    # Con range=max Yahoo agrega a velas mensuales; con period1/period2 conserva la frecuencia diaria.
    period1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.now(timezone.utc).timestamp())
    params = {"period1": period1, "period2": period2, "interval": "1d"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{urlencode(params)}"
    res = get_json(url)["chart"]["result"][0]
    stamps = res.get("timestamp", [])
    closes = res["indicators"]["quote"][0]["close"]
    out = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({"date": d, "value": float(close)})
    return out


def fetch_binance(symbol: str, start: str = "2017-08-17", limit_days: int | None = None) -> list[dict]:
    """Velas diarias paginadas (1000 por petición)."""
    base = "https://api.binance.com/api/v3/klines"
    start_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    out = []
    while True:
        params = {"symbol": symbol, "interval": "1d", "startTime": start_ms, "limit": 1000}
        kl = get_json(f"{base}?{urlencode(params)}")
        if not kl:
            break
        for k in kl:
            d = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            out.append({"date": d, "value": float(k[4]), "volume": float(k[5])})
        if len(kl) < 1000:
            break
        start_ms = kl[-1][0] + 86_400_000
    return out[-limit_days:] if limit_days else out
