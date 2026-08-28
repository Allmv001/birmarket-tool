@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title ARMA - Testler
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" set "VPY=python"
echo Reqressiya testleri isleyir...
echo.
"%VPY%" tests\test_arma.py
echo.
pause
