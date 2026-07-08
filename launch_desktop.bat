@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHONW=%ROOT%.venv\Scripts\pythonw.exe"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "LAUNCHER=%ROOT%launcher.py"

if exist "%PYTHONW%" (
    start "" "%PYTHONW%" "%LAUNCHER%"
    exit /b 0
)

if exist "%PYTHON%" (
    start "" "%PYTHON%" "%LAUNCHER%"
    exit /b 0
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    start "" py "%LAUNCHER%"
    exit /b 0
)

echo Cannot find Python. Please create .venv first:
echo   python -m venv .venv
echo   .\.venv\Scripts\python.exe -m pip install -r 2_1\requirements.txt
pause
exit /b 1
