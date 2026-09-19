"""Gespeicherte Baustellen (Datei baustellen.json im Arbeitsverzeichnis).

Aufruf:
    python baustellen.py                                  # alle anzeigen
    python baustellen.py Sedrun 2706600,1166200 --notiz "Portal Nord"
    python baustellen.py Sedrun --loeschen
"""
import argparse
import json
import os

from koordinaten import parse_standort, lv95_zu_wgs84

DATEI = "baustellen.json"


def laden(datei=DATEI):
    if not os.path.exists(datei):
        return {}
    with open(datei, encoding="utf-8") as f:
        return json.load(f)


def speichern(daten, datei=DATEI):
    with open(datei, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=2)


def hole(name, datei=DATEI):
    """Gibt (E, N) einer gespeicherten Baustelle zurück, sonst None."""
    b = laden(datei).get(name)
    return (b["e_lv95"], b["n_lv95"]) if b else None


def aufloesen(text, datei=DATEI):
    """Name einer gespeicherten Baustelle ODER Koordinatenpaar -> (E, N)."""
    treffer = hole(text, datei)
    if treffer:
        return treffer
    try:
        return parse_standort(text)
    except ValueError:
        bekannt = ", ".join(laden(datei)) or "keine gespeichert"
        raise SystemExit(f"Baustelle '{text}' nicht gefunden und keine gültige Koordinate.\n"
                         f"Gespeicherte Baustellen: {bekannt}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("koordinate", nargs="*", help="LV95 'E,N' oder WGS84 'lat,lon'")
    ap.add_argument("--notiz", default="")
    ap.add_argument("--loeschen", action="store_true")
    ap.add_argument("--datei", default=DATEI)
    args = ap.parse_args()
    args.koordinate = " ".join(args.koordinate) if args.koordinate else None
    daten = laden(args.datei)

    if not args.name:
        if not daten:
            print("Noch keine Baustelle gespeichert.")
        for n, b in daten.items():
            print(f"{n:20} {b['e_lv95']:>10.0f} / {b['n_lv95']:>10.0f}  {b.get('notiz','')}")
        return

    if args.loeschen:
        if daten.pop(args.name, None) is None:
            raise SystemExit(f"'{args.name}' ist nicht gespeichert.")
        speichern(daten, args.datei)
        print(f"'{args.name}' gelöscht.")
        return

    if not args.koordinate:
        raise SystemExit("Koordinate fehlt, z. B.: python baustellen.py Sedrun 2706600,1166200")
    e, n = parse_standort(args.koordinate)
    lat, lon = lv95_zu_wgs84(e, n)
    daten[args.name] = {"e_lv95": e, "n_lv95": n, "lat": round(lat, 6),
                        "lon": round(lon, 6), "notiz": args.notiz}
    speichern(daten, args.datei)
    print(f"'{args.name}' gespeichert: {e:.0f} / {n:.0f}  ({lat:.5f}, {lon:.5f})")


if __name__ == "__main__":
    main()
