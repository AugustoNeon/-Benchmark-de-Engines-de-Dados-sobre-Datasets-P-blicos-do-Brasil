"""Gera os graficos do README a partir de results/benchmark_results.csv.

Escolhas de visualizacao:
  - Um painel por query com barras horizontais em escala LINEAR propria:
    barra em escala log mente sobre proporcao, e uma escala unica p/ todas
    as queries esconderia as diferencas nas queries rapidas.
  - Cor fixa por engine em todos os graficos (a cor segue a entidade).
  - Rotulo de valor direto em cada barra; grade e eixos recessivos.

Uso:
    python scripts/make_charts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
RESULTS_CSV = BASE / "results" / "benchmark_results.csv"
CHARTS = BASE / "results" / "charts"

# Paleta categorica validada (CVD-safe na ordem dada); cor fixa por engine.
COLORS = {
    "pandas": "#2a78d6",
    "sqlite": "#1baf7a",
    "duckdb": "#eda100",
    "polars": "#008300",
}
ENGINE_ORDER = ["pandas", "sqlite", "duckdb", "polars"]
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "text.color": TEXT,
        "axes.edgecolor": TEXT_2,
        "axes.labelcolor": TEXT_2,
        "xtick.color": TEXT_2,
        "ytick.color": TEXT_2,
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def fmt_ms(seconds: float) -> str:
    ms = seconds * 1000
    if ms < 10:
        return f"{ms:.1f} ms"
    return f"{ms:,.0f} ms" if ms < 10_000 else f"{seconds:.1f} s"


def hbars(ax, medians: pd.Series, fmt=fmt_ms) -> None:
    """Barras horizontais por engine, com rotulo direto no valor."""
    engines = [e for e in ENGINE_ORDER if e in medians.index]
    values = [medians[e] for e in engines]
    ax.barh(engines, values, color=[COLORS[e] for e in engines], height=0.55)
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) * 1.28)
    for i, v in enumerate(values):
        ax.text(v + max(values) * 0.02, i, fmt(v), va="center", fontsize=10, color=TEXT)
    ax.xaxis.set_visible(False)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False)


def chart_queries(df: pd.DataFrame) -> None:
    q = df[(df["fase"] == "query") & (df["run"] > 0)]
    med = q.groupby(["query", "engine"])["tempo_s"].median()
    queries = sorted(q["query"].unique())

    fig, axes = plt.subplots(3, 2, figsize=(11, 9))
    for ax, name in zip(axes.flat, queries):
        hbars(ax, med[name])
        ax.set_title(name, fontsize=11, loc="left", color=TEXT)
    for ax in axes.flat[len(queries):]:
        ax.set_visible(False)
    fig.suptitle(
        "Tempo mediano por query (menor = melhor; escala propria por painel)",
        fontsize=13, x=0.02, ha="left",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(CHARTS / "tempo_queries.png", dpi=200)
    plt.close(fig)


def chart_load(df: pd.DataFrame) -> None:
    load = df[df["fase"] == "carga"].set_index("engine")["tempo_s"]
    fig, ax = plt.subplots(figsize=(8, 3))
    hbars(ax, load, fmt=lambda s: f"{s:.1f} s")
    ax.set_title(
        "Tempo de carga: Parquet -> engine pronta para consulta",
        fontsize=13, loc="left",
    )
    fig.tight_layout()
    fig.savefig(CHARTS / "tempo_carga.png", dpi=200)
    plt.close(fig)


def chart_memory(df: pd.DataFrame) -> None:
    peak = df.groupby("engine")["mem_mb"].max()
    fig, ax = plt.subplots(figsize=(8, 3))
    hbars(ax, peak, fmt=lambda v: f"{v:,.0f} MB")
    ax.set_title(
        "Pico de memoria do processo (carga + queries)", fontsize=13, loc="left"
    )
    fig.tight_layout()
    fig.savefig(CHARTS / "memoria_pico.png", dpi=200)
    plt.close(fig)


def main() -> int:
    if not RESULTS_CSV.exists():
        print(f"{RESULTS_CSV} nao existe. Rode run_benchmark.py antes.")
        return 1
    CHARTS.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(RESULTS_CSV)
    chart_queries(df)
    chart_load(df)
    chart_memory(df)
    print(f"Graficos gerados em {CHARTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
