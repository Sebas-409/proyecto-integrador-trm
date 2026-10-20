"""Fase 1 (KDD: selección/adquisición). Descarga la historia completa de todas las fuentes.

Uso:  python -m src.ingest.run
Formatos de salida en data/raw/: JSON (TRM y titulares) y CSV (mercados y cripto).
"""
import csv
import io
import json
from datetime import date

from src.config import BINANCE_SYMBOLS, NEWS_SECTIONS, YAHOO_SYMBOLS
from src.ingest.apis import fetch_binance, fetch_trm, fetch_yahoo
from src.ingest.scraper import scrape_headlines, sentiment_score
from src.storage import save


def _to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def main():
    trm = fetch_trm()
    save("data/raw/trm.json", json.dumps(trm).encode("utf-8"))
    print(f"TRM: {len(trm)} registros ({trm[0]['date']} -> {trm[-1]['date']})")

    for name, symbol in YAHOO_SYMBOLS.items():
        rows = fetch_yahoo(symbol)
        save(f"data/raw/market_{name}.csv", _to_csv(rows))
        print(f"{name} ({symbol}): {len(rows)} registros")

    for name, symbol in BINANCE_SYMBOLS.items():
        rows = fetch_binance(symbol)
        save(f"data/raw/crypto_{name}.csv", _to_csv(rows))
        print(f"{name} ({symbol}): {len(rows)} registros")

    headlines = scrape_headlines(NEWS_SECTIONS)
    today = date.today().isoformat()
    save(f"data/raw/news/headlines_{today}.json", json.dumps(headlines, ensure_ascii=False).encode("utf-8"))
    score = sentiment_score(headlines)
    save(f"data/raw/news/sentiment_{today}.json", json.dumps(score).encode("utf-8"))
    print(f"Titulares: {len(headlines)} | sentimiento: {score}")


if __name__ == "__main__":
    main()
