@echo off
cd /d "%~dp0"
title System Sentinel Backend
powershell -NoProfile -ExecutionPolicy Bypass -File "start_server.ps1"
if %errorlevel% neq 0 pause
