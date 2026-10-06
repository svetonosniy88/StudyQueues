@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.12 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3, 12) <= sys.version_info < (3, 15) else 1)"
if errorlevel 1 (
  echo Python 3.12 to 3.14 is required. Python 3.12 is recommended. See README.md.
  goto failed
)
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed
echo.
echo StudyQueues is ready. Double-click Start.cmd to open the application.
pause
exit /b 0
:failed
echo.
echo Setup could not finish. Check the error above and the installation section in README.md.
pause
exit /b 1
