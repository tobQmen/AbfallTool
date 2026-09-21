"""Koordinaten: Parsen, Reparieren und Umrechnen LV95 -> WGS84.

Die Umrechnung nutzt die Näherungsformel von swisstopo (Genauigkeit ca. 1 m),
damit keine zusätzlichen Bibliotheken nötig sind.
"""
import math
import re

# Plausibler Bereich LV95 für CH + FL
E_MIN, E_MAX = 2_480_000, 2_840_000
N_MIN, N_MAX = 1_070_000, 1_300_000


def parse_zahl(wert):
    """'2'760'020.0' -> 2760020.0; offensichtliche Tippfehler (O statt 0) werden korrigiert."""
    if wert is None:
        return None
    s = str(wert).strip()
    if not s or s.lower() == "nan":
        return None
    s = s.replace("'", "").replace("’", "").replace(" ", "")
    s = s.replace("O", "0").replace("o", "0")
    if s.count(",") == 1 and "." not in s:
        s = s.replace(",", ".")
    s = s.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    return float(s)


def _im_bereich(e, n):
    return E_MIN <= e <= E_MAX and N_MIN <= n <= N_MAX


def _ergaenze_lv95(e, n):
    """Ergänzt fehlende LV95-Präfixe (LV03-Werte oder vergessene '1'/'2')."""
    if 480_000 <= e <= 840_000:
        e += 2_000_000
    if 70_000 <= n <= 300_000:
        n += 1_000_000
    return e, n


def repariere(e_roh, n_roh):
    """Gibt (e, n, status) zurück. status: 'ok', 'repariert' oder 'fehlt'."""
    e, n = parse_zahl(e_roh), parse_zahl(n_roh)
    if e is None or n is None:
        return None, None, "fehlt"
    if _im_bereich(e, n):
        return e, n, "ok"
    # Kandidaten: Original und vertauscht, jeweils mit LV03->LV95-Ergänzung
    for a, b in ((e, n), (n, e)):
        a2, b2 = _ergaenze_lv95(a, b)
        if _im_bereich(a2, b2):
            return a2, b2, "repariert"
    return None, None, "fehlt"


def lv95_zu_wgs84(e, n):
    """Näherungsformel swisstopo. Rückgabe (lat, lon) in Dezimalgrad."""
    y = (e - 2_600_000) / 1_000_000
    x = (n - 1_200_000) / 1_000_000
    lon = (2.6779094 + 4.728982 * y + 0.791484 * y * x
           + 0.1306 * y * x ** 2 - 0.0436 * y ** 3)
    lat = (16.9023892 + 3.238272 * x - 0.270978 * y ** 2
           - 0.002528 * x ** 2 - 0.0447 * y ** 2 * x - 0.0140 * x ** 3)
    return lat * 100 / 36, lon * 100 / 36


def distanz_luftlinie_km(e1, n1, e2, n2):
    """Luftlinie in km, direkt in LV95 (metrisch). Nur als Vorfilter gedacht."""
    return math.hypot(e1 - e2, n1 - n2) / 1000


def wgs84_zu_lv95(lat, lon):
    """Näherungsformel swisstopo. Rückgabe (E, N) in LV95."""
    p = (lat * 3600 - 169028.66) / 10000
    l = (lon * 3600 - 26782.5) / 10000
    e = (2600072.37 + 211455.93 * l - 10938.51 * l * p
         - 0.36 * l * p ** 2 - 44.54 * l ** 3)
    n = (1200147.07 + 308807.95 * p + 3745.25 * l ** 2
         + 76.63 * p ** 2 - 194.56 * l ** 2 * p + 119.79 * p ** 3)
    return e, n


def parse_standort(text):
    """Koordinatenpaar -> (E, N) in LV95.

    Erlaubt sind Komma, Leerzeichen, Semikolon oder Schrägstrich als Trenner,
    Apostrophe als Tausenderzeichen: '2666000,1211000', "2'666'000 / 1'211'000",
    '47.05 8.30' (WGS84).
    """
    teile = [t for t in re.split(r"[,;/\s]+", str(text).replace("'", "").strip()) if t]
    if len(teile) != 2:
        raise ValueError(f"Koordinatenpaar erwartet, erhalten: {text!r}")
    try:
        a, b = (float(t) for t in teile)
    except ValueError:
        raise ValueError(f"Koordinatenpaar erwartet, erhalten: {text!r}")
    if a > 1_000_000:
        return a, b
    return wgs84_zu_lv95(a, b)


def zahlendreher(e, n, ziel_e, ziel_n, radius_km=5):
    """Sucht einen Zahlendreher (zwei benachbarte Ziffern vertauscht) in E oder N.

    Gibt (e, n) zurück, wenn genau eine Vertauschung den Punkt auf höchstens
    radius_km an das Ziel heranbringt, sonst None. Beispiel: N 1'118'101 statt
    1'181'101.
    """
    kandidaten = []
    for achse, wert in (("e", e), ("n", n)):
        s = str(int(round(wert)))
        for i in range(1, len(s) - 1):  # erste Ziffer (LV95-Präfix) bleibt
            if s[i] == s[i + 1]:
                continue
            neu = float(s[:i] + s[i + 1] + s[i] + s[i + 2:])
            e2, n2 = (neu, n) if achse == "e" else (e, neu)
            if _im_bereich(e2, n2) and distanz_luftlinie_km(e2, n2, ziel_e, ziel_n) <= radius_km:
                kandidaten.append((e2, n2))
    return kandidaten[0] if len(kandidaten) == 1 else None
