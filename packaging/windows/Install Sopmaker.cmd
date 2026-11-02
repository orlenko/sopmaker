@echo off
setlocal DisableDelayedExpansion
title Install Sopmaker
echo Sopmaker setup - no administrator access required
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Sopmaker.ps1"
if errorlevel 1 (
  echo.
  echo Setup stopped. Read the message above. If your organization blocks scripts,
  echo ask your IT contact to approve this package; do not change security policies.
  pause
  exit /b 1
)
echo.
echo Setup finished. You can close this window.
pause
