@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
set PYTHONUTF8=1
py -3 --version >nul 2>nul
if !errorlevel!==0 (
  py -3 "%~dp0mwx.py" %*
  exit /b !errorlevel!
)
python --version >nul 2>nul
if !errorlevel!==0 (
  python "%~dp0mwx.py" %*
  exit /b !errorlevel!
)
echo Python 3 not found. Install it from https://www.python.org/downloads/windows/
exit /b 1
