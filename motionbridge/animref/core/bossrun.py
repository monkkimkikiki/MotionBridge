"""보스전 자동 녹화의 실행부 (화면 없이 시험할 수 있게 UI 와 분리).

BossSampler : OBS 에서 화면 한 장을 받아 '체력바가 있나/남은 체력/연출 중인가' 를 알아낸다 (백그라운드 스레드에서 호출).
BossRun     : 알아낸 값을 받아 시작/종료를 판단하고, 수동 녹화와 겹치지 않게 해야 할 일(시작·종료)을 돌려준다 (화면 스레드에서 호출).
merge_prebuffer : 감지 직전 영상(OBS 리플레이 버퍼)과 본 녹화를 한 파일로 이어 붙인다.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import boss, media


@dataclass
class Sample:
    t: float
    present: bool = False
    score: float = 0.0
    hp: Optional[float] = None
    cutscene: bool = False
    error: str = ""


class BossSampler:
    def __init__(self, s: dict, obs_factory: Callable, clock: Callable[[], float] = time.time) -> None:
        self.cfg = boss.cfg(s)
        self.det = boss.make_detector(self.cfg)
        self._factory = obs_factory
        self._obs = None
        self.clock = clock

    def ready(self) -> bool:
        return self.det is not None

    def sample(self) -> Sample:
        t = self.clock()
        if self.det is None:
            return Sample(t, error="체력바 영역을 아직 지정하지 않았어요")
        try:
            if self._obs is None:
                self._obs = self._factory()
            data = self._obs.screenshot(960)
        except Exception as e:  # noqa: BLE001
            self.close()
            return Sample(t, error="OBS 연결 실패: %s" % e)
        if not data:
            return Sample(t, error="OBS 화면을 받지 못했어요 (장면에 소스가 있는지 확인)")
        img = boss.decode_image(data)
        if img is None:
            return Sample(t, error="OBS 화면을 읽지 못했어요")
        present, sc, hp = self.det.present(img)
        cut = boss.letterbox(boss.normalize(img)) if self.cfg.get("letterbox_pause") else False
        return Sample(t, present, sc, hp, cut)

    def close(self) -> None:
        if self._obs is not None:
            try:
                self._obs.close()
            except Exception:  # noqa: BLE001
                pass
        self._obs = None


class BossRun:
    """UI 스레드에서 쓰는 상태. recording_busy() 가 True 면(수동 녹화 또는 다른 작업) 자동으로 시작하지 않는다."""

    def __init__(self, s: dict) -> None:
        self.cfg = boss.cfg(s)
        self.watcher = boss.BossWatcher.from_cfg(self.cfg)
        self.auto_recording = False      # 자동으로 시작한 녹화가 진행 중인가
        self.skipped_for_manual = False  # 시작하려 했지만 수동 녹화 중이라 건드리지 않았음

    def apply(self, smp: Sample, other_recording: bool) -> List[Tuple[str, dict]]:
        """돌려주는 값: [("start"|"end"|"skip", info)]. other_recording = 자동 아닌 녹화가 진행 중."""
        out: List[Tuple[str, dict]] = []
        if smp.error:
            return out
        if self.watcher._present_since == float("inf"):
            boss.fix_abort(self.watcher, smp.present)
        for kind, info in self.watcher.update(smp.t, smp.present, smp.hp, smp.cutscene):
            if kind == "start":
                if other_recording and not self.auto_recording:
                    self.skipped_for_manual = True       # 수동 녹화는 건드리지 않는다
                    out.append(("skip", {"reason": "manual"}))
                    continue
                self.auto_recording = True
                self.skipped_for_manual = False
                out.append(("start", info))
            elif kind == "end":
                if self.auto_recording:
                    self.auto_recording = False
                    out.append(("end", info))
                self.skipped_for_manual = False
        return out

    def manual_stopped(self) -> None:
        """사용자가 자동 녹화를 직접 멈췄을 때."""
        self.auto_recording = False
        self.watcher.abort()

    def status(self, smp: Optional[Sample]) -> str:
        if smp is not None and smp.error:
            return "⚠ " + smp.error
        t = smp.t if smp is not None else time.time()
        if self.skipped_for_manual and self.watcher.state == "active":
            return "보스전 감지됨 · 수동 녹화 중이라 자동 녹화는 건드리지 않아요"
        return self.watcher.status_text(t) if smp is not None else "대기 중"


def merge_prebuffer(pre: str, main: str) -> Optional[str]:
    """pre(직전 영상) + main(본 녹화) 를 다시 인코딩 없이 이어 붙인다. 성공하면 새 파일 경로, 안 맞으면 None."""
    try:
        p, m = Path(pre), Path(main)
        out = m.with_name(m.stem + "_boss" + m.suffix)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("file '%s'\nfile '%s'\n" % (str(p).replace("'", "'\\''"), str(m).replace("'", "'\\''")))
            lst = f.name
        r = subprocess.run([media.ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", str(out)],
                           capture_output=True, text=True, timeout=600)
        try:
            Path(lst).unlink()
        except OSError:
            pass
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return str(out)
    except Exception:  # noqa: BLE001
        pass
    return None
