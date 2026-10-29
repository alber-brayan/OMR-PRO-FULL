@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ejecute primero IniciarOMR.bat para instalar las dependencias.
  pause
  exit /b 1
)
set "SRC=%~1"
if not defined SRC (
  for /f "delims=" %%I in ('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $f=New-Object System.Windows.Forms.OpenFileDialog; $f.Filter='Pack o foto OMR|*.zip;*.jpg;*.jpeg;*.png;*.webp'; $f.Title='OMR 2.5 - Seleccionar lote'; if($f.ShowDialog() -eq 'OK'){$f.FileName}"') do set "SRC=%%I"
)
if not defined SRC exit /b 0
".venv\Scripts\python.exe" -X utf8 calificar_pack.py "%SRC%"
echo.
pause
