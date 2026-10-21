"""Figuras de los experimentos de nube para el reporte (en español e inglés).

Uso:  python scripts/experiments/plot.py
Lee reports/experiments/{run1_min1,run2_min2}/ha_probes.csv y load_timeline.csv.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

EXP = Path(__file__).resolve().parents[2] / "reports" / "experiments"

TEXT = {
    "es": {
        "min": "ASG mín. = {}", "avail": "{}: disponibilidad {:.1f} %", "lat": "Latencia (ms)",
        "t": "Tiempo (s); línea punteada = instancia terminada",
        "p95": "Latencia p95 (ms)", "p50": "Latencia p50 (ms)", "minutes": "Minutos de carga",
        "healthy": "Instancias sanas", "desired": "Capacidad deseada", "inst": "Instancias",
    },
    "en": {
        "min": "ASG min. = {}", "avail": "{}: availability {:.1f}%", "lat": "Latency (ms)",
        "t": "Time (s); dashed line = instance terminated",
        "p95": "p95 latency (ms)", "p50": "p50 latency (ms)", "minutes": "Minutes under load",
        "healthy": "Healthy instances", "desired": "Desired capacity", "inst": "Instances",
    },
}

for lang, tx in TEXT.items():
    suffix = "" if lang == "es" else "_en"
    runs = [("run1_min1", tx["min"].format(1)), ("run2_min2", tx["min"].format(2))]
    fig, axes = plt.subplots(len(runs), 1, figsize=(7, 4.2), sharex=True)
    for ax, (folder, label) in zip(axes, runs):
        p = pd.read_csv(EXP / folder / "ha_probes.csv")
        ok = p[p.status == 200]
        bad = p[p.status != 200]
        ax.scatter(ok.t_s, ok.latency_ms, s=3, label="200 OK")
        ax.scatter(bad.t_s, [0] * len(bad), s=6, color="tab:red", marker="x", label="error")
        ax.axvline(30, color="k", ls="--", lw=0.8)
        avail = 100 * len(ok) / len(p)
        ax.set_title(tx["avail"].format(label, avail), fontsize=9)
        ax.set_ylabel(tx["lat"])
        ax.legend(fontsize=7, loc="upper right")
    axes[-1].set_xlabel(tx["t"])
    fig.tight_layout()
    fig.savefig(EXP / f"ha_timeline{suffix}.png", dpi=200)
    plt.close(fig)

    load = EXP / "load_timeline.csv"
    if load.exists():
        t = pd.read_csv(load)
        fig, ax = plt.subplots(figsize=(7, 3.2))
        ax.plot(t.t_min, t.p95_ms, marker="o", ms=3, label=tx["p95"])
        ax.plot(t.t_min, t.p50_ms, marker="o", ms=3, label=tx["p50"])
        ax.set_xlabel(tx["minutes"]); ax.set_ylabel("ms")
        ax2 = ax.twinx()
        ax2.step(t.t_min, t.healthy, where="post", color="tab:green", label=tx["healthy"])
        ax2.step(t.t_min, t.desired, where="post", color="tab:gray", ls="--", label=tx["desired"])
        ax2.set_ylabel(tx["inst"]); ax2.set_ylim(0, t.desired.max() + 1)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left")
        fig.tight_layout()
        fig.savefig(EXP / f"load_timeline{suffix}.png", dpi=200)
        plt.close(fig)
print("figuras en", EXP)
