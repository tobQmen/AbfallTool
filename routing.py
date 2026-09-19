"""Strassendistanzen über eine lokale Valhalla-Instanz, mit dauerhaftem Cache.

Der Cache liegt in einer eigenen Datei (routen.db) und überlebt jeden Neuimport
der Anlagendaten. Einmal berechnete Strecken brauchen Valhalla nie wieder.

Aufruf (füllt den Cache, braucht eine laufende Valhalla-Instanz):
    python routing.py --baustelle Sedrun --vorlage tunnel --top 20
    python routing.py --baustelle Sedrun --code "17 05 06" --umkreis 60
    python routing.py --test                       # Erreichbarkeit prüfen
    python routing.py --cache                      # was ist für welche Baustelle gespeichert
    python routing.py --baustelle Sedrun --vorlage tunnel --hoehen   # mit Höhenprofil

Danach arbeiten abfrage.py und liste.py ohne Valhalla mit den gespeicherten Werten.
"""
import argparse
import contextlib
import datetime as dt
import json
import sqlite3
import urllib.error
import urllib.request

from koordinaten import lv95_zu_wgs84

CACHE_DB = "routen.db"
HOST = "http://localhost:8002"
RASTER = 10  # Koordinaten auf 10 m runden, damit der Cache trifft
BATCH = 50   # Ziele pro Anfrage

# Lastwagen: Werte für einen typischen 4-Achser im Baustellenverkehr
LKW = {"height": 4.0, "width": 2.55, "length": 10.0,
       "weight": 32.0, "axle_load": 10.0, "hazmat": False}


def _raster(wert):
    return round(wert / RASTER) * RASTER


@contextlib.contextmanager
def _cache(datei=CACHE_DB):
    con = sqlite3.connect(datei)
    con.execute("""CREATE TABLE IF NOT EXISTS routen (
        e1 INTEGER, n1 INTEGER, e2 INTEGER, n2 INTEGER, costing TEXT,
        km REAL, minuten REAL, berechnet_am TEXT, start_name TEXT,
        aufstieg_m REAL, abstieg_m REAL,
        PRIMARY KEY (e1, n1, e2, n2, costing))""")
    vorhanden = {r[1] for r in con.execute("PRAGMA table_info(routen)")}
    for spalte, typ in (("start_name", "TEXT"), ("aufstieg_m", "REAL"), ("abstieg_m", "REAL")):
        if spalte not in vorhanden:
            con.execute(f"ALTER TABLE routen ADD COLUMN {spalte} {typ}")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def gespeicherte_distanzen(start, ziele, costing="truck", datei=CACHE_DB):
    """{(e, n): (km, minuten)} für alle Ziele, die bereits im Cache liegen."""
    e1, n1 = _raster(start[0]), _raster(start[1])
    treffer = {}
    with _cache(datei) as con:
        for e, n in ziele:
            zeile = con.execute(
                "SELECT km, minuten FROM routen WHERE e1=? AND n1=? AND e2=? AND n2=? AND costing=?",
                (e1, n1, _raster(e), _raster(n), costing)).fetchone()
            if zeile:
                treffer[(e, n)] = zeile
    return treffer


