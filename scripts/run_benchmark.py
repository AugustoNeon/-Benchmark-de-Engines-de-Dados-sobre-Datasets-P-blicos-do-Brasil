"""Orquestra o benchmark: carga + queries em cada engine, com medicao.

Decisoes de metodologia (explicadas no README):
  - Cada engine roda em um SUBPROCESSO proprio: a medicao de memoria de uma
    nao contamina a outra e o interprete comeca "frio" para todas.
  - Tempo de CARGA (Parquet -> engine pronta p/ consulta) e medido separado
    do tempo de QUERY: sao perfis diferentes e a carga so acontece uma vez.
  - Cada query roda 5x; a 1a execucao (aquecimento de caches) e descartada
    na analise. Reportamos a mediana das restantes.
  - Memoria = pico de RSS do processo, amostrado a cada 50 ms por thread.
  - CORRETUDE ANTES DE VELOCIDADE: `--check` roda tudo numa amostra e
    compara os resultados das 4 engines linha a linha (DuckDB e referencia).
    Benchmark de resultado errado nao vale nada.

Uso:
    python scripts/run_benchmark.py --check     # corretude na amostra
    python scripts/run_benchmark.py             # benchmark completo
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, time as dtime
from pathlib import Path

import duckdb
import pandas as pd
import polars as pl
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queries import QUERIES, Query  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
RESULTS = BASE / "results"
TABLES = ["empresas", "estabelecimentos", "municipios", "cnaes", "naturezas", "acidentes"]
ENGINES = ["pandas", "sqlite", "duckdb", "polars"]
N_RUNS = 5


def read_parquet_pandas(path: Path) -> pd.DataFrame:
    """pd.read_parquet devolve colunas DATE como objetos datetime.date, que
    nao vetorizam; converte p/ datetime64 (colunas TIME ficam como estao,
    pandas nao tem tipo p/ hora-do-dia)."""
    df = pd.read_parquet(path)
    for col in df.columns:
        if df[col].dtype == object:
            first = df[col].dropna().head(1)
            if not first.empty and type(first.iloc[0]) is date:
                df[col] = pd.to_datetime(df[col])
    return df


# ------------------------------------------------------------------ engines
class DuckDBAdapter:
    name = "duckdb"

    def load(self, pq: Path) -> None:
        self.con = duckdb.connect()
        for t in TABLES:
            self.con.execute(
                f"CREATE TABLE {t} AS SELECT * FROM '{(pq / t).as_posix()}.parquet'"
            )

    def run(self, q: Query) -> list[tuple]:
        return self.con.execute(q.sql_duckdb).fetchall()


class SQLiteAdapter:
    name = "sqlite"

    def load(self, pq: Path) -> None:
        self.con = sqlite3.connect(":memory:")
        for t in TABLES:
            df = read_parquet_pandas(pq / f"{t}.parquet")
            # SQLite nao tem DATE/TIME: converte p/ texto ISO na carga.
            for col in df.columns:
                if pd.api.types.is_datetime64_any_dtype(df[col]):
                    df[col] = df[col].dt.strftime("%Y-%m-%d")
                elif df[col].dtype == object and df[col].map(
                    lambda v: isinstance(v, dtime), na_action="ignore"
                ).any():
                    df[col] = df[col].map(
                        lambda v: v.strftime("%H:%M:%S") if isinstance(v, dtime) else v
                    )
            cols = ", ".join(df.columns)
            marks = ", ".join("?" * len(df.columns))
            self.con.execute(f"CREATE TABLE {t} ({cols})")
            self.con.executemany(
                f"INSERT INTO {t} VALUES ({marks})",
                df.itertuples(index=False, name=None),
            )
        self.con.commit()

    def run(self, q: Query) -> list[tuple]:
        return self.con.execute(q.sql_sqlite).fetchall()


class PandasAdapter:
    name = "pandas"

    def load(self, pq: Path) -> None:
        self.tables = {t: read_parquet_pandas(pq / f"{t}.parquet") for t in TABLES}

    def run(self, q: Query) -> list[tuple]:
        df = q.fn_pandas(self.tables)
        return list(df.itertuples(index=False, name=None))


class PolarsAdapter:
    name = "polars"

    def load(self, pq: Path) -> None:
        self.tables = {t: pl.read_parquet(pq / f"{t}.parquet") for t in TABLES}

    def run(self, q: Query) -> list[tuple]:
        return q.fn_polars(self.tables).rows()


ADAPTERS = {
    a.name: a for a in (DuckDBAdapter, SQLiteAdapter, PandasAdapter, PolarsAdapter)
}


# ------------------------------------------------------------------ medicao
class MemSampler:
    """Amostra o pico de RSS do processo em background."""

    def __init__(self) -> None:
        self.proc = psutil.Process()
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, self.proc.memory_info().rss)
            self._stop.wait(0.05)

    def __enter__(self) -> "MemSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, self.proc.memory_info().rss)


def measure(fn) -> tuple[float, float]:
    """Retorna (tempo em s, pico de memoria em MB) da execucao de fn."""
    with MemSampler() as mem:
        t0 = time.perf_counter()
        fn()
        elapsed = time.perf_counter() - t0
    return elapsed, mem.peak / (1 << 20)


# ------------------------------------------------------------------ corretude
def normalize(rows: list[tuple]) -> list[tuple]:
    def norm(v):
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, float):
            return round(v, 4)
        if isinstance(v, date):
            return v.isoformat()
        return v

    return [tuple(norm(v) for v in row) for row in rows]


def check_correctness(data_dir: Path) -> bool:
    print(f"== Checagem de corretude ({data_dir.name}) ==")
    results: dict[str, dict[str, list[tuple]]] = {}
    for name, cls in ADAPTERS.items():
        adapter = cls()
        adapter.load(data_dir)
        results[name] = {q.nome: normalize(adapter.run(q)) for q in QUERIES}
        print(f"  {name}: {len(QUERIES)} queries executadas")

    ok = True
    for q in QUERIES:
        ref = results["duckdb"][q.nome]
        for engine in ENGINES:
            got = results[engine][q.nome]
            if got != ref:
                ok = False
                print(f"  [DIVERGE] {q.nome}: {engine} != duckdb")
                for i, (r, g) in enumerate(zip(ref, got)):
                    if r != g:
                        print(f"    linha {i}: duckdb={r}  {engine}={g}")
                        break
                if len(ref) != len(got):
                    print(f"    tamanhos: duckdb={len(ref)} {engine}={len(got)}")
    print("Corretude OK: as 4 engines concordam." if ok else "HA DIVERGENCIAS.")
    return ok


# ------------------------------------------------------------------ benchmark
def bench_engine(engine: str, data_dir: Path, out_csv: Path) -> None:
    adapter = ADAPTERS[engine]()
    rows: list[dict] = []

    t, mem = measure(lambda: adapter.load(data_dir))
    rows.append(
        {"engine": engine, "fase": "carga", "query": "-", "run": 0, "tempo_s": t, "mem_mb": mem}
    )
    print(f"[{engine}] carga: {t:.2f}s (pico {mem:.0f} MB)")

    for q in QUERIES:
        for run in range(N_RUNS):
            t, mem = measure(lambda: adapter.run(q))
            rows.append(
                {
                    "engine": engine,
                    "fase": "query",
                    "query": q.nome,
                    "run": run,
                    "tempo_s": t,
                    "mem_mb": mem,
                }
            )
        med = sorted(r["tempo_s"] for r in rows[-N_RUNS + 1 :])[len(rows[-N_RUNS + 1 :]) // 2]
        print(f"[{engine}] {q.nome}: mediana {med * 1000:.0f} ms")

    write_header = not out_csv.exists()
    with out_csv.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="so checa corretude na amostra")
    ap.add_argument("--engine", choices=ENGINES, help="(interno) roda uma engine so")
    ap.add_argument("--sample", action="store_true", help="benchmark na amostra")
    args = ap.parse_args()

    data_dir = BASE / "data" / ("parquet_sample" if (args.check or args.sample) else "parquet")
    if not data_dir.exists():
        print(f"{data_dir} nao existe. Rode scripts/prepare_data.py antes.")
        return 1

    if args.check:
        return 0 if check_correctness(data_dir) else 1

    RESULTS.mkdir(exist_ok=True)
    out_csv = RESULTS / "benchmark_results.csv"

    if args.engine:
        bench_engine(args.engine, data_dir, out_csv)
        return 0

    # Orquestracao: um subprocesso novo por engine (memoria isolada).
    if out_csv.exists():
        out_csv.unlink()
    for engine in ENGINES:
        cmd = [sys.executable, __file__, "--engine", engine]
        if args.sample:
            cmd.append("--sample")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"Engine {engine} falhou (exit {result.returncode}).")
            return 1
    print(f"Resultados em {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
