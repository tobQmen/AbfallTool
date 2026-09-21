"""Geokodierung über den Suchdienst von swisstopo (api3.geo.admin.ch).

Als Modul:
    from geokodierung import suche
    treffer = suche("Seestrasse 12 6052 Hergiswil")

Als Skript (ergänzt fehlende Anlagenkoordinaten in der Datenbank):
    python geokodierung.py                      # alle mit Status 'fehlt'
    python geokodierung.py --test "Sedrun"      # einzelne Abfrage prüfen

Es werden alle Suchvarianten geprüft und der genaueste Treffer genommen; ein nur
ortsgenauer Treffer kommt erst zum Zug, wenn keine Variante eine Adresse liefert.

Anlagen mit Status 'unplausibel' werden bewusst nicht geokodiert: Dort ist meist die
Adresse die Firmen- oder Postadresse, die Koordinate aber richtig. Ein Adresstreffer
würde den Standort verschlechtern. Diese Fälle mit kontrolle.py prüfen und bei Bedarf
mit korrekturen.py von Hand setzen.

Ergebnisse werden in geocode_cache.json zwischengespeichert, damit ein zweiter
Lauf keine erneuten Abfragen auslöst.
"""
import argparse
import contextlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

from koordinaten import repariere, lv95_zu_wgs84

URL = "https://api3.geo.admin.ch/rest/services/ech/SearchServer"
CACHE = "geocode_cache.json"
PAUSE = 0.15  # Sekunden zwischen Abfragen, um den Dienst nicht zu überlasten

# Herkunft des Treffers -> Genauigkeit
GENAU = {"address": "Adresse", "parcel": "Parzelle"}
UNGENAU = {"zipcode": "Ortschaft", "gg25": "Gemeinde", "sn25": "Ortsname",
           "district": "Bezirk", "kantone": "Kanton"}


