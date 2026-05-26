#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
cd "$ROOT"

if [ ! -d ".venv311" ]; then
  python3.11 -m venv .venv311
fi

source .venv311/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

PYI_CONFIG_DIR="$ROOT/build/pyinstaller_config"
mkdir -p "$PYI_CONFIG_DIR"
export PYINSTALLER_CONFIG_DIR="$PYI_CONFIG_DIR"

pyinstaller -y packaging/pyinstaller_app.spec
