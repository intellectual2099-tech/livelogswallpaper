@echo off
setlocal
cd /d "%~dp0"
set EXE=dist\GHOSTOPS.exe
if not exist "%EXE%" (
    echo [!] %EXE% not found - run build.bat first
    exit /b 1
)
start "" "%EXE%" --embed