def _cache_laden(datei=CACHE):
    if os.path.exists(datei):
        with open(datei, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _cache_speichern(cache, datei=CACHE):
    with open(datei, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def _abfrage(text, timeout=20):
    """Rohantwort des Suchdienstes. Wirft bei Netzwerk- oder Serverfehlern."""
    params = urllib.parse.urlencode({
        "searchText": text, "type": "locations", "sr": "2056", "limit": "5"})
    req = urllib.request.Request(f"{URL}?{params}", headers={"User-Agent": "Abfalltool/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def suche(text, cache=None, timeout=20):
    """Gibt {'e','n','lat','lon','genauigkeit','label'} zurück oder None."""
    if cache is not None and text in cache:
        return cache[text]
    antwort = _abfrage(text, timeout)
    ergebnis = None
    for treffer in antwort.get("results", []):
        a = treffer.get("attrs", {})
        herkunft = a.get("origin", "")
        # Im geoadmin-Dienst ist attrs.y der Ost- und attrs.x der Nordwert.
        e, n, status = repariere(a.get("y"), a.get("x"))
        if status == "fehlt":
            continue
        lat, lon = lv95_zu_wgs84(e, n)
        ergebnis = {"e": e, "n": n, "lat": round(lat, 6), "lon": round(lon, 6),
                    "genauigkeit": GENAU.get(herkunft) or UNGENAU.get(herkunft) or herkunft,
                    "grob": herkunft not in GENAU,
                    "label": a.get("label", "").replace("<b>", "").replace("</b>", "")}
        if herkunft in GENAU:  # genauen Treffer sofort nehmen
            break
    if cache is not None:
        cache[text] = ergebnis
    return ergebnis


def flurname(standortname):
    """Objekt-/Flurname aus dem Standortnamen, sonst None.

    Beispiel: 'Lötscher Tiefbau AG, Deponie Typ A, Hochrüti' -> 'Hochrüti'.
    Reine Nummern und Codes werden verworfen.
    """
    if not standortname:
        return None
    teil = standortname.split(",")[-1].strip()
    teil = re.sub(r"\bN\.?:?\s*\d+\b", "", teil).strip(" -–")
    if len(teil) < 4 or not re.search(r"[A-Za-zÄÖÜäöü]{4}", teil):
        return None
    if re.fullmatch(r"(?i)(deponie|typ [a-e]|kies|aushub)\s*[a-e]?", teil):
        return None
    return teil


def suchtexte(anlage):
    """Abfragevarianten von genau nach grob."""
    strasse, nr, plz, ort = (anlage["strasse"] or "", anlage["hausnummer"] or "",
                             anlage["plz"] or "", anlage["ort"] or "")
    name = flurname(anlage["standortname"] if "standortname" in anlage.keys() else None)
    varianten = []
    if strasse and nr:
        varianten.append(f"{strasse} {nr} {plz} {ort}")
    if name:
        varianten.append(f"{name} {ort}")
    if strasse:
        varianten.append(f"{strasse} {plz} {ort}")
    if plz or ort:
        varianten.append(f"{plz} {ort}")
    varianten = [re.sub(r"\s+", " ", v).strip() for v in varianten]
    return [v for v in dict.fromkeys(varianten) if v]


def ergaenze_db(db="abfallanlagen.db", limit=None):
    with contextlib.closing(sqlite3.connect(db)) as con:
        con.row_factory = sqlite3.Row
        if "koord_quelle" not in {r[1] for r in con.execute("PRAGMA table_info(anlagen)")}:
            con.execute("ALTER TABLE anlagen ADD COLUMN koord_quelle TEXT")
            con.execute("UPDATE anlagen SET koord_quelle = 'Export' WHERE e_lv95 IS NOT NULL")
            con.commit()
        offen = con.execute("SELECT * FROM anlagen WHERE koord_status = 'fehlt'").fetchall()
        if limit:
            offen = offen[:limit]
        print(f"{len(offen)} Anlagen zu geokodieren …")

        cache = _cache_laden()
        gefunden = grob = fehlgeschlagen = 0
        try:
            for i, anlage in enumerate(offen, start=1):
                treffer = grober_treffer = None
                for text in suchtexte(anlage):
                    frisch = text not in cache
                    try:
                        gefunden_jetzt = suche(text, cache)
                    except Exception as e:
                        print(f"  FEHLER bei '{text}': {type(e).__name__}: {e}")
                        gefunden_jetzt = None
                    if frisch:
                        time.sleep(PAUSE)
                    if not gefunden_jetzt:
                        continue
                    if not gefunden_jetzt["grob"]:
                        treffer = gefunden_jetzt  # genauer Treffer: fertig
                        break
                    grober_treffer = grober_treffer or gefunden_jetzt
                treffer = treffer or grober_treffer  # grob nur als letzte Wahl
                if not treffer:
                    fehlgeschlagen += 1
                    continue
                gefunden += 1
                grob += bool(treffer["grob"])
                con.execute(
                    "UPDATE anlagen SET e_lv95=?, n_lv95=?, lat=?, lon=?, "
                    "koord_status=?, koord_quelle=? WHERE betriebsnummer=?",
                    (treffer["e"], treffer["n"], treffer["lat"], treffer["lon"],
                     "geokodiert_grob" if treffer["grob"] else "geokodiert",
                     f"swisstopo/{treffer['genauigkeit']}", anlage["betriebsnummer"]))
                if i % 50 == 0:
                    con.commit()
                    _cache_speichern(cache)
                    print(f"  {i}/{len(offen)} …")
        finally:
            con.commit()
            _cache_speichern(cache)
        print(f"\nGeokodiert: {gefunden} (davon {grob} nur ortsgenau), "
              f"ohne Treffer: {fehlgeschlagen}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="abfallanlagen.db")
    ap.add_argument("--auch-unplausibel", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--limit", type=int, help="nur die ersten N (zum Ausprobieren)")
    ap.add_argument("--test", help="einzelnen Suchtext abfragen und Rohantwort zeigen")
    args = ap.parse_args()

    if args.test:
        try:
            antwort = _abfrage(args.test)
        except Exception as e:
            sys.exit(f"Abfrage fehlgeschlagen: {type(e).__name__}: {e}")
        print(json.dumps(antwort, ensure_ascii=False, indent=1)[:2000])
        print("\nAusgewertet:", suche(args.test))
        return
    if args.auch_unplausibel:
        print("Hinweis: --auch-unplausibel wird nicht mehr unterstützt. Unplausible "
              "Koordinaten bitte mit kontrolle.py prüfen und mit korrekturen.py setzen.")
    ergaenze_db(args.db, args.limit)


if __name__ == "__main__":
    main()
