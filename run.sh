#!/usr/bin/env bash
# Carbon Markets Daily — quick start

set -e
cd "$(dirname "$0")"

# Create venv if needed
if [ ! -d "venv" ]; then
  echo "→ Creating virtual environment…"
  python3 -m venv venv
fi

# Install / upgrade dependencies quietly
echo "→ Installing dependencies…"
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Carbon Markets Daily"
echo "  Open http://localhost:5000 in your browser"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

venv/bin/python app.py
