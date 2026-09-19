"""Oberfläche für das Abfalltool. Start:  streamlit run app.py

Führt durch den ganzen Ablauf: Export einlesen, Koordinaten ergänzen, Baustelle
festlegen, Entsorgungsliste erstellen. Kein Terminal nötig.
"""
import io
import json
import os
import sqlite3
import subprocess
import sys

import pandas as pd
import streamlit as st

import baustellen
from koordinaten import parse_standort, lv95_zu_wgs84
from liste import erstelle, lade_vorlage, positionen, VORLAGEN

try:
    import folium
    from folium.plugins import MarkerCluster  # noqa: F401  (Verfügbarkeit prüfen)
    from streamlit_folium import st_folium
    FOLIUM = True
except ImportError:
    FOLIUM = False

DB = "abfallanlagen.db"
EXPORT = "Export.xlsx"
KARTE_URL = "https://map.geo.admin.ch"

st.set_page_config(page_title="Abfalltool", page_icon="♻️", layout="wide")

SWISSTOPO = {
    "Landeskarte": "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/"
                   "default/current/3857/{z}/{x}/{y}.jpeg",
    "Luftbild": "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/"
                "default/current/3857/{z}/{x}/{y}.jpeg",
}
STUFENFARBE = {1: "green", 2: "beige", 3: "orange", 4: "orange", 5: "red"}


def zeichne_karte(punkte, baustelle, hervorgehoben=(), hintergrund="Landeskarte"):
    """Folium-Karte mit swisstopo-Hintergrund, anklickbaren Anlagen und Baustelle."""
    lat_bs, lon_bs = baustelle
    karte = folium.Map(location=[lat_bs, lon_bs], zoom_start=10, tiles=None)
    folium.TileLayer(tiles=SWISSTOPO[hintergrund], attr="© swisstopo",
                     name=hintergrund).add_to(karte)
    folium.Marker([lat_bs, lon_bs], tooltip="Baustelle",
                  icon=folium.Icon(color="black", icon="home", prefix="fa")).add_to(karte)
    for r in punkte.itertuples():
        gewaehlt = r.Index in hervorgehoben
        text = (f"<b>{r.firma}</b><br>{r.ort} · {r.typ}<br>"
                f"{r.bestes_verfahren} – {r.stufe_text}<br>"
                f"<b>{r.distanz_km} km</b> ({r.distanz_quelle})<br>"
                f"Code {r.code} {r.v_pflicht or ''}<br>"
                f"Betriebsnummer {r.betriebsnummer}")
        folium.CircleMarker(
            [r.lat, r.lon], radius=11 if gewaehlt else 6,
            color="#1565c0" if gewaehlt else "#37474f", weight=3 if gewaehlt else 1,
            fill=True, fill_color=("#1565c0" if gewaehlt else
                                   {1: "#2e7d32", 2: "#9e9d24", 3: "#ef6c00",
                                    4: "#ef6c00", 5: "#c62828"}.get(r.stufe, "#616161")),
            fill_opacity=0.9, tooltip=f"{r.firma} – {r.distanz_km} km",
            popup=folium.Popup(text, max_width=320)).add_to(karte)
    return karte


