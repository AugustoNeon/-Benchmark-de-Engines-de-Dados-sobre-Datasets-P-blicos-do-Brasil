"""Baixa os dados brutos usados no benchmark.

Fontes (ambas dados abertos do governo federal):
  1. CNPJ - Receita Federal: arquivos mensais em
     https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/AAAA-MM/
     A RFB nao publica um arquivo por estado: as tabelas grandes sao fatiadas
     em 10 partes arbitrarias (Empresas0..9, Estabelecimentos0..9). Baixamos a
     parte 0 de cada uma + tabelas de referencia, e o recorte por UF e feito
     depois, em prepare_data.py.
  2. Acidentes PRF: CSVs anuais (datatran) linkados na pagina de dados
     abertos da PRF. Os links sao shares do ownCloud e mudam de token, entao
     o script raspa a pagina oficial para descobri-los.

Uso:
    python scripts/download_data.py
"""

from __future__ import annotations

import re
import sys
import zipfile
from datetime import date
from pathlib import Path

import requests
from tqdm import tqdm

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

RFB_BASE = "https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj"
RFB_FILES = [
    "Empresas0.zip",
    "Estabelecimentos0.zip",
    "Municipios.zip",
    "Cnaes.zip",
    "Naturezas.zip",
]

PRF_PAGE = "https://www.gov.br/prf/pt-br/acesso-a-informacao/dados-abertos/dados-abertos-da-prf"
PRF_YEARS = ["2022", "2023", "2024"]

HEADERS = {"User-Agent": "Mozilla/5.0 (benchmark de engines de dados; uso academico)"}


def discover_rfb_month(session: requests.Session) -> str:
    """Encontra a pasta mensal mais recente disponivel no servidor da RFB."""
    today = date.today()
    year, month = today.year, today.month
    for _ in range(8):
        folder = f"{year}-{month:02d}"
        url = f"{RFB_BASE}/{folder}/{RFB_FILES[0]}"
        try:
            resp = session.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
            if resp.status_code == 200:
                return folder
        except requests.RequestException:
            pass
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    raise RuntimeError(
        "Nenhuma pasta mensal da RFB encontrada nos ultimos 8 meses. "
        f"Verifique manualmente em {RFB_BASE}/"
    )


def download(session: requests.Session, url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [ok] {dest.name} ja existe ({dest.stat().st_size / 1e6:.1f} MB), pulando")
        return
    resp = session.get(url, headers=HEADERS, stream=True, timeout=60)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("wb") as fh, tqdm(
        total=total, unit="B", unit_scale=True, desc=f"  {dest.name}"
    ) as bar:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            fh.write(chunk)
            bar.update(len(chunk))
    tmp.rename(dest)


def validate_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f"{path.name} corrompido (membro {bad}). Apague e baixe de novo.")


def download_rfb(session: requests.Session) -> None:
    dest_dir = RAW_DIR / "cnpj"
    dest_dir.mkdir(parents=True, exist_ok=True)
    month = discover_rfb_month(session)
    print(f"[RFB] Usando referencia mensal {month}")
    for name in RFB_FILES:
        download(session, f"{RFB_BASE}/{month}/{name}", dest_dir / name)
        validate_zip(dest_dir / name)


def discover_prf_links(session: requests.Session) -> dict[str, str]:
    """Raspa a pagina de dados abertos da PRF atras dos links de datatran por ano."""
    resp = session.get(PRF_PAGE, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    html = resp.text
    links: dict[str, str] = {}
    # Os links de download aparecem como shares do ownCloud proximos ao texto do ano.
    for match in re.finditer(
        r'href="(https://arquivos\.prf\.gov\.br/arquivos/index\.php/s/[^"]+)"[^>]*>([^<]*)',
        html,
    ):
        url, text = match.group(1), match.group(2)
        for year in PRF_YEARS:
            if year in text and year not in links:
                links[year] = url.rstrip("/") + ("" if url.endswith("/download") else "/download")
    return links


def download_prf(session: requests.Session) -> None:
    dest_dir = RAW_DIR / "prf"
    dest_dir.mkdir(parents=True, exist_ok=True)
    links = discover_prf_links(session)
    missing = [y for y in PRF_YEARS if y not in links]
    if missing:
        print(
            f"[PRF] AVISO: nao achei link automatico para {missing}.\n"
            f"      Baixe manualmente 'Agrupados por ocorrencia' em {PRF_PAGE}\n"
            f"      e salve como data/raw/prf/datatran<ano>.zip"
        )
    for year, url in links.items():
        download(session, url, dest_dir / f"datatran{year}.zip")
        validate_zip(dest_dir / f"datatran{year}.zip")


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        print("== CNPJ (Receita Federal) ==")
        download_rfb(session)
        print("== Acidentes (PRF) ==")
        download_prf(session)
    print("Concluido. Proximo passo: python scripts/prepare_data.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
