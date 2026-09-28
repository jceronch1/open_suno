@echo off
rem Open Suno - instalacion: entorno de Python, motor acestep.cpp, modelos y configuracion CPU/GPU.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
title Open Suno - instalacion
echo.
echo   ==========  Open Suno  ==========
echo.

set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Necesitas Python 3.10 o superior: https://www.python.org/downloads/
    echo         Durante la instalacion marca "Add python.exe to PATH".
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo [Open Suno] Creando entorno virtual de Python...
    %PY% -m venv .venv || goto :fail
)
echo [Open Suno] Instalando dependencias de Python...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail

".venv\Scripts\python.exe" -m app.installer %* || goto :fail
echo %* | find /i "--no-pause" >nul || pause
exit /b 0

:fail
echo.
echo [Open Suno] La instalacion no se completo. Revisa el mensaje anterior y vuelve a ejecutar install.bat
echo %* | find /i "--no-pause" >nul || pause
exit /b 1
