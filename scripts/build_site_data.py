#!/usr/bin/env python3
"""
Gera os dados do site (site/data/data.json) a partir dos arquivos de RPL em data/raw/<edição>/.

Para cada companhia usa, nesta ordem:
  1. data/raw/<edição>/Cia_<ICAO>_CS.txt   (baixado por scripts/rpl_cgna.py)
  2. data/raw/<edição>/pdf/<ICAO>.pdf      (relatório impresso do portal; precisa do pdftotext)

Cada rota é resolvida fixo a fixo: fixos, auxílios e aerovias vêm de uma base AIRAC publicada
(projeto waypoint-atlas), e os aeroportos do OurAirports. As duas bases são baixadas na primeira
execução para data/cache/ (fora do git).

Uso:
  python scripts/build_site_data.py                 # edição mais recente em data/raw
  python scripts/build_site_data.py --edition 2026-11-19
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import json
import logging
import math
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rpl_parse import parse_any  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CIAS = {"AZU": "Azul", "ACN": "Azul Conecta", "GLO": "Gol", "TAM": "LATAM"}
NAVDATA_URL = "https://raw.githubusercontent.com/kamng0222/waypoint-atlas/main/public_data/network.json.gz"
AIRPORTS_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
BR_REGIONS = ("SB", "SD", "SI", "SJ", "SN", "SS", "SW")
MAG_VAR = -21  # declinação aproximada (°) para pontos "radial/distância" de auxílios
log = logging.getLogger("build")


# ------------------------------------------------------------------------ bases de dados
def cached(url: str, name: str) -> Path:
    p = ROOT / "data" / "cache" / name
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        log.info("Baixando %s", url)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "site-airlines"}), timeout=120) as r:
            p.write_bytes(r.read())
    return p


class Nav:
    def __init__(self):
        net = json.load(gzip.open(cached(NAVDATA_URL, "network.json.gz")))
        self.airac = net.get("manifest", {}).get("metadata", {}).get("airac_cycle", "?")
        self.wp, self.byname = {}, collections.defaultdict(list)
        for i, ident, reg, lon, lat, typ in net["waypoints"]:
            self.wp[i] = (ident, reg, lat, lon, typ)
            self.byname[ident].append(i)
        self.awy = collections.defaultdict(lambda: collections.defaultdict(set))
        for s in net["segments"]:
            _, a, b, name = s[:4]
            self.awy[name][a].add(b)
            self.awy[name][b].add(a)
        self.apt = {}
        text = cached(AIRPORTS_URL, "airports.csv").read_text(encoding="utf-8")
        for r in csv.DictReader(io.StringIO(text)):
            for k in (r["icao_code"], r["gps_code"], r["ident"]):
                if k and re.fullmatch(r"[A-Z]{4}", k) and (k not in self.apt or r["type"] in ("large_airport", "medium_airport")):
                    self.apt[k] = (float(r["latitude_deg"]), float(r["longitude_deg"]), r["name"], r["municipality"] or "")

    def pick(self, name, near, airway=None):
        cands = self.byname.get(name, [])
        if airway and airway in self.awy:
            on = [c for c in cands if c in self.awy[airway]]
            cands = on or cands
        if not cands:
            return None

        def score(c):
            d = dist((self.wp[c][2], self.wp[c][3]), near)
            return ((0 if self.wp[c][1] in BR_REGIONS else 1) if d < 600 else 2, d)
        return min(cands, key=score)

    def airway_path(self, name, a, b):
        g = self.awy.get(name)
        if not g or a not in g:
            return None
        prev, q = {a: None}, collections.deque([a])
        while q:
            u = q.popleft()
            if u == b:
                break
            for v in g[u]:
                if v not in prev:
                    prev[v] = u
                    q.append(v)
        if b not in prev:
            return None
        path, u = [], b
        while u is not None:
            path.append(u)
            u = prev[u]
        return path[::-1]


def dist(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(h))


AW = re.compile(r"^U?[A-Z]{1,2}\d{1,4}[A-Z]?$")
COORD = re.compile(r"^(\d{2})(\d{2})([NS])(\d{3})(\d{2})([EW])$")
RADIAL = re.compile(r"^([A-Z]{2,3})(\d{3})(\d{3})$")


def resolve(nav: Nav, rec: dict):
    """Lista de pontos (nome, lat, lon, tipo, aerovia_de_chegada, id_na_base, expandido) e problemas encontrados."""
    o, d = nav.apt[rec["adep"]], nav.apt[rec["dest"]]
    toks = [t.split("/")[0] for t in rec["route"] if t.split("/")[0] not in ("DCT", "IFR", "VFR")]
    pts, issues, cur, pending = [], [], (o[0], o[1]), None
    for t in toks:
        m = COORD.match(t)
        if AW.match(t) and not m:
            pending = t
            continue
        ll = None
        if m:
            la, lo = int(m[1]) + int(m[2]) / 60, int(m[4]) + int(m[5]) / 60
            ll = (-la if m[3] == "S" else la, -lo if m[6] == "W" else lo)
        elif RADIAL.match(t):
            r = RADIAL.match(t)
            nid = nav.pick(r[1], cur)
            if nid is not None:
                brg, nm = math.radians((int(r[2]) + MAG_VAR) % 360), int(r[3]) / 60
                la0, lo0 = nav.wp[nid][2], nav.wp[nid][3]
                ll = (la0 + nm * math.cos(brg), lo0 + nm * math.sin(brg) / math.cos(math.radians(la0)))
        if ll:
            p, pid = (t, ll[0], ll[1], "C", pending), None
        else:
            pid = nav.pick(t, cur, pending)
            if pid is None:
                issues.append(t)
                pending = None
                continue
            w = nav.wp[pid]
            p = (t, w[2], w[3], {"V": "V", "N": "N"}.get(w[4], "F"), pending)
        if pending and pts and pts[-1][5] is not None and pid is not None:
            path = nav.airway_path(pending, pts[-1][5], pid)
            if path:
                for mid in path[1:-1]:
                    w = nav.wp[mid]
                    pts.append((w[0], w[2], w[3], {"V": "V", "N": "N"}.get(w[4], "F"), pending, mid, True))
        pts.append(p + (pid, False))
        cur, pending = (p[1], p[2]), None
    return pts, issues


# --------------------------------------------------------------------------------- main
def load_edition(raw: Path, edition: str) -> tuple[list[dict], dict]:
    recs, src = [], {}
    base = raw / edition
    for c in CIAS:
        txt, pdf = base / f"Cia_{c}_CS.txt", base / "pdf" / f"{c}.pdf"
        f = txt if txt.exists() else pdf if pdf.exists() else None
        if not f:
            log.warning("%s: sem arquivo em %s", c, base)
            continue
        r = parse_any(f)
        for x in r:
            x["cia"] = c
        recs += r
        src[c] = f.name
        log.info("%s: %d planos (%s)", c, len(r), f.name)
    return recs, src


def main() -> int:
    ap = argparse.ArgumentParser(description="Gera site/data/data.json a partir de data/raw/<edição>.")
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--edition", help="AAAA-MM-DD (padrão: a mais recente em data/raw)")
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raw, out = Path(a.raw), Path(a.out)
    eds = sorted(p.name for p in raw.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
    if not eds:
        raise SystemExit(f"Nenhuma edição em {raw}")
    edition = a.edition or eds[-1]
    recs, src = load_edition(raw, edition)
    if not recs:
        raise SystemExit("Nenhum plano lido.")
    nav = Nav()

    start = dt.date.fromisoformat(edition)
    week = [start + dt.timedelta(i) for i in range(7)]
    pdate = lambda s: dt.date(2000 + int(s[4:]), int(s[2:4]), int(s[:2]))

    def freq(x):  # voos na semana de referência (início da edição + 6 dias)
        a_ = pdate(x["desde"])
        b_ = dt.date.max if x["ate"] == "UFN" else pdate(x["ate"])
        return sum(1 for d in week if a_ <= d <= b_ and x["dias"][d.weekday()] != "0")

    points, pidx, routes, ridx, flights, apts = [], {}, [], {}, [], {}
    issues, no_apt = collections.Counter(), collections.Counter()

    def addpt(key, name, lat, lon, kind):
        if key not in pidx:
            pidx[key] = len(points)
            points.append([name, round(lat, 5), round(lon, 5), kind])
        return pidx[key]

    for r in recs:
        if not r.get("dest"):
            issues["sem destino"] += 1
            continue
        miss = [x for x in (r["adep"], r["dest"]) if x not in nav.apt]
        if miss:
            no_apt[miss[0]] += 1
            continue
        key = (r["adep"], r["dest"], " ".join(r["route"]))
        if key not in ridx:
            pts, iss = resolve(nav, r)
            for t in iss:
                issues[t] += 1
            seq = [[addpt(pid if pid is not None else name, name, lat, lon, kind), via or "", int(ex)]
                   for name, lat, lon, kind, via, pid, ex in pts]
            o, d = nav.apt[r["adep"]], nav.apt[r["dest"]]
            chain = [(o[0], o[1])] + [(points[s[0]][1], points[s[0]][2]) for s in seq] + [(d[0], d[1])]
            ridx[key] = len(routes)
            routes.append({"o": r["adep"], "d": r["dest"], "raw": key[2], "p": seq,
                           "nm": round(sum(dist(p, q) for p, q in zip(chain, chain[1:]))), "gc": round(dist(chain[0], chain[-1]))})
        for x in (r["adep"], r["dest"]):
            la, lo, nm, city = nav.apt[x]
            apts[x] = [round(la, 4), round(lo, 4), nm, city]
        flights.append([r["cia"], r["ident"], r["tipo"].split("/")[0], r["adep"], r["dest"], r["eobt"], r["eet"],
                        r["fl"], r["vel"], r["dias"], r["desde"], r["ate"], ridx[key], freq(r)])

    end = start + dt.timedelta(days=6)
    meta = {"edicao": edition, "edicao_txt": f"{start:%d/%m/%Y} a {end:%d/%m/%Y}", "airac": nav.airac, "fontes": src,
            "gerado_em": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "fixos_nao_encontrados": dict(issues.most_common(50)), "aeroportos_nao_encontrados": dict(no_apt)}
    out.mkdir(parents=True, exist_ok=True)
    data = {"meta": meta, "cias": {c: n for c, n in CIAS.items() if c in src}, "airports": apts, "points": points,
            "routes": routes, "flights": flights}
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("data.json: %d planos, %d rotas, %d pontos, %d aeroportos (%.0f KB)", len(flights), len(routes), len(points),
             len(apts), (out / "data.json").stat().st_size / 1024)
    if issues or no_apt:
        log.warning("Fixos não encontrados: %s | aeroportos sem coordenada: %s", issues.most_common(10), no_apt.most_common(10))
    return 0


if __name__ == "__main__":
    sys.exit(main())
