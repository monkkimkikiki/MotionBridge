# -*- mode: python ; coding: utf-8 -*-
# 빌드: pyinstaller --noconfirm --clean packaging/motionbridge.spec   (프로젝트 루트에서)
# 결과: dist/MotionBridge/MotionBridge.exe (GUI), dist/MotionBridge/MotionBridgeCLI.exe (콘솔, 배치용)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

_helper_out = os.path.join(ROOT, "tools", "mbextract", "out")
_helper_datas = [(_helper_out, "tools")] if os.path.isdir(_helper_out) else []

a = Analysis(
    [os.path.join(ROOT, "MotionBridge.py")],
    pathex=[ROOT],
    binaries=[],
    # Blender 안에서 실행되는 스크립트는 파일로 함께 배포해야 한다 (bridge.scripts_dir 이 찾는 위치)
    datas=[(os.path.join(ROOT, "blender_scripts"), "blender_scripts")] + _helper_datas,
    hiddenimports=["imageio_ffmpeg"],  # frames.find_ffmpeg 가 try 안에서 불러오므로 명시
    excludes=["tkinter", "matplotlib", "PIL", "scipy", "pandas", "IPython", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MotionBridge", console=False, debug=False, strip=False, upx=False)
exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MotionBridgeCLI", console=True, debug=False, strip=False, upx=False)

coll = COLLECT(exe_gui, exe_cli, a.binaries, a.datas, strip=False, upx=False, name="MotionBridge")
