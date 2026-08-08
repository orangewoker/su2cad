@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-SU2CAD-Plugins.ps1"
if errorlevel 1 pause
endlocal
