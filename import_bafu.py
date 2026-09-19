"""Import des BAFU-Exports «Datenexport Abfallanlagen» in eine lokale SQLite-Datenbank.

Aufruf:
    python import_bafu.py <export.xlsx> [--db abfallanlagen.db]

Ablauf:
  1. Format prüfen (bricht ab, wenn das BAFU die Spalten ändert)
  2. Anlagen und Bewilligungen trennen und bereinigen
  3. Koordinaten reparieren, auf Plausibilität prüfen, nach WGS84 umrechnen
  4. Änderungen gegenüber dem letzten Import protokollieren
  5. Datenbank schreiben
"""
import argparse
import contextlib
import datetime as dt
import os
import sqlite3
import sys

import pandas as pd

from koordinaten import repariere, lv95_zu_wgs84, distanz_luftlinie_km
from korrekturen import anwenden as korrekturen_anwenden

ERWARTETE_SPALTEN = [
    "Standortname", "Betriebsnummer", "Firmenname", "Strasse", "Hausnummer", "PLZ",
    "Ort", "Parzellennummer", "X-Koordinate", "Y-Koordinate", "Code Abfallanlagentyp",
    "Abfallanlagentyp", "Klassierung", "Abfallcode", "Abfallcode", "Entsorgungsverfahren",
    "Entsorgungsverfahren", "Gültig von", "Gültig bis", "Kantone", "Status", "Status Kommentar",
]
SPALTEN = [
    "standortname", "betriebsnummer", "firma", "strasse", "hausnummer", "plz", "ort",
    "parzelle", "x_roh", "y_roh", "typ_code", "typ", "klassierung", "abfallcode",
    "abfallcode_text", "verfahren", "verfahren_text", "gueltig_von", "gueltig_bis",
    "kanton", "status", "status_kommentar",
]
# Zwischenlager/Umschlag: kein Endverfahren, der Abfall wird weitergeleitet
ZWISCHENVERFAHREN = {"R151", "R152", "R153", "D151", "D152", "D153"}
PLAUSI_RADIUS_KM = 20  # Abweichung vom Median der Anlagen gleicher PLZ


def lies_export(pfad):
    xl = pd.ExcelFile(pfad)
    kopf = pd.read_excel(xl, "1_Standorte", header=None, nrows=1).iloc[0].tolist()
    if [str(k).strip() for k in kopf] != ERWARTETE_SPALTEN:
        sys.exit("FEHLER: Spaltenstruktur von '1_Standorte' hat sich geändert. "
                 f"Gefunden: {kopf}")
    # Zeilen 0-3: Titel DE/FR/IT/EN, Zeile 4: technische Namen
    df = pd.read_excel(xl, "1_Standorte", header=None, skiprows=5, dtype=str)
    df.columns = SPALTEN
    df = df.apply(lambda s: s.str.strip() if s.dtype == object else s)

    datenstand = pd.read_excel(xl, "5_Version", header=None).iloc[0, 1]
    codes = pd.read_excel(xl, "2_Abfallcodes", header=None, skiprows=4, dtype=str)
    codes = codes.iloc[:, :5]
    codes.columns = ["code", "klassierung", "text_de", "text_fr", "text_it"]
    verf = pd.read_excel(xl, "3_Entsorgungsverfahren", header=None, skiprows=4, dtype=str)
    verf = verf.iloc[:, :2]
    verf.columns = ["code", "text_de"]
    typen = pd.read_excel(xl, "4_Abfallanlagentypen", header=None, skiprows=1, dtype=str)
    typen = typen.iloc[:, :2]
    typen.columns = ["code", "text_de"]
    return df, str(datenstand), codes, verf, typen


def baue_anlagen(df):
    a = df.drop_duplicates("betriebsnummer")[[
        "betriebsnummer", "standortname", "firma", "strasse", "hausnummer", "plz", "ort",
        "parzelle", "kanton", "typ_code", "typ", "status", "status_kommentar", "x_roh", "y_roh",
    ]].copy()
    res = a.apply(lambda r: repariere(r.x_roh, r.y_roh), axis=1, result_type="expand")
    a["e_lv95"], a["n_lv95"], a["koord_status"] = res[0], res[1], res[2]

    # Plausibilität: Abstand zum Median der übrigen Anlagen mit gleicher PLZ
    gueltig = a[a.e_lv95.notna()]
    for plz, gruppe in gueltig.groupby("plz"):
        if len(gruppe) < 3:
            continue
        for idx, r in gruppe.iterrows():
            andere = gruppe.drop(idx)
            d = distanz_luftlinie_km(r.e_lv95, r.n_lv95,
                                     andere.e_lv95.median(), andere.n_lv95.median())
            if d > PLAUSI_RADIUS_KM:
                a.at[idx, "koord_status"] = "unplausibel"

    ll = a.apply(lambda r: lv95_zu_wgs84(r.e_lv95, r.n_lv95)
                 if pd.notna(r.e_lv95) else (None, None), axis=1, result_type="expand")
    a["lat"], a["lon"] = ll[0], ll[1]
    return a


