"""Prüfbericht zur Datenqualität: auffällige Koordinaten und Testdatensätze im Export.

Gedacht als Rückmeldung an die Datenherren (BAFU, Kantone) oder zur eigenen Kontrolle.

Aufruf:
    python datenqualitaet.py                     # schreibt datenqualitaet_abfallanlagen.xlsx
    python datenqualitaet.py --xlsx datei.xlsx --db abfallanlagen.db

Blatt «Koordinaten»: pro auffälliger Anlage der Originalwert aus dem Export, der
Befund und, wo möglich, ein Korrekturvorschlag mit Quelle und Kartenlinks.
Blatt «Testdatensätze»: Einträge, die nach Test aussehen.
Blatt «Hinweise»: Erläuterungen für die Empfänger.

Nach import_bafu.py und geokodierung.py ausführen, dann sind die Vorschläge vollständig.
"""
import argparse
import contextlib
import re
import sqlite3

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from koordinaten import (_ergaenze_lv95, _im_bereich, distanz_luftlinie_km, parse_zahl,
                         repariere)

KARTE = ("https://map.geo.admin.ch/?E={e:.0f}&N={n:.0f}&zoom=9&crosshair=marker"
         "&bgLayer=ch.swisstopo.swissimage")
GEOKODIERT = {"geokodiert", "geokodiert_grob"}
TEST = re.compile(r"\btest(?!station)", re.IGNORECASE)
PLATZHALTER = re.compile(r"(\d)\1{4,}|12345|23456|98765")
PLAUSI_RADIUS_KM = 20


def _ziffern(wert):
    z = parse_zahl(wert)
    return str(int(z)) if z is not None else ""


def _tippfehler(wert, ziel, toleranz_m=2000):
    """True, wenn eine Ziffer zu viel oder zu wenig den Wert nahe ans Ziel bringt."""
    s = _ziffern(wert)
    if not s or ziel is None or pd.isna(ziel):
        return False
    kandidaten = [s[:i] + s[i + 1:] for i in range(len(s))]
    kandidaten += [s[:i] + d + s[i:] for i in range(len(s) + 1) for d in "0123456789"]
    kandidaten = [k for k in kandidaten if k and k[0] != "0" and k != s]
    return any(abs(int(k) - ziel) <= toleranz_m for k in kandidaten)


def befund(r):
    """Problem der Exportkoordinate in Worten, oder None wenn alles in Ordnung ist."""
    x_leer = pd.isna(r.x_roh) or not str(r.x_roh).strip()
    y_leer = pd.isna(r.y_roh) or not str(r.y_roh).strip()
    e, n = parse_zahl(r.x_roh), parse_zahl(r.y_roh)
    teile = []
    if any(re.search(r"[Oo]", str(v)) for v in (r.x_roh, r.y_roh) if pd.notna(v)):
        teile.append("Buchstabe O statt Ziffer 0")
    if x_leer and y_leer:
        teile.insert(0, "Koordinate fehlt")
    elif x_leer or y_leer:
        teile.insert(0, ("X" if x_leer else "Y") + "-Koordinate fehlt")
    elif e is None or n is None:
        teile.insert(0, "Koordinate nicht lesbar")
    elif not _im_bereich(e, n):
        if repariere(r.x_roh, r.y_roh)[2] != "fehlt":
            a2, b2 = _ergaenze_lv95(e, n)
            if _im_bereich(a2, b2):
                teile.insert(0, "LV03-Wert bzw. fehlendes LV95-Präfix")
            else:
                a2, b2 = _ergaenze_lv95(n, e)
                teile.insert(0, "Achsen vertauscht"
                             + (" und LV03-Wert" if (a2, b2) != (n, e) else ""))
        elif _im_bereich(e / 1000, n / 1000):
            teile.insert(0, "Wert um Faktor 1000 zu gross (Dezimaltrennzeichen)")
        elif (len(_ziffern(r.x_roh)) == len(_ziffern(r.y_roh)) == 7 and _im_bereich(
                float(_ziffern(r.y_roh)[0] + _ziffern(r.x_roh)[1:]),
                float(_ziffern(r.x_roh)[0] + _ziffern(r.y_roh)[1:]))):
            teile.insert(0, "Anfangsziffern 2 und 1 vertauscht")
        elif _ziffern(r.x_roh)[1:] == _ziffern(r.y_roh)[1:]:
            teile.insert(0, "X- und Y-Wert identisch (Wert doppelt erfasst)")
        elif PLATZHALTER.search(_ziffern(r.x_roh) + " " + _ziffern(r.y_roh)):
            teile.insert(0, "Platzhalterwert, keine echte Koordinate")
        elif (_tippfehler(r.x_roh, r.e_lv95) or _tippfehler(r.y_roh, r.n_lv95)
              or _tippfehler(r.x_roh, r.n_lv95) or _tippfehler(r.y_roh, r.e_lv95)):
            teile.insert(0, "Tippfehler (Ziffer zu viel oder zu wenig)")
        else:
            teile.insert(0, "Wert ausserhalb der Schweiz")
    elif r.koord_status == "repariert":
        teile.insert(0, "Zahlendreher (zwei Ziffern vertauscht)")
    elif r.koord_status == "unplausibel" or r.koord_status in GEOKODIERT:
        teile.insert(0, "Adresse und Koordinate liegen über 20 km auseinander")
    if r.koord_status == "unplausibel" and teile and not teile[0].startswith("Adresse"):
        teile.append("auch korrigiert über 20 km vom Adressort entfernt, Ursache unklar")
    if r.koord_status == "manuell" and not teile:
        teile.append("Lage anhand Luftbild korrigiert")
    return "; ".join(teile) or None


