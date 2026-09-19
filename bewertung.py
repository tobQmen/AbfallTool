"""Ökologische Rangfolge der Entsorgungsverfahren (VVEA-Abfallhierarchie).

Stufe 1 ist die beste Verwertung, Stufe 7 die schlechteste Variante.
Grundlage: Verwertung vor Beseitigung (Art. 12 VVEA), stofflich vor energetisch,
Beseitigung mit Nutzen vor reiner Ablagerung. Zwischenlager sind kein Endverfahren:
wohin der Abfall danach geht, ist aus den Daten nicht ersichtlich.
"""

STUFEN = {
    1: "stoffliche Verwertung",
    2: "energetische Verwertung",
    3: "thermische Beseitigung",
    4: "Behandlung",
    5: "Deponie",
    6: "Zwischenlager (Verwertungsweg)",
    7: "Zwischenlager (Beseitigungsweg)",
}

VERFAHREN_STUFE = {
    # stofflich
    "R2": 1, "R3": 1, "R4": 1, "R5": 1, "R6": 1, "R7": 1, "R8": 1, "R9": 1,
    "R10": 1, "R11": 1, "R160": 1,
    # energetisch
    "R101": 2, "R103": 2, "R104": 2,
    # thermische Beseitigung
    "D101": 3, "D102": 3, "D103": 3, "D104": 3,
    # sonstige Behandlung
    "D2": 4, "D8": 4, "D9": 4, "D160": 4,
    # Ablagerung
    "D1": 5, "D5": 5, "D12": 5,
    # Zwischenlager / Umschlag
    "R151": 6, "R152": 6, "R153": 6,
    "D151": 7, "D152": 7, "D153": 7,
}


def stufe(verfahren):
    """Beste (niedrigste) Stufe einer Verfahrensliste, z. B. 'D1, R5' -> 1."""
    if not verfahren:
        return 9
    codes = [v.strip() for v in str(verfahren).split(",") if v.strip()]
    return min((VERFAHREN_STUFE.get(c, 8) for c in codes), default=9)


def stufe_text(nummer):
    return STUFEN.get(nummer, "unbekanntes Verfahren")


def bestes_verfahren(verfahren):
    """Verfahrenscode mit der besten Stufe, z. B. 'D1, R5' -> 'R5'."""
    codes = [v.strip() for v in str(verfahren).split(",") if v.strip()]
    return min(codes, key=lambda c: VERFAHREN_STUFE.get(c, 8), default="")
