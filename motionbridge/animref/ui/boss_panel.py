"""보스전 자동 녹화 카드: 감지 켜기/끄기, 체력바 영역 지정, 녹화본으로 감지 시험, 감지 설정.

화면 인식은 core/boss.py, OBS 연결·시작/종료 판단은 core/bossrun.py. 여기는 화면만 맡는다.
자동 녹화는 수동 녹화와 같은 버튼 흐름(CollectTab.start_record / stop_record)을 쓰므로 둘이 충돌하지 않는다:
  · 수동 녹화 중에는 자동으로 시작하지 않고, · 자동 녹화 중에 사용자가 [녹화 끝내고 분석하기]를 누르면 그 자리에서 끝낸다.
"""
import threading
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..core import boss, bossrun, settings as S
from ..core.obs import ObsRecorder

from .widgets import card, muted, restyle


class _SamplerThread(QThread):
    sampled = Signal(object)

    def __init__(self, sampler, poll):
        super().__init__()
        self.sampler, self.poll = sampler, max(float(poll), 0.2)
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            self.sampled.emit(self.sampler.sample())
            self._stop.wait(self.poll)
        self.sampler.close()

    def stop(self):
        self._stop.set()


class _RegionLabel(QLabel):
    """그림 위에서 끌어서 네모를 고르는 칸. 고른 영역은 그림 대비 비율(0~1)로 돌려준다."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(640, 360)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pm = None
        self._a = self._b = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_image(self, img: QImage):
        self._pm = QPixmap.fromImage(img)
        self._a = self._b = None
        self.update()

    def _fit(self) -> QRect:
        if self._pm is None:
            return QRect()
        s = self._pm.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect((self.width() - s.width()) // 2, (self.height() - s.height()) // 2, s.width(), s.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 22, 28))
        if self._pm is not None:
            r = self._fit()
            p.drawPixmap(r, self._pm)
            if self._a is not None and self._b is not None:
                p.setPen(QPen(QColor(255, 80, 80), 2))
                p.drawRect(QRect(self._a, self._b).normalized())
        else:
            p.setPen(QColor(150, 150, 160))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "보스전 화면을 불러온 뒤, 체력바(틀 포함)를 끌어서 감싸세요")

    def mousePressEvent(self, e):
        if self._pm is not None:
            self._a = self._b = e.position().toPoint()
            self.update()

    def mouseMoveEvent(self, e):
        if self._a is not None:
            self._b = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._a is not None:
            self._b = e.position().toPoint()
            self.update()
            self.changed.emit()

    def region(self):
        """[x, y, w, h] 그림 대비 비율. 아직 안 골랐으면 None."""
        if self._pm is None or self._a is None or self._b is None:
            return None
        r, sel = self._fit(), QRect(self._a, self._b).normalized().intersected(self._fit())
        if r.width() < 2 or sel.width() < 6 or sel.height() < 3:
            return None
        return [(sel.x() - r.x()) / r.width(), (sel.y() - r.y()) / r.height(), sel.width() / r.width(), sel.height() / r.height()]


def _qimage(bgr) -> QImage:
    h, w = bgr.shape[:2]
    rgb = bgr[:, :, ::-1].copy()
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class CalibDialog(QDialog):
    """체력바 영역 지정: OBS 에서 지금 화면을 가져오거나 스크린샷 파일을 열고, 체력바를 끌어서 고른다."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.frame = None
        self.result = None
        self.setWindowTitle("보스 체력바 영역 지정")
        self.resize(900, 600)
        v = QVBoxLayout(self)
        v.addWidget(muted("보스전 화면이 없어도 돼요. ① 화면을 하나 가져오세요(아무 때나). ② 보스 체력바가 뜨는 자리를 넉넉하게 마우스로 끌어 네모로 잡으세요 "
                       "(바 위아래 여백은 조금 있어도 괜찮아요). ③ [이 영역으로 저장]. 이 영역 안에 가로로 긴 체력바가 나타나면 자동으로 알아봐요. "
                       "보스전 중 화면으로 잡으면 체력바 모양도 함께 배워서 더 정확해져요."))
        self.region = _RegionLabel()
        v.addWidget(self.region, 1)
        row = QHBoxLayout()
        b = QPushButton("OBS 에서 지금 화면 가져오기")
        b.clicked.connect(self._from_obs)
        row.addWidget(b)
        b = QPushButton("스크린샷 파일 열기…")
        b.clicked.connect(self._from_file)
        row.addWidget(b)
        row.addStretch(1)
        self.btn_save = QPushButton("이 영역으로 저장")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save)
        row.addWidget(self.btn_save)
        v.addLayout(row)
        self.note = muted("")
        v.addWidget(self.note)

    def _set(self, bgr):
        self.frame = boss.normalize(bgr)
        self.region.set_image(_qimage(self.frame))
        self.note.setText("그림을 불러왔어요. 체력바를 끌어서 고르세요.")

    def _from_obs(self):
        try:
            o = ObsRecorder(self.panel.main.s["obs"])
            data = o.screenshot(960)
            o.close()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "OBS", "OBS 에 연결하지 못했어요: %s" % e)
            return
        img = boss.decode_image(data) if data else None
        if img is None:
            QMessageBox.warning(self, "OBS", "OBS 화면을 받지 못했어요. OBS 에 게임 화면이 보이는지 확인하세요.")
            return
        self._set(img)

    def _from_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "스크린샷", "", "이미지 (*.png *.jpg *.jpeg *.bmp);;모든 파일 (*)")
        if not p:
            return
        img = boss.decode_image(Path(p).read_bytes())
        if img is None:
            QMessageBox.warning(self, "이미지", "이미지를 읽지 못했어요.")
            return
        self._set(img)

    def _save(self):
        roi = self.region.region()
        if self.frame is None or roi is None:
            QMessageBox.information(self, "영역", "화면을 불러오고 체력바를 끌어서 고르세요.")
            return
        res = boss.calibrate_any(self.frame, roi)
        if res["mode"] == "exact":
            out = boss.save_calibration(res["cal"], str(S.app_dir() / "boss_ref.png"))
            out["baseline"] = 0.0
            QMessageBox.information(self, "영역", "체력바가 보이는 화면이라, 체력바의 모양과 색도 함께 배웠어요 (정밀 인식).")
        else:
            out = {"roi": res["roi"], "ref": "", "hsv": [], "full_cols": 0, "fill_rows": 0, "baseline": res["baseline"]}
            QMessageBox.information(self, "영역", "이 영역을 저장했어요. 체력바가 안 보이는 화면이라 '길고 얇은 바가 나타남'으로 자동 인식해요. "
                                    "[녹화본으로 감지 시험…]이나 [지금 화면으로 시험]으로 확인해 보세요.")
        self.result = out
        self.accept()