def quelle(r, befund_text):
    if r.koord_status == "unplausibel":
        if befund_text.startswith("Adresse"):
            return "Koordinate vermutlich korrekt, Adresse ist evtl. Firmen- oder Postadresse"
        return "kein Vorschlag, bitte prüfen"
    if r.koord_status in ("repariert", "ok"):
        return "rechnerisch korrigiert"
    if r.koord_status in GEOKODIERT:
        genau = str(r.koord_quelle or "").split("/")[-1] or "Adresse"
        return f"swisstopo-Adresssuche ({genau})"
    if r.koord_status == "manuell":
        return "von Hand gesetzt (Luftbild map.geo.admin.ch)"
    return "kein Vorschlag"


def plz_distanz(df):
    """Abstand jeder Anlage zum Median der übrigen verorteten Anlagen gleicher PLZ."""
    gueltig = df[df.e_lv95.notna() & ~df.koord_status.isin(["unplausibel", "fehlt"])]
    abstand = {}
    for idx, r in df[df.koord_status == "unplausibel"].iterrows():
        andere = gueltig[(gueltig.plz == r.plz) & (gueltig.index != idx)]
        if len(andere) >= 2 and pd.notna(r.e_lv95):
            abstand[idx] = round(distanz_luftlinie_km(
                r.e_lv95, r.n_lv95, andere.e_lv95.median(), andere.n_lv95.median()))
    return abstand


def ist_test(df):
    return (df.standortname.fillna("").str.contains(TEST)
            | df.firma.fillna("").str.contains(TEST))


def koordinaten(df):
    df = df[~ist_test(df)]
    abstand = plz_distanz(df)
    zeilen = []
    for idx, r in df.iterrows():
        text = befund(r)
        if not text:
            continue
        e0, n0, st0 = repariere(r.x_roh, r.y_roh)
        vorschlag = pd.notna(r.e_lv95) and r.koord_status != "unplausibel"
        zeilen.append({
            "Betriebsnummer": r.betriebsnummer, "Standortname": r.standortname,
            "Firma": r.firma,
            "Adresse": " ".join(str(v) for v in (r.strasse, r.hausnummer) if pd.notna(v) and v),
            "PLZ": r.plz, "Ort": r.ort, "Kanton": r.kanton, "Anlagentyp": r.typ,
            "Status Anlage": r.status,
            "X-Koordinate Export": r.x_roh, "Y-Koordinate Export": r.y_roh,
            "Befund": text,
            "Distanz zum Adressort (km)": abstand.get(idx),
            "E Vorschlag (LV95)": round(r.e_lv95) if vorschlag else None,
            "N Vorschlag (LV95)": round(r.n_lv95) if vorschlag else None,
            "Quelle Vorschlag": quelle(r, text) if vorschlag or r.koord_status == "unplausibel"
                                else "kein Vorschlag",
            "Genauigkeit": ("nur ortsgenau, kann einige km abweichen"
                            if r.koord_status == "geokodiert_grob" else ""),
            "Karte Export": KARTE.format(e=e0, n=n0) if st0 != "fehlt" else "",
            "Karte Vorschlag": KARTE.format(e=r.e_lv95, n=r.n_lv95) if vorschlag else "",
        })
    return pd.DataFrame(zeilen).sort_values(["Kanton", "Ort"], na_position="last")


def testdaten(df):
    return df[ist_test(df)][["betriebsnummer", "standortname", "firma", "ort", "kanton",
                             "status"]].rename(columns={
        "betriebsnummer": "Betriebsnummer", "standortname": "Standortname", "firma": "Firma",
        "ort": "Ort", "kanton": "Kanton", "status": "Status"})


