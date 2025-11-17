@echo off
chcp 65001 >nul
title IBKR to Notion Quick Sync

echo.
echo ========================================
echo   IBKR to Notion Quick Sync
echo ========================================
echo.

REM Check if IBKR Gateway is running on port 4001 (Live) or 4002 (Paper)
echo [Step 1/4] Checking IBKR Gateway...
netstat -an | findstr ":4001" >nul 2>&1
if %errorlevel% == 0 (
    echo   ✓ IBKR Gateway detected on port 4001 ^(Live^)
    set GATEWAY_PORT=4001
    goto gateway_found
)

netstat -an | findstr ":4002" >nul 2>&1
if %errorlevel% == 0 (
    echo   ✓ IBKR Gateway detected on port 4002 ^(Paper^)
    set GATEWAY_PORT=4002
    goto gateway_found
)

echo   ✗ IBKR Gateway not detected
echo.
echo   Please start IBKR Gateway first!
echo   - Live Trading: Port 4001
echo   - Paper Trading: Port 4002
echo.
pause
exit /b 1

:gateway_found
echo.

REM Navigate to project root directory (one level up from scripts)
echo [Step 2/4] Setting up environment...
cd /d "%~dp0.."
echo   ✓ Project directory: %CD%
echo.

REM Check if uv is available
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo   ✗ UV not found. Please install UV first.
    echo   Visit: https://github.com/astral-sh/uv
    echo.
    pause
    exit /b 1
)
echo   ✓ UV found
echo.

REM Run the sync script
echo [Step 3/4] Syncing positions to Notion...
echo   Wait time: 20 seconds for Greeks data
echo   Using cache if market is closed
echo.
echo ========================================
echo.

uv run scripts/sync_positions_with_greeks_to_notion.py --wait-greeks 20

echo.
echo ========================================

REM Check exit code
if %errorlevel% == 0 (
    echo [Step 4/4] ✓ Sync completed successfully!
) else (
    echo [Step 4/4] ✗ Sync failed with errors
    echo   Check logs folder for details
)

echo ========================================
echo.
pause

