"""Transforma os CSVs brutos do governo em Parquet padronizado.

Atritos reais dos dados que este script resolve:
  - CNPJ/RFB: CSV sem header, delimitador ';', encoding ISO-8859-1,
    virgula como separador decimal (capital_social), datas como texto
    AAAAMMDD onde '0' e '00000000' significam nulo.
  - PRF: encoding varia por ano (utf-8 ou latin-1), datas ora AAAA-MM-DD
    ora DD/MM/AAAA, tambem com virgula decimal.

O recorte regional (SP, RJ, MG, BA) e aplicado aqui: Estabelecimentos e
filtrado por UF e Empresas por semi-join no cnpj_basico restante.

Mantemos apenas as colunas usadas nas queries do benchmark - isso enxuga o
Parquet e evita carregar dados de contato/endereco que nao interessam.

Saida:
  data/parquet/          dataset completo (recorte regional)
  data/parquet_sample/   amostra deterministica p/ checagem de corretude

Uso:
    python scripts/prepare_data.py
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent.parent
RAW = BASE / "data" / "raw"
STAGING = BASE / "data" / "staging"
PARQUET = BASE / "data" / "parquet"
SAMPLE = BASE / "data" / "parquet_sample"

UFS = ("SP", "RJ", "MG", "BA")

# Layout oficial dos metadados do CNPJ (RFB) - arquivos nao tem header.
EMPRESAS_COLS = {
    "cnpj_basico": "VARCHAR",
    "razao_social": "VARCHAR",
    "natureza_juridica": "VARCHAR",
    "qualificacao_responsavel": "VARCHAR",
    "capital_social": "DOUBLE",
    "porte": "VARCHAR",
    "ente_federativo": "VARCHAR",
}
ESTAB_COLS = {
    "cnpj_basico": "VARCHAR",
    "cnpj_ordem": "VARCHAR",
    "cnpj_dv": "VARCHAR",
    "matriz_filial": "VARCHAR",
    "nome_fantasia": "VARCHAR",
    "situacao_cadastral": "VARCHAR",
    "data_situacao_cadastral": "VARCHAR",
    "motivo_situacao": "VARCHAR",
    "cidade_exterior": "VARCHAR",
    "pais": "VARCHAR",
    "data_inicio_atividade": "VARCHAR",
    "cnae_principal": "VARCHAR",
    "cnae_secundaria": "VARCHAR",
    "tipo_logradouro": "VARCHAR",
    "logradouro": "VARCHAR",
    "numero": "VARCHAR",
    "complemento": "VARCHAR",
    "bairro": "VARCHAR",
    "cep": "VARCHAR",
    "uf": "VARCHAR",
    "municipio": "VARCHAR",
    "ddd_1": "VARCHAR",
    "telefone_1": "VARCHAR",
    "ddd_2": "VARCHAR",
    "telefone_2": "VARCHAR",
    "ddd_fax": "VARCHAR",
    "fax": "VARCHAR",
    "email": "VARCHAR",
    "situacao_especial": "VARCHAR",
    "data_situacao_especial": "VARCHAR",
}
REF_COLS = {"codigo": "VARCHAR", "descricao": "VARCHAR"}


def extract_reencode(zip_path: Path, dest: Path, src_encoding: str = "latin-1") -> None:
    """Extrai o unico membro do zip re-encodando para UTF-8 em streaming."""
    if dest.exists():
        print(f"  [ok] {dest.name} ja em staging, pulando")
        return
    with zipfile.ZipFile(zip_path) as zf:
        member = zf.namelist()[0]
        with zf.open(member) as raw, dest.open("w", encoding="utf-8", newline="") as out:
            reader = io.TextIOWrapper(raw, encoding=src_encoding, errors="strict")
            while chunk := reader.read(1 << 20):
                out.write(chunk)
    print(f"  [ok] {zip_path.name} -> {dest.name} ({dest.stat().st_size / 1e6:.0f} MB)")


def detect_encoding(zip_path: Path) -> str:
    """PRF alterna entre utf-8 e latin-1 conforme o ano; detecta pelo conteudo."""
    with zipfile.ZipFile(zip_path) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        head = zf.open(member).read(1 << 16)
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def csv_opts(cols: dict[str, str]) -> str:
    cols_sql = ", ".join(f"'{name}': '{typ}'" for name, typ in cols.items())
    # escape='"' explicito: os CSVs da RFB tem aspas duplicadas dentro de
    # campo (ex.: ESQ COM RUA ""C"") e o sniffer nao detecta sozinho.
    return (
        f"delim=';', quote='\"', escape='\"', header=false, columns={{{cols_sql}}}, "
        "decimal_separator=',', encoding='utf-8'"
    )


def prepare_cnpj(con: duckdb.DuckDBPyConnection) -> None:
    print("== CNPJ ==")
    cnpj_raw = RAW / "cnpj"
    for name in ["Empresas0", "Estabelecimentos0", "Municipios", "Cnaes", "Naturezas"]:
        extract_reencode(cnpj_raw / f"{name}.zip", STAGING / f"{name}.csv")

    ufs_sql = ", ".join(f"'{u}'" for u in UFS)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE estabelecimentos AS
        SELECT
            cnpj_basico,
            matriz_filial,
            situacao_cadastral,
            try_strptime(nullif(nullif(data_situacao_cadastral, '0'), '00000000'),
                         '%Y%m%d')::DATE AS data_situacao_cadastral,
            try_strptime(nullif(nullif(data_inicio_atividade, '0'), '00000000'),
                         '%Y%m%d')::DATE AS data_inicio_atividade,
            cnae_principal,
            uf,
            municipio
        FROM read_csv('{(STAGING / "Estabelecimentos0.csv").as_posix()}',
                      {csv_opts(ESTAB_COLS)})
        WHERE uf IN ({ufs_sql})
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE empresas AS
        SELECT cnpj_basico, natureza_juridica, capital_social, porte
        FROM read_csv('{(STAGING / "Empresas0.csv").as_posix()}', {csv_opts(EMPRESAS_COLS)})
        WHERE cnpj_basico IN (SELECT DISTINCT cnpj_basico FROM estabelecimentos)
        """
    )
    for ref in ["Municipios", "Cnaes", "Naturezas"]:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {ref.lower()} AS
            SELECT * FROM read_csv('{(STAGING / ref).as_posix()}.csv', {csv_opts(REF_COLS)})
            """
        )


def prepare_prf(con: duckdb.DuckDBPyConnection) -> None:
    print("== PRF ==")
    prf_raw = RAW / "prf"
    zips = sorted(prf_raw.glob("datatran*.zip"))
    if not zips:
        raise RuntimeError(f"Nenhum datatran*.zip em {prf_raw}. Rode download_data.py antes.")
    for zp in zips:
        extract_reencode(zp, STAGING / f"{zp.stem}.csv", src_encoding=detect_encoding(zp))

    glob = (STAGING / "datatran*.csv").as_posix()
    # data_inversa muda de formato entre anos; coalesce cobre os dois.
    con.execute(
        f"""
        CREATE OR REPLACE TABLE acidentes AS
        SELECT
            coalesce(try_strptime(data_inversa, '%Y-%m-%d'),
                     try_strptime(data_inversa, '%d/%m/%Y'))::DATE AS data,
            dia_semana,
            horario::TIME AS horario,
            uf,
            br,
            causa_acidente,
            tipo_acidente,
            classificacao_acidente,
            condicao_metereologica,
            try_cast(pessoas AS INTEGER) AS pessoas,
            try_cast(mortos AS INTEGER) AS mortos,
            try_cast(feridos AS INTEGER) AS feridos,
            try_cast(veiculos AS INTEGER) AS veiculos
        FROM read_csv('{glob}', delim=';', quote='"', escape='"', header=true,
                      all_varchar=true, encoding='utf-8', union_by_name=true)
        """
    )


TABLES = ["empresas", "estabelecimentos", "municipios", "cnaes", "naturezas", "acidentes"]


def export(con: duckdb.DuckDBPyConnection) -> None:
    PARQUET.mkdir(parents=True, exist_ok=True)
    SAMPLE.mkdir(parents=True, exist_ok=True)
    print("== Exportando Parquet ==")
    for table in TABLES:
        full = PARQUET / f"{table}.parquet"
        con.execute(f"COPY {table} TO '{full.as_posix()}' (FORMAT PARQUET)")
        rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        # Amostra deterministica (mesmo input p/ todas as engines na checagem
        # de corretude). Tabelas de referencia e PRF vao inteiras.
        if table in ("empresas", "estabelecimentos"):
            con.execute(
                f"""
                COPY (SELECT * FROM {table} USING SAMPLE 5 PERCENT (bernoulli, 42))
                TO '{(SAMPLE / f"{table}.parquet").as_posix()}' (FORMAT PARQUET)
                """
            )
        else:
            con.execute(
                f"COPY {table} TO '{(SAMPLE / f'{table}.parquet').as_posix()}' (FORMAT PARQUET)"
            )
        print(f"  {table}: {rows:,} linhas -> {full.stat().st_size / 1e6:.1f} MB")


def main() -> int:
    STAGING.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    prepare_cnpj(con)
    prepare_prf(con)
    export(con)
    print("Concluido. Proximo passo: python scripts/run_benchmark.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
