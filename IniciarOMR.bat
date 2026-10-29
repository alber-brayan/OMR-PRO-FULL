@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Preparando Python para OMR 2.5...
  where py >nul 2>nul
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto :error
)
if not exist ".venv\.omr25-ready" (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :error
  echo OMR25>".venv\.omr25-ready"
)
set "OMR_PORT=5025"
set "OMR_HOST=0.0.0.0"
echo.
echo OMR 2.5: http://127.0.0.1:5025
echo Mantenga esta ventana abierta mientras utiliza el programa.
echo.
".venv\Scripts\python.exe" -X utf8 iniciar_omr.py
if errorlevel 1 goto :error
exit /b 0
:error
echo.
echo No se pudo iniciar OMR 2.5. Revise el mensaje anterior.
pause
exit /b 1
