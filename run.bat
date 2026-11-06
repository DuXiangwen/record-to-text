@echo off
setlocal
cd /d "%~dp0"

set "PYW=C:\Python314\pythonw.exe"
set "PY=C:\Python314\python.exe"

if exist "%PYW%" goto run
if exist "%PY%" (
    echo Python found at %PY%, but pythonw.exe is missing.
    echo Starting with console window instead.
    "%PY%" main.py
    goto end
)

echo.
echo  [!] Cannot find Python at C:\Python314
echo      Edit this bat file and change the PYW variable.
echo.
pause
goto end

:run
start "" "%PYW%" main.py

:end
endlocal
