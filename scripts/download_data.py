"""Baixa os dados brutos usados no benchmark.

Fontes (ambas dados abertos do governo federal):
  1. CNPJ - Receita Federal: a RFB migrou a distribuicao para um Nextcloud
     publico (SERPRO+). O acesso programatico e via WebDAV do share publico:
       https://arquivos.receitafederal.gov.br/public.php/dav/files/<token>/
         Dados/Cadastros/CNPJ/AAAA-MM/
     com basic auth usuario=<token do share>, senha vazia.
     A RFB nao publica um arquivo por estado: as tabelas grandes sao fatiadas
     em 10 partes arbitrarias (Empresas0..9, Estabelecimentos0..9). Baixamos a
     parte 0 de cada uma + tabelas de referencia, e o recorte por UF e feito
     depois, em prepare_data.py.
  2. Acidentes PRF: CSVs anuais (datatran) linkados na pagina de dados
     abertos da PRF. Os arquivos hoje ficam no Google Drive e os IDs mudam,
     entao o script raspa a tabela da pagina oficial para descobrir o link
     "Agrupados por ocorrencia" de cada ano.

Uso:
    python scripts/download_data.py
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import requests
from tqdm import tqdm

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Token do share publico "Arquivos da Receita Federal" (raiz de
# https://arquivos.receitafederal.gov.br/ redireciona para ele).
RFB_TOKEN = "gn672Ad4CF8N6TK"
RFB_DAV = f"https://arquivos.receitafederal.gov.br/public.php/dav/files/{RFB_TOKEN}"
RFB_CNPJ_DIR = "Dados/Cadastros/CNPJ"
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


def propfind(session: requests.Session, path: str) -> list[str]:
    """Lista nomes de arquivos/pastas de um diretorio do share via WebDAV."""
    resp = session.request(
        "PROPFIND",
        f"{RFB_DAV}/{path}/",
        headers={**HEADERS, "Depth": "1"},
        auth=(RFB_TOKEN, ""),
        timeout=60,
    )
    resp.raise_for_status()
    names = re.findall(r"<d:href>[^<]*?/([^/<]+)/?</d:href>", resp.text)
    return [n for n in names if n and n != path.rsplit("/", 1)[-1]]


def discover_rfb_month(session: requests.Session) -> str:
    """Mes mais recente cuja pasta ja contem todos os arquivos que precisamos.

    A pasta do mes corrente pode existir com upload ainda em andamento, por
    isso valida o conteudo em vez de so pegar a ultima.
    """
    months = sorted(
        m for m in propfind(session, RFB_CNPJ_DIR) if re.fullmatch(r"\d{4}-\d{2}", m)
    )
    for month in reversed(months[-4:]):
        files = set(propfind(session, f"{RFB_CNPJ_DIR}/{month}"))
        if all(f in files for f in RFB_FILES):
            return month
    raise RuntimeError(
        f"Nenhum mes recente completo em {RFB_DAV}/{RFB_CNPJ_DIR} (meses vistos: {months[-4:]})"
    )


def download(session: requests.Session, url: str, dest: Path, auth=None) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [ok] {dest.name} ja existe ({dest.stat().st_size / 1e6:.1f} MB), pulando")
        return
    resp = session.get(url, headers=HEADERS, stream=True, timeout=60, auth=auth)
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
        url = f"{RFB_DAV}/{RFB_CNPJ_DIR}/{month}/{name}"
        download(session, url, dest_dir / name, auth=(RFB_TOKEN, ""))
        validate_zip(dest_dir / name)


def discover_prf_links(session: requests.Session) -> dict[str, str]:
    """Raspa a tabela da pagina da PRF: ano -> link de download no Google Drive.

    Cada linha da tabela tem um texto tipo "Acidentes 2023 (Agrupados por
    ocorrencia)" e um link drive.google.com/file/d/<id>/view; convertemos
    para a URL de download direto (uc?export=download).
    """
    resp = session.get(PRF_PAGE, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    links: dict[str, str] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", resp.text, re.S):
        text = " ".join(re.sub(r"<[^>]+>", " ", row).split())
        match = re.search(r"https://drive\.google\.com/file/d/([\w-]+)/", row)
        if not match or "por ocorr" not in text.lower():
            continue
        for year in PRF_YEARS:
            if f"Acidentes {year}" in text and year not in links:
                links[year] = (
                    f"https://drive.google.com/uc?export=download&id={match.group(1)}"
                )
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
