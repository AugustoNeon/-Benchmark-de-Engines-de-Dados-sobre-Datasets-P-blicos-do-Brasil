"""As 6 queries do benchmark, implementadas para cada engine.

Regras de desenho:
  - Mesma semantica nas 4 engines; a checagem de corretude compara os
    resultados linha a linha antes de qualquer medicao de tempo valer.
  - Toda ordenacao tem criterio de desempate explicito, senao engines
    diferentes retornam ordens diferentes em empates e a comparacao falha.
  - Datas ficam como DATE no Parquet; cada engine as trata do jeito nativo
    (SQLite nao tem tipo de data: recebe strings ISO na carga e usa
    strftime/substr - essa e uma limitacao real e relevante da engine).

Tabelas: empresas, estabelecimentos, municipios, cnaes, naturezas, acidentes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd
import polars as pl


@dataclass(frozen=True)
class Query:
    nome: str
    descricao: str
    sql_duckdb: str
    sql_sqlite: str
    fn_pandas: Callable[[dict[str, pd.DataFrame]], pd.DataFrame]
    fn_polars: Callable[[dict[str, pl.DataFrame]], pl.DataFrame]


# ---------------------------------------------------------------- Q1
# Aberturas de empresas (matriz) por ano e UF desde 2000.

Q1_DUCK = """
SELECT year(data_inicio_atividade) AS ano, uf, count(*) AS aberturas
FROM estabelecimentos
WHERE matriz_filial = '1'
  AND data_inicio_atividade >= DATE '2000-01-01'
GROUP BY ano, uf
ORDER BY ano, uf
"""

Q1_SQLITE = """
SELECT CAST(strftime('%Y', data_inicio_atividade) AS INTEGER) AS ano, uf,
       count(*) AS aberturas
FROM estabelecimentos
WHERE matriz_filial = '1'
  AND data_inicio_atividade >= '2000-01-01'
GROUP BY ano, uf
ORDER BY ano, uf
"""


def q1_pandas(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    e = t["estabelecimentos"]
    e = e[(e["matriz_filial"] == "1") & (e["data_inicio_atividade"] >= "2000-01-01")]
    out = (
        e.groupby([e["data_inicio_atividade"].dt.year.rename("ano"), "uf"])
        .size()
        .reset_index(name="aberturas")
        .sort_values(["ano", "uf"], kind="mergesort")
    )
    return out.reset_index(drop=True)


def q1_polars(t: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return (
        t["estabelecimentos"]
        .lazy()
        .filter(
            (pl.col("matriz_filial") == "1")
            & (pl.col("data_inicio_atividade") >= pl.date(2000, 1, 1))
        )
        .group_by(pl.col("data_inicio_atividade").dt.year().alias("ano"), "uf")
        .agg(pl.len().alias("aberturas"))
        .sort(["ano", "uf"])
        .collect()
    )


# ---------------------------------------------------------------- Q2
# Top 10 atividades economicas (CNAE) das empresas abertas nos ultimos 5 anos.

Q2_DUCK = """
SELECT c.descricao AS atividade, count(*) AS n
FROM estabelecimentos e
JOIN cnaes c ON e.cnae_principal = c.codigo
WHERE e.data_inicio_atividade >= DATE '2021-01-01'
GROUP BY c.descricao
ORDER BY n DESC, atividade
LIMIT 10
"""

Q2_SQLITE = """
SELECT c.descricao AS atividade, count(*) AS n
FROM estabelecimentos e
JOIN cnaes c ON e.cnae_principal = c.codigo
WHERE e.data_inicio_atividade >= '2021-01-01'
GROUP BY c.descricao
ORDER BY n DESC, atividade
LIMIT 10
"""


def q2_pandas(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    e = t["estabelecimentos"]
    e = e[e["data_inicio_atividade"] >= "2021-01-01"]
    m = e.merge(t["cnaes"], left_on="cnae_principal", right_on="codigo")
    out = (
        m.groupby("descricao")
        .size()
        .reset_index(name="n")
        .rename(columns={"descricao": "atividade"})
        .sort_values(["n", "atividade"], ascending=[False, True], kind="mergesort")
        .head(10)
    )
    return out.reset_index(drop=True)


def q2_polars(t: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return (
        t["estabelecimentos"]
        .lazy()
        .filter(pl.col("data_inicio_atividade") >= pl.date(2021, 1, 1))
        .join(t["cnaes"].lazy(), left_on="cnae_principal", right_on="codigo")
        .group_by(pl.col("descricao").alias("atividade"))
        .agg(pl.len().alias("n"))
        .sort(["n", "atividade"], descending=[True, False])
        .head(10)
        .collect()
    )


# ---------------------------------------------------------------- Q3
# Municipios com maior taxa de empresas baixadas (situacao 08), min. 1000.

Q3_DUCK = """
SELECT m.descricao AS municipio, e.uf,
       count(*) AS total,
       round(avg(CASE WHEN e.situacao_cadastral = '08' THEN 1.0 ELSE 0.0 END), 4)
           AS taxa_baixadas