def _matrix(host, start, ziele, costing, timeout):
    """Eine Valhalla-Matrixabfrage: ein Start, viele Ziele."""
    lat1, lon1 = lv95_zu_wgs84(*start)
    anfrage = {
        "sources": [{"lat": lat1, "lon": lon1}],
        "targets": [dict(zip(("lat", "lon"), lv95_zu_wgs84(e, n))) for e, n in ziele],
        "costing": costing,
        "units": "kilometers",
    }
    if costing == "truck":
        anfrage["costing_options"] = {"truck": LKW}
    req = urllib.request.Request(
        f"{host}/sources_to_targets", method="POST",
        data=json.dumps(anfrage).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["sources_to_targets"][0]


def berechne(start, ziele, host=HOST, costing="truck", datei=CACHE_DB, timeout=120,
             fortschritt=True, start_name=None):
    """Berechnet fehlende Strecken und legt sie im Cache ab. Gibt alle Treffer zurück."""
    bekannt = gespeicherte_distanzen(start, ziele, costing, datei)
    offen = [z for z in ziele if z not in bekannt]
    if not offen:
        return bekannt
    e1, n1 = _raster(start[0]), _raster(start[1])
    heute = dt.date.today().isoformat()
    for i in range(0, len(offen), BATCH):
        teil = offen[i:i + BATCH]
        antwort = _matrix(host, start, teil, costing, timeout)
        with _cache(datei) as con:
            for ziel, eintrag in zip(teil, antwort):
                km, sekunden = eintrag.get("distance"), eintrag.get("time")
                if km is None:  # von Valhalla nicht erreichbar
                    continue
                minuten = round(sekunden / 60, 1) if sekunden is not None else None
                bekannt[ziel] = (round(km, 1), minuten)
                con.execute(
                    "INSERT OR REPLACE INTO routen (e1,n1,e2,n2,costing,km,minuten,"
                    "berechnet_am,start_name) VALUES (?,?,?,?,?,?,?,?,?)",
                    (e1, n1, _raster(ziel[0]), _raster(ziel[1]), costing,
                     round(km, 1), minuten, heute, start_name))
        if fortschritt:
            print(f"  {min(i + BATCH, len(offen))}/{len(offen)} Strecken berechnet …")
    return bekannt


def _post(host, pfad, daten, timeout):
    req = urllib.request.Request(
        f"{host}/{pfad}", method="POST", data=json.dumps(daten).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def hoehenprofil(host, start, ziel, costing="truck", timeout=60):
    """Kumulierte Steigung und Gefälle einer Route in Metern.

    Braucht Höhendaten im Valhalla-Container (build_elevation=True).
    """
    lat1, lon1 = lv95_zu_wgs84(*start)
    lat2, lon2 = lv95_zu_wgs84(*ziel)
    anfrage = {"locations": [{"lat": lat1, "lon": lon1}, {"lat": lat2, "lon": lon2}],
               "costing": costing, "units": "kilometers"}
    if costing == "truck":
        anfrage["costing_options"] = {"truck": LKW}
    route = _post(host, "route", anfrage, timeout)
    form = route["trip"]["legs"][0]["shape"]
    hoehen = _post(host, "height", {"encoded_polyline": form, "range": True}, timeout)
    werte = [punkt[1] for punkt in hoehen.get("range_height", []) if punkt[1] is not None]
    auf = sum(max(0, b - a) for a, b in zip(werte, werte[1:]))
    ab = sum(max(0, a - b) for a, b in zip(werte, werte[1:]))
    return round(auf), round(ab)


def ergaenze_hoehen(start, host=HOST, costing="truck", datei=CACHE_DB, timeout=60):
    """Holt Steigung und Gefälle für alle Strecken dieser Baustelle ohne Höhenangabe."""
    e1, n1 = _raster(start[0]), _raster(start[1])
    with _cache(datei) as con:
        offen = con.execute(
            "SELECT e2, n2 FROM routen WHERE e1=? AND n1=? AND costing=? AND aufstieg_m IS NULL",
            (e1, n1, costing)).fetchall()
    print(f"{len(offen)} Strecken ohne Höhenprofil …")
    fertig = 0
    for i, (e2, n2) in enumerate(offen, start=1):
        try:
            auf, ab = hoehenprofil(host, start, (e2, n2), costing, timeout)
        except Exception as e:
            print(f"  FEHLER bei Ziel {e2}/{n2}: {type(e).__name__}: {e}")
            continue
        with _cache(datei) as con:
            con.execute("UPDATE routen SET aufstieg_m=?, abstieg_m=? "
                        "WHERE e1=? AND n1=? AND e2=? AND n2=? AND costing=?",
                        (auf, ab, e1, n1, e2, n2, costing))
        fertig += 1
        if i % 20 == 0:
            print(f"  {i}/{len(offen)} …")
    print(f"Höhenprofil ergänzt: {fertig}")


def erreichbar(host=HOST, timeout=10):
    """Prüft, ob Valhalla antwortet. Gibt (True, Text) oder (False, Fehler) zurück."""
    try:
        ergebnis = _matrix(host, (2666000, 1211000), [(2683000, 1247000)], "truck", timeout)
        km = ergebnis[0].get("distance")
        return True, f"Valhalla antwortet, Testroute Luzern–Zug: {km} km"
    except urllib.error.URLError as e:
        return False, f"Valhalla nicht erreichbar unter {host}: {e.reason}"
    except Exception as e:
        return False, f"Unerwartete Antwort von {host}: {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--db", default="abfallanlagen.db")
    ap.add_argument("--baustelle")
    ap.add_argument("--code", help="einzelner Abfallcode")
    ap.add_argument("--vorlage", help="alle Codes einer Vorlage")
    ap.add_argument("--umkreis", type=float, default=80, help="Vorfilter Luftlinie in km")
    ap.add_argument("--top", type=int, default=20, help="nächstgelegene Anlagen pro Code")
    ap.add_argument("--costing", default="truck", choices=["truck", "auto"])
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--cache", action="store_true", help="zeigt, für welche Baustellen gerechnet wurde")
    ap.add_argument("--hoehen", action="store_true",
                    help="Steigung und Gefälle pro Strecke nachtragen (braucht build_elevation=True)")
    args = ap.parse_args()

    if args.cache:
        with _cache() as con:
            zeilen = con.execute(
                "SELECT coalesce(start_name,'(ohne Name)'), e1, n1, costing, count(*), "
                "min(berechnet_am), max(berechnet_am) FROM routen GROUP BY 1,2,3,4").fetchall()
        if not zeilen:
            print(f"{CACHE_DB} enthält noch keine Strecken.")
        for name, e1, n1, costing, anzahl, von, bis in zeilen:
            print(f"{name:20} {e1}/{n1}  {costing:6} {anzahl:5} Strecken  {von} bis {bis}")
        return

    if args.test:
        ok, text = erreichbar(args.host)
        print(text)
        raise SystemExit(0 if ok else 1)
    if not args.baustelle or not (args.code or args.vorlage):
        raise SystemExit("--baustelle und --code oder --vorlage angeben")

    # erst hier nötig: braucht pandas und die Anlagendatenbank
    from abfrage import anlagen_fuer_code, normalisiere_code
    from baustellen import aufloesen
    from liste import lade_vorlage, positionen

    start = aufloesen(args.baustelle)
    con = sqlite3.connect(args.db)
    codes = ([normalisiere_code(args.code)] if args.code else
             [normalisiere_code(p["code"]) for _, p in positionen(lade_vorlage(args.vorlage))])

    ziele = set()
    for code in dict.fromkeys(codes):
        treffer = anlagen_fuer_code(con, code, baustelle=start)
        treffer = treffer[treffer.distanz_luftlinie_km.notna()]
        if args.umkreis:
            treffer = treffer[treffer.distanz_luftlinie_km <= args.umkreis]
        for _, a in treffer.head(args.top).iterrows():
            ziele.add((a.e_lv95, a.n_lv95))

    print(f"{len(codes)} Codes, {len(ziele)} verschiedene Anlagenstandorte")
    ok, text = erreichbar(args.host)
    if not ok:
        raise SystemExit(text)
    ergebnis = berechne(start, sorted(ziele), args.host, args.costing,
                        start_name=args.baustelle)
    print(f"Im Cache: {len(ergebnis)} Strecken ({CACHE_DB})")
    if args.hoehen:
        ergaenze_hoehen(start, args.host, args.costing)


if __name__ == "__main__":
    main()
