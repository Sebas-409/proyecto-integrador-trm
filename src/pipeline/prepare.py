"""Fases KDD de preprocesamiento y transformación.

- Limpieza: tipos, duplicados, fechas inválidas, valores faltantes, atípicos.
- Integración: une la TRM con los mercados usando merge_asof estricto (solo se usa
  información publicada ANTES de cada fecha de TRM, para evitar fuga de datos).
- Ingeniería de atributos: retornos logarítmicos, rezagos, volatilidad, medias móviles,
  z-score, calendario.
- Objetivos: y_ret (retorno logarítmico de la siguiente TRM) e y_up (sube = 1).

Uso:  python -m src.pipeline.prepare
"""
import json

import numpy as np
import pandas as pd

from src.config import BINANCE_SYMBOLS, DATA_PROCESSED, DATA_RAW, REPORTS, YAHOO_SYMBOLS

START = "2017-09-01"  # primera fecha con todas las fuentes (Binance inicia en ago-2017)
MARKETS = list(YAHOO_SYMBOLS) + list(BINANCE_SYMBOLS)


def load_trm() -> pd.DataFrame:
    df = pd.read_json(DATA_RAW / "trm.json")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna().drop_duplicates("date").sort_values("date")
    return df.rename(columns={"value": "trm"})


def load_market(name: str) -> pd.DataFrame:
    prefix = "crypto" if name in BINANCE_SYMBOLS else "market"
    df = pd.read_csv(DATA_RAW / f"{prefix}_{name}.csv", parse_dates=["date"])
    df = df[["date", "value"]].dropna().drop_duplicates("date").sort_values("date")
    return df.rename(columns={"value": name})


def quality_report(trm: pd.DataFrame, markets: dict[str, pd.DataFrame]) -> dict:
    rep = {"trm": {"rows": len(trm), "from": str(trm.date.min().date()), "to": str(trm.date.max().date()),
                   "min": float(trm.trm.min()), "max": float(trm.trm.max())}}
    for name, df in markets.items():
        r = np.log(df[name]).diff().dropna()
        q1, q3 = r.quantile([0.25, 0.75])
        iqr = q3 - q1
        rep[name] = {"rows": len(df), "from": str(df.date.min().date()), "to": str(df.date.max().date()),
                     "nonpositive": int((df[name] <= 0).sum()),
                     "outlier_returns_iqr3": int(((r < q1 - 3 * iqr) | (r > q3 + 3 * iqr)).sum())}
    return rep


def make_features(trm: pd.DataFrame, markets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Variables explicativas para cada fecha de TRM. La usan el entrenamiento y la API (inferencia)."""
    df = trm.copy()
    lr = np.log(df.trm).diff()
    for k in range(1, 6):
        df[f"trm_ret_lag{k}"] = lr.shift(k - 1)
    df["trm_ret_5d"] = np.log(df.trm).diff(5)
    df["trm_vol_5"] = lr.rolling(5).std()
    df["trm_vol_20"] = lr.rolling(20).std()
    df["trm_ma_ratio"] = df.trm.rolling(5).mean() / df.trm.rolling(20).mean() - 1
    df["trm_z20"] = (df.trm - df.trm.rolling(20).mean()) / df.trm.rolling(20).std()
    df["dow"] = df.date.dt.dayofweek
    df["month"] = df.date.dt.month

    for name, m in markets.items():
        m = m[m[name] > 0].copy()
        m[f"{name}_ret_1"] = np.log(m[name]).diff()
        m[f"{name}_ret_5"] = np.log(m[name]).diff(5)
        m[f"{name}_vol_20"] = m[f"{name}_ret_1"].rolling(20).std()
        cols = ["date", f"{name}_ret_1", f"{name}_ret_5", f"{name}_vol_20"]
        if name == "usdcop":
            cols.append(name)  # se necesita el nivel para calcular la brecha spot - TRM
        # allow_exact_matches=False: para la TRM de la fecha d solo se usa el cierre de mercado anterior a d.
        df = pd.merge_asof(df.sort_values("date"), m[cols].sort_values("date"), on="date",
                           direction="backward", allow_exact_matches=False, tolerance=pd.Timedelta("7D"))

    # La TRM de la fecha d es el promedio de las operaciones del día hábil anterior. La brecha entre el
    # último cierre spot y esa TRM anticipa hacia dónde se moverá el siguiente promedio.
    df["spot_gap"] = np.log(df.pop("usdcop") / df.trm)
    return df


def build_features(trm: pd.DataFrame, markets: dict[str, pd.DataFrame]) -> pd.DataFrame:
    df = make_features(trm, markets)

    # Objetivos: la TRM siguiente frente a la actual.
    df["y_ret"] = np.log(df.trm.shift(-1) / df.trm)
    df["y_up"] = (df.y_ret > 0).astype(int)

    df = df[df.date >= START]
    df = df.iloc[:-1]  # la última fila no tiene objetivo todavía
    before = len(df)
    df = df.dropna()
    print(f"Filas tras eliminar faltantes: {len(df)} (se descartaron {before - len(df)})")
    return df.reset_index(drop=True)


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ("date", "trm", "y_ret", "y_up")]


def main():
    trm = load_trm()
    markets = {name: load_market(name) for name in MARKETS}
    rep = quality_report(trm, markets)
    (REPORTS / "data_quality.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")

    df = build_features(trm, markets)
    df.to_csv(DATA_PROCESSED / "dataset.csv", index=False)
    print(f"Dataset final: {df.shape[0]} filas x {len(feature_columns(df))} variables "
          f"({df.date.min().date()} -> {df.date.max().date()}); % días al alza: {df.y_up.mean():.3f}")


if __name__ == "__main__":
    main()
