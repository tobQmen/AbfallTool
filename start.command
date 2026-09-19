#!/bin/bash
# Abfalltool starten (macOS): Doppelklick auf diese Datei.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 fehlt. Bitte von python.org installieren und diese Datei erneut öffnen."
  read -r -p "Enter zum Schliessen "
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Richte die Arbeitsumgebung ein (einmalig) …"
  python3 -m venv .venv || exit 1
fi

# Fehlende Pakete nachinstallieren, auch wenn .venv schon bestand
if ! ./.venv/bin/python -c "import streamlit, pandas, openpyxl, folium, streamlit_folium" >/dev/null 2>&1; then
  echo "Installiere fehlende Pakete (dauert ein bis zwei Minuten) …"
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  ./.venv/bin/python -m pip install --quiet pandas openpyxl streamlit folium streamlit-folium || {
    echo "Installation fehlgeschlagen."
    read -r -p "Enter zum Schliessen "
    exit 1
  }
fi

echo "Abfalltool startet, das Fenster öffnet sich im Browser."
echo "Zum Beenden dieses Fenster schliessen."
exec ./.venv/bin/python -m streamlit run app.py