def lauf(befehl, platzhalter):
    """Skript starten und Ausgabe live anzeigen."""
    zeilen = []
    prozess = subprocess.Popen([sys.executable] + befehl, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    for zeile in prozess.stdout:
        zeilen.append(zeile.rstrip())
        platzhalter.code("\n".join(zeilen[-12:]))
    prozess.wait()
    return prozess.returncode == 0, "\n".join(zeilen)


@st.cache_data(show_spinner=False)
def datenstand(mtime):
    with sqlite3.connect(DB) as con:
        info = con.execute("SELECT datenstand, anlagen, bewilligungen FROM import_info").fetchone()
        status = dict(con.execute("SELECT koord_status, count(*) FROM anlagen GROUP BY 1"))
    return info, status


def db_status():
    if not os.path.exists(DB):
        return None, None
    return datenstand(os.path.getmtime(DB))


def routen_status():
    if not os.path.exists("routen.db"):
        return {}
    with sqlite3.connect("routen.db") as con:
        try:
            return dict(con.execute("SELECT coalesce(start_name,'?'), count(*) "
                                    "FROM routen GROUP BY 1").fetchall())
        except sqlite3.OperationalError:
            return {}


# ---------------------------------------------------------------- Seitenleiste
info, koord = db_status()
with st.sidebar:
    st.header("Status")
    if info:
        st.success(f"Daten vom {info[0]}")
        st.caption(f"{info[1]} Anlagen · {info[2]} Bewilligungen")
        fehlt = koord.get("fehlt", 0)
        if fehlt:
            st.warning(f"{fehlt} Anlagen ohne Koordinate")
        else:
            st.caption("Alle Anlagen verortet")
    else:
        st.error("Noch keine Daten eingelesen")
    routen = routen_status()
    if routen:
        st.caption("Strassendistanzen: " +
                   ", ".join(f"{k} ({v})" for k, v in routen.items()))
    else:
        st.caption("Strassendistanzen: keine (es gilt Luftlinie)")

seite = st.sidebar.radio("Schritte", [
    "1 · Daten einlesen", "2 · Koordinaten prüfen", "3 · Baustelle",
    "4 · Strassendistanzen (optional)", "5 · Entsorgungsliste"])

# ------------------------------------------------------------- 1 Daten einlesen
if seite.startswith("1"):
    st.title("Daten einlesen")
    st.markdown(
        "Die Anlagendaten kommen vom Bund. Auf **eGov.Swiss** anmelden, den Service "
        "«Datenexport Abfallanlagen» öffnen, ganze Schweiz wählen und die Excel-Datei "
        "herunterladen. Danach hier hochladen.")
    datei = st.file_uploader("Export als Excel-Datei", type=["xlsx"])
    if datei:
        with open(EXPORT, "wb") as f:
            f.write(datei.getbuffer())
        st.success(f"{datei.name} gespeichert")
    if os.path.exists(EXPORT):
        if st.button("Einlesen starten", type="primary"):
            platz = st.empty()
            with st.spinner("Liest Export …"):
                ok, ausgabe = lauf(["import_bafu.py", EXPORT], platz)
            st.cache_data.clear()
            (st.success if ok else st.error)("Fertig" if ok else "Fehler beim Einlesen")
            st.rerun() if ok else st.code(ausgabe)
    else:
        st.info("Noch keine Exportdatei vorhanden.")

# -------------------------------------------------------- 2 Koordinaten prüfen
elif seite.startswith("2"):
    st.title("Koordinaten prüfen")
    if not info:
        st.error("Zuerst Schritt 1 ausführen.")
        st.stop()
    st.write(pd.DataFrame(sorted(koord.items()), columns=["Status", "Anlagen"]))
    st.markdown(
        "Ein Teil der Anlagen hat im Export keine oder eine falsche Koordinate. "
        "Die Adresssuche von swisstopo ergänzt sie. Das dauert beim ersten Mal einige "
        "Minuten, danach greift ein Zwischenspeicher.")
    spalte1, spalte2 = st.columns(2)
    if spalte1.button("Fehlende ergänzen", type="primary"):
        platz = st.empty()
        ok, _ = lauf(["geokodierung.py"], platz)
        st.cache_data.clear()
        st.success("Fertig") if ok else st.error("Fehler")
    if spalte2.button("Verdächtige ersetzen"):
        platz = st.empty()
        ok, _ = lauf(["geokodierung.py", "--auch-unplausibel"], platz)
        st.cache_data.clear()
        st.success("Fertig") if ok else st.error("Fehler")

    with sqlite3.connect(DB) as con:
        pruef = pd.read_sql(
            "SELECT betriebsnummer, firma, ort, kanton, koord_status, koord_quelle, "
            "e_lv95, n_lv95 FROM anlagen WHERE koord_status NOT IN ('ok','geokodiert','manuell')",
            con)
    if not pruef.empty:
        st.subheader(f"{len(pruef)} Anlagen zum Nachschauen")
        pruef["karte"] = [f"{KARTE_URL}/?E={r.e_lv95:.0f}&N={r.n_lv95:.0f}&zoom=9"
                          "&crosshair=marker&bgLayer=ch.swisstopo.swissimage"
                          if pd.notna(r.e_lv95) else "" for r in pruef.itertuples()]
        st.dataframe(pruef.drop(columns=["e_lv95", "n_lv95"]),
                     column_config={"karte": st.column_config.LinkColumn("Karte",
                                                                         display_text="öffnen")},
                     hide_index=True)
        st.caption("Stimmt ein Punkt nicht: in der Karte die richtige Stelle anklicken, "
                   "Koordinate ablesen und unten eintragen.")
        with st.form("korrektur"):
            sp = st.columns([2, 3, 3, 2])
            bnr = sp[0].text_input("Betriebsnummer")
            koordinate = sp[1].text_input("Koordinate (E, N)")
            notiz = sp[2].text_input("Notiz", "ab map.geo.admin.ch")
            if sp[3].form_submit_button("Speichern") and bnr and koordinate:
                befehl = ["korrekturen.py", bnr] + koordinate.replace(",", " ").split()
                ok, ausgabe = lauf(befehl + ["--notiz", notiz], st.empty())
                st.cache_data.clear()
                st.success("Gespeichert") if ok else st.error(ausgabe)

# --------------------------------------------------------------- 3 Baustelle
elif seite.startswith("3"):
    st.title("Baustelle")
    gespeichert = baustellen.laden()
    if gespeichert:
        st.dataframe(pd.DataFrame([
            {"Name": n, "E": b["e_lv95"], "N": b["n_lv95"], "Notiz": b.get("notiz", "")}
            for n, b in gespeichert.items()]), hide_index=True)
    st.markdown(f"Koordinate ablesen: [map.geo.admin.ch]({KARTE_URL}) öffnen, Stelle "
                "anklicken, Werte hier eintragen (LV95 oder Breiten-/Längengrad).")
    with st.form("baustelle"):
        sp = st.columns([3, 3, 3, 2])
        name = sp[0].text_input("Name")
        koordinate = sp[1].text_input("Koordinate", placeholder="2706600, 1166200")
        notiz = sp[2].text_input("Notiz", "")
        if sp[3].form_submit_button("Speichern", type="primary"):
            try:
                e, n = parse_standort(koordinate)
            except ValueError as fehler:
                st.error(str(fehler))
            else:
                daten = baustellen.laden()
                lat, lon = lv95_zu_wgs84(e, n)
                daten[name] = {"e_lv95": e, "n_lv95": n, "lat": round(lat, 6),
                               "lon": round(lon, 6), "notiz": notiz}
                baustellen.speichern(daten)
                st.success(f"{name} gespeichert")
                st.rerun()
    if gespeichert:
        weg = st.selectbox("Baustelle löschen", ["–"] + list(gespeichert))
        if weg != "–" and st.button("Löschen"):
            daten = baustellen.laden()
            daten.pop(weg, None)
            baustellen.speichern(daten)
            st.rerun()

# ------------------------------------------------------- 4 Strassendistanzen
elif seite.startswith("4"):
    st.title("Strassendistanzen")
    st.markdown(
        "Ohne diesen Schritt rechnet das Tool mit Luftlinie. Das genügt im Flachland, "
        "führt im Gebirge aber in die Irre. Für echte Strassendistanzen braucht es "
        "**Docker** und den Routing-Dienst Valhalla (siehe README).")
    host = st.text_input("Adresse des Routing-Dienstes", "http://localhost:8002")
    if st.button("Verbindung prüfen"):
        from routing import erreichbar
        ok, text = erreichbar(host)
        st.success(text) if ok else st.error(text)
    gespeichert = list(baustellen.laden())
    if not gespeichert:
        st.info("Zuerst eine Baustelle anlegen (Schritt 3).")
    else:
        sp = st.columns(4)
        bs = sp[0].selectbox("Baustelle", gespeichert)
        with open(VORLAGEN, encoding="utf-8") as f:
            namen = list(json.load(f).get("vorlagen", {}))
        vorlage = sp[1].selectbox("Vorlage", namen)
        umkreis = sp[2].number_input("Umkreis (km)", 10, 200, 60, step=10)
        top = sp[3].number_input("Anlagen pro Code", 5, 100, 30, step=5)
        hoehen = st.checkbox("Höhenprofil mitrechnen (langsamer)")
        if st.button("Strecken berechnen", type="primary"):
            befehl = ["routing.py", "--host", host, "--baustelle", bs, "--vorlage", vorlage,
                      "--umkreis", str(umkreis), "--top", str(int(top))]
            ok, ausgabe = lauf(befehl + (["--hoehen"] if hoehen else []), st.empty())
            st.success("Fertig") if ok else st.error(ausgabe)

# ------------------------------------------------------- 5 Entsorgungsliste
else:
    st.title("Entsorgungsliste")
    if not info:
        st.error("Zuerst Schritt 1 ausführen.")
        st.stop()
    gespeichert = list(baustellen.laden())
    if not gespeichert:
        st.error("Zuerst eine Baustelle anlegen (Schritt 3).")
        st.stop()

    sp = st.columns(5)
    bs = sp[0].selectbox("Baustelle", gespeichert)
    with open(VORLAGEN, encoding="utf-8") as f:
        namen = list(json.load(f).get("vorlagen", {}))
    vorlage_name = sp[1].selectbox("Vorlage", namen)
    umkreis = sp[2].number_input("Umkreis (km)", 10, 200, 60, step=10)
    anzahl = sp[3].number_input("Vorschläge pro Position", 1, 10, 3)
    nur_end = sp[4].checkbox("Zwischenlager ausblenden", value=True)

    vorlage = lade_vorlage(vorlage_name)
    with sqlite3.connect(DB) as con:
        df = erstelle(con, vorlage, baustellen.hole(bs), umkreis, int(anzahl), nur_end)

    zeigen = st.radio("Anzeige", ["Nur bester Vorschlag", "Alle Vorschläge"],
                      horizontal=True, label_visibility="collapsed")
    tabelle = df[df.rang.isna() | (df.rang == 1)] if zeigen.startswith("Nur") else df
    tabelle = tabelle.reset_index(drop=True)

    st.caption("Zeile anklicken, um die Anlage auf der Karte hervorzuheben.")
    auswahl = st.dataframe(
        tabelle[["abschnitt", "code", "abfallart", "v_pflicht", "rang", "distanz_km",
                 "distanz_quelle", "fahrzeit_min", "firma", "ort", "bestes_verfahren",
                 "stufe_text", "hinweis"]],
        hide_index=True, height=420, on_select="rerun", selection_mode="multi-row",
        key="liste_auswahl")
    gewaehlt = auswahl.selection.rows if auswahl and auswahl.selection else []

    warnung = df[(df.rang == 1) & df.hinweis.fillna("").str.contains("V-Pflicht")]
    if not warnung.empty:
        st.warning(f"{len(warnung)} verwertungspflichtige Positionen ohne Verwertung im "
                   "Umkreis – Begründung nötig oder Umkreis vergrössern.")

    # ---- Karte: gewählte Zeilen hervorgehoben
    with sqlite3.connect(DB) as con:
        orte = pd.read_sql("SELECT betriebsnummer, lat, lon FROM anlagen", con)
    karte = tabelle.merge(orte, on="betriebsnummer", how="left").dropna(subset=["lat"])
    lat_bs, lon_bs = lv95_zu_wgs84(*baustellen.hole(bs))
    if not karte.empty:
        if FOLIUM:
            hintergrund = st.radio("Hintergrund", list(SWISSTOPO), horizontal=True,
                                   label_visibility="collapsed")
            st_folium(zeichne_karte(karte, (lat_bs, lon_bs), set(gewaehlt), hintergrund),
                      height=520, width=None, returned_objects=[])
        else:
            farben = {1: "#2e7d32", 2: "#9e9d24", 3: "#ef6c00", 4: "#ef6c00", 5: "#c62828"}
            karte["farbe"] = karte.stufe.map(farben).fillna("#616161")
            karte["groesse"] = [320 if i in gewaehlt else 90 for i in karte.index]
            st.map(karte, latitude="lat", longitude="lon", color="farbe", size="groesse")
            st.info("Für die Karte mit swisstopo-Hintergrund und anklickbaren Punkten: "
                    "pip install folium streamlit-folium")
        st.caption("Schwarz = Baustelle · grün = stoffliche Verwertung · gelb = energetisch "
                   "· rot = Deponie · blau = ausgewählte Zeile. Punkt anklicken für Details.")

    # ---- Alternativen zur gewählten Position
    if gewaehlt:
        for i in gewaehlt[:3]:
            zeile = tabelle.iloc[i]
            andere = df[df.code == zeile.code].sort_values("rang")
            st.markdown(f"**Alternativen für {zeile.code} – {zeile.abfallart}**")
            st.dataframe(andere[["rang", "distanz_km", "fahrzeit_min", "firma", "ort", "typ",
                                 "verfahren", "stufe_text", "gueltig_bis", "hinweis"]],
                         hide_index=True)

    st.divider()
    sp = st.columns([2, 2, 5])
    if sp[1].button("GeoJSON für QGIS erzeugen"):
        befehl = ["karte.py", "--baustelle", bs, "--vorlage", vorlage_name,
                  "--umkreis", str(umkreis)] + (["--nur-endverfahren"] if nur_end else [])
        ok, ausgabe = lauf(befehl, st.empty())
        st.success("Dateien im Projektordner erzeugt") if ok else st.error(ausgabe)

    puffer = io.BytesIO()
    with pd.ExcelWriter(puffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Entsorgungsliste")
    sp[0].download_button("Als Excel herunterladen", puffer.getvalue(),
                       file_name=f"entsorgungsliste_{bs}.xlsx", type="primary",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
