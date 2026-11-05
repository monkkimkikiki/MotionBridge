"""MotionBridge 안에 들어간 AnimRef 코어: 같은 프로그램 직접 전달, 자원 경로, 가져오기 확인."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


with tempfile.TemporaryDirectory() as d:
    os.environ["APPDATA"] = d
    os.environ["HOME"] = d
    from motionbridge.animref.core import link, settings

    got = []
    link.LOCAL_TOOL = got.append
    try:
        r = link.open_in_tool({}, "C:/x/query.json")
        check(r == "live" and got == ["C:/x/query.json"], "LOCAL_TOOL 이 있으면 창구 없이 바로 전달: %s %s" % (r, got))
        link.LOCAL_TOOL = lambda p: (_ for _ in ()).throw(RuntimeError("읽지 못함"))
        try:
            link.open_in_tool({}, "q")
            check(False, "오류가 전달돼야 함")
        except RuntimeError as e:
            check("읽지 못함" in str(e), "오류 전달")
    finally:
        link.LOCAL_TOOL = None
    check(link.tool_status({"link_dir": d})[0] in ("off", "unknown", "running"), "LOCAL_TOOL 없으면 기존 방식")
    ic = settings.resource_path("assets/icon.png")
    check(ic.exists(), "아이콘 위치: %s" % ic)
    check(settings.resource_path("motion_ai/bvh2motion.py").exists() and settings.resource_path("install_motion_ai.bat").exists(), "모션 AI 스크립트 위치")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