def hinweise(datenstand, df, koord, test):
    ohne = (koord.Befund == "Koordinate fehlt").sum()
    return pd.DataFrame({"Hinweise": [
        f"Grundlage: BAFU-Datenexport Abfallanlagen, Datenstand {datenstand}, "
        f"{len(df)} Anlagen",
        f"Blatt «Koordinaten»: {len(koord)} Anlagen mit fehlender oder auffälliger "
        f"Koordinate, davon {ohne} ganz ohne Koordinate (Testdatensätze nicht mitgezählt)",
        f"Blatt «Testdatensätze»: {len(test)} Einträge, die nach Testdaten aussehen",
        "",
        "Die Vorschläge sind Hinweise und nicht vor Ort geprüft.",
        "«rechnerisch korrigiert»: vertauschte Achsen, LV03-Werte oder Zahlendreher umgerechnet.",
        "«swisstopo-Adresssuche»: Treffer über die Adresse der Anlage (api3.geo.admin.ch). "
        "Bei grossen Arealen wie Deponien kann die Adresse vom Betriebsstandort abweichen.",
        "«Adresse und Koordinate liegen über 20 km auseinander»: Die Koordinate liegt über "
        "20 km vom Median der übrigen Anlagen mit gleicher PLZ. Oft ist die Koordinate "
        "richtig und die Adresse ist die Firmen- oder Postadresse (z. B. Deponien mit "
        "Adresse Chur). Deshalb ohne Korrekturvorschlag.",
        "Die Kartenlinks öffnen map.geo.admin.ch mit Luftbild am jeweiligen Punkt.",
        "",
        "Erstellt mit https://github.com/tobQmen/AbfallTool",
    ]})


def formatieren(ws, breiten, links=()):
    for zelle in ws[1]:
        zelle.font = Font(name="Arial", bold=True, color="FFFFFF")
        zelle.fill = PatternFill("solid", fgColor="37474F")
        zelle.alignment = Alignment(wrap_text=True, vertical="top")
    for zeile in ws.iter_rows(min_row=2):
        for zelle in zeile:
            zelle.font = Font(name="Arial")
            zelle.alignment = Alignment(vertical="top", wrap_text=True)
    for spalte, breite in breiten.items():
        ws.column_dimensions[spalte].width = breite
    for spalte in links:  # Links als anklickbare Hyperlinks mit kurzem Text
        for zelle in ws[spalte][1:]:
            if zelle.value:
                zelle.hyperlink = zelle.value
                zelle.value = "öffnen"
                zelle.font = Font(name="Arial", color="0563C1", underline="single")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="abfallanlagen.db")
    ap.add_argument("--xlsx", default="datenqualitaet_abfallanlagen.xlsx")
    args = ap.parse_args()

    with contextlib.closing(sqlite3.connect(args.db)) as con:
        stand = con.execute("SELECT datenstand FROM import_info").fetchone()[0]
        spalten = {r[1] for r in con.execute("PRAGMA table_info(anlagen)")}
        kq = "koord_quelle" if "koord_quelle" in spalten else "NULL AS koord_quelle"
        df = pd.read_sql(f"SELECT betriebsnummer, standortname, firma, strasse, hausnummer, "
                         f"plz, ort, kanton, typ, status, x_roh, y_roh, e_lv95, n_lv95, "
                         f"koord_status, {kq} FROM anlagen", con)
    koord, test = koordinaten(df), testdaten(df)

    with pd.ExcelWriter(args.xlsx, engine="openpyxl") as w:
        koord.to_excel(w, sheet_name="Koordinaten", index=False)
        test.to_excel(w, sheet_name="Testdatensätze", index=False)
        hinweise(stand, df, koord, test).to_excel(w, sheet_name="Hinweise", index=False)
        formatieren(w.sheets["Koordinaten"],
                    {"A": 13, "B": 32, "C": 26, "D": 20, "E": 7, "F": 16, "G": 8, "H": 18,
                     "I": 10, "J": 14, "K": 14, "L": 30, "M": 10, "N": 12, "O": 12, "P": 28,
                     "Q": 22, "R": 9, "S": 9}, links=("R", "S"))
        formatieren(w.sheets["Testdatensätze"],
                    {"A": 13, "B": 40, "C": 30, "D": 16, "E": 8, "F": 10})
        formatieren(w.sheets["Hinweise"], {"A": 110})

    aktiv = df.status == "Aktiv"
    print(f"Datenstand {stand}: {len(df)} Anlagen, im Export ohne vollständige Koordinate: "
          f"{(df.x_roh.isna() | df.y_roh.isna()).sum()} (davon aktiv "
          f"{((df.x_roh.isna() | df.y_roh.isna()) & aktiv).sum()})")
    print(f"Koordinaten: {len(koord)} Anlagen (ohne Testdatensätze)")
    for text, anzahl in koord.Befund.str.split(";").str[0].value_counts().items():
        print(f"  {anzahl:5}  {text}")
    print(f"Testdatensätze: {len(test)}")
    print(f"-> {args.xlsx}")


if __name__ == "__main__":
    main()
