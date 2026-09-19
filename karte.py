"""Exportiert Baustelle und mögliche Anlagen als GeoJSON für QGIS.

Pro Kombination aus Anlage und Abfallcode entsteht ein Punkt. Damit lässt sich in
QGIS nach Abfallcode filtern (z. B. code = '17 05 06') und nach Verfahrensstufe
oder Anlagentyp einfärben.

Aufruf:
    python karte.py --baustelle Sedrun --vorlage tunnel --umkreis 60
    python karte.py --baustelle Sedrun --code "17 05 06" --umkreis 80 --top 30
    python karte.py --baustelle Sedrun --vorlage tunnel --nur-endverfahren

Ergebnis: <baustelle>_anlagen.geojson und <baustelle>_baustelle.geojson (WGS84).
"""
import argparse
import json
import re
import sqlite3

import pandas as pd

from abfrage import anlagen_fuer_code, normalisiere_code
from baustellen import aufloesen
from koordinaten import lv95_zu_wgs84
from liste import lade_vorlage, positionen


def punkt(lon, lat, eigenschaften):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": eigenschaften}


def schreibe(features, pfad):
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)
    return len(features)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="abfallanlagen.db")
    ap.add_argument("--baustelle", required=True)
    ap.add_argument("--code", help="einzelner Abfallcode")
    ap.add_argument("--vorlage", help="alle Codes einer Vorlage")
    ap.add_argument("--umkreis", type=float, default=60)
    ap.add_argument("--top", type=int, default=30, help="Anlagen pro Code")
    ap.add_argument("--nur-endverfahren", action="store_true")
    ap.add_argument("--stichtag")
    ap.add_argument("--praefix", help="Dateiname, Standard: Name der Baustelle")
    args = ap.parse_args()

    if not (args.code or args.vorlage):
        raise SystemExit("--code oder --vorlage angeben")
    start = aufloesen(args.baustelle)
    con = sqlite3.connect(args.db)

    if args.code:
        eintraege = [(normalisiere_code(args.code), "", False)]
    else:
        eintraege = [(normalisiere_code(p["code"]), p.get("abfallart", ""),
                      bool(p.get("v_pflicht"))) for _, p in positionen(lade_vorlage(args.vorlage))]

    features, codes_ohne = [], []
    for code, abfallart, v_pflicht in eintraege:
        treffer = anlagen_fuer_code(con, code, args.stichtag, start, args.nur_endverfahren,
                                    "verwertung" if v_pflicht else "distanz")
        treffer = treffer[treffer.lat.notna()]
        if args.umkreis:
            treffer = treffer[treffer.distanz_km <= args.umkreis]
        if treffer.empty:
            codes_ohne.append(code)
            continue
        for rang, (_, a) in enumerate(treffer.head(args.top).iterrows(), start=1):
            features.append(punkt(a.lon, a.lat, {
                "code": code, "abfallart": abfallart,
                "v_pflicht": "V" if v_pflicht else "",
                "rang": rang, "firma": a.firma, "standort": a.standortname, "ort": a.ort,
                "kanton": a.kanton, "typ": a.typ, "verfahren": a.verfahren,
                "bestes_verfahren": a.bestes_verfahren, "stufe": int(a.stufe),
                "stufe_text": a.stufe_text, "distanz_km": a.distanz_km,
                "distanz_quelle": a.distanz_quelle, "fahrzeit_min": a.fahrzeit_min,
                "betriebsnummer": a.betriebsnummer, "gueltig_bis": a.gueltig_bis,
                "koord_status": a.koord_status}))

    praefix = args.praefix or re.sub(r"[^\w-]", "_", args.baustelle.lower())
    lat, lon = lv95_zu_wgs84(*start)
    n1 = schreibe(features, f"{praefix}_anlagen.geojson")
    schreibe([punkt(lon, lat, {"name": args.baustelle, "e_lv95": start[0], "n_lv95": start[1]})],
             f"{praefix}_baustelle.geojson")

    codes = sorted({f["properties"]["code"] for f in features})
    anlagen = len({f["properties"]["betriebsnummer"] for f in features})
    print(f"{n1} Punkte ({anlagen} Anlagen, {len(codes)} Codes) -> {praefix}_anlagen.geojson")
    print(f"Baustelle -> {praefix}_baustelle.geojson")
    if codes_ohne:
        print(f"Ohne Anlage im Umkreis: {', '.join(codes_ohne)}")
    mit_strasse = sum(1 for f in features if f["properties"]["distanz_quelle"] == "Strasse")
    print(f"Davon mit Strassendistanz: {mit_strasse}, mit Luftlinie: {n1 - mit_strasse}")
    print(f"\nIn QGIS filtern mit z. B.:  \"code\" = '{codes[0] if codes else '17 05 06'}'")


if __name__ == "__main__":
    main()
