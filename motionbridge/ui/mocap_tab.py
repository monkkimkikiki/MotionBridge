"""AI 모캡 탭: 영상 한 편 -> 사람 3D 모션 (GVHMR / WHAM). 엔진 선택, 설치, SMPL 파일 놓기, 실행, 결과를 소스 모션으로 열기.

엔진은 리눅스용이라 Windows 에서는 WSL2(우분투) 안에 설치한다(mocapai). 이 탭은 화면만 맡고 실제 일은 core.mocapai / core.smplmotion 이 한다.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core import bridge, mocapai, smplmotion
from .widgets import CollapsibleBox

WSL_HELP = ("Windows 에서는 WSL2(우분투)가 필요합니다. 관리자 권한 PowerShell 에서  wsl --install -d Ubuntu  를 실행하고 재부팅한 뒤 "
            "우분투를 한 번 열어 사용자 이름/비밀번호를 만드세요. NVIDIA 그래픽 드라이버는 Windows 쪽 최신 버전이면 됩니다.")


class MocapTab(QWidget):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self.status: Optional[mocapai.Status] = None
        self._log: List[str] = []
        self._build()

    # ------------------------------------------------------------------ 화면
    def _build(self) -> None:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner = QWidget()
        sc.setWidget(inner)
        lay = QVBoxLayout(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(sc)

        intro = QLabel("<b>AI 모캡</b> — 영상 한 편에서 사람의 3D 모션을 뽑습니다(무료 오픈소스). 결과는 SMPL 뼈대(22개 관절)의 BVH 로 저장되어 "
                       "바로 소스 모션으로 열리고, 매핑·리타겟은 평소처럼 합니다. 연구·비영리용 라이선스입니다 (상업 이용 금지).")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        g = CollapsibleBox("1) 엔진 고르기")
        v = QVBoxLayout(g)
        self.engine = QComboBox()
        for e in mocapai.ENGINES.values():
            self.engine.addItem(e.name, e.key)
        self.engine.setCurrentIndex(max(0, self.engine.findData(self.cfg.get("mocap_engine", "gvhmr"))))
        self.engine.currentIndexChanged.connect(self._engine_changed)
        v.addWidget(self.engine)
        self.blurb = QLabel("")
        self.blurb.setWordWrap(True)
        v.addWidget(self.blurb)
        lay.addWidget(g)

        g = CollapsibleBox("2) 설치 상태")
        v = QVBoxLayout(g)
        self.st_label = QLabel("아직 확인하지 않았습니다.")
        self.st_label.setWordWrap(True)
        v.addWidget(self.st_label)
        h = QHBoxLayout()
        self.btn_check = QPushButton("상태 확인")
        self.btn_check.clicked.connect(lambda *_a: self.check())
        h.addWidget(self.btn_check)
        self.btn_install = QPushButton("자동 설치 시작")
        self.btn_install.setToolTip("WSL(우분투) 안에 환경 관리자·파이썬 환경·엔진·체크포인트를 설치합니다. 수 GB를 받아 30분 이상 걸릴 수 있고, "
                                    "SMPL 인체 모델은 라이선스 때문에 아래에서 따로 받아야 합니다.")
        self.btn_install.clicked.connect(lambda *_a: self.install())
        h.addWidget(self.btn_install)
        h.addStretch(1)
        v.addLayout(h)
        lay.addWidget(g)

        g = CollapsibleBox("3) SMPL 인체 모델 파일 (직접 받아야 합니다 — 무료 가입 + 라이선스 동의)")
        v = QVBoxLayout(g)
        v.addWidget(QLabel("아래 사이트에서 가입하고 모델을 내려받은 뒤(zip 그대로 OK), 받은 폴더를 고르면 알맞은 자리에 놓아 줍니다."))
        h = QHBoxLayout()
        for label, url in mocapai.SMPL_LINKS:
            b = QPushButton(label)
            b.clicked.connect(lambda *_a, u=url: QDesktopServices.openUrl(QUrl(u)))
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        self.btn_models = QPushButton("받은 폴더 고르기 → 자동으로 놓기…")
        self.btn_models.clicked.connect(lambda *_a: self.place_models())
        v.addWidget(self.btn_models)
        lay.addWidget(g)

        g = CollapsibleBox("4) 영상에서 모션 뽑기")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.video = QLineEdit(self.cfg.get("mocap_video", ""))
        self.video.setPlaceholderText("사람이 나오는 영상 파일 (mp4 등). 한 사람이 처음부터 끝까지 보이는 클립이 잘 됩니다")
        h.addWidget(self.video, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(lambda *_a: self._pick_video())
        h.addWidget(b)
        v.addLayout(h)
        h = QHBoxLayout()
        self.ck_static = QCheckBox("카메라 고정 (삼각대 영상)")
        self.ck_static.setToolTip("끄면 카메라 움직임을 추정해서 월드 좌표 이동을 복원합니다(느림, 실패할 수 있음). 켜면 빠르고 안정적입니다.")
        self.ck_static.setChecked(bool(self.cfg.get("mocap_static", True)))
        h.addWidget(self.ck_static)
        h.addWidget(QLabel("프레임 속도"))
        self.fps = QDoubleSpinBox()
        self.fps.setRange(0, 240)
        self.fps.setSpecialValueText("자동")
        self.fps.setSuffix(" fps")
        self.fps.setValue(float(self.cfg.get("mocap_fps", 0)))
        h.addWidget(self.fps)
        h.addStretch(1)
        self.btn_run = QPushButton("모션 뽑기")
        self.btn_run.setMinimumHeight(30)
        self.btn_run.clicked.connect(lambda *_a: self.run())
        h.addWidget(self.btn_run)
        v.addLayout(h)
        v.addWidget(QLabel("결과: 출력 폴더 / AI모캡 / 영상이름 / 영상이름.bvh (+ .npz 원본 매개변수). 끝나면 이 탭에 머문 채 소스 모션으로 열립니다."))
        lay.addWidget(g)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(220)
        self.log.setPlaceholderText("설치·실행 로그가 여기에 표시됩니다.")
        lay.addWidget(self.log, 1)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._flush_log)
        self._timer.start()
        self._engine_changed()

    # ------------------------------------------------------------------ 보조
    def _eng(self) -> mocapai.Engine:
        return mocapai.ENGINES[self.engine.currentData() or "gvhmr"]

    def _engine_changed(self, *_a) -> None:
        e = self._eng()
        self.cfg["mocap_engine"] = e.key
        self.blurb.setText(e.blurb + "\n· " + "\n· ".join(e.notes))
        self.status = None
        self.st_label.setText("아직 확인하지 않았습니다. [상태 확인]을 눌러 보세요.")

    def _say(self, line: str) -> None:
        self._log.append(line)
        if len(self._log) > 4000:
            del self._log[:1000]

    def _flush_log(self) -> None:
        if getattr(self, "_shown", 0) != len(self._log):
            self._shown = len(self._log)
            self.log.setPlainText("\n".join(self._log[-600:]))
            sb = self.log.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _pick_video(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "영상 고르기", os.path.dirname(self.video.text()) or self.cfg.get("last_dir", ""),
                                           "영상 (*.mp4 *.mov *.avi *.mkv *.webm *.m4v);;모든 파일 (*)")
        if p:
            self.video.setText(p)

    def _out_root(self) -> str:
        return (self.win.out_edit.text().strip() if hasattr(self.win, "out_edit") else "") or os.path.join(os.path.expanduser("~"), "MotionBridge_out")

    def _render_status(self, st: mocapai.Status) -> str:
        e = self._eng()
        if not st.wsl:
            return "✘ WSL/리눅스 셸을 실행하지 못했습니다.\n" + (WSL_HELP if mocapai.is_windows() else "bash 를 찾을 수 없습니다.") + ("\n" + st.raw[:300] if st.raw else "")
        ok = lambda b: "✔" if b else "✘"  # noqa: E731
        lines = ["%s 그래픽카드(NVIDIA): %s" % (ok(bool(st.gpu)), st.gpu or "nvidia-smi 가 안 보입니다 — GPU 없이는 이 엔진을 쓸 수 없습니다")]
        if not (st.curl and st.bzip2):
            lines.append("✘ WSL 에 curl/bzip2 가 없습니다: 우분투에서  sudo apt-get update && sudo apt-get install -y curl bzip2 git")
        lines.append("%s 엔진 설치 (소스 %s · 파이썬 환경 %s)" % (ok(st.installed()), ok(st.repo), ok(st.env)))
        for i, (nm, _rel) in enumerate(e.need_files):
            lines.append("%s %s" % (ok(st.needs.get(i, False)), nm))
        lines.append("→ " + ("바로 쓸 수 있습니다." if st.ready(e) else "빠진 것을 채워 주세요 (설치 → SMPL 파일 놓기)."))
        return "\n".join(lines)

    # ------------------------------------------------------------------ 동작
    def check(self) -> None:
        e = self._eng()
        self._say("== 상태 확인 (%s)" % e.name)

        def job(w):
            return mocapai.check_status(e)

        def done(st):
            self.status = st
            self.st_label.setText(self._render_status(st))
            self.win.statusBar().showMessage("AI 모캡 상태 확인 완료")

        self.win.run_async(job, done, "AI 모캡 환경 확인 중")

    def install(self) -> None:
        e = self._eng()
        if QMessageBox.question(self, "자동 설치", "%s 을(를) 설치합니다.\n\n· WSL(우분투) 안에 수 GB 를 받습니다 (30분 이상 걸릴 수 있음)\n· 중간에 멈춰 보여도 로그가 올라오고 있으면 정상입니다\n"
                                "· SMPL 인체 모델은 이 단계에 포함되지 않습니다 (3단계)\n\n진행할까요?" % e.name,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._say("== 설치 시작: %s" % e.name)
        script = mocapai.install_script(e)

        def job(w):
            def on_line(line):
                self._say(line)
                w.progress.emit(0, 1, re.sub(r"\s+", " ", line)[:90])
            rc = mocapai.run_script(script, on_line, w.cancel)
            if rc != 0:
                raise RuntimeError("설치가 중간에 멈췄습니다 (코드 %s). 로그의 '!!' 줄을 확인하세요." % rc + ("\n" + WSL_HELP if rc == 127 else ""))
            return rc

        def done(_rc):
            self._say("== 설치 단계 끝")
            self.win.statusBar().showMessage("설치 끝 — 상태 확인으로 빠진 것을 확인하세요")
            self.check()

        self.win.run_async(job, done, "AI 모캡 설치 중 (오래 걸립니다)", cancellable=True,
                           on_cancel=lambda: self._say("설치를 취소했습니다 (WSL 안에서 하던 작업이 잠시 남아 있을 수 있어요)."))

    def place_models(self) -> None:
        e = self._eng()
        d = QFileDialog.getExistingDirectory(self, "SMPL / SMPL-X 를 받아 둔 폴더", self.cfg.get("mocap_models_dir", "") or os.path.expanduser("~"))
        if not d:
            return
        self.cfg["mocap_models_dir"] = d
        bridge.save_config(self.cfg)
        self._say("== SMPL 파일 놓기: %s" % d)
        script = mocapai.place_models_script(e, d)

        def job(w):
            rc = mocapai.run_script(script, self._say, w.cancel)
            if rc != 0:
                raise RuntimeError("SMPL 파일을 놓지 못했습니다 (코드 %s). 로그를 확인하세요." % rc)
            return rc

        self.win.run_async(job, lambda _r: (self.win.statusBar().showMessage("SMPL 파일을 놓았습니다"), self.check()), "SMPL 파일 놓는 중")

    def run(self) -> None:
        e = self._eng()
        video = self.video.text().strip()
        if not video or not os.path.isfile(video):
            QMessageBox.warning(self, "모션 뽑기", "영상 파일을 고르세요.")
            return
        if self.status is not None and not self.status.ready(e):
            if QMessageBox.question(self, "모션 뽑기", "설치 상태에 빠진 것이 있습니다. 그래도 실행해 볼까요?\n(실패하면 로그에 이유가 나옵니다)",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        stem = re.sub(r"[^\w.\-]+", "_", os.path.splitext(os.path.basename(video))[0]).strip("._") or "clip"
        out_dir = os.path.join(self._out_root(), "AI모캡", stem)
        os.makedirs(out_dir, exist_ok=True)
        npz, bvh = os.path.join(out_dir, stem + ".npz"), os.path.join(out_dir, stem + ".bvh")
        fps = float(self.fps.value()) or None
        static = bool(self.ck_static.isChecked())
        self.cfg.update({"mocap_video": video, "mocap_static": static, "mocap_fps": float(self.fps.value())})
        bridge.save_config(self.cfg)
        self._say("== 모션 뽑기: %s (%s)" % (os.path.basename(video), e.name))
        script = mocapai.run_script_text(e, video, npz, static, fps)
        pr = self.win.pr

        def job(w):
            def on_line(line):
                self._say(line)
                w.progress.emit(0, 1, re.sub(r"\s+", " ", line)[:90])
            rc = mocapai.run_script(script, on_line, w.cancel)
            if rc != 0:
                raise RuntimeError("엔진 실행이 실패했습니다 (코드 %s). 로그의 오류를 확인하세요." % rc + ("\n" + WSL_HELP if rc == 127 else ""))
            if not os.path.isfile(npz):
                raise RuntimeError("결과 파일이 만들어지지 않았습니다: " + npz)
            w.progress.emit(0, 1, "BVH 로 변환 중")
            smplmotion.npz_to_bvh(npz, bvh, stem)
            w.progress.emit(0, 1, "모션 읽는 중")
            return pr.load_source(bvh)

        def done(m):
            self.win._remember_dir(bvh)
            self.win._after_load("source")  # 탭은 옮기지 않는다
            n = m.animations[0].n_frames
            self._say("== 완료: %s (%d프레임)" % (bvh, n))
            self.win.statusBar().showMessage("AI 모캡 완료: %d프레임 — 소스 모션으로 열었습니다 (%s)" % (n, bvh))

        self.win.run_async(job, done, "AI 모캡 실행 중 (GPU 에서 수 분)", cancellable=True,
                           on_cancel=lambda: self._say("취소했습니다 (WSL 안에서 하던 작업이 잠시 남아 있을 수 있어요)."))
