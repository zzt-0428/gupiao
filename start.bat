@echo off
title Stock Portfolio - News Service

echo.
echo ===========================================
echo   Stock Portfolio - Multi-source News Service
echo ===========================================
echo.

cd /d "%~dp0"

echo [1/2] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)
echo         Python OK

echo [2/2] Starting news service...
echo.
start /B "StockNewsSvc" python server.py --host 127.0.0.1 --port 8765

echo Waiting for service to start...
timeout /t 3 >nul

python -c "import requests; r=requests.get('http://127.0.0.1:8765/api/health',timeout=5); print(r.json()['status'])" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Service may not have started. Check terminal window.
) else (
    echo [ OK ] News service started! http://127.0.0.1:8765
)

echo.
echo Opening frontend...
start "" "docs\index.html"

echo.
echo ===========================================
echo   Startup complete!
echo   Frontend + Backend (localhost:8765)
echo   Closing this window will NOT stop the service
echo ===========================================
echo.
echo To stop the backend, close the Python process in system tray
echo.

timeout /t 5 >nul
exit /b 0
