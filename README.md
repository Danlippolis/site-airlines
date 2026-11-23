# site-airlines

Mapa das rotas dos Planos de Voo Repetitivos (RPL) da Azul, Azul Conecta, Gol e LATAM, fixo a fixo, com comparação de rotas por companhia.

Os dados vêm do [portal do CGNA/DECEA](https://portal.cgna.decea.mil.br) e são atualizados por um GitHub Action. O site em `site/` só lê arquivos deste repositório.

## Como funciona

```
portal do CGNA ──► scripts/rpl_cgna.py ──► data/raw/<edição>/Cia_XXX_CS.txt
                                                   │
                         scripts/build_site_data.py│  (resolve fixos e aerovias)
                                                   ▼
                                         site/data/data.json ──► site/index.html (GitHub Pages)
```

| Pasta / arquivo | O que é |
|---|---|
| `data/raw/<AAAA-MM-DD>/` | Arquivos originais de cada edição do RPL (a data é o início da edição). `Cia_<ICAO>_CS.txt` vem do portal; `pdf/<ICAO>.pdf` é o relatório impresso, usado só quando não há o `.txt`. |
| `site/data/data.json` | Dados do site: voos, rotas resolvidas, fixos e aeroportos. Gerado — não editar à mão. |
| `site/data/base.json` | Mapa de fundo (países e estados). Gerado uma vez por `scripts/build_basemap.py`. |
| `scripts/rpl_cgna.py` | Baixa a edição vigente (ou `--date`) para `data/raw/`. Só biblioteca padrão. |
| `scripts/rpl_parse.py` | Leitores do RPL: `.txt` do portal, PDF impresso e base completa (`RPL_NAVBRASIL.zip`). |
| `scripts/build_site_data.py` | Lê a edição mais recente de `data/raw/` e gera `site/data/data.json`. |
| `.github/workflows/update-rpl.yml` | Roda todo dia às 03:17 (Brasília): baixa, gera e faz commit se algo mudou; depois publica. |
| `.github/workflows/pages.yml` | Publica `site/` no GitHub Pages. |

## Rodar localmente

```bash
python scripts/rpl_cgna.py               # baixa a edição vigente para data/raw/
python scripts/build_site_data.py        # gera site/data/data.json
python -m http.server -d site 8000       # abre http://localhost:8000
```

Na primeira execução o build baixa para `data/cache/` (fora do git) a base de fixos/aerovias e a de aeroportos.

Opções úteis do download: `--cias GLO TAM`, `--date 2026-11-19`, `--csv` (gera um CSV por arquivo, um plano por linha), `--parse arquivo.txt` (só converte um arquivo local).

## Configuração no GitHub (uma vez)

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
2. **Actions → Atualizar RPL → Run workflow** para a primeira carga (depois ele roda sozinho todo dia).

## Observações

- **Certificado do CGNA:** o site usa a cadeia ICP-Brasil, que costuma faltar no Python. O workflow tenta com verificação e, se falhar por SSL, repete com `--insecure`; o script confere se o conteúdo é mesmo um RPL antes de gravar. Localmente, prefira `--ca-bundle` com a cadeia ICP-Brasil.
- **Fixos e aerovias:** base AIRAC publicada pelo projeto [waypoint-atlas](https://github.com/kamng0222/waypoint-atlas) (dados Navigraph); aeroportos do [OurAirports](https://ourairports.com/data/). SID e STAR não fazem parte do RPL — os trechos aeroporto↔primeiro/último fixo são desenhados retos.
- **Histórico:** cada edição fica em sua pasta em `data/raw/` (≈2 MB por edição para as quatro companhias).
- Visualização para estudo — **não usar para navegação**.
