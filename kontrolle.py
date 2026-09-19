"""Koordinaten kontrollieren: Übersicht, Kartenlinks und GeoJSON-Export.

Aufruf:
    python kontrolle.py                                  # Übersicht nach Status
    python kontrolle.py --status geokodiert_grob         # diese Anlagen auflisten
    python kontrolle.py --status manuell,geokodiert_grob --csv pruefen.csv
    python kontrolle.py --baustelle Sedrun --umkreis 50  # nur Anlagen in der Region
    python kontrolle.py --status geokodiert_grob --geojson pruefen.geojson

Die CSV enthält pro Anlage einen Link auf map.geo.admin.ch mit gesetztem Fadenkreuz.
Die GeoJSON-Datei lässt sich in QGIS öffnen oder direkt auf map.geo.admin.ch ziehen.
"""
import argparse
import json
import sqlite3

import pandas as pd

from baustellen import aufloesen
from koordinaten import distanz_luftlinie_km

KARTE = ("https://map.geo.admin.ch/?E={e:.0f}&N={n:.0f}&zoom=9&crosshair=marker"
         "&bgLayer=ch.swisstopo.swissimage")


def lade(db, status=None, baustelle=None, umkreis=None):
    con = sqlite3.connect(db)
    df = pd.read_sql("SELECT betriebsnummer, standortname, firma, strasse, hausnummer, plz, "
                     "ort, kanton, typ, e_lv95, n_lv95, lat, lon, koord_status, koord_quelle "
                     "FROM anlagen", con)
    if status:
        df = df[df.koord_status.isin(status)]
    if baustelle:
        e, n = baustelle
        df = df.assign(distanz_km=df.apply(
            lambda r: round(distanz_luftlinie_km(e, n, r.e_lv95, r.n_lv95), 1)
            if pd.notna(r.e_lv95) else None, axis=1))
        if umkreis:
            df = df[df.distanz_km.notna() & (df.distanz_km <= umkreis)]
        df = df.sort_values("distanz_km", na_position="last")
    df = df.assign(adresse=(df.strasse.fillna("") + " " + df.hausnummer.fillna("") + ", "
                            + df.plz.fillna("") + " " + df.ort.fillna("")).str.strip(" ,"))
    df["karte"] = [KARTE.format(e=r.e_lv95, n=r.n_lv95) if pd.notna(r.e_lv95) else ""
                   for r in df.itertuples()]
    return df


def geojson(df, pfad):
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
        "properties": {"name": f"{r.firma} ({r.ort})", "betriebsnummer": r.betriebsnummer,
                       "standortname": r.standortname, "adresse": r.adresse, "typ": r.typ,
                       "koord_status": r.koord_status, "koord_quelle": r.koord_quelle},
    } for r in df.itertuples() if pd.notna(r.lat)]
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)
    return len(features)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="abfallanlagen.db")
    ap.add_argument("--status", help="z. B. geokodiert_grob,manuell,unplausibel")
    ap.add_argument("--baustelle")
    ap.add_argument("--umkreis", type=float)
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--csv")
    ap.add_argument("--geojson")
    args = ap.parse_args()

    status = [s.strip() for s in args.status.split(",")] if args.status else None
    baustelle = aufloesen(args.baustelle) if args.baustelle else None
    df = lade(args.db, status, baustelle, args.umkreis)

    if not status and not baustelle:
        con = sqlite3.connect(args.db)
        print(pd.read_sql("SELECT koord_status, koord_quelle, count(*) AS anzahl "
                          "FROM anlagen GROUP BY 1, 2 ORDER BY 3 DESC", con).to_string(index=False))
        print("\nDetails z. B. mit:  python kontrolle.py --status geokodiert_grob")
        return

    print(f"{len(df)} Anlagen\n")
    spalten = (["distanz_km"] if baustelle else []) + [
        "firma", "ort", "kanton", "adresse", "koord_status", "koord_quelle", "betriebsnummer"]
    with pd.option_context("display.max_colwidth", 30, "display.width", 250):
        print(df[spalten].head(args.limit).to_string(index=False))
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nCSV mit Kartenlinks: {args.csv}")
    if args.geojson:
        print(f"\n{geojson(df, args.geojson)} Punkte -> {args.geojson}")


if __name__ == "__main__":
    main()
