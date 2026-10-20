"""Cliente HTTP mínimo con la librería estándar.

Se usa urllib (no requests) para que el mismo código corra dentro de AWS Lambda
sin capas ni dependencias adicionales.
"""
import json
import time
import urllib.error
import urllib.request

USER_AGENT = "Mozilla/5.0 (proyecto-integrador-trm; uso academico)"


def get(url: str, retries: int = 3, timeout: int = 30) -> bytes:
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as err:
            last_err = err
            time.sleep(2 ** attempt)
    raise RuntimeError(f"No se pudo descargar {url}: {last_err}")


def get_json(url: str, **kw):
    return json.loads(get(url, **kw).decode("utf-8"))


def get_text(url: str, **kw) -> str:
    raw = get(url, **kw)
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")
