#!/data/data/com.termux/files/usr/bin/sh
# One-time setup inside Termux. Installs only what the engine needs: Python and git.
# Usage (inside the newal-mobile folder):  sh termux/setup.sh
set -e
pkg update -y
pkg install -y python git
mkdir -p "$HOME/workspace"
echo "Setup complete."
echo "Start the server:  python -m newal_mobile --port 8787"
echo "Keep it alive:     termux-wake-lock   (needs Termux:API; optional)"
