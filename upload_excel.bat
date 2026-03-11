@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0upload_excel.ps1"