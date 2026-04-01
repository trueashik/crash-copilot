#!/usr/bin/env bash
# install.sh — One-time setup to make `ccp` available globally on macOS/Linux
# Run: chmod +x install.sh && ./install.sh

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="/usr/local/bin/ccp"

echo "============================================"
echo "  Crash-Copilot  |  Global Install"
echo "============================================"
echo ""

# Make the shell script executable
chmod +x "$REPO_DIR/ccp"

# Symlink into /usr/local/bin
if [ -L "$TARGET" ] || [ -f "$TARGET" ]; then
  echo "[INFO] Removing existing $TARGET"
  sudo rm -f "$TARGET"
fi

sudo ln -sf "$REPO_DIR/ccp" "$TARGET"

if [ $? -eq 0 ]; then
  echo "[OK]  Symlinked: $TARGET → $REPO_DIR/ccp"
  echo ""
  echo "  Run from anywhere:"
  echo ""
  echo "    ccp python script.py"
  echo ""
else
  echo "[FAIL] Could not create symlink. Try adding this to your PATH manually:"
  echo "  export PATH=\"\$PATH:$REPO_DIR\""
fi
