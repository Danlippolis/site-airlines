"""
Leitores do RPL (Planos de Voo Repetitivos) do CGNA/DECEA.

Três formatos de entrada, todos devolvendo a mesma estrutura (lista de dicts):
  - parse_company_txt: arquivo por companhia baixado do portal (Cia_XXX_CS.txt), largura fixa
  - parse_pdf:         o mesmo relatório impresso em PDF pelo navegador (precisa do pdftotext / poppler)
  - parse_bdr:         base completa RPL_NAVBRASIL (um plano por linha, todas as companhias)

Campos: cia, ident, desde, ate, dias, tipo, adep, eobt, vel, fl, route (lista de tokens), dest, eet, obs
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REC = re.compile(r"^\s*(\d{6}) (\d{6}|UFN) +(\d{7}) +([A-Z0-9]{3}\d+[A-Z]?)\s+(\S+)\s+([A-Z]{4})(\d{4})\s+(\S+)\s+(\S+)\s*(.*)$")
DEST = re.compile(r"^([A-Z]{4})(\d{4})")
SKIP = ("Planos de Voo", "Classifica", "CIA:", "PAG.", "----", "VALIDO", "DESDE")
# token que pode ser rota: DCT, IFR/VFR, aerovia, fixo/auxílio, coordenada, com troca opcional de velocidade/nível
ROUTE_TOK = re.compile(r"^(DCT|VFR|IFR|U?[A-Z]{1,2}\d{1,4}[A-Z]?|[A-Z]{2,5}|\d{4}[NS]\d{5}[EW]|[A-Z]{2,3}\d{6})(/[NKM]\d{4}[FAS]\d{3,4})?$")


def _new(m) -> dict:
    return dict(cia=m[4][:3], ident=m[4], desde=m[1], ate=m[2], dias=m[3], tipo=m[5], adep=m[6], eobt=m[7],
                vel=m[8], fl=m[9], route=[], dest=None, eet=None, obs=[])


def parse_company_txt(text: str) -> list[dict]:
    """Cia_XXX_CS.txt: rota entre as colunas ROTA e DEST; continuação nas linhas seguintes, mesmas colunas."""
    lines = text.replace("\r", "").split("\n")
    hdr = next((l for l in lines if "ROTA" in l and "DEST" in l and "VALIDO" in l), None)
    if hdr is None:
        raise ValueError("cabeçalho com ROTA/DEST não encontrado — o layout mudou?")
    c_rota, c_dest = hdr.index("ROTA"), hdr.index("DEST")
    out, cur = [], None
    for ln in lines:
        s = ln.strip()
        if not s or s == ":" or s.startswith(SKIP) or ("ROTA" in ln and "DEST" in ln):
            continue
        m = REC.match(ln)
        if m:
            cur = _new(m)
            out.append(cur)
            md = DEST.match(ln[c_dest:c_dest + 8])
            if md:
                cur["route"], cur["dest"], cur["eet"] = ln[c_rota:c_dest].split(), md[1], md[2]
                cur["obs"].append(ln[c_dest + 9:].strip())
            else:  # rota invadiu a coluna do destino
                mm = re.search(r"\b([A-Z]{4})(\d{4})(\s+\S+/.*)?$", ln[c_rota:])
                if mm:
                    cur["route"] = ln[c_rota:c_rota + mm.start()].split()
                    cur["dest"], cur["eet"] = mm[1], mm[2]
                    cur["obs"].append((mm[3] or "").strip())
            continue
        if cur is None:
            continue
        if len(ln) - len(ln.lstrip()) >= c_rota - 2:
            cur["route"] += (ln[c_rota:c_dest] if len(ln) > c_rota else "").split()
            if len(ln) > c_dest:
                cur["obs"].append(ln[c_dest:].strip())
        else:  # texto na coluna 0 = quebra das observações
            cur["obs"].append(s)
    return out


def parse_layout_text(text: str) -> list[dict]:
    """Texto de `pdftotext -layout` do relatório impresso: as quebras de página do navegador mudam as colunas,
    então a rota é reconhecida pelo recuo (continuação à direita) e pelo padrão DEST+EET."""
    out, cur = [], None
    for ln in text.split("\n"):
        m = REC.match(ln)
        if m:
            cur = _new(m)
            cur["route"] = m[10].split()
            out.append(cur)
            continue
        if cur is None:
            continue
        s = ln.strip()
        if not s or s == ":" or (s.startswith(SKIP + ("EET",)) and not s.startswith("EET/")):
            continue
        if cur["dest"] is None:
            md = re.match(r"^(.*?)([A-Z]{4})(\d{4})(\s+.*)?$", s)
            if md:
                cur["route"] += md[1].split()
                cur["dest"], cur["eet"] = md[2], md[3]
                if md[4]:
                    cur["obs"].append(md[4].strip())
                continue
        if len(ln) - len(ln.lstrip()) >= 40:
            cur["route"] += s.split()
            continue
        # rota que quebrou para a coluna 0, seguida de observações depois de um vão largo
        parts = re.split(r" {5,}", s, maxsplit=1)
        if len(parts) == 2 and parts[0].split() and all(ROUTE_TOK.match(t) for t in parts[0].split()) and "/" in parts[1][:12]:
            cur["route"] += parts[0].split()
            cur["obs"].append(parts[1])
        else:
            cur["obs"].append(s)
    return out


def parse_pdf(path: Path) -> list[dict]:
    exe = shutil.which("pdftotext")
    if not exe:
        raise SystemExit("pdftotext não encontrado (instale o poppler: apt install poppler-utils / brew install poppler)")
    txt = subprocess.run([exe, "-layout", str(path), "-"], check=True, capture_output=True).stdout.decode("utf-8", "replace")
    return parse_layout_text(txt)


BDR = re.compile(r"^(\d{6}) (\d{6}|UFN) +(\d{7}) +([A-Z0-9]{3}\d+[A-Z]?) +(\S+) +([A-Z]{4})(\d{4}) +(\S+) +(\S+) (.+?) {2,}([A-Z]{4})(\d{4})(?: +(.*))?$")


def parse_bdr(text: str) -> list[dict]:
    out = []
    for ln in text.replace("\r", "").split("\n"):
        m = BDR.match(ln.strip())
        if m:
            r = _new(m)
            r.update(route=m[10].split(), dest=m[11], eet=m[12], obs=[m[13] or ""])
            out.append(r)
    return out


def parse_any(path: Path) -> list[dict]:
    """Escolhe o leitor pela extensão / conteúdo."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        return parse_pdf(path)
    if path.suffix.lower() == ".zip":
        import zipfile
        rows = []
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                rows += parse_bdr(z.read(n).decode("latin-1"))
        return rows
    text = path.read_bytes().decode("latin-1")
    return parse_company_txt(text) if "ROTA" in text[:5000] else parse_bdr(text)
