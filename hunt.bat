@echo off
cd /d "%~dp0"
title Palm Beach Apartment Hunt

:: Install dependencies if needed
python -c "import typer" 2>nul || (
    echo Installing dependencies...
    pip install -r requirements.txt -q
)

:: Seed DB on first run
if not exist "data\hunt.db" (
    echo First run -- seeding database...
    python main.py seed
)

:: If arguments passed, forward them directly
if not "%~1"=="" (
    python main.py %*
    if errorlevel 1 echo.& echo [ERROR] Command failed. See above for details.
    pause
    goto end
)

:: Interactive menu
echo.
echo   Palm Beach Apartment Hunt
echo   ─────────────────────────
echo   1) Open web dashboard     (http://localhost:8000)
echo   2) Scrape live data
echo   3) Show apartments
echo   4) Show all locations
echo   5) Search for new apartments
echo   6) Show price changes (diff)
echo.
set /p choice="  Choice [1]: "
if "%choice%"=="" set choice=1

if "%choice%"=="1" goto web
if "%choice%"=="2" python main.py scrape
if "%choice%"=="3" python main.py show --type apartment
if "%choice%"=="4" python main.py show
if "%choice%"=="5" python main.py search
if "%choice%"=="6" python main.py diff
goto done

:web
echo   Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start "" http://localhost:8000
echo   Server is running at http://localhost:8000
echo   Press Ctrl+C to stop.
echo.
python main.py web

:done
if errorlevel 1 echo.& echo [ERROR] Command failed. See above for details.
echo.
pause

:end
