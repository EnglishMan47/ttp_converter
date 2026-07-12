@echo off
rem Windows command-line launcher for the preflight check.
rem Usage (from the project folder):  scripts\preflight.bat
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0preflight.ps1" %*
exit /b %ERRORLEVEL%
