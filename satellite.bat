@echo off
REM ===========================================================================
REM satellite.bat — lanzador de la CLI unificada de Satellite (Python + C++).
REM
REM Uso (desde cualquier proyecto consumidor):
REM     satellite.bat init
REM     satellite.bat agents
REM     satellite.bat run "objetivo"
REM     ...
REM
REM Encapsula: el runtime Python (venv del framework), los binarios C++ de
REM backend (satellite.exe, satellite_agent_host.exe, satellite_factory_cli.exe)
REM y el g++ de MSYS2 (para compilar agentes) si está presente.
REM ===========================================================================
setlocal

REM Directorio raiz del framework (donde vive este script).
set "SAT_ROOT=%~dp0"
if "%SAT_ROOT:~-1%"=="\" set "SAT_ROOT=%SAT_ROOT:~0,-1%"

set "SATELLITE_CPP_BIN=%SAT_ROOT%\build\satellite.exe"
set "SATELLITE_AGENT_HOST=%SAT_ROOT%\build\satellite_agent_host.exe"
set "SATELLITE_FACTORY_CLI=%SAT_ROOT%\build\satellite_factory_cli.exe"
set "PYTHONPATH=%SAT_ROOT%"

REM g++ para compilar agentes generados (agent create / expand), si MSYS2 existe.
if exist "C:\msys64\mingw64\bin\g++.exe" set "PATH=C:\msys64\mingw64\bin;%PATH%"

if not exist "%SAT_ROOT%\.venv\Scripts\python.exe" (
    echo Error: no se encontro el venv del framework en %SAT_ROOT%\.venv
    exit /b 1
)

"%SAT_ROOT%\.venv\Scripts\python.exe" -m satellite_py.cli %*
exit /b %ERRORLEVEL%
