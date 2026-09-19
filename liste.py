"""Erstellt aus einer Codevorlage eine Entsorgungsliste mit Abnehmervorschlägen.

Struktur nach der BAFU-Entsorgungstabelle Bauabfälle (VVEA-Vollzugshilfe):
Abfallart, Details, LVA-Code, genereller Entsorgungsweg, Verwertungspflicht,
Entsorgungsort und Mengen in m3 fest, m3 lose und Tonnen.

Aufruf:
    python liste.py --vorlagen
    python liste.py bauabfaelle --baustelle Sedrun
    python liste.py tunnel --baustelle Sedrun --umkreis 40 --vorschlaege 3 --excel liste.xlsx
"""
import argparse
import json
import sqlite3

import pandas as pd

from abfrage import anlagen_fuer_code, normalisiere_code
from baustellen import aufloesen
from bewertung import stufe as verfahrensstufe

VORLAGEN = "vorlagen.json"

# Reihenfolge der Spalten im Export: BAFU-Tabelle, dann Vorschlag, dann Baustellenbetrieb
SPALTEN = ["abschnitt", "abfallart", "details", "code", "entsorgungsweg", "v_pflicht",
           "rang", "firma", "standort", "ort", "kanton", "typ", "verfahren",
           "bestes_verfahren", "stufe", "stufe_text", "nur_zwischenlager",
           "betriebsnummer", "distanz_km", "distanz_quelle", "fahrzeit_min", "gueltig_bis",
           "menge_m3_fest", "menge_m3_lose", "menge_t",
           "transportunternehmen", "transportmittel", "bemerkung", "hinweis"]


def lade_vorlage(name, datei=VORLAGEN):
    with open(datei, encoding="utf-8") as f:
        inhalt = json.load(f)
    vorlagen = inhalt.get("vorlagen", inhalt)
    if name not in vorlagen:
        raise SystemExit(f"Vorlage '{name}' nicht gefunden. Verfügbar: {', '.join(vorlagen)}")
    return vorlagen[name]


def positionen(vorlage):
    """Liefert (Abschnittstitel, Position) für beide Vorlagenformate."""
    if "abschnitte" in vorlage:
        for abschnitt in vorlage["abschnitte"]:
            for pos in abschnitt["positionen"]:
                yield abschnitt["titel"], pos
    else:
        for pos in vorlage["positionen"]:
            yield "", pos


