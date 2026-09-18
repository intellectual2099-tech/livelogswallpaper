@echo off
setlocal
cd /d "%~dp0.."

echo [*] Installing requirements...
python -m pip install --upgrade pygame pyinstaller || goto :err

echo [*] Building GHOSTOPS.exe...
python -m PyInstaller --onefile --noconsole --name GHOSTOPS --distpath dist --workpath build win\wall_win.py || goto :err

echo.
echo [OK] Built dist\GHOSTOPS.exe
echo     Preview :  dist\GHOSTOPS.exe --preview
echo     Wallpaper:  dist\GHOSTOPS.exe --embed
goto :eof

:err
echo [!] Build failed.
exit /b 1