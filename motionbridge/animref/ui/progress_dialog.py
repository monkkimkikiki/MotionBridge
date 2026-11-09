"""진행 현황 창: 오래 걸리는 작업을 백그라운드에서 돌리면서 진행률·단계·경과 시간·로그를 보여 주고, 취소할 수 있다."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from .widgets import Job, muted


class ProgressDialog(QDialog):
    """fn(*args, log=, progress=, cancel=) 를 Job 으로 돌린다. 끝나면 self.result / self.error / self.cancelled 에 결과가 남는다."""

    def __init__(self, parent, title, fn, args=(), kwargs=None, intro="", cancel_text="취소"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)
        self.result, self.error, self.cancelled, self.finished_ok = None, None, False, False
        self.t0 = time.time()
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        root.addWidget(t)
        if intro:
            root.addWidget(muted(intro))
        self.step = QLabel("준비 중...")
        self.step.setWordWrap(True)
        root.addWidget(self.step)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)           # 진행률을 받기 전에는 '움직이는 막대'
        self.bar.setMinimumHeight(22)
        root.addWidget(self.bar)
        self.elapsed = muted("경과 0초")
        root.addWidget(self.elapsed)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumHeight(110)
        self.logbox.setPlaceholderText("진행 기록이 여기에 나와요")
        root.addWidget(self.logbox)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn = QPushButton(cancel_text)
        self.btn.clicked.connect(self._cancel)
        row.addWidget(self.btn)
        root.addLayout(row)
        self.job = Job(title, fn, args, kwargs)
        self.job.log.connect(self._log)
        self.job.progress.connect(self._progress)
        self.job.succeeded.connect(self._ok)
        self.job.failed.connect(self._fail)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(300)

    def exec(self):
        self.job.start()
        return super().exec()

    def _tick(self):
        self.elapsed.setText("경과 %d초" % int(time.time() - self.t0))

    def _log(self, m):
        if m:
            self.logbox.appendPlainText(m)

    def _progress(self, f, text):
        if f is not None and f >= 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(min(max(f, 0.0), 1.0) * 1000))
        if text:
            self.step.setText(text)

    def _cancel(self):
        if self.job.isRunning():
            self.job.cancel.set()
            self.btn.setEnabled(False)
            self.step.setText("취소하는 중...")
        else:
            self.reject()

    def _finish(self):
        self.timer.stop()
        self.job.wait(5000)

    def _ok(self, r):
        self.result, self.finished_ok = r, True
        self._finish()
        self.accept()

    def _fail(self, msg, cancelled):
        self.error, self.cancelled = msg, bool(cancelled)
        self._finish()
        self.reject()

    def reject(self):
        if self.job.isRunning():       # 창 닫기(X)/Esc 는 취소와 같게 취급
            self._cancel()
            return
        super().reject()

    def closeEvent(self, e):
        if self.job.isRunning():
            self._cancel()
            e.ignore()
            return
        super().closeEvent(e)
