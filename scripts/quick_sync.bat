@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title IBKR Sync

echo Starting IBKR to Notion sync...
uv run .\scripts\sync_positions_with_greeks_to_notion.py
echo.
echo Sync completed. Press any key to close...
pause >nul