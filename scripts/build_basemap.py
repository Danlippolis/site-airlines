#!/usr/bin/env python3
"""
Gera o mapa de fundo do site (site/data/base.json): países (Natural Earth; América do Sul em 1:50m,
resto do mundo em 1:110m) e estados do Brasil. Só precisa rodar uma vez — o arquivo fica versionado.

  python scripts/build_basemap.py
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
STATES = "https://raw.githubusercontent.com/codeforgermany/click_that_hood/main/public/data/brazil-states.geojson"
sys.setrecursionlimit(100000)


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "site-airlines"}), timeout=120) as r:
        return json.loads(r.read())


def simplify(pts, eps):  # Douglas-Peucker
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** .5 or 1e-9
    dmax, idx = 0, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        d = abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / n
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return simplify(pts[:idx + 1], eps)[:-1] + simplify(pts[idx:], eps)
    return [a, b]


def rings(geom, eps):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    out = []
    for poly in polys:
        ring = poly[0]
        m = len(ring) // 2
        r = [[round(x, 2), round(y, 2)] for x, y in simplify(ring[:m + 1], eps)[:-1] + simplify(ring[m:], eps)]
        if len(r) >= 4:
            out.append(r)
    return out


def is_fg(r):  # Guiana Francesa dentro do polígono da França
    return all(x < -40 and y > 0 for x, y in r)


def main():
    ne50, ne110 = fetch(NE + "ne_50m_admin_0_countries.geojson"), fetch(NE + "ne_110m_admin_0_countries.geojson")
    countries = []
    for f in ne50["features"]:
        p = f["properties"]
        if p.get("CONTINENT") == "South America" or p.get("ADM0_A3") == "FRA":
            rr = rings(f["geometry"], 0.02)
            if p.get("ADM0_A3") == "FRA":
                rr = [r for r in rr if is_fg(r)]
            if rr:
                countries.append({"n": p.get("NAME_PT") or p["NAME"], "br": p.get("ADM0_A3") == "BRA", "r": rr})
    for f in ne110["features"]:
        p = f["properties"]
        if p.get("CONTINENT") == "South America" or p.get("ADM0_A3") == "ATA":
            continue
        rr = rings(f["geometry"], 0.15)
        if p.get("ADM0_A3") == "FRA":
            rr = [r for r in rr if not is_fg(r)]
        if rr:
            countries.append({"n": p.get("NAME_PT") or p["NAME"], "br": False, "r": rr})
    states = [{"n": f["properties"]["sigla"], "r": rings(f["geometry"], 0.02)} for f in fetch(STATES)["features"]]
    out = ROOT / "site" / "data" / "base.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"countries": countries, "states": states}, separators=(",", ":")))
    print(f"{out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
