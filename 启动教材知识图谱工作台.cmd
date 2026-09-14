@echo off
chcp 65001 >nul
title LZH-P2 Textbook-Knowledge-Graph-Workbench

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_workbench.ps1" %*
set "tool_exit=%ERRORLEVEL%"

if not "%tool_exit%"=="0" (
    echo.
    echo Tool exited with code: %tool_exit%
    pause
)
exit /b %tool_exit%
