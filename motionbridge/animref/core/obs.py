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

    def close(self):
        try:
            self.cl.disconnect()
        except Exception:
            pass
