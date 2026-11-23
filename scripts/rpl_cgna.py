#!/usr/bin/env python3
"""
Baixa os Planos de Voo Repetitivos (RPL) publicados pelo CGNA/DECEA e, se pedido,
converte para CSV (um plano por linha).

Fontes no portal https://portal.cgna.decea.mil.br (painel "Planos de Voo Repetitivos"):
  - por companhia: files/abas/<AAAA-MM-DD>/painel_rpl/companhias/Cia_<ICAO>_CS.txt
  - base completa:  files/abas/<AAAA-MM-DD>/painel_rpl/bdr/RPL_NAVBRASIL.zip
<AAAA-MM-DD> é a data de início da edição (ex.: 2026-11-19 para a edição de 19 a 25/11/2026).

Só usa a biblioteca padrão do Python (3.9+).

Os arquivos vão para data/raw/<AAAA-MM-DD>/ dentro do repositório; depois rode
scripts/build_site_data.py para atualizar o site.

Exemplos:
  python scripts/rpl_cgna.py                          # edição atual, AZU ACN GLO TAM + base completa
  python scripts/rpl_cgna.py --cias GLO TAM --no-bdr
  python scripts/rpl_cgna.py --date 2026-11-19 --csv  # também gera CSV (um plano por linha)
  python scripts/rpl_cgna.py --parse Cia_GLO_CS.txt   # só converte um arquivo local para CSV

Certificado: o servidor do CGNA usa cadeia da ICP-Brasil, que costuma não estar no
repositório de certificados do Python. Se der erro de SSL, use --ca-bundle apontando
para a cadeia ICP-Brasil (recomendado) ou, em último caso, --insecure.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import logging
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://portal.cgna.decea.mil.br"
ABAS = f"{BASE}/files/abas"
DEFAULT_CIAS = ["AZU", "ACN", "GLO", "TAM"]
UA = "Mozilla/5.0 (rpl_cgna.py; uso pessoal)"
log = logging.getLogger("rpl")


# ----------------------------------------------------------------------------- HTTP
def make_ctx(ca_bundle: str | None, insecure: bool) -> ssl.SSLContext:
    if insecure:
        log.warning("Verificação de certificado DESLIGADA (--insecure).")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=ca_bundle) if ca_bundle else ssl.create_default_context()


def get(url: str, ctx: ssl.SSLContext, tries: int = 3, timeout: int = 60) -> bytes | None:
    """Retorna o corpo da resposta, ou None em 404. Repete em erros transitórios."""
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            log.warning("HTTP %s em %s (tentativa %d/%d)", e.code, url, attempt, tries)
        except ssl.SSLError as e:
            raise SystemExit(f"Erro de SSL em {url}: {e}\n"
                             "Use --ca-bundle com a cadeia ICP-Brasil ou --insecure.") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                raise SystemExit(f"Erro de SSL em {url}: {e.reason}\n"
                                 "Use --ca-bundle com a cadeia ICP-Brasil ou --insecure.") from e
            log.warning("%s em %s (tentativa %d/%d)", e, url, attempt, tries)
        time.sleep(2 * attempt)
    raise SystemExit(f"Falha ao baixar {url} após {tries} tentativas.")


# ------------------------------------------------------------------- edição vigente
def discover_edition(ctx: ssl.SSLContext, max_days: int = 60) -> str:
    """Lê a data da edição no portal; se não achar, procura o arquivo voltando dia a dia."""
    html = get(f"{BASE}/?l=pt_br", ctx)
    if html:
        txt = html.decode("utf-8", "replace")
        m = re.search(r"Edi\S{1,8}\s+(\d{2})/(\d{2})/(\d{4})\s+a\s+\d{2}/\d{2}/\d{4}", txt)
        if m:
            d = f"{m[3]}-{m[2]}-{m[1]}"
            log.info("Edição informada no portal: %s", d)
            return d
        m = re.search(r"files/abas/(\d{4}-\d{2}-\d{2})/painel_rpl/companhias/", txt)
        if m:
            return m[1]
    log.info("Data da edição não encontrada na página; procurando nos últimos %d dias…", max_days)
    day = dt.date.today()
    for _ in range(max_days):
        d = day.isoformat()
        body = get(f"{ABAS}/{d}/painel_rpl/companhias/Cia_AZU_CS.txt", ctx)
        if body and b"Planos de Voo" in body[:2000]:
            return d
        day -= dt.timedelta(days=1)
    raise SystemExit("Nenhuma edição de RPL encontrada.")


# --------------------------------------------------------------------------- CSV
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rpl_parse import parse_any  # noqa: E402

FIELDS = ["cia", "ident", "desde", "ate", "dias", "tipo", "adep", "eobt", "vel", "fl", "route", "dest", "eet", "obs", "fonte"]


def rows_for_csv(path: Path) -> list[dict]:
    rows = parse_any(path)
    for r in rows:
        r["route"], r["obs"], r["fonte"] = " ".join(r["route"]), " ".join(o for o in r["obs"] if o), path.name
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    log.info("CSV: %s (%d planos)", path, len(rows))


# ---------------------------------------------------------------------------- main
def save(body: bytes, path: Path) -> bool:
    """Grava se mudou; retorna True se o arquivo é novo ou diferente."""
    if path.exists() and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(body).digest():
        log.info("Sem mudança: %s", path.name)
        return False
    path.write_bytes(body)
    log.info("Gravado: %s (%.0f KB)", path, len(body) / 1024)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Baixa o RPL do CGNA por companhia e a base completa.")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "raw"), help="pasta de saída (padrão: data/raw do repositório)")
    ap.add_argument("--date", help="data de início da edição, AAAA-MM-DD (padrão: descobre a vigente)")
    ap.add_argument("--cias", nargs="*", default=DEFAULT_CIAS, help="designadores ICAO (padrão: AZU ACN GLO TAM)")
    ap.add_argument("--no-bdr", action="store_true", help="não baixa a base completa RPL_NAVBRASIL.zip")
    ap.add_argument("--csv", action="store_true", help="gera CSV com um plano por linha")
    ap.add_argument("--parse", nargs="+", metavar="ARQ", help="só converte arquivos locais (.txt ou .zip) para CSV")
    ap.add_argument("--ca-bundle", help="arquivo PEM com a cadeia ICP-Brasil")
    ap.add_argument("--insecure", action="store_true", help="não verifica o certificado (último recurso)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.parse:
        for f in map(Path, a.parse):
            write_csv(rows_for_csv(f), f.with_suffix(".csv"))
        return 0

    ctx = make_ctx(a.ca_bundle, a.insecure)
    edition = a.date or discover_edition(ctx)
    ed_dir = out / edition
    ed_dir.mkdir(exist_ok=True)
    log.info("Edição %s → %s", edition, ed_dir)

    all_rows, missing = [], []
    for cia in [c.upper() for c in a.cias]:
        name = f"Cia_{cia}_CS.txt"
        body = get(f"{ABAS}/{edition}/painel_rpl/companhias/{name}", ctx)
        if body is None or b"Planos de Voo" not in body[:2000]:
            missing.append(cia)
            log.warning("Sem arquivo para %s nesta edição.", cia)
            continue
        save(body, ed_dir / name)
        if a.csv:
            rows = rows_for_csv(ed_dir / name)
            write_csv(rows, ed_dir / f"Cia_{cia}_CS.csv")
            all_rows += rows

    if not a.no_bdr:
        body = get(f"{ABAS}/{edition}/painel_rpl/bdr/RPL_NAVBRASIL.zip", ctx)
        if body is None:
            log.warning("RPL_NAVBRASIL.zip não encontrado nesta edição.")
        else:
            save(body, ed_dir / "RPL_NAVBRASIL.zip")
            if a.csv:
                write_csv(rows_for_csv(ed_dir / "RPL_NAVBRASIL.zip"), ed_dir / "RPL_NAVBRASIL.csv")

    if a.csv and all_rows:
        write_csv(all_rows, ed_dir / "rpl_companhias.csv")
    if missing:
        log.warning("Companhias sem arquivo: %s", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