def baue_bewilligungen(df):
    b = df[["betriebsnummer", "abfallcode", "klassierung", "verfahren",
            "gueltig_von", "gueltig_bis"]].copy()
    for s in ("gueltig_von", "gueltig_bis"):
        b[s] = pd.to_datetime(b[s], errors="coerce").dt.strftime("%Y-%m-%d")
    b["art"] = b.verfahren.str[0]  # 'R' oder 'D'
    b["zwischenverfahren"] = b.verfahren.isin(ZWISCHENVERFAHREN).astype(int)
    return b.drop_duplicates()


def aenderungen(db_pfad, neu):
    """Vergleicht Bewilligungen mit dem bestehenden Stand der Datenbank."""
    if not os.path.exists(db_pfad):
        return None
    with contextlib.closing(sqlite3.connect(db_pfad)) as con:
        alt = pd.read_sql("SELECT betriebsnummer, abfallcode, verfahren FROM bewilligungen", con)
    key = ["betriebsnummer", "abfallcode", "verfahren"]
    m = neu[key].merge(alt, on=key, how="outer", indicator=True)
    return (m[m._merge == "left_only"][key].assign(aenderung="neu"),
            m[m._merge == "right_only"][key].assign(aenderung="entfallen"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--db", default="abfallanlagen.db")
    args = ap.parse_args()

    print("Lese Export …")
    df, datenstand, codes, verf, typen = lies_export(args.export)
    anlagen = baue_anlagen(df)
    bew = baue_bewilligungen(df)

    diff = aenderungen(args.db, bew)
    if diff:
        protokoll = pd.concat(diff)
        name = f"aenderungen_{dt.date.today():%Y-%m-%d}.csv"
        protokoll.to_csv(name, index=False)
        print(f"Änderungen: {len(diff[0])} neue, {len(diff[1])} entfallene Bewilligungen -> {name}")

    verf["art"] = verf.code.str[0]
    verf["zwischenverfahren"] = verf.code.isin(ZWISCHENVERFAHREN).astype(int)

    tmp = args.db + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    with contextlib.closing(sqlite3.connect(tmp)) as con:
        anlagen.drop(columns=["x_roh", "y_roh"]).assign(
            x_roh=anlagen.x_roh, y_roh=anlagen.y_roh).to_sql("anlagen", con, index=False)
        bew.to_sql("bewilligungen", con, index=False)
        codes.to_sql("abfallcodes", con, index=False)
        verf.to_sql("verfahren", con, index=False)
        typen.to_sql("anlagentypen", con, index=False)
        pd.DataFrame([{
            "datei": os.path.basename(args.export), "datenstand": datenstand,
            "importiert_am": dt.datetime.now().isoformat(timespec="seconds"),
            "anlagen": len(anlagen), "bewilligungen": len(bew),
        }]).to_sql("import_info", con, index=False)
        con.executescript("""
            CREATE UNIQUE INDEX ix_anl ON anlagen(betriebsnummer);
            CREATE INDEX ix_bew_code ON bewilligungen(abfallcode);
            CREATE INDEX ix_bew_bnr ON bewilligungen(betriebsnummer);
        """)
        con.commit()
    os.replace(tmp, args.db)  # erst am Schluss ersetzen: kein halbfertiger Stand

    korrekturen_anwenden(args.db, still=True)

    with contextlib.closing(sqlite3.connect(args.db)) as con:
        ks = pd.read_sql("SELECT koord_status, count(*) n FROM anlagen GROUP BY 1", con)
        ks = ks.set_index("koord_status").n
    print(f"Datenstand {datenstand}: {len(anlagen)} Anlagen, {len(bew)} Bewilligungen")
    print("Koordinaten:", ", ".join(f"{k} {v}" for k, v in ks.items()))
    print(f"Datenbank geschrieben: {args.db}")


if __name__ == "__main__":
    main()