FROM estabelecimentos e
JOIN municipios m ON e.municipio = m.codigo
GROUP BY m.descricao, e.uf
HAVING count(*) >= 1000
ORDER BY taxa_baixadas DESC, municipio, uf
LIMIT 20
"""

Q3_SQLITE = Q3_DUCK  # mesma sintaxe funciona nas duas engines


def q3_pandas(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    e = t["estabelecimentos"].merge(
        t["municipios"], left_on="municipio", right_on="codigo"
    )
    g = e.groupby(["descricao", "uf"]).agg(
        total=("situacao_cadastral", "size"),
        taxa_baixadas=("situacao_cadastral", lambda s: (s == "08").mean()),
    )
    g = g[g["total"] >= 1000].reset_index().rename(columns={"descricao": "municipio"})
    g["taxa_baixadas"] = g["taxa_baixadas"].round(4)
    out = g.sort_values(
        ["taxa_baixadas", "municipio", "uf"],
        ascending=[False, True, True],
        kind="mergesort",
    ).head(20)
    return out[["municipio", "uf", "total", "taxa_baixadas"]].reset_index(drop=True)


def q3_polars(t: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return (
        t["estabelecimentos"]
        .lazy()
        .join(t["municipios"].lazy(), left_on="municipio", right_on="codigo")
        .group_by(pl.col("descricao").alias("municipio"), "uf")
        .agg(
            pl.len().alias("total"),
            (pl.col("situacao_cadastral") == "08").mean().round(4).alias("taxa_baixadas"),
        )
        .filter(pl.col("total") >= 1000)
        .sort(["taxa_baixadas", "municipio", "uf"], descending=[True, False, False])
        .head(20)
        .collect()
    )


# ---------------------------------------------------------------- Q4
# Capital social medio por porte x divisao CNAE (join 2 tabelas grandes).

Q4_DUCK = """
SELECT emp.porte, substr(e.cnae_principal, 1, 2) AS divisao_cnae,
       count(*) AS n,
       round(avg(emp.capital_social), 2) AS capital_medio
FROM estabelecimentos e
JOIN empresas emp ON e.cnpj_basico = emp.cnpj_basico
WHERE e.matriz_filial = '1'
GROUP BY emp.porte, divisao_cnae
HAVING count(*) >= 500
ORDER BY capital_medio DESC, porte, divisao_cnae
LIMIT 15
"""

Q4_SQLITE = Q4_DUCK


def q4_pandas(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    e = t["estabelecimentos"]
    e = e[e["matriz_filial"] == "1"]
    m = e.merge(t["empresas"], on="cnpj_basico")
    m["divisao_cnae"] = m["cnae_principal"].str[:2]
    g = (
        m.groupby(["porte", "divisao_cnae"])
        .agg(n=("capital_social", "size"), capital_medio=("capital_social", "mean"))
        .reset_index()
    )
    g = g[g["n"] >= 500]
    g["capital_medio"] = g["capital_medio"].round(2)
    out = g.sort_values(
        ["capital_medio", "porte", "divisao_cnae"],
        ascending=[False, True, True],
        kind="mergesort",
    ).head(15)
    return out[["porte", "divisao_cnae", "n", "capital_medio"]].reset_index(drop=True)


def q4_polars(t: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return (
        t["estabelecimentos"]
        .lazy()
        .filter(pl.col("matriz_filial") == "1")
        .join(t["empresas"].lazy(), on="cnpj_basico")
        .with_columns(pl.col("cnae_principal").str.slice(0, 2).alias("divisao_cnae"))
        .group_by("porte", "divisao_cnae")
        .agg(
            pl.len().alias("n"),
            pl.col("capital_social").mean().round(2).alias("capital_medio"),
        )
        .filter(pl.col("n") >= 500)
        .sort(
            ["capital_medio", "porte", "divisao_cnae"], descending=[True, False, False]
        )
        .head(15)
        .select("porte", "divisao_cnae", "n", "capital_medio")
        .collect()
    )


# ---------------------------------------------------------------- Q5
# Causas de acidente x rodovia, ordenado por mortos.

Q5_DUCK = """
SELECT causa_acidente, br, count(*) AS acidentes, sum(mortos) AS mortos
FROM acidentes
WHERE br IS NOT NULL
GROUP BY causa_acidente, br
ORDER BY mortos DESC, acidentes DESC, causa_acidente, br
LIMIT 15
"""

Q5_SQLITE = Q5_DUCK


def q5_pandas(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    a = t["acidentes"]
    a = a[a["br"].notna()]
    g = (
        a.groupby(["causa_acidente", "br"])
        .agg(acidentes=("mortos", "size"), mortos=("mortos", "sum"))
        .reset_index()
    )
    out = g.sort_values(
        ["mortos", "acidentes", "causa_acidente", "br"],
        ascending=[False, False, True, True],
        kind="mergesort",
    ).head(15)
    return out.reset_index(drop=True)


def q5_polars(t: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return (
        t["acidentes"]
        .lazy()
        .filter(pl.col("br").is_not_null())
        .group_by("causa_acidente", "br")
        .agg(pl.len().alias("acidentes"), pl.col("mortos").sum().alias("mortos"))
        .sort(
            ["mortos", "acidentes", "causa_acidente", "br"],
            descending=[True, True, False, False],
        )
        .head(15)
        .collect()
    )


# ---------------------------------------------------------------- Q6
# Dia da semana x hora com mais acidentes fatais.

Q6_DUCK = """
SELECT dia_semana, hour(horario) AS hora, count(*) AS acidentes,
       sum(mortos) AS mortos
