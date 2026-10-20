#!/usr/bin/env bash
# Empaqueta las Lambdas en build/ingest y build/etl (Terraform las comprime con archive_file).
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf build && mkdir -p build/ingest/src/ingest build/etl

# Ingesta: solo librería estándar + boto3 (ya incluido en el runtime de Lambda)
cp lambdas/ingest/handler.py build/ingest/
touch build/ingest/src/__init__.py
cp src/ingest/__init__.py src/ingest/http.py src/ingest/apis.py src/ingest/scraper.py build/ingest/src/ingest/

# ETL: necesita pg8000 (driver PostgreSQL en Python puro, sin binarios)
cp lambdas/etl/handler.py build/etl/
python -m pip install --quiet --target build/etl pg8000==1.31.5

echo "Paquetes listos en build/"