class BossPanel(QWidget):
    def __init__(self, collect):
        super().__init__()
        self.collect = collect
        self.main = collect.main
        self.thread = None
        self.run = None
        self.last = None
        self.pre_path = None
        self.rec_started = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        c, l = card("👹  보스전 자동 녹화", "화면에 보스 체력바가 나타나면 녹화를 시작하고, 사라져서 보스전이 끝났다고 판단되면 자동으로 끝내고 분석해요.")
        self.status = QLabel("꺼짐")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        l.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_watch = QPushButton("👹  보스전 감지 시작")
        self.btn_watch.setMinimumHeight(40)
        self.btn_watch.clicked.connect(self.toggle)
        row.addWidget(self.btn_watch, 1)
        b = QPushButton("체력바 영역 지정…")
        b.setToolTip("보스 체력바가 어디에 어떻게 나오는지 한 번 알려 주는 단계(필수). 보스전 화면 한 장이 필요해요.")
        b.clicked.connect(self.calibrate)
        row.addWidget(b)
        b = QPushButton("녹화본으로 감지 시험…")
        b.setToolTip("이미 녹화한 영상으로, 보스전 시작/끝을 제대로 찾는지 미리 확인해요 (녹화하지 않고 안전하게 시험)")
        b.clicked.connect(self.test_video)
        row.addWidget(b)
        l.addLayout(row)
        row2 = QHBoxLayout()
        b = QPushButton("지금 화면으로 시험 (OBS)")
        b.setToolTip("보스전 중에 눌러 보세요. 지금 OBS 화면에서 체력바를 알아보는지, 점수가 얼마인지 바로 보여 줘요 (감지를 켜지 않아도 돼요)")
        b.clicked.connect(self.test_now)
        row2.addWidget(b)
        row2.addStretch(1)
        l.addLayout(row2)
        self.g = QGroupBox("감지 설정 (시험하면서 조정하세요)")
        gl = QVBoxLayout(self.g)
        gl.setContentsMargins(6, 14, 6, 6)
        self.scroll = QScrollArea()            # 항목이 많아서 칸이 좁아져도 숫자가 잘리지 않게 스크롤로 보여 준다
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setMinimumHeight(220)
        self.scroll.setMaximumHeight(300)
        gl.addWidget(self.scroll)
        inner = QWidget()
        self.scroll.setWidget(inner)
        f = QFormLayout(inner)
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        f.setVerticalSpacing(8)
        self.spins = {}
        # (설정 이름, 보이는 이름, 최소, 최대, 한 칸, 단위, 설명, 화면값<-저장값, 저장값<-화면값)
        ident = (lambda v: v, lambda v: v)
        sens = (lambda v: (1.0 - v) * 100.0, lambda v: 1.0 - v / 100.0)     # 저장은 '엄격함(0~1)', 화면은 '민감도(%)'
        pct = (lambda v: v * 100.0, lambda v: v / 100.0)
        for key, label, lo, hi, step, suffix, tip, (to_ui, from_ui) in (
                ("match", "민감도 — 체력바를 얼마나 잘 알아볼까", 5, 80, 5, " %",
                 "높을수록 체력바가 흐리거나 배경이 복잡해도 잡아요. 대신 체력바가 아닌 것을 잘못 잡을 수 있어요.\n"
                 "· 보스전인데 감지가 안 되면 → 올리세요\n· 보스전이 아닌데 녹화가 시작되면 → 내리세요\n(처음엔 기본값 그대로 두고, [지금 화면으로 시험]으로 확인하세요)", sens),
                ("start_hold", "녹화 시작: 체력바가 이만큼 계속 보이면", 0.5, 30, 0.5, " 초",
                 "체력바가 이 시간 동안 끊기지 않고 보이면 '보스전이 시작됐다'고 보고 녹화를 시작해요. 잠깐 스치는 화면에는 반응하지 않게 하는 값이에요.", ident),
                ("end_grace", "녹화 종료: 체력바가 이만큼 안 보이면", 5, 600, 5, " 초",
                 "체력바가 이 시간 동안 안 보이면 '보스전이 끝났다'고 보고 녹화를 끝내요. 페이즈가 바뀔 때 연출로 체력바가 잠깐 사라져도 기다려 주는 시간이라, 너무 짧게 하면 중간에 끊겨요. (기본 60초)", ident),
                ("defeat_grace", "쓰러뜨린 것 같으면: 이만큼만 기다리고 종료", 2, 300, 1, " 초",
                 "보스 체력이 거의 0 이 된 채로 체력바가 사라지면 '쓰러뜨렸다'고 보고, 위의 긴 시간 대신 이 짧은 시간만 기다린 뒤 끝내요.", ident),
                ("defeat_hp", "'쓰러뜨림'으로 보는 남은 체력", 0, 30, 1, " % 이하",
                 "체력바가 이 % 이하로 줄어든 채 사라지면 쓰러뜨린 것으로 봐요. (4% = 거의 다 깎인 상태)", pct),
                ("poll", "화면 확인 간격", 0.3, 5, 0.1, " 초마다",
                 "OBS 화면을 몇 초마다 한 번 확인할지예요. 짧을수록 빨리 반응하지만 컴퓨터가 조금 더 바빠져요.", ident),
                ("max_minutes", "최대 녹화 시간 (안전장치)", 1, 120, 1, " 분",
                 "혹시 끝을 못 알아봐도, 이 시간이 지나면 강제로 녹화를 끝내요.", ident),
                ("min_seconds", "너무 짧은 녹화는 분석 안 함", 0, 300, 5, " 초 미만",
                 "체력바를 잘못 알아봐서 아주 짧게 녹화된 경우, 이보다 짧으면 분석하지 않아요 (영상 파일은 그대로 남겨요).", ident)):
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(2 if step < 0.1 else 1 if step < 1 else 0)
            sp.setSuffix(suffix)
            sp.setToolTip(tip)
            sp.setMinimumWidth(150)
            sp.setMinimumHeight(34)
            sp.setStyleSheet("QDoubleSpinBox { padding: 4px 26px 4px 10px; }")   # 위아래 버튼이 숫자를 가리지 않게 오른쪽을 비움
            sp.setValue(float(to_ui(float(boss.cfg(self.main.s)[key]))))
            sp.valueChanged.connect(lambda v, k=key, fu=from_ui: self._set(k, float(fu(float(v)))))
            self.spins[key] = sp
            lb = QLabel(label)
            lb.setWordWrap(True)
            lb.setToolTip(tip)
            f.addRow(lb, sp)
        self.ck_letter = QCheckBox("연출(위아래 검은 띠) 중에는 '안 보인 시간'을 세지 않기")
        self.ck_letter.setChecked(bool(boss.cfg(self.main.s)["letterbox_pause"]))
        self.ck_letter.toggled.connect(lambda v: self._set("letterbox_pause", bool(v)))
        f.addRow(self.ck_letter)
        self.ck_pre = QCheckBox("감지 직전 영상도 함께 저장 (OBS 리플레이 버퍼를 켜 두면)")
        self.ck_pre.setToolTip("OBS → 설정 → 출력 → 리플레이 버퍼를 켜고(시작은 이 프로그램이 안 해요, OBS 에서 [리플레이 버퍼 시작]) 길이를 20~30초로 두면, 체력바가 보이기 직전 장면도 남아요.")
        self.ck_pre.setChecked(bool(boss.cfg(self.main.s)["prebuffer"]))
        self.ck_pre.toggled.connect(lambda v: self._set("prebuffer", bool(v)))
        f.addRow(self.ck_pre)
        l.addWidget(self.g)
        self.ready_note = muted("")
        l.addWidget(self.ready_note)
        lay.addWidget(c)
        self._refresh_ready()

    # ------------------------------------------------------------------ 설정
    def _set(self, key, value):
        b = dict(self.main.s.get("boss") or {})
        b[key] = value
        self.main.s["boss"] = b
        self.main.save_settings()

    def _refresh_ready(self):
        c = boss.cfg(self.main.s)
        ok = boss.make_detector(c) is not None
        self.ready_note.setText("✔ 체력바 영역이 지정돼 있어요." if ok else "⚠ 먼저 [체력바 영역 지정…]으로 보스 체력바가 나오는 자리를 알려 주세요 (한 번만).")
        return ok

    def calibrate(self):
        dlg = CalibDialog(self)
        if dlg.exec() and dlg.result:
            b = dict(self.main.s.get("boss") or {})
            b.update(dlg.result)
            self.main.s["boss"] = b
            self.main.save_settings()
            self._refresh_ready()
            self.main.flash("체력바 영역을 저장했어요. [녹화본으로 감지 시험…]으로 확인해 보세요.")

    # ------------------------------------------------------------------ 감지 켜기/끄기
    def toggle(self):
        if self.thread is not None:
            self.stop_watch()
            return
        if not self._refresh_ready():
            QMessageBox.information(self, "보스전 감지", "먼저 [체력바 영역 지정…]을 해 주세요.")
            return
        sampler = bossrun.BossSampler(self.main.s, lambda: ObsRecorder(self.main.s["obs"]))
        self.run = bossrun.BossRun(self.main.s)
        self.thread = _SamplerThread(sampler, boss.cfg(self.main.s)["poll"])
        self.thread.sampled.connect(self._on_sample)
        self.thread.start()
        self.btn_watch.setText("■  보스전 감지 중지")
        restyle(self.btn_watch, "danger")
        self.status.setText("보스 체력바를 기다리는 중…")

    def stop_watch(self):
        if self.thread is not None:
            self.thread.stop()
            self.thread.wait(4000)
            self.thread = None
        self.run = None
        self.btn_watch.setText("👹  보스전 감지 시작")
        restyle(self.btn_watch, "")
        self.status.setText("꺼짐")

    def active(self):
        return self.thread is not None

    def manual_stopped(self):
        """사용자가 (자동으로 시작된) 녹화를 직접 끝냈을 때."""
        if self.run is not None:
            self.run.manual_stopped()

    def _on_sample(self, smp):
        if self.run is None:
            return
        self.last = smp
        other = self.collect.rec is not None and not self.run.auto_recording
        for kind, info in self.run.apply(smp, other):
            if kind == "start":
                self._start_recording()
            elif kind == "end":
                self._end_recording(info)
        self.status.setText(self.run.status(smp))

    def _start_recording(self):
        self.pre_path = None
        if boss.cfg(self.main.s)["prebuffer"]:
            try:
                o = ObsRecorder(self.main.s["obs"])
                self.pre_path = o.save_replay()
                o.close()
            except Exception:  # noqa: BLE001
                self.pre_path = None
        if self.main.busy or self.collect.rec is not None:
            self.run.auto_recording = False
            self.main.flash("다른 작업 중이라 보스전 자동 녹화를 시작하지 못했어요.")
            return
        self.collect.start_record(auto=True)
        if self.collect.rec is None:       # OBS 연결 실패 등
            self.run.auto_recording = False
            return
        import time
        self.rec_started = time.time()
        self.main.flash("보스전 감지! 자동 녹화를 시작했어요.")

    def _end_recording(self, info):
        if self.collect.rec is None:
            return
        import time
        dur = time.time() - self.rec_started
        reason = {"defeat": "보스 처치", "bar_lost": "체력바가 오래 안 보임", "max_time": "최대 시간"}.get(info.get("reason"), "보스전 끝")
        self.main.flash("보스전이 끝났어요 (%s). 녹화를 마치고 분석해요." % reason)
        self.collect.stop_record(auto=True, prebuffer=self.pre_path, min_seconds=float(boss.cfg(self.main.s)["min_seconds"]), took=dur)
        self.pre_path = None

    def test_now(self):
        if not self._refresh_ready():
            QMessageBox.information(self, "지금 화면 시험", "먼저 [체력바 영역 지정…]을 해 주세요.")
            return
        smp = bossrun.BossSampler(self.main.s, lambda: ObsRecorder(self.main.s["obs"]))
        try:
            r = smp.sample()
        finally:
            smp.close()
        if r.error:
            QMessageBox.warning(self, "지금 화면 시험", r.error)
            return
        hp = "알 수 없음" if r.hp is None else "%d%%" % round(r.hp * 100)
        QMessageBox.information(self, "지금 화면 시험", "체력바: %s\n남은 체력: %s\n연출(검은 띠) 중: %s\n\n%s\n\n"
                                "보스전 중인데 '없음'이면: [민감도]를 올리거나 체력바 영역을 다시 지정하세요. "
                                "보스전이 아닌데 '있음'이면 민감도를 내리세요." % ("있음" if r.present else "없음", hp, "예" if r.cutscene else "아니요", r.detail))

    # ------------------------------------------------------------------ 녹화본으로 시험
    def test_video(self):
        if not self._refresh_ready():
            QMessageBox.information(self, "감지 시험", "먼저 [체력바 영역 지정…]을 해 주세요.")
            return
        p, _ = QFileDialog.getOpenFileName(self, "보스전이 들어 있는 녹화본", "", "영상 (*.mp4 *.mkv *.mov *.avi *.webm);;모든 파일 (*)")
        if not p:
            return
        c = boss.cfg(self.main.s)
        det = boss.make_detector(c)

        def job(path, log=None, progress=None, cancel=None):
            def prog(f):
                if progress:
                    progress(f, "녹화본에서 보스전 찾는 중…")
            return boss.scan_video(path, det, c, step=max(float(c["poll"]), 0.5), progress=prog, cancel=cancel)

        self.main.start_job("보스전 감지 시험", job, (p,), on_done=lambda res: self._show_test(p, res))

    def _show_test(self, path, res):
        segs, st = res
        def fmt(t):
            return "%d:%02d" % (int(t) // 60, int(t) % 60)
        why = {"defeat": "처치", "bar_lost": "체력바 사라짐", "max_time": "최대 시간", "영상 끝": "영상 끝"}
        lines = ["%s ~ %s  (%s)" % (fmt(s.start), fmt(s.end), why.get(s.reason, s.reason)) for s in segs]
        head = "영상 %s 에서 보스전 %d구간을 찾았어요." % (fmt(st["duration"]), len(segs))
        tail = "\n\n체력바 인식 점수: 최고 %.2f / 가운데값 %.2f (기준 %.2f). 체력바가 보인 화면 %d/%d장." % (
            st["score_max"], st["score_median"], boss.cfg(self.main.s)["match"], st["present"], st["frames"])
        if not segs:
            tail += "\n\n못 찾았어요. '최고'가 판단 기준보다 낮으면 [민감도]를 올리거나 체력바 영역을 다시 지정해 보세요."
        QMessageBox.information(self, "감지 시험 결과", head + ("\n\n" + "\n".join(lines) if lines else "") + tail)
