@echo off
REM Windows 에서 exe 와 설치 프로그램을 만든다. 프로젝트 루트의 packaging 폴더에서 실행해도 된다.
REM 필요: Python 3.10+ (PATH), (선택) Inno Setup 6 의 iscc.exe
cd /d "%~dp0.."
if not exist .venv-build (
    python -m venv .venv-build || goto :err
)
call .venv-build\Scripts\activate.bat || goto :err
python -m pip install -U pip || goto :err
pip install -r requirements.txt pyinstaller || goto :err
set PYTHONUTF8=1
if not defined MB_SKIP_TESTS (python tests\run_all.py || echo [WARN] some tests failed - continuing)
REM (선택) 추출 도우미: .NET 10 SDK 와 git 이 있으면 빌드한다. 실패해도 계속 진행.
where dotnet >nul 2>nul
if %errorlevel%==0 (
    if not exist third_party\CUE4Parse git clone --depth 1 --recursive https://github.com/FabianFG/CUE4Parse third_party\CUE4Parse
    dotnet publish tools\mbextract -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o tools\mbextract\out || echo [WARN] helper build failed - extraction feature will be missing
) else (
    echo [WARN] dotnet not found - extraction helper skipped
)
REM dotnet 없이 빌드한 경우: 이미 설치된 MotionBridge 의 추출 도우미를 그대로 가져다 쓴다 (추출 기능이 빠지지 않게)
if not exist tools\mbextract\out\mbextract.exe (
    if exist "%LOCALAPPDATA%\Programs\MotionBridge\_internal\tools\mbextract.exe" (
        echo [INFO] reusing extraction helper from the installed MotionBridge
        xcopy /E /I /Y "%LOCALAPPDATA%\Programs\MotionBridge\_internal\tools" tools\mbextract\out >nul
    )
)
pyinstaller --noconfirm --clean packaging\motionbridge.spec || goto :err
echo.
echo [OK] dist\MotionBridge\MotionBridge.exe
set "ISCC="
where iscc >nul 2>nul && set "ISCC=iscc"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC (
    "%ISCC%" packaging\installer.iss || goto :err
    echo [OK] dist_installer\MotionBridge-Setup-0.1.0.exe created
) else (
    echo Inno Setup 6 not found - installer skipped. Install Inno Setup 6 and run this file again.
)
exit /b 0
:err
echo BUILD FAILED
exit /b 1
