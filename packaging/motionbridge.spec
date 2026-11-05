# -*- mode: python ; coding: utf-8 -*-
# 빌드: pyinstaller --noconfirm --clean packaging/motionbridge.spec   (프로젝트 루트에서)
# 결과: dist/MotionBridge/MotionBridge.exe (GUI), dist/MotionBridge/MotionBridgeCLI.exe (콘솔, 배치용)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

_helper_out = os.path.join(ROOT, "tools", "mbextract", "out")
_helper_datas = [(_helper_out, "tools")] if os.path.isdir(_helper_out) else []

# AnimRef 탭이 쓰는 파일 (아이콘, 모션 AI 스크립트). core/settings.py resource_path 가 animref 패키지 폴더 기준으로 찾는다.
_ar = os.path.join(ROOT, "motionbridge", "animref")
_animref_datas = [(os.path.join(_ar, "assets"), "motionbridge/animref/assets"),
                  (os.path.join(_ar, "motion_ai"), "motionbridge/animref/motion_ai"),
                  (os.path.join(_ar, "install_motion_ai.bat"), "motionbridge/animref"),
                  # 분석 작업자: AnimRef 용 별도 파이썬(.venv)이 소스로 읽어야 하므로 .py 파일도 그대로 둔다 (core/remote.py)
                  (os.path.join(_ar, "worker.py"), "motionbridge/animref"),
                  (os.path.join(_ar, "__init__.py"), "motionbridge/animref"),
                  (os.path.join(_ar, "core"), "motionbridge/animref/core")]

a = Analysis(
    [os.path.join(ROOT, "MotionBridge.py")],
    pathex=[ROOT],
    binaries=[],
    # Blender 안에서 실행되는 스크립트는 파일로 함께 배포해야 한다 (bridge.scripts_dir 이 찾는 위치)
    datas=[(os.path.join(ROOT, "blender_scripts"), "blender_scripts")] + _helper_datas + _animref_datas,
    hiddenimports=["imageio_ffmpeg", "cv2", "PIL", "obsws_python", "pynput"],  # 함수 안에서 불러오는 것들(frames.find_ffmpeg, AnimRef)
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MotionBridge", console=False, debug=False, strip=False, upx=False)
exe_cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MotionBridgeCLI", console=True, debug=False, strip=False, upx=False)

coll = COLLECT(exe_gui, exe_cli, a.binaries, a.datas, strip=False, upx=False, name="MotionBridge")
