"""Welche Anlagen dürfen einen Abfallcode annehmen?

Aufruf:
    python abfrage.py "17 05 04"
    python abfrage.py 170504 --baustelle Sedrun --limit 15      (gespeicherte Baustelle)
    python abfrage.py 170504 --baustelle 2666000,1211000 --limit 15
    python abfrage.py 170504 --baustelle 47.05,8.30 --nur-endverfahren --csv resultat.csv

Verwendet wird die Strassendistanz aus routen.db, wo eine vorliegt, sonst die
Luftlinie. Die Spalte distanz_quelle zeigt, welcher Wert es war.
"""
import argparse
import datetime as dt
import re
import sqlite3

import pandas as pd

from baustellen import aufloesen
from bewertung import stufe, stufe_text, bestes_verfahren
from koordinaten import distanz_luftlinie_km
from routing import gespeicherte_distanzen


def normalisiere_code(code):
    ziffern = re.sub(r"\D", "", code)
    if len(ziffern) != 6:
        raise ValueError(f"Ungültiger Abfallcode: {code!r} (erwartet 6 Ziffern, z. B. 17 05 04)")
    return f"{ziffern[:2]} {ziffern[2:4]} {ziffern[4:]}"


def anlagen_fuer_code(con, code, stichtag=None, baustelle=None, nur_endverfahren=False,
                      sortierung="distanz"):
    """Gibt pro Anlage eine Zeile zurück, mit allen bewilligten Verfahren für den Code."""
    code = normalisiere_code(code)
    stichtag = stichtag or dt.date.today().isoformat()
    sql = """
        SELECT a.betriebsnummer, a.standortname, a.firma, a.strasse, a.hausnummer,
               a.plz, a.ort, a.kanton, a.typ, a.e_lv95, a.n_lv95, a.lat, a.lon,
               a.koord_status, b.verfahren, b.zwischenverfahren, b.gueltig_bis, b.klassierung
        FROM bewilligungen b
        JOIN anlagen a USING (betriebsnummer)
        WHERE b.abfallcode = ?
          AND a.status = 'Aktiv'
          AND b.gueltig_von <= ? AND b.gueltig_bis >= ?
    """
    if nur_endverfahren:
        sql += " AND b.zwischenverfahren = 0"
    df = pd.read_sql(sql, con, params=(code, stichtag, stichtag))
    if df.empty:
        return df

    gruppe = df.groupby("betriebsnummer")
    erg = gruppe.first().drop(columns=["verfahren", "zwischenverfahren", "gueltig_bis"])
    erg["verfahren"] = gruppe.verfahren.apply(lambda s: ", ".join(sorted(set(s))))
    erg["nur_zwischenlager"] = gruppe.zwischenverfahren.min().astype(bool)
    erg["gueltig_bis"] = gruppe.gueltig_bis.min()
    erg = erg.reset_index()
    erg["stufe"] = erg.verfahren.apply(stufe)
    erg["stufe_text"] = erg.stufe.apply(stufe_text)
    erg["bestes_verfahren"] = erg.verfahren.apply(bestes_verfahren)

    if baustelle:
        e, n = baustelle
        erg["distanz_luftlinie_km"] = erg.apply(
            lambda r: round(distanz_luftlinie_km(e, n, r.e_lv95, r.n_lv95), 1)
            if pd.notna(r.e_lv95) else None, axis=1)
        # Strassendistanz aus dem Routing-Cache, wo vorhanden
        ziele = [(r.e_lv95, r.n_lv95) for r in erg.itertuples() if pd.notna(r.e_lv95)]
        strassen = gespeicherte_distanzen((e, n), ziele)
        erg["strasse_km"] = [strassen.get((r.e_lv95, r.n_lv95), (None, None))[0]
                             for r in erg.itertuples()]
        erg["fahrzeit_min"] = [strassen.get((r.e_lv95, r.n_lv95), (None, None))[1]
                               for r in erg.itertuples()]
        erg["distanz_km"] = erg.strasse_km.fillna(erg.distanz_luftlinie_km)
        erg["distanz_quelle"] = ["Strasse" if pd.notna(k) else "Luftlinie"
                                 for k in erg.strasse_km]
        if sortierung == "verwertung":
            erg = erg.sort_values(["stufe", "distanz_km"], na_position="last")
        else:
            erg = erg.sort_values("distanz_km", na_position="last")
    return erg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--db", default="abfallanlagen.db")
    ap.add_argument("--baustelle",
                    help="Name einer gespeicherten Baustelle, LV95 'E,N' oder WGS84 'lat,lon'")
    ap.add_argument("--stichtag", help="YYYY-MM-DD, Standard: heute")
    ap.add_argument("--nur-endverfahren", action="store_true",
                    help="Zwischenlager/Umschlag (R/D151-153) ausschliessen")
    ap.add_argument("--sortierung", choices=["distanz", "verwertung"], default="distanz",
                    help="'verwertung': zuerst nach Verfahrensstufe, dann nach Distanz")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--csv")
    args = ap.parse_args()

    try:
        code = normalisiere_code(args.code)
    except ValueError as e:
        raise SystemExit(str(e))
    con = sqlite3.connect(args.db)
    txt = con.execute("SELECT text_de FROM abfallcodes WHERE code = ?", (code,)).fetchone()
    if not txt:
        raise SystemExit(f"Abfallcode {code} existiert nicht im LVA-Verzeichnis dieses Exports.")
    stand = con.execute("SELECT datenstand FROM import_info").fetchone()[0]
    baustelle = aufloesen(args.baustelle) if args.baustelle else None
    erg = anlagen_fuer_code(con, code, args.stichtag, baustelle, args.nur_endverfahren,
                            args.sortierung)

    print(f"{txt[0]}   (Datenstand {stand})")
    if erg.empty:
        print("Keine aktive Anlage mit gültiger Bewilligung gefunden.")
        return
    ohne = erg.e_lv95.isna().sum()
    print(f"{len(erg)} Anlagen, davon {ohne} ohne verwendbare Koordinaten\n")

    spalten = ["distanz_km", "distanz_quelle"] if baustelle else []
    spalten += ["firma", "ort", "typ", "verfahren", "stufe_text", "betriebsnummer", "koord_status"]
    with pd.option_context("display.max_colwidth", 40, "display.width", 250):
        print(erg[spalten].head(args.limit).to_string(index=False))
    if args.csv:
        erg.to_csv(args.csv, index=False)
        print(f"\nVollständiges Resultat: {args.csv}")


if __name__ == "__main__":
    main()
