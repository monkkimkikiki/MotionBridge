@echo off
rem ------------------------------------------------------------
rem  AnimRef - Motion AI (WHAM) one-click setup  (double-click)
rem  It can also be started from the AnimRef window.
rem  Safe to run again: finished steps are skipped.
rem ------------------------------------------------------------
setlocal enableextensions
cd /d "%~dp0"
title AnimRef - Motion AI setup
set "DISTRO=Ubuntu-22.04"
set "ROOTDIR=/root/animref_mocap"
set "MARK=%TEMP%\animref_wsl_tried.txt"
set "PROBE=%TEMP%\animref_wsl_probe.txt"

echo.
echo ============================================================
echo   AnimRef Motion AI setup  (WHAM running inside WSL)
echo   - First time: 20~60 minutes, several GB of downloads
echo   - If something fails, just run this file again
echo ============================================================
echo.

where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo [WARNING] nvidia-smi was not found. WHAM needs an NVIDIA graphics card
    echo           and an up-to-date NVIDIA driver. Setup will continue anyway.
    echo.
)

echo [1/3] Checking WSL ...
call :wsl_ok
if not errorlevel 1 (
    echo       WSL and %DISTRO% are working.
    goto :copy_files
)

echo       WSL / %DISTRO% is not working yet. Installing it now.
set "BOOT=unknown"
for /f "usebackq delims=" %%B in (`powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('s')"`) do set "BOOT=%%B"
set "PREV="
if exist "%MARK%" set /p PREV=<"%MARK%"

if not "%BOOT%"=="unknown" if "%PREV%"=="%BOOT%" (
    echo.
    echo ============================================================
    echo   WSL was installed earlier but Windows has NOT been
    echo   restarted since then.  Please RESTART your PC, then
    echo   double-click this file again.
    echo ============================================================
    pause
    exit /b 2
)

echo       A Windows permission window will appear - please click "Yes".
echo       A second window shows the install progress. Wait until it says FINISHED.
powershell -NoProfile -Command "Start-Process -FilePath '%~dp0motion_ai\wsl_admin.bat' -Verb RunAs -Wait"
if errorlevel 1 (
    echo.
    echo [ERROR] The administrator window could not be started. Please click "Yes"
    echo         in the Windows permission window, then run this file again.
    pause
    exit /b 1
)
>"%MARK%" echo %BOOT%

echo.
echo       Starting %DISTRO% for the first time ...
call :wsl_ok
if not errorlevel 1 (
    echo       WSL and %DISTRO% are working.
    goto :copy_files
)
where ubuntu2204.exe >nul 2>nul
if not errorlevel 1 (
    echo       Trying the Ubuntu launcher ...
    ubuntu2204.exe install --root
    call :wsl_ok
)
if not errorlevel 1 (
    echo       WSL and %DISTRO% are working.
    goto :copy_files
)

if defined PREV (
    echo.
    echo ============================================================
    echo   WSL still does not work after a restart.
    echo   Most common causes:
    echo    1. Virtualization is OFF in the BIOS/UEFI  ^(Intel VT-x / AMD SVM^)
    echo       Task Manager - Performance - CPU - "Virtualization" must say Enabled.
    echo    2. Windows is too old  ^(Windows 10 needs version 2004 or newer,
    echo       run "winver" to check^) - run Windows Update first.
    echo    3. A company/school PC or security software blocks WSL.
    echo   Please take a screenshot of the administrator window ^(it shows the
    echo   exact error^) and of the "[d] Checks" lines, and send it for help.
    echo   You can run this file again to see that window once more.
    echo ============================================================
    pause
    exit /b 3
)
echo.
echo ============================================================
echo   WSL was installed, but Windows needs a RESTART first.
echo   Please restart your PC, then double-click this file again.
echo   ^(If it shows this message again after a restart, the cause is
echo    usually BIOS virtualization being off - the next run explains.^)
echo ============================================================
pause
exit /b 2

:copy_files
echo.
echo [2/3] Copying setup files into %DISTRO% ...
wsl -d %DISTRO% -u root --exec bash -c "mkdir -p %ROOTDIR% && tr -d '\r' > %ROOTDIR%/install_wham.sh" < "%~dp0motion_ai\install_wham.sh"
if errorlevel 1 goto :copy_failed
wsl -d %DISTRO% -u root --exec bash -c "mkdir -p %ROOTDIR% && tr -d '\r' > %ROOTDIR%/wham2motion.py" < "%~dp0motion_ai\wham2motion.py"
if errorlevel 1 goto :copy_failed

echo.
echo [3/3] Installing WHAM (log file: %ROOTDIR%/install.log inside %DISTRO%) ...
echo.
wsl -d %DISTRO% -u root --exec bash %ROOTDIR%/install_wham.sh
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   Setup stopped. Read the message above, fix it, and run
    echo   this file again - it continues where it stopped.
    echo   If you need help, send the last lines of the log:
    echo     wsl -d %DISTRO% -u root --exec tail -n 40 %ROOTDIR%/install.log
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done! Go back to AnimRef and use the Motion AI window.
echo ============================================================
pause
exit /b 0

:copy_failed
echo.
echo [ERROR] Could not copy setup files into WSL. Make sure WSL works:
echo         open a command prompt and type:  wsl -d %DISTRO% -u root --exec echo hi
pause
exit /b 1

:wsl_ok
rem Real test: run a command inside the distro and look for our marker word.
rem (When WSL itself is not installed, wsl.exe only prints its help text.)
del "%PROBE%" >nul 2>nul
wsl -d %DISTRO% -u root --exec echo AR_WSL_OK >"%PROBE%" 2>&1
findstr /c:"AR_WSL_OK" "%PROBE%" >nul 2>nul
exit /b %errorlevel%
