# Benchmark de Engines de Dados sobre Datasets Públicos do Brasil

Comparação rigorosa e reproduzível de **Pandas, SQLite, DuckDB e Polars** executando
as mesmas 6 queries analíticas sobre dois datasets públicos brasileiros de verdade:

- **Cadastro Nacional de Empresas (CNPJ)** — a Receita Federal publica gratuitamente,
  todo mês, o cadastro completo de todas as empresas do Brasil (mais de 60 milhões).
  Pouca gente sabe que isso existe. Aqui usamos um recorte de SP, RJ, MG e BA.
- **Acidentes em rodovias federais (PRF)** — ocorrências registradas pela Polícia
  Rodoviária Federal, 2022–2024.

Sem dataset de tutorial, sem dado sintético: os números medem as engines contra o
atrito de dados reais de governo — CSV sem cabeçalho, encoding ISO-8859-1, vírgula
como separador decimal e formato de data que muda entre anos.

## Resultados

*(seção preenchida ao final da execução do benchmark — veja `results/`)*

## Por que essas engines

| Engine | O que representa |
|--------|------------------|
| Pandas | o baseline que todo mundo usa por padrão |
| SQLite | banco relacional embutido clássico, linha a linha |
| DuckDB | engine analítica colunar e vetorizada moderna |
| Polars | dataframes em Rust com execução lazy |

Todas rodam **embutidas no processo** (sem servidor), o que torna a comparação justa
e o benchmark reproduzível em qualquer máquina.

## As 6 queries

| # | Query | Padrão que exercita |
|---|-------|---------------------|
| 1 | Aberturas de empresas por ano e UF desde 2000 | agregação + extração de data |
| 2 | Top 10 atividades econômicas dos últimos 5 anos | join + group by + top-k |
| 3 | Municípios com maior taxa de empresas baixadas | agregação condicional + having |
| 4 | Capital social médio por porte × divisão CNAE | join de 2 tabelas grandes |
| 5 | Causas de acidente por rodovia, por mortos | group by + ordenação |
| 6 | Dia da semana × hora dos acidentes fatais | extração de tempo |

## Metodologia

- **Corretude antes de velocidade**: antes de qualquer medição, as 6 queries rodam
  numa amostra e os resultados das 4 engines são comparados linha a linha
  (`run_benchmark.py --check`). Benchmark com resultado errado não vale nada.
- **Um subprocesso por engine** — medição de memória isolada, interpretador frio.
- **Carga medida separada da query**: "Parquet → engine pronta" é um custo pago uma
  vez e com perfil próprio.
- Cada query roda **5×**; a primeira execução (aquecimento) é descartada e
  reportamos a **mediana** das demais.
- **Memória** = pico de RSS do processo, amostrado a cada 50 ms.
- Ordenações com critério de desempate explícito, para comparação determinística
  entre engines.

## Atritos reais que o código enfrenta (e que você vai enfrentar também)

1. A RFB **desativou as URLs diretas antigas** de download: hoje os arquivos ficam
   num Nextcloud público (SERPRO+). O `download_data.py` navega o share via WebDAV
   (`PROPFIND`) e descobre sozinho o mês mais recente completo.
2. A RFB **não publica os dados por estado**: as tabelas grandes vêm fatiadas em 10
   partes arbitrárias. O recorte regional é feito localmente, após o parse.
3. CSVs do CNPJ: **sem cabeçalho**, `;` como delimitador, encoding **ISO-8859-1**,
   vírgula decimal no capital social e datas `AAAAMMDD` onde `0` significa nulo.
4. CSVs da PRF: o **encoding muda conforme o ano** (UTF-8 ou Latin-1) e o formato de
   data alterna entre `AAAA-MM-DD` e `DD/MM/AAAA`.

## Como reproduzir

```bash
pip install -r requirements.txt
python scripts/download_data.py     # baixa RFB (via WebDAV) e PRF  (~1,5 GB)
python scripts/prepare_data.py      # normaliza e converte p/ Parquet
python scripts/run_benchmark.py --check   # corretude na amostra
python scripts/run_benchmark.py     # benchmark completo
python scripts/make_charts.py       # gráficos em results/charts/
```

## Fontes e licenças dos dados

- [Dados Abertos do CNPJ — Receita Federal](https://arquivos.receitafederal.gov.br/)
  — dados públicos sob a Lei de Acesso à Informação.
- [Dados Abertos — Polícia Rodoviária Federal](https://www.gov.br/prf/pt-br/acesso-a-informacao/dados-abertos/dados-abertos-da-prf)
  — dados públicos do Governo Federal.

Os dados brutos não são versionados neste repositório (veja `.gitignore`); os
scripts baixam tudo das fontes oficiais.
