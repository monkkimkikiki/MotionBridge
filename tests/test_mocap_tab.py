"""AI 모캡 탭 시험 (가짜 Qt, 가짜 WSL). 실제 엔진은 돌리지 않는다."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.core import bridge, mocapai, smplmotion, vision  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402
from motionbridge.ui import mocap_tab as M  # noqa: E402
from qt_stub import QFileDialog, QMessageBox  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


def sync_async(mw):
    """run_async 를 그 자리에서 실행한다 (진행 알림은 무시)."""
    W = type("W", (), {"cancel": __import__("threading").Event(), "progress": type("P", (), {"emit": lambda *a: None})()})()
    errs = []

    def run(fn, done, label, **k):
        try:
            r = fn(W)
        except Exception as e:  # noqa: BLE001
            errs.append(str(e))
            return
        done(r)
    mw.run_async = run
    return errs


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        mw = A.MainWindow()
        mt = mw.mocap
        errs = sync_async(mw)
        check(mw.tabs.indexOf(mt) == 8 and mw.tabs.tabText(8) == "AI모캡", "탭 등록")
        check(mt.engine.count() == 2 and mt._eng().key == "gvhmr", "엔진 2개, 기본 GVHMR")
        mt.engine.setCurrentIndex(mt.engine.findData("wham"))
        mt._engine_changed()
        check(mt._eng().key == "wham" and mw.cfg.get("mocap_engine") == "wham" and "비상업" in mt.blurb.text(), "엔진 바꾸기 + 라이선스 안내")
        check("실험" in mocapai.WHAM.name, "WHAM 은 실험적 표시")

        # 상태 확인
        orig_cap = mocapai.run_capture
        mocapai.is_windows = lambda: True
        mocapai.run_capture = lambda s, timeout=60: (0, "wsl=0\n")
        mt.check()
        check(mt.status is not None and not mt.status.wsl and "wsl --install" in mt.st_label.text(), "WSL 없음 안내")
        mocapai.run_capture = lambda s, timeout=60: (0, "wsl=1\ngpu=RTX 3080\ncurl=1\nbzip2=1\nmamba=1\nrepo=1\nenv=1\nneed:0=1\nneed:1=1\nneed:2=1\nneed:3=1\nneed:4=1\n")
        mt.check()
        check(mt.status.ready(mocapai.WHAM) and "RTX 3080" in mt.st_label.text() and "바로 쓸 수" in mt.st_label.text(), "준비 완료 표시: " + mt.st_label.text())
        mocapai.run_capture = orig_cap

        # 설치: 확인창에서 아니오 -> 아무것도 안 돌림
        ran = []
        orig_run = mocapai.run_script
        mocapai.run_script = lambda script, on_line=None, cancel=None, timeout=None: (ran.append(script), on_line and on_line("hello"), 0)[2]
        QMessageBox.question_yes = False
        mt.install()
        check(not ran, "설치 취소하면 실행 안 함")
        QMessageBox.question_yes = True
        mocapai.run_capture = lambda s, timeout=60: (0, "wsl=1\n")
        mt.install()
        check(len(ran) == 1 and "micromamba" in ran[0] and "hello" in "\n".join(mt._log), "설치 스크립트 실행 + 로그")
        mocapai.run_capture = orig_cap

        # SMPL 놓기
        ran.clear()
        QFileDialog.dir_ret = d
        mt.place_models()
        check(ran and "SMPL_NEUTRAL" in ran[0] and mw.cfg.get("mocap_models_dir") == d, "SMPL 파일 놓기 스크립트 %d %r %r" % (len(ran), mw.cfg.get("mocap_models_dir"), d))

        # 영상 없으면 안내
        QMessageBox.calls.clear()
        mt.video.setText("")
        mt.run()
        check(QMessageBox.calls and "영상" in QMessageBox.calls[-1][2], "영상 없음 안내")

        # 실행: 가짜 엔진이 .npz 를 만들면 BVH 로 바꾸고 소스로 연다 (탭은 그대로)
        vid = os.path.join(d, "클립 1.mp4")
        open(vid, "wb").write(b"x")
        mt.video.setText(vid)
        mw.out_edit.setText(os.path.join(d, "out"))
        F = 12
        J = np.array([[0, 0.9, 0], [0.1, 0.85, 0], [-0.1, 0.85, 0], [0, 1.0, 0], [0.1, 0.5, 0], [-0.1, 0.5, 0], [0, 1.15, 0], [0.1, 0.1, 0],
                      [-0.1, 0.1, 0], [0, 1.3, 0], [0.1, 0.02, 0.1], [-0.1, 0.02, 0.1], [0, 1.5, 0], [0.05, 1.45, 0], [-0.05, 1.45, 0], [0, 1.65, 0],
                      [0.2, 1.45, 0], [-0.2, 1.45, 0], [0.45, 1.45, 0], [-0.45, 1.45, 0], [0.7, 1.45, 0], [-0.7, 1.45, 0]])
        scripts = []

        def fake_run(script, on_line=None, cancel=None, timeout=None):
            scripts.append(script)
            if "dump" in script.lower() or ".npz" in script:
                npz = [t for t in script.replace("'", " ").replace('"', " ").split() if t.endswith(".npz")]
            out = os.path.join(d, "out", "AI모캡", "클립_1", "클립_1.npz")
            np.savez(out, joints=J, root_orient=np.zeros((F, 3)), body_pose=np.zeros((F, 21, 3)), transl=np.zeros((F, 3)), fps=30.0)
            return 0

        mocapai.run_script = fake_run
        mt.status = None
        mw.tabs.setCurrentIndex(3)
        mt.run()
        bvh = os.path.join(d, "out", "AI모캡", "클립_1", "클립_1.bvh")
        check(not errs, "실행 오류 없음: %s" % errs)
        check(os.path.isfile(bvh), "BVH 만들어짐")
        check(mw.pr.src is not None and mw.pr.src.animations[0].n_frames == F, "소스 모션으로 열림")
        check(mw.tabs.currentIndex() == 3, "탭은 옮기지 않음")
        check(mw.cfg.get("mocap_video") == vid, "영상 경로 기억")

        # 엔진이 실패 코드를 주면 오류 안내
        mocapai.run_script = lambda *a, **k: 1
        mt.run()
        check(errs and "실패" in errs[-1], "실패 안내: %s" % errs)
        mocapai.run_script = orig_run

        # 비전 프리셋
        check(any("nemotron-3-nano-omni" in p[2] for p in vision.OPENAI_PRESETS), "Nemotron Omni 프리셋")
        check(all(p[0] in vision.PRESET_INFO for p in vision.OPENAI_PRESETS), "프리셋마다 안내 있음")

    print("모두 통과" if not fails else "실패 %d개" % len(fails))
    sys.exit(1 if fails else 0)


main()
