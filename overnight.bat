@echo off
REM Overnight-Build (Windows): richtet alles ein und testet es - ohne Rueckfragen.
REM   overnight.bat                 normaler Lauf (wenige Minuten)
REM   overnight.bat --stress 3000   zusaetzlich langer Stabilitaetstest
REM Ergebnis: logs\overnight_report.md, Beispiele in examples\
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .venv\Scripts\python.exe (
  echo == Lege virtuelle Umgebung .venv an
  py -3 -m venv .venv >> logs\overnight_setup.log 2>&1 || python -m venv .venv >> logs\overnight_setup.log 2>&1
)
if not exist .venv\Scripts\python.exe (
  echo FEHLER: Python 3.9+ nicht gefunden. Bitte von https://www.python.org installieren.
  exit /b 1
)
echo == Installiere Pakete (Details: logs\overnight_setup.log)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q --upgrade pip >> logs\overnight_setup.log 2>&1
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt >> logs\overnight_setup.log 2>&1
if errorlevel 1 (
  echo FEHLER: Paketinstallation fehlgeschlagen, siehe logs\overnight_setup.log
  exit /b 1
)
echo == Starte Build
.venv\Scripts\python.exe main.py overnight %*
set STATUS=%ERRORLEVEL%
echo Fertig (Status %STATUS%). Bericht: logs\overnight_report.md
exit /b %STATUS%
