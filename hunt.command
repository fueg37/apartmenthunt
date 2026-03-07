#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

title="Palm Beach Apartment Hunt"

# Make Homebrew available in non-interactive shells (Automator/Finder)
if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 is not installed."
  echo "Install it first with: brew install python"
  read -r -p "Press Enter to close..."
  exit 1
fi

# Create virtualenv if needed
if [[ ! -d .venv ]]; then
  echo "Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Install dependencies if needed
python -c "import typer, fastapi" >/dev/null 2>&1 || {
  echo "Installing dependencies..."
  python -m pip install --upgrade pip
  pip install -r requirements.txt
}

# Install Playwright browser binaries if needed
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.executable_path; p.stop()" >/dev/null 2>&1 || {
  echo "Installing Playwright Chromium (one-time download, ~130 MB)..."
  python -m playwright install chromium
}

# Seed DB on first run
if [[ ! -f data/hunt.db ]]; then
  echo "First run -- seeding database..."
  python main.py seed
fi

run_command() {
  if ! python main.py "$@"; then
    echo
    echo "[ERROR] Command failed. See above for details."
    read -r -p "Press Enter to continue..."
  fi
}

# If arguments passed, forward directly
if [[ $# -gt 0 ]]; then
  run_command "$@"
  exit 0
fi

echo
echo "  $title"
echo "  ─────────────────────────"
echo "  1) Open web dashboard     (http://localhost:8000)"
echo "  2) Scrape live data"
echo "  3) Show apartments"
echo "  4) Show all locations"
echo "  5) Search for new apartments"
echo "  6) Show price changes (diff)"
echo
read -r -p "  Choice [1]: " choice
choice=${choice:-1}

case "$choice" in
  1)
    echo "  Opening browser in 3 seconds..."
    sleep 3
    open "http://localhost:8000"
    echo "  Server is running at http://localhost:8000"
    echo "  Press Ctrl+C to stop."
    echo
    python main.py web
    ;;
  2)
    run_command scrape
    ;;
  3)
    run_command show --type apartment
    ;;
  4)
    run_command show
    ;;
  5)
    run_command search
    ;;
  6)
    run_command diff
    ;;
  *)
    echo "Unknown choice: $choice"
    read -r -p "Press Enter to close..."
    exit 1
    ;;
esac

echo
read -r -p "Press Enter to close..."
