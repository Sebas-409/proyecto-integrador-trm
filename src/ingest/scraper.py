"""Módulo de web scraping (requisito R6).

Función concreta: extraer a diario los titulares económicos de La República
(secciones Finanzas, Economía y Globoeconomía) y convertirlos en un indicador de
"sentimiento cambiario" del día:

- n_titulares_dolar: cuántos titulares hablan del dólar / tasa de cambio.
- sentimiento_dolar: balance de palabras que suelen asociarse a presión alcista
  (devaluación del peso) frente a bajista.

Estos indicadores se guardan en S3 cada día, se muestran en el tablero como
contexto de la predicción y se incorporan como variable explicativa cuando hay
historia suficiente. Las APIs no traen esta información cualitativa: por eso
el scraping está justificado.

Solo usa la librería estándar (html.parser) para correr en Lambda.
Respeta robots.txt y hace una sola petición por sección al día.
"""
import re
import unicodedata
from datetime import date
from html.parser import HTMLParser

from .http import get_text

DOLLAR_TERMS = ("dolar", "trm", "tasa de cambio", "divisa", "peso colombiano", "devaluacion", "revaluacion")
UP_TERMS = ("sube", "subio", "alza", "dispara", "devaluacion", "encarece", "presion", "incertidumbre",
            "riesgo", "deficit", "rebaja de calificacion", "caida del petroleo", "aranceles", "crisis")
DOWN_TERMS = ("baja", "bajo", "cae", "cayo", "descenso", "revaluacion", "abarata", "fortalece",
              "superavit", "inversion extranjera", "alivio", "repunte del petroleo", "recorte de tasas de la fed")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


class _HeadlineParser(HTMLParser):
    """Captura el texto de los <a> dentro de <h2 class="tt"> (estructura de La República)."""

    def __init__(self):
        super().__init__()
        self.in_h2 = False
        self.in_a = False
        self.href = ""
        self.buf = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h2", "h3") and "tt" in (a.get("class") or "").split():
            self.in_h2 = True
        elif tag == "a" and self.in_h2:
            self.in_a, self.href, self.buf = True, a.get("href", ""), []

    def handle_endtag(self, tag):
        if tag == "a" and self.in_a:
            title = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if title:
                self.items.append({"title": title, "url": self.href})
            self.in_a = False
        elif tag in ("h2", "h3"):
            self.in_h2 = False

    def handle_data(self, data):
        if self.in_a:
            self.buf.append(data)


def scrape_headlines(sections: list[str]) -> list[dict]:
    seen, out = set(), []
    for url in sections:
        parser = _HeadlineParser()
        parser.feed(get_text(url))
        for it in parser.items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            it["section"] = url.rstrip("/").rsplit("/", 1)[-1]
            out.append(it)
    return out


def sentiment_score(headlines: list[dict]) -> dict:
    n_dollar, up, down = 0, 0, 0
    for h in headlines:
        t = _norm(h["title"])
        if any(term in t for term in DOLLAR_TERMS):
            n_dollar += 1
        up += sum(term in t for term in UP_TERMS)
        down += sum(term in t for term in DOWN_TERMS)
    total = up + down
    return {
        "date": date.today().isoformat(),
        "n_titulares": len(headlines),
        "n_titulares_dolar": n_dollar,
        "sentimiento_dolar": round((up - down) / total, 4) if total else 0.0,
    }


if __name__ == "__main__":
    import json

    from src.config import NEWS_SECTIONS

    hl = scrape_headlines(NEWS_SECTIONS)
    print(json.dumps(sentiment_score(hl), ensure_ascii=False, indent=2))
    for h in hl[:10]:
        print("-", h["title"])
