"""OBS WebSocket으로 녹화 시작/종료."""


def friendly_error(e) -> str:
    t = f"{type(e).__name__}: {e}"
    low = t.lower()
    if "refused" in low or "10061" in low or "timed out" in low or "timeout" in low:
        return ("OBS에 연결할 수 없어요.\n"
                "· OBS가 켜져 있는지\n"
                "· OBS 메뉴 [도구 → WebSocket 서버 설정]에서 'WebSocket 서버 활성화'가 켜져 있는지 확인하세요.")
    if "auth" in low or "password" in low or "4009" in low:
        return "OBS 비밀번호가 맞지 않아요. [설정] 탭의 OBS 비밀번호를 OBS WebSocket 설정과 똑같이 입력하세요."
    return t


class ObsRecorder:
    def __init__(self, cfg):
        import obsws_python as obs

        self.cl = obs.ReqClient(host=cfg.get("host", "localhost"), port=int(cfg.get("port", 4455)),
                                password=cfg.get("password", ""), timeout=3)

    def version(self):
        return self.cl.get_version().obs_version

    def is_recording(self):
        return bool(self.cl.get_record_status().output_active)

    def start(self):
        if not self.is_recording():
            self.cl.start_record()

    def record_dir(self):
        """OBS 녹화 저장 폴더."""
        try:
            return getattr(self.cl.get_record_directory(), "record_directory", None)
        except Exception:
            return None

    def stop(self) -> str:
        r = self.cl.stop_record()
        return getattr(r, "output_path", None)

    # ---- 보스전 자동 녹화용: 화면 한 장 / 리플레이 버퍼(직전 영상)
    def screenshot(self, width: int = 960):
        """OBS 가 지금 보여 주는 화면(현재 장면)을 JPEG 바이트로. 실패하면 None."""
        import base64
        try:
            r = self.cl.get_current_program_scene()
            name = getattr(r, "scene_name", None) or getattr(r, "current_program_scene_name", None)
            v = self.cl.get_video_settings()
            bw, bh = int(getattr(v, "base_width", 1920)), int(getattr(v, "base_height", 1080))
            h = max(int(round(width * bh / max(bw, 1))), 2)
            img = self.cl.get_source_screenshot(name, "jpg", width, h, 70).image_data
        except Exception:
            return None
        if not img:
            return None
        return base64.b64decode(img.split(",", 1)[1] if "," in img else img)

    def replay_active(self) -> bool:
        try:
            return bool(self.cl.get_replay_buffer_status().output_active)
        except Exception:
            return False

    def save_replay(self):
        """리플레이 버퍼를 저장하고 그 파일 경로를 돌려준다 (버퍼가 꺼져 있으면 None)."""
        import time as _t
        if not self.replay_active():
            return None
        try:
            self.cl.save_replay_buffer()
            for _ in range(20):  # 저장이 끝나 경로가 나올 때까지 잠깐 기다림
                _t.sleep(0.25)
                p = getattr(self.cl.get_last_replay_buffer_replay(), "saved_replay_path", None)
                if p:
                    return p
        except Exception:
            return None
        return None

    def close(self):
        try:
            self.cl.disconnect()
        except Exception:
            pass