FROM acidentes
WHERE mortos > 0
GROUP BY dia_semana, hora
ORDER BY mortos DESC, acidentes DESC, dia_semana, hora
LIMIT 20
"""

Q6_SQLITE = """
SELECT dia_semana, CAST(substr(horario, 1, 2) AS INTEGER) AS hora,
       count(*) AS acidentes, sum(mortos) AS mortos
FROM acidentes
WHERE mortos > 0
GROUP BY dia_semana, hora
ORDER BY mortos DESC, acidentes DESC, dia_semana, hora
LIMIT 20
"""


def q6_pandas(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    a = t["acidentes"]
    a = a[a["mortos"] > 0].copy()
    # pandas nao tem tipo nativo p/ hora-do-dia: horario vem do Parquet como
    # objetos datetime.time e a extracao da hora nao e vetorizavel.
    a["hora"] = a["horario"].map(lambda h: h.hour)
    g = (
        a.groupby(["dia_semana", "hora"])
        .agg(acidentes=("mortos", "size"), mortos=("mortos", "sum"))
        .reset_index()
    )
    out = g.sort_values(
        ["mortos", "acidentes", "dia_semana", "hora"],
        ascending=[False, False, True, True],
        kind="mergesort",
    ).head(20)
    return out.reset_index(drop=True)


def q6_polars(t: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return (
        t["acidentes"]
        .lazy()
        .filter(pl.col("mortos") > 0)
        .group_by("dia_semana", pl.col("horario").dt.hour().alias("hora"))
        .agg(pl.len().alias("acidentes"), pl.col("mortos").sum().alias("mortos"))
        .sort(
            ["mortos", "acidentes", "dia_semana", "hora"],
            descending=[True, True, False, False],
        )
        .head(20)
        .collect()
    )


QUERIES: list[Query] = [
    Query(
        "q1_aberturas_ano_uf",
        "Aberturas de empresas por ano e UF desde 2000",
        Q1_DUCK, Q1_SQLITE, q1_pandas, q1_polars,
    ),
    Query(
        "q2_top_cnaes_5anos",
        "Top 10 atividades economicas dos ultimos 5 anos (join + top-k)",
        Q2_DUCK, Q2_SQLITE, q2_pandas, q2_polars,
    ),
    Query(
        "q3_taxa_baixadas_municipio",
        "Municipios com maior taxa de empresas baixadas (agregacao condicional)",
        Q3_DUCK, Q3_SQLITE, q3_pandas, q3_polars,
    ),
    Query(
        "q4_capital_porte_cnae",
        "Capital social medio por porte x divisao CNAE (join de 2 tabelas grandes)",
        Q4_DUCK, Q4_SQLITE, q4_pandas, q4_polars,
    ),
    Query(
        "q5_causas_rodovias",
        "Causas de acidente por rodovia, ordenado por mortos (PRF)",
        Q5_DUCK, Q5_SQLITE, q5_pandas, q5_polars,
    ),
    Query(
        "q6_fatais_dia_hora",
        "Dia da semana x hora dos acidentes fatais (extracao de tempo)",
        Q6_DUCK, Q6_SQLITE, q6_pandas, q6_polars,
    ),
]
