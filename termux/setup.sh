#!/data/data/com.termux/files/usr/bin/sh
# One-time setup inside Termux. Installs what the engine needs (Python, git) and the GitHub CLI,
# so the agent can clone, commit, push and open pull requests through approved commands.
# Usage (inside the newal-mobile folder):  sh termux/setup.sh
set -e
pkg update -y
pkg install -y python git gh
mkdir -p "$HOME/workspace"
echo "Setup complete."
echo "Connect GitHub once (optional):  gh auth login   then   gh auth setup-git"
echo "Start the server:  python -m newal_mobile --port 8787"
echo "Keep it alive:     termux-wake-lock   (needs Termux:API; optional)"
