"""Lambda de ingesta diaria (FaaS), disparada por EventBridge al cierre del mercado.

Descarga la TRM, los mercados, las criptos y los titulares (web scraping) y deja
un único paquete JSON en s3://<bucket>/data/raw/daily/<fecha>.json.
Esa escritura dispara la Lambda de ETL que carga los datos en RDS.

Evento opcional {"backfill": true}: descarga toda la historia (primer despliegue).
"""
import json
import os
from datetime import date, timedelta

import boto3

from src.ingest.apis import fetch_binance, fetch_trm, fetch_yahoo
from src.ingest.scraper import scrape_headlines, sentiment_score

BUCKET = os.environ["DATA_BUCKET"]
YAHOO = json.loads(os.environ["YAHOO_SYMBOLS"])
BINANCE = json.loads(os.environ["BINANCE_SYMBOLS"])
NEWS = json.loads(os.environ["NEWS_SECTIONS"])


def handler(event, context):
    backfill = bool((event or {}).get("backfill"))
    since = "2017-01-01" if backfill else (date.today() - timedelta(days=45)).isoformat()

    bundle = {"run_date": date.today().isoformat(), "backfill": backfill, "trm": fetch_trm(since), "markets": {}}
    for name, sym in YAHOO.items():
        bundle["markets"][name] = [r for r in fetch_yahoo(sym, start=since)]
    for name, sym in BINANCE.items():
        bundle["markets"][name] = fetch_binance(sym, start=since)

    errors = []
    try:
        headlines = scrape_headlines(NEWS)
        bundle["sentiment"] = sentiment_score(headlines)
        bundle["headlines"] = headlines
    except Exception as err:  # el scraping no debe tumbar la ingesta de datos numéricos
        errors.append(f"scraping: {err}")
    bundle["errors"] = errors

    key = f"data/raw/daily/{bundle['run_date']}{'-backfill' if backfill else ''}.json"
    boto3.client("s3").put_object(Bucket=BUCKET, Key=key, Body=json.dumps(bundle, ensure_ascii=False).encode("utf-8"),
                                  ContentType="application/json")
    summary = {"key": key, "trm_rows": len(bundle["trm"]),
               "markets": {k: len(v) for k, v in bundle["markets"].items()}, "errors": errors}
    print(json.dumps(summary))
    return summary
