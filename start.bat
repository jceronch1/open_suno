@echo off
rem Open Suno - arranque. Si aun no esta instalado, ejecuta antes la instalacion.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
title Open Suno

if not exist ".venv\Scripts\python.exe" (
    echo [Open Suno] Primera ejecucion: instalando...
    call install.bat --yes --no-pause
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
