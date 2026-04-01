@echo off
REM install.bat — One-time setup to make `ccp` available globally on Windows
REM Run this once from inside the crash-copilot folder

echo ============================================
echo   Crash-Copilot  ^|  Global Install
echo ============================================
echo.

REM Get the directory of THIS script (the crash-copilot repo folder)
set "REPO_DIR=%~dp0"
REM Remove trailing backslash
if "%REPO_DIR:~-1%"=="\" set "REPO_DIR=%REPO_DIR:~0,-1%"

REM Check if already in PATH (user scope)
echo %PATH% | find /i "%REPO_DIR%" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] Already in PATH: %REPO_DIR%
    goto done
)

REM Add permanently to user PATH
setx PATH "%PATH%;%REPO_DIR%" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] Added to PATH: %REPO_DIR%
    echo.
    echo  ^> Open a NEW terminal, then run:
    echo.
    echo     ccp python script.py
    echo.
) else (
    echo [FAIL] Could not add to PATH automatically.
    echo  ^> Add this manually to your system PATH:
    echo     %REPO_DIR%
)

:done
pause
