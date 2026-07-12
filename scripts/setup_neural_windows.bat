@echo off
rem Windows command-line launcher for the neural engine installer.
rem Usage (from the project folder):
rem     scripts\setup_neural_windows.bat
rem     scripts\setup_neural_windows.bat -DownloadModels
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_neural_windows.ps1" %*
exit /b %ERRORLEVEL%
