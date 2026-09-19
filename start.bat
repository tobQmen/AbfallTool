@echo off
REM Abfalltool starten (Windows): Doppelklick auf diese Datei.
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python fehlt. Bitte aus dem Microsoft Store oder von python.org installieren.
  pause
  exit /b 1
)

if not exist .venv (
  echo Richte die Arbeitsumgebung ein ^(einmalig^) ...
  py -m venv .venv || exit /b 1
)

REM Fehlende Pakete nachinstallieren, auch wenn .venv schon bestand
.venv\Scripts\python.exe -c "import streamlit, pandas, openpyxl, folium, streamlit_folium" >nul 2>nul
if errorlevel 1 (
  echo Installiere fehlende Pakete ^(dauert ein bis zwei Minuten^) ...
  .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  .venv\Scripts\python.exe -m pip install --quiet pandas openpyxl streamlit folium streamlit-folium || exit /b 1
)

echo Abfalltool startet, das Fenster oeffnet sich im Browser.
echo Zum Beenden dieses Fenster schliessen.
.venv\Scripts\python.exe -m streamlit run app.py
