@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title ARMA - Marja Sistemi v4

echo ============================================
echo   ARMA - Marja Sistemi v4
echo ============================================
echo.

set "PYEXE="
if exist "C:\Program Files\Python312\python.exe" set "PYEXE=C:\Program Files\Python312\python.exe"
if not defined PYEXE if exist "C:\Program Files\Python311\python.exe" set "PYEXE=C:\Program Files\Python311\python.exe"
if not defined PYEXE if exist "C:\Program Files\Python310\python.exe" set "PYEXE=C:\Program Files\Python310\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"

if not defined PYEXE (
  echo [X] Python tapilmadi.
  echo     python.org-dan yukleyin ve "Add Python to PATH" qutusunu isareleyin.
  pause
  exit /b 1
)
echo [i] Python: %PYEXE%
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Virtual muhit yaradilir ^(ilk defe, 10-20 saniye^)...
  "%PYEXE%" -m venv .venv
)

set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" set "VPY=%PYEXE%"

echo [2/3] Asililiqlar yoxlanilir...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
  echo [!] Xeta oldu, --user rejimi ile tekrar cehd edilir...
  "%VPY%" -m pip install -r requirements.txt --user
)

echo.
echo [3/3] Server baslayir -^> http://localhost:5000
echo     ^(Bu pencereni baglamayin. Dayandirmaq: Ctrl+C^)
echo.
"%VPY%" app.py
pause
