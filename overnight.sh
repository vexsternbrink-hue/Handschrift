#!/usr/bin/env bash
# Overnight-Build (macOS / Linux): richtet alles ein und testet es – ohne Rückfragen.
#   ./overnight.sh                 normaler Lauf (wenige Minuten)
#   ./overnight.sh --stress 3000   zusätzlich langer Stabilitätstest (läuft ggf. Stunden)
# Ergebnis: logs/overnight_report.md, Beispiele in examples/
set -u
cd "$(dirname "$0")"
mkdir -p logs
SETUP_LOG="logs/overnight_setup_$(date +%Y%m%d_%H%M%S).log"
PY="${PYTHON:-python3}"
{
  echo "== Overnight-Build $(date) =="
  if [ ! -x .venv/bin/python ]; then
    echo "== Lege virtuelle Umgebung .venv an"
    "$PY" -m venv .venv || { echo "FEHLER: venv konnte nicht angelegt werden"; exit 1; }
  fi
  echo "== Installiere Pakete"
  .venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt \
    || { echo "FEHLER: Paketinstallation fehlgeschlagen"; exit 1; }
  echo "== Starte Build"
  .venv/bin/python main.py overnight "$@"
} 2>&1 | tee -a "$SETUP_LOG"
STATUS=${PIPESTATUS[0]}
echo "Fertig (Status $STATUS). Bericht: logs/overnight_report.md"
exit "$STATUS"
