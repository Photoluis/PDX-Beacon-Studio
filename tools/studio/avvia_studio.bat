@echo off
rem PDX Beacon Studio (sviluppo): avvio con il venv del repository
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" "studio\pdx_studio.py"
