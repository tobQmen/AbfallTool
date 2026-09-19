"""Manuelle Koordinaturkorrekturen für einzelne Anlagen.

Aufruf:
    python korrekturen.py                                       # alle anzeigen
    python korrekturen.py 105400298 2666000,1211000 --notiz "ab map.geo.admin.ch"
    python korrekturen.py 105400298 --loeschen
    python korrekturen.py --anwenden                            # in die Datenbank schreiben

Die Korrekturen stehen in koordinaten_manuell.json und werden bei jedem Import
automatisch wieder angewendet, überstehen also einen neuen BAFU-Export.
"""
import argparse
import contextlib
import json
import os
import sqlite3

from koordinaten import parse_standort, lv95_zu_wgs84

DATEI = "koordinaten_manuell.json"


def laden(datei=DATEI):
    if not os.path.exists(datei):
        return {}
    with open(datei, encoding="utf-8") as f:
        return json.load(f)


def anwenden(db="abfallanlagen.db", datei=DATEI, still=False):
    """Schreibt alle manuellen Korrekturen in die Datenbank."""
    korrekturen = laden(datei)
    if not korrekturen:
        return 0
    with contextlib.closing(sqlite3.connect(db)) as con:
        spalten = {r[1] for r in con.execute("PRAGMA table_info(anlagen)")}
        if "koord_quelle" not in spalten:
            con.execute("ALTER TABLE anlagen ADD COLUMN koord_quelle TEXT")
            con.execute("UPDATE anlagen SET koord_quelle='Export' WHERE e_lv95 IS NOT NULL")
        geaendert = unbekannt = 0
        for bnr, k in korrekturen.items():
            lat, lon = lv95_zu_wgs84(k["e_lv95"], k["n_lv95"])
            cur = con.execute(
                "UPDATE anlagen SET e_lv95=?, n_lv95=?, lat=?, lon=?, "
                "koord_status='manuell', koord_quelle=? WHERE betriebsnummer=?",
                (k["e_lv95"], k["n_lv95"], round(lat, 6), round(lon, 6),
                 "manuell: " + k.get("notiz", "") if k.get("notiz") else "manuell", bnr))
            geaendert += cur.rowcount
            unbekannt += cur.rowcount == 0
        con.commit()
    if not still:
        print(f"{geaendert} manuelle Korrekturen angewendet"
              + (f", {unbekannt} Betriebsnummern nicht in der Datenbank" if unbekannt else ""))
    return geaendert


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("betriebsnummer", nargs="?")
    ap.add_argument("koordinate", nargs="*", help="LV95 'E,N' oder WGS84 'lat,lon'")
    ap.add_argument("--notiz", default="")
    ap.add_argument("--loeschen", action="store_true")
    ap.add_argument("--anwenden", action="store_true")
    ap.add_argument("--db", default="abfallanlagen.db")
    args = ap.parse_args()
    args.koordinate = " ".join(args.koordinate) if args.koordinate else None
    daten = laden()

    if args.anwenden:
        anwenden(args.db)
        return
    if not args.betriebsnummer:
        if not daten:
            print("Keine manuellen Korrekturen erfasst.")
        for bnr, k in daten.items():
            print(f"{bnr}  {k['e_lv95']:>10.0f} / {k['n_lv95']:>10.0f}  {k.get('notiz','')}")
        return
    if args.loeschen:
        if daten.pop(args.betriebsnummer, None) is None:
            raise SystemExit(f"Für {args.betriebsnummer} ist keine Korrektur erfasst.")
    else:
        if not args.koordinate:
            raise SystemExit("Koordinate fehlt, z. B.: python korrekturen.py 105400298 2666000,1211000")
        e, n = parse_standort(args.koordinate)
        daten[args.betriebsnummer] = {"e_lv95": e, "n_lv95": n, "notiz": args.notiz}
    with open(DATEI, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)
    anwenden(args.db)


if __name__ == "__main__":
    main()
