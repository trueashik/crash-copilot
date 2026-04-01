@echo off
REM Crash-Copilot wrapper — resolves ccp.py from THIS script's directory
python "%~dp0ccp.py" %*
