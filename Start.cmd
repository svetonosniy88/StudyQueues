@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo StudyQueues is not installed yet. Run Setup.cmd first.
  pause
  exit /b 1
)
if exist "%~dp0StudyQueues.local.cmd" (
  call "%~dp0StudyQueues.local.cmd"
  exit /b
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m studyqueues
