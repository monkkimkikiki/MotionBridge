@echo off
setlocal enableextensions
title AnimRef - WSL setup (administrator)
echo.
echo ============================================================
echo   AnimRef: installing WSL (runs as administrator)
echo   Keep this window open until it says FINISHED.
echo   This needs an internet connection and may take a while.
echo ============================================================
echo.
echo [a] WSL core
wsl --install --no-distribution --web-download
echo     exit code: %errorlevel%
echo.
echo [b] WSL update
wsl --update --web-download
echo     exit code: %errorlevel%
echo.
echo [c] Ubuntu-22.04
wsl --install -d Ubuntu-22.04 --web-download --no-launch
echo     exit code: %errorlevel%
echo.
echo [d] Checks
powershell -NoProfile -Command "$c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_Processor | Select-Object -First 1; Write-Host ('Windows hypervisor running            : ' + $c.HypervisorPresent); Write-Host ('Virtualization enabled in BIOS/UEFI   : ' + $p.VirtualizationFirmwareEnabled)"
echo     If BOTH lines above say False, virtualization is OFF in your BIOS/UEFI.
echo     Restart the PC, open BIOS/UEFI and turn on Intel VT-x / AMD SVM
echo     (also called "Virtualization Technology"), then run setup again.
echo.
wsl --status
echo.
echo ============================================================
echo   FINISHED.  If any step above shows a red error, take a
echo   screenshot of this window.  Otherwise close it, RESTART the
echo   PC if Windows asked you to, and run the AnimRef setup again.
echo ============================================================
pause
exit /b 0
