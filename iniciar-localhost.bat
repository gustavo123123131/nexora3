@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -m pip install -r requirements-web.txt
    if errorlevel 1 goto failure
    py -m playwright install chromium
    if errorlevel 1 goto failure
    start "" "http://127.0.0.1:8765"
    py servidor.py
) else (
    python -m pip install -r requirements-web.txt
    if errorlevel 1 goto failure
    python -m playwright install chromium
    if errorlevel 1 goto failure
    start "" "http://127.0.0.1:8765"
    python servidor.py
)
goto end
:failure
echo.
echo Falhou a instalacao. Confira Python 3.11+ e internet.
pause
:end
endlocal
