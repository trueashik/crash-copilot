#!/usr/bin/env bash
# Crash-Copilot wrapper — resolves ccp.py from THIS script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/ccp.py" "$@"
