@echo off
setlocal EnableExtensions

set "BIN_DIR=%~dp0"
if "%BIN_DIR:~-1%"=="\" set "BIN_DIR=%BIN_DIR:~0,-1%"
for %%I in ("%BIN_DIR%\..") do set "ROOT=%%~fI"

set "OCTOP_GREEN_PACKAGES=%ROOT%\packages"
if not defined OCTOP_HOME set "OCTOP_HOME=%ROOT%\data"

set "PY=%ROOT%\runtime\python.exe"
if not exist "%PY%" (
  echo octop.cmd: portable Python not found at %PY%
  exit /b 1
)

if not exist "%ROOT%\launch.py" (
  echo octop.cmd: launch.py missing — rebuild the green package
  exit /b 1
)

set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="

"%PY%" "%ROOT%\launch.py" %*
exit /b %ERRORLEVEL%
