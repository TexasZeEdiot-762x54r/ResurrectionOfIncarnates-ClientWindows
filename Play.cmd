@echo off
where python >nul 2>nul
if errorlevel 1 (
  py -3 -I "%~dp0roi_update.py" --launch
) else (
  python -I "%~dp0roi_update.py" --launch
)
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\play.ps1"
if errorlevel 1 pause
