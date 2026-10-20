"""Lambda de ETL: se dispara cuando llega un paquete a data/raw/daily/ y lo carga en PostgreSQL (RDS).

Corre dentro de la VPC (subredes privadas). Lee S3 a través del VPC Gateway Endpoint,
por lo que no necesita NAT ni salida a Internet. Las cargas son idempotentes (UPSERT).
"""
import json
import os
import urllib.parse

import boto3
import pg8000.native

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS trm (
         date DATE PRIMARY KEY, value NUMERIC(12,2) NOT NULL, loaded_at TIMESTAMPTZ DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS market (
         date DATE NOT NULL, name TEXT NOT NULL, value DOUBLE PRECISION NOT NULL,
         PRIMARY KEY (date, name))""",
    """CREATE TABLE IF NOT EXISTS news_sentiment (
         date DATE PRIMARY KEY, n_titulares INT, n_titulares_dolar INT, sentimiento_dolar DOUBLE PRECISION)""",
    """CREATE TABLE IF NOT EXISTS predictions (
         created_at TIMESTAMPTZ DEFAULT now(), base_date DATE NOT NULL, trm_base NUMERIC(12,2),
         prob_up DOUBLE PRECISION, expected_ret DOUBLE PRECISION, trm_expected NUMERIC(12,2), model TEXT,
         PRIMARY KEY (base_date, model))""",
]


def _conn():
    return pg8000.native.Connection(
        host=os.environ["DB_HOST"], database=os.environ["DB_NAME"],
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], timeout=30)


def handler(event, context):
    s3 = boto3.client("s3")
    con = _conn()
    for stmt in SCHEMA:
        con.run(stmt)

    loaded = {}
    for rec in event["Records"]:
        bucket = rec["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(rec["s3"]["object"]["key"])
        bundle = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())

        con.run("BEGIN")
        for r in bundle["trm"]:
            con.run("INSERT INTO trm(date, value) VALUES (:d, :v) "
                    "ON CONFLICT (date) DO UPDATE SET value = EXCLUDED.value, loaded_at = now()",
                    d=r["date"], v=r["value"])
        n_mkt = 0
        for name, rows in bundle["markets"].items():
            for r in rows:
                con.run("INSERT INTO market(date, name, value) VALUES (:d, :n, :v) "
                        "ON CONFLICT (date, name) DO UPDATE SET value = EXCLUDED.value",
                        d=r["date"], n=name, v=r["value"])
                n_mkt += 1
        s = bundle.get("sentiment")
        if s:
            con.run("INSERT INTO news_sentiment VALUES (:d, :a, :b, :c) ON CONFLICT (date) DO UPDATE SET "
                    "n_titulares = EXCLUDED.n_titulares, n_titulares_dolar = EXCLUDED.n_titulares_dolar, "
                    "sentimiento_dolar = EXCLUDED.sentimiento_dolar",
                    d=s["date"], a=s["n_titulares"], b=s["n_titulares_dolar"], c=s["sentimiento_dolar"])
        con.run("COMMIT")
        loaded[key] = {"trm": len(bundle["trm"]), "market": n_mkt}

    con.close()
    print(json.dumps(loaded))
    return loaded
