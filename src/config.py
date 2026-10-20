"""Configuración central del proyecto (rutas, fuentes y parámetros)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
MODELS = ROOT / "models"
FIGURES = ROOT / "reports" / "figures"
REPORTS = ROOT / "reports"

for p in (DATA_RAW, DATA_PROCESSED, MODELS, FIGURES):
    p.mkdir(parents=True, exist_ok=True)

# Si DATA_BUCKET está definido (en AWS), los datos crudos y el modelo se guardan también en S3.
DATA_BUCKET = os.getenv("DATA_BUCKET", "")


# Variables de mercado (Yahoo Finance chart API): nombre -> símbolo
YAHOO_SYMBOLS = {
    "brent": "BZ=F",        # petróleo Brent: principal exportación de Colombia
    "dxy": "DX-Y.NYB",      # índice dólar global
    "us10y": "^TNX",        # tasa del Tesoro de EE. UU. a 10 años
    "vix": "^VIX",          # aversión al riesgo
    "sp500": "^GSPC",       # renta variable global
    "usdmxn": "MXN=X",      # moneda par emergente (México)
    "usdbrl": "BRL=X",      # moneda par emergente (Brasil)
    "usdcop": "COP=X",      # dólar spot en Colombia (cotización de cierre, no oficial)
}

# Criptoactivos (Binance, API pública de velas diarias)
BINANCE_SYMBOLS = {"btc": "BTCUSDT", "eth": "ETHUSDT"}

# Web scraping: titulares económicos (La República)
NEWS_SECTIONS = [
    "https://www.larepublica.co/finanzas",
    "https://www.larepublica.co/economia",
    "https://www.larepublica.co/globoeconomia",
]

START_DATE = "2010-01-01"
RANDOM_STATE = 42
TEST_FRACTION = 0.2
