@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "C:\Python314\python.exe" goto run
echo.
echo  [!] Cannot find C:\Python314\python.exe
echo.
pause
goto end

:run
"C:\Python314\python.exe" main.py
echo.
echo  --- exited ---
pause

:end
endlocal