def erstelle(con, vorlage, baustelle, umkreis=None, vorschlaege=3,
             nur_endverfahren=False, stichtag=None, sortierung="auto"):
    zeilen = []
    for abschnitt, pos in positionen(vorlage):
        code = normalisiere_code(pos["code"])
        # "auto": bei Verwertungspflicht zuerst nach Verfahrensstufe sortieren
        wie = sortierung
        if sortierung == "auto":
            wie = "verwertung" if pos.get("v_pflicht") else "distanz"
        treffer = anlagen_fuer_code(con, code, stichtag, baustelle, nur_endverfahren, wie)
        if not treffer.empty and umkreis:
            treffer = treffer[treffer.distanz_km <= umkreis]
        basis = {"abschnitt": abschnitt, "abfallart": pos.get("abfallart", ""),
                 "details": pos.get("details", ""), "code": code,
                 "entsorgungsweg": pos.get("entsorgungsweg", ""),
                 "v_pflicht": "V" if pos.get("v_pflicht") else "",
                 "menge_m3_fest": pos.get("menge_m3_fest"),
                 "menge_m3_lose": pos.get("menge_m3_lose"),
                 "menge_t": pos.get("menge_t")}
        if treffer.empty:
            zeilen.append({**basis, "hinweis":
                           "keine Anlage im Umkreis" if umkreis else "keine Anlage gefunden"})
            continue
        for rang, (_, a) in enumerate(treffer.head(vorschlaege).iterrows(), start=1):
            hinweise = []
            if pos.get("v_pflicht") and a.stufe >= 3:
                hinweise.append("V-Pflicht: keine Verwertung, Begründung nötig")
            if a.nur_zwischenlager:
                hinweise.append("nur Zwischenlager/Umschlag")
            if a.koord_status not in ("ok", "manuell", "geokodiert"):
                hinweise.append(f"Koordinate {a.koord_status}")
            zeilen.append({**basis, "rang": rang, "distanz_km": a.distanz_km,
                           "distanz_quelle": a.distanz_quelle, "fahrzeit_min": a.fahrzeit_min,
                           "firma": a.firma, "standort": a.standortname, "ort": a.ort,
                           "kanton": a.kanton, "typ": a.typ, "verfahren": a.verfahren,
                           "stufe": a.stufe, "stufe_text": a.stufe_text,
                           "bestes_verfahren": a.bestes_verfahren,
                           "nur_zwischenlager": a.nur_zwischenlager,
                           "betriebsnummer": a.betriebsnummer, "gueltig_bis": a.gueltig_bis,
                           "hinweis": "; ".join(hinweise)})
    return pd.DataFrame(zeilen).reindex(columns=SPALTEN)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vorlage", nargs="?")
    ap.add_argument("--db", default="abfallanlagen.db")
    ap.add_argument("--baustelle", help="Name einer gespeicherten Baustelle oder Koordinate")
    ap.add_argument("--umkreis", type=float, help="max. Distanz in km")
    ap.add_argument("--vorschlaege", type=int, default=3, help="Anlagen pro Position")
    ap.add_argument("--nur-endverfahren", action="store_true")
    ap.add_argument("--stichtag")
    ap.add_argument("--sortierung", choices=["auto", "distanz", "verwertung"], default="auto",
                    help="auto: bei Verwertungspflicht nach Verfahrensstufe, sonst nach Distanz")
    ap.add_argument("--excel")
    ap.add_argument("--csv")
    ap.add_argument("--vorlagen", action="store_true")
    args = ap.parse_args()

    if args.vorlagen or not args.vorlage:
        with open(VORLAGEN, encoding="utf-8") as f:
            inhalt = json.load(f)
        for name, v in inhalt.get("vorlagen", inhalt).items():
            anzahl = sum(1 for _ in positionen(v))
            print(f"{name:14} {v['bezeichnung']}  ({anzahl} Positionen)")
        if "quelle" in inhalt:
            print(f"\n{inhalt['quelle']}")
        return
    if not args.baustelle:
        raise SystemExit("--baustelle fehlt")

    vorlage = lade_vorlage(args.vorlage)
    con = sqlite3.connect(args.db)
    stand = con.execute("SELECT datenstand FROM import_info").fetchone()[0]
    df = erstelle(con, vorlage, aufloesen(args.baustelle), args.umkreis,
                  args.vorschlaege, args.nur_endverfahren, args.stichtag, args.sortierung)

    print(f"{vorlage['bezeichnung']} – Baustelle {args.baustelle} (Datenstand {stand})\n")
    beste = df[df.rang.isna() | (df.rang == 1)]
    with pd.option_context("display.max_colwidth", 26, "display.width", 250):
        for abschnitt, teil in beste.groupby("abschnitt", sort=False):
            if abschnitt:
                print(abschnitt)
            print(teil[["code", "abfallart", "v_pflicht", "distanz_km", "firma", "ort",
                        "bestes_verfahren", "stufe_text", "hinweis"]].to_string(index=False), "\n")
    ohne = df[df.rang.isna()]
    if not ohne.empty:
        print(f"Ohne Vorschlag: {', '.join(sorted(set(ohne.code)))}")

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"Alle Vorschläge: {args.csv}")
    if args.excel:
        df.to_excel(args.excel, index=False, sheet_name="Entsorgungsliste")
        print(f"Alle Vorschläge: {args.excel}")


if __name__ == "__main__":
    main()
