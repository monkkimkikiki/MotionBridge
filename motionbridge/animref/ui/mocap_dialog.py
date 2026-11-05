"""모션 AI 창: 클립에서 3D 동작을 뽑아(WHAM) Blender / 3ds Max 의 '이미 세팅된 리그'에 키프레임으로 넣는다.

  ① 준비 상태: 설치 여부를 확인하고 [설치 시작]으로 한 번에 설치 (WSL + WHAM)
  ② 모션 뽑기: 선택한 클립마다 .bvh / .motion.json / .motion.txt 를 만든다
  ③ 보내기: Blender (창 없이 적용 → 결과 확인 → 저장된 파일을 열어 줌) / 3ds Max (파일 폴더 + 스크립트)"""
import copy
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..core import characters, external, handoff, library, link, mocap

from .characters_dialog import CharactersDialog, SubjectPickerDialog, SubjectReviewDialog
from .widgets import Job, card, muted, open_path

REDUCE = [("매 프레임 키 (정확하지만 키가 아주 많아요)", 0.0),
          ("키 줄이기 - 약하게 (오차 1.5°)", 1.5),
          ("키 줄이기 - 강하게 (오차 4°, 손보기 쉬워요)", 4.0)]
SMPL_URLS = ("https://smpl.is.tue.mpg.de", "https://smplify.is.tue.mpg.de")
TIP = ("사람이 크게, 전신이 보이는 짧은 클립(10초 이하)이 가장 잘 돼요. 몬스터·네발 짐승, 이펙트가 몸을 가리는 장면, "
       "카메라가 급히 바뀌는 장면은 손발이 튀거나 틀어질 수 있어요. 결과는 완성본이 아니라 '타이밍과 큰 포즈를 잡아 주는 초벌'로 보세요.")


def _status_job(log=None, progress=None, cancel=None):
    return mocap.status()


def clip_name(meta):
    return Path(meta["_json"]).stem


def motion_ok(meta):
    """클립에 뽑아 둔 모션이 있고 파일도 남아 있는지."""
    m = meta.get("motion") or {}
    return bool(m) and all(Path(m.get(k, "")).exists() for k in ("bvh", "json", "txt"))


def subject_mark(meta):
    sj = meta.get("subject") or {}
    if not sj.get("boxes"):
        return ""
    who = sj.get("character") or "직접 고른 사람"
    return f"🎯 {who}"


def _export_batch(metas, s, follow=True, log=None, progress=None, cancel=None):
    """여러 클립의 서비스용 영상을 차례로 만든다. 하나가 실패해도 나머지는 계속."""
    log = log or (lambda *_: None)
    progress = progress or (lambda *_: None)
    ok, failed = [], []
    for i, m in enumerate(metas):
        name = Path(m["_json"]).stem
        try:
            ok.append(external.export_for_external(
                m, s, follow=follow, log=log, cancel=cancel,
                progress=lambda f, t, i=i: progress((i + max(0.0, f)) / len(metas), f"({i + 1}/{len(metas)}) {t}")))
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "Cancelled":
                raise
            log(f"[서비스용 영상] {name} 실패: {e}")
            failed.append((name, str(e)))
    if not ok and failed:
        raise RuntimeError(failed[0][1])
    return {"ok": ok, "failed": failed}


def row_text(meta):
    m = meta.get("motion") or {}
    mark = f"✅ 모션 있음 ({m.get('frames', '?')}프레임)" if motion_ok(meta) else "—"
    sj = subject_mark(meta)
    ga = meta.get("game_anim")
    link = f"    🔗 {handoff.describe(ga)}" if ga else ("    📨 보냄" if meta.get("handoff") else "")
    return f"{clip_name(meta)}    {mark}" + (f"    {sj}" if sj else "") + link


def status_html(st):
    lv = st.get("level")
    if lv == "ok":
        return "<b style='color:#46c37b'>✅ 준비 완료</b> — 바로 모션을 뽑을 수 있어요."
    if lv in ("partial", "not_installed", "no_distro", "no_wsl"):
        return f"<b style='color:#f0a03c'>⚠ 설치가 필요해요</b><br>{st.get('message', '')}"
    return f"<b style='color:#f0a03c'>⚠ {st.get('message', '확인하지 못했어요')}</b>"


def _send_tool_job(s, query_path, log=None, progress=None, cancel=None):
    """Job 안에서 실행 (툴이 켜질 때까지 최대 20초 기다릴 수 있어서 화면을 막지 않게)."""
    if progress:
        progress(-1, "추출 툴에 보내는 중...")
    return link.open_in_tool(s, query_path, log=log or (lambda *_: None))


class MocapDialog(QDialog):
    def __init__(self, main, metas):
        super().__init__(main)
        self.main = main
        self.metas = list(metas)
        self.changed = False
        self.job = None
        self.sjob = None
        self.st = None
        self.installer_started = False
        self.setWindowTitle("🦴 모션 AI — 영상에서 3D 동작 뽑기")
        self.resize(800, 860)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 16, 20, 8)
        lay.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- ① 준비 상태
        c1, l1 = card("① 모션 AI 준비 상태",
                      "영상에서 사람의 3D 동작을 뽑는 AI(WHAM)를 쓰려면 한 번만 설치가 필요해요. "
                      "Windows 안에 리눅스(WSL)를 깔고 그 안에서 돌리기 때문에 설치가 조금 길어요.")
        self.lbl_status = QLabel("상태를 확인하는 중...")
        self.lbl_status.setWordWrap(True)
        l1.addWidget(self.lbl_status)
        self.lbl_gpu = muted("")
        l1.addWidget(self.lbl_gpu)
        row = QHBoxLayout()
        self.btn_install = QPushButton("⚙ 설치 시작 (원클릭)")
        self.btn_install.setObjectName("primary")
        self.btn_install.clicked.connect(self.install)
        self.btn_check = QPushButton("⟳ 상태 새로고침")
        self.btn_check.clicked.connect(lambda: self.refresh_status())
        row.addWidget(self.btn_install)
        row.addWidget(self.btn_check)
        row.addStretch(1)
        l1.addLayout(row)
        l1.addWidget(muted(
            "설치할 때 알아 둘 점:\n"
            "· 20~60분 걸리고 수 GB를 내려받아요. 검은 설치 창이 열리고, 중간에 Windows 권한 창이 한 번 떠서 두 번째 창(관리자)에서 WSL을 설치해요. 처음엔 PC 재시작이 필요할 수 있어요.\n· 재시작 후에도 같은 안내가 나오면 대부분 BIOS에서 가상화(Intel VT-x / AMD SVM)가 꺼져 있는 거예요. 작업 관리자 → 성능 → CPU의 '가상화'가 '사용'인지 확인하세요.\n"
            "· 설치 막바지에 SMPL 사이트의 아이디/비밀번호를 직접 입력해야 해요. 아래 두 사이트에 같은 아이디로 먼저 가입하세요. "
            "SMPL은 보통 '비상업' 사용 조건에 동의하게 돼요.\n"
            "· NVIDIA 그래픽카드가 필요해요. 중간에 멈춰도 [설치 시작]을 다시 누르면 이어서 해요."))
        row = QHBoxLayout()
        for url in SMPL_URLS:
            b = QPushButton("🌐 " + url.replace("https://", ""))
            b.setObjectName("link")
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            row.addWidget(b)
        row.addStretch(1)
        l1.addLayout(row)
        lay.addWidget(c1)

        # ---- ② 모션 뽑기
        c2, l2 = card("② 모션 뽑기", "목록에서 클립을 고르고(여러 개 가능, 안 고르면 전부) [모션 뽑기]를 누르세요.")
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setMinimumHeight(130)
        for m in self.metas:
            self.list.addItem(QListWidgetItem(row_text(m)))
        if self.list.count():
            self.list.setCurrentRow(0)
        l2.addWidget(self.list)
        # -- 대상 캐릭터 (화면에 여러 명이 나올 때 '누구의 모션'인지 정하기)
        l2.addWidget(QLabel("<b>🎯 모션을 뽑을 캐릭터 (선택)</b>"))
        l2.addWidget(muted("화면에 캐릭터가 여럿 나오거나 겹칠 때, 등록한 캐릭터와 닮은 사람만 골라서 그 모션만 뽑아요. "
                           "고르지 않으면 지금까지처럼 '가장 오래 나온 사람'을 써요."))
        row = QHBoxLayout()
        self.cb_char = QComboBox()
        self.cb_char.setMinimumWidth(220)
        self.btn_chars = QPushButton("👤 캐릭터 등록/관리")
        self.btn_chars.clicked.connect(self.manage_characters)
        row.addWidget(self.cb_char, 1)
        row.addWidget(self.btn_chars)
        l2.addLayout(row)
        row = QHBoxLayout()
        self.btn_find = QPushButton("🎯 대상 찾기 (선택한 클립)")
        self.btn_find.clicked.connect(self.find_targets)
        self.btn_unsub = QPushButton("대상 해제")
        self.btn_unsub.clicked.connect(self.clear_targets)
        row.addWidget(self.btn_find)
        row.addWidget(self.btn_unsub)
        row.addStretch(1)
        l2.addLayout(row)
        self.ck_vlm = QCheckBox("애매하면 AI(Qwen3-VL)에게 물어보기 (Ollama가 켜져 있어야 해요)")
        self.ck_vlm.setChecked(bool(main.s.get("mocap_use_vlm", True)))
        self.ck_crop = QCheckBox("대상을 따라가며 확대해서 분석 (작은 캐릭터도 잘 찾아요 · 제자리 모션으로 뽑아요)")
        self.ck_crop.setChecked(bool(main.s.get("mocap_crop_subject", False)))
        self.ck_crop.setToolTip("다른 캐릭터가 옆에 붙어 있어 엉뚱한 사람이 뽑힐 때 켜 보세요. 이동 정보는 정확하지 않아 빠져요.")
        l2.addWidget(self.ck_vlm)
        l2.addWidget(self.ck_crop)
        self._fill_chars(main.s.get("mocap_character", ""))
        self.ck_inplace = QCheckBox("제자리로 (앞뒤·좌우 이동은 빼고 동작만)")
        self.ck_inplace.setChecked(bool(main.s.get("mocap_in_place", False)))
        self.ck_inplace.setToolTip("카메라 기준으로 추정한 이동은 부정확해서, 이동 없이 동작만 참고하고 싶을 때 켜세요.")
        self.ck_markers = QCheckBox("타격 시점을 마커로 표시하고, 키 줄이기에서 그 주변은 꼭 남기기")
        self.ck_markers.setChecked(bool(main.s.get("mocap_markers", True)))
        l2.addWidget(self.ck_inplace)
        l2.addWidget(self.ck_markers)
        row = QHBoxLayout()
        self.btn_extract = QPushButton("🦴 모션 뽑기")
        self.btn_extract.setObjectName("primary")
        self.btn_extract.clicked.connect(self.extract)
        self.btn_cancel = QPushButton("⏹ 취소")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cancel.setVisible(False)
        row.addWidget(self.btn_extract)
        row.addWidget(self.btn_cancel)
        row.addStretch(1)
        l2.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        l2.addWidget(self.bar)
        self.lbl_prog = muted("")
        l2.addWidget(self.lbl_prog)
        l2.addWidget(muted(TIP))
        lay.addWidget(c2)

        # ---- ②-B 외부 AI 서비스
        c2b, l2b = card("②-B 외부 AI 서비스로 뽑기 (선택)",
                        "DeepMotion · Move.ai · Rokoko Vision · Plask 같은 서비스에 영상을 올려 모션을 받아서 쓰는 방법이에요. "
                        "①~②의 모션 AI(WHAM)보다 나은지는 영상마다 달라서, 같은 클립으로 비교해 보세요.")
        l2b.addWidget(muted("1) [📤 서비스에 올릴 영상 만들기] → 만들어진 영상을 서비스 사이트에 올려요 (서비스 이용약관과 요금은 직접 확인하세요)\n"
                            "2) 서비스에서 결과를 BVH(또는 FBX)로 받아요 — 가능하면 BVH 가 간단해요\n"
                            "3) 목록에서 그 클립을 고르고 [📥 받은 모션 가져오기] → 이후 ③ Blender/3ds Max 로 보내는 건 똑같아요"))
        self.ck_ext_follow = QCheckBox("대상을 따라가며 확대한 영상으로 만들기 (대상이 정해진 클립만 · 1024×1024)")
        self.ck_ext_follow.setChecked(True)
        l2b.addWidget(self.ck_ext_follow)
        row = QHBoxLayout()
        self.btn_ext_out = QPushButton("📤 서비스에 올릴 영상 만들기")
        self.btn_ext_out.clicked.connect(self.export_external)
        self.btn_ext_in = QPushButton("📥 받은 모션(BVH/FBX) 가져오기")
        self.btn_ext_in.clicked.connect(self.import_external)
        row.addWidget(self.btn_ext_out)
        row.addWidget(self.btn_ext_in)
        row.addStretch(1)
        l2b.addLayout(row)
        l2b.addWidget(muted("가져온 모션은 선택한 클립 한 개에 붙어요 (기존 모션은 이 클립의 현재 모션으로 대체돼요). "
                            "사람 모양 뼈대(골반·다리 2·척추·팔 2·머리)만 읽을 수 있어요. 크리처(꼬리·날개 등) 뼈대는 지원하지 않아요."))
        lay.addWidget(c2b)

        # ---- ②-C 게임 애니메이션 추출 툴 연동
        c2c, l2c = card("②-C 게임 애니메이션 추출 툴로 보내기 (선택)",
                        "영상 속 캐릭터와 모션 정보를 별도 '추출 툴'로 넘기면, 툴이 그 캐릭터의 게임 애니메이션 중 비슷한 후보를 나열해요. "
                        "후보를 영상과 비교해서 맞는 것을 고르면 툴이 그 애니메이션과 모델링 데이터를 꺼내요. (AnimRef는 영상을 보여 주고 선택 결과를 클립에 연결 표시해요)")
        self.lbl_tool_state = QLabel("")
        l2c.addWidget(self.lbl_tool_state)
        l2c.addWidget(muted("순서: ① [🎯 대상 찾기]로 캐릭터를 정하고 ② (있으면) [모션 뽑기]로 거친 모션을 만든 뒤 ③ 아래 [📨 보내기] → 추출 툴에서 후보 선택 → ④ [📥 결과 가져오기]. 모션 AI가 실패해도 캐릭터·길이·타격 시각만으로 보낼 수 있어요."))
        row = QHBoxLayout()
        row.addWidget(QLabel("추출 툴 실행 명령"))
        self.ed_tool = QLineEdit(main.s.get("tool_cmd", ""))
        self.ed_tool.setPlaceholderText(handoff.CMD_HELP)
        b_tool = QPushButton("찾아보기")
        b_tool.clicked.connect(self._pick_tool)
        row.addWidget(self.ed_tool, 1)
        self.ed_tool.editingFinished.connect(self._tool_hint_changed)
        row.addWidget(b_tool)
        l2c.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("켜졌는지 확인할 이름"))
        self.ed_tool_proc = QLineEdit(main.s.get("tool_process", ""))
        self.ed_tool_proc.setPlaceholderText("프로그램 이름(mbextract.exe) 또는 툴 창 제목의 일부. 비우면 실행 명령에서 알아내요")
        row.addWidget(self.ed_tool_proc, 1)
        self.ed_tool_proc.editingFinished.connect(self._tool_hint_changed)
        l2c.addLayout(row)
        l2c.addWidget(muted("추출 툴이 켜져 있으면 바로 보내요. 꺼져 있으면 이 명령으로 켜서 보내요. "
                            "툴이 실시간 연결을 지원하지 않거나 명령이 비어 있으면 연동 폴더만 열어 줘요 (툴에서 query.json 을 직접 열면 돼요)."))
        self.ck_tool_open = QCheckBox("보낸 뒤 영상 열기 (후보와 비교하며 볼 수 있게)")
        self.ck_tool_open.setChecked(bool(main.s.get("tool_open_video", True)))
        self.ck_tool_copy = QCheckBox("영상을 연동 폴더에 복사해서 보내기 (툴이 라이브러리 경로를 못 읽을 때)")
        self.ck_tool_copy.setChecked(bool(main.s.get("tool_copy_video", False)))
        l2c.addWidget(self.ck_tool_open)
        l2c.addWidget(self.ck_tool_copy)
        row = QHBoxLayout()
        self.btn_tool_send = QPushButton("📨 추출 툴로 보내기 (선택한 클립)")
        self.btn_tool_send.clicked.connect(self.send_tool)
        self.btn_tool_get = QPushButton("📥 결과 가져오기 (실시간이 안 될 때)")
        self.btn_tool_get.clicked.connect(self.get_tool_result)
        row.addWidget(self.btn_tool_send)
        row.addWidget(self.btn_tool_get)
        row.addStretch(1)
        l2c.addLayout(row)
        lay.addWidget(c2c)

        # ---- ③ Blender
        c3, l3 = card("③-A Blender로 보내기",
                      "이미 뼈(Armature)가 세팅된 .blend 파일을 고르면, 이름으로 뼈를 자동으로 찾아 키프레임을 넣어요. "
                      "원래 리그 파일은 그대로 두고 옆에 새 이름으로 저장해요.")
        self.ed_blender = QLineEdit(main.s.get("blender_exe") or mocap.find_blender())
        self.ed_blender.setPlaceholderText("blender.exe 경로 (설치돼 있으면 자동으로 찾아요)")
        self.ed_rig = QLineEdit(main.s.get("rig_blend", ""))
        self.ed_rig.setPlaceholderText("리그가 들어 있는 .blend 파일")
        l3.addLayout(self._path_row("Blender", self.ed_blender, self._pick_blender))
        l3.addLayout(self._path_row("리그 파일", self.ed_rig, self._pick_rig))
        self.cb_reduce = QComboBox()
        for t, v in REDUCE:
            self.cb_reduce.addItem(t, v)
        cur = float(main.s.get("mocap_reduce", 0.0) or 0.0)
        for i, (_t, v) in enumerate(REDUCE):
            if abs(v - cur) < 1e-9:
                self.cb_reduce.setCurrentIndex(i)
        l3.addWidget(self.cb_reduce)
        self.ck_video = QCheckBox("참고 영상을 카메라 배경으로 깔기 (실패해도 모션은 들어가요)")
        self.ck_video.setChecked(bool(main.s.get("mocap_video_bg", False)))
        l3.addWidget(self.ck_video)
        self.btn_blender = QPushButton("🟠 Blender로 보내기")
        self.btn_blender.setObjectName("primary")
        self.btn_blender.clicked.connect(self.send_blender)
        l3.addWidget(self.btn_blender)
        l3.addWidget(muted("리그에 IK/FK 전환이나 제약(Constraint)이 있으면 FK 뼈에 키를 넣어요. 못 찾은 뼈는 결과 안내에 나와요."))
        lay.addWidget(c3)

        # ---- ③ 3ds Max
        c4, l4 = card("③-B 3ds Max로 보내기",
                      "Max용 파일이 모인 폴더를 열어 줘요. 폴더 안의 max_apply.ms 를 Max에서 실행하고 .motion.txt 를 고르면 돼요.")
        row = QHBoxLayout()
        self.btn_max = QPushButton("📁 Max용 파일 폴더 열기")
        self.btn_max.clicked.connect(self.open_max)
        self.btn_bvh = QPushButton("📁 BVH 파일 폴더 열기 (Biped·기타 프로그램용)")
        self.btn_bvh.clicked.connect(self.open_bvh)
        row.addWidget(self.btn_max)
        row.addWidget(self.btn_bvh)
        row.addStretch(1)
        l4.addLayout(row)
        l4.addWidget(muted("max_apply.ms 는 3ds Max 없이 만들어서 아직 실행해 보지 못했어요. 오류가 나면 Listener(F11) 내용을 알려 주세요. "
                           "Biped 리그도 지원하지만 실험 단계예요: Figure Mode를 켠 기본 자세에서 [적용]하세요. 안 되면 BVH를 Biped의 Motion Capture로 불러오세요."))
        lay.addWidget(c4)
        lay.addStretch(1)

        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(20, 0, 20, 0)
        bottom.addStretch(1)
        bottom.addWidget(close)
        outer.addLayout(bottom)

        self.poll = QTimer(self)
        self.poll.setInterval(8000)
        self.poll.timeout.connect(self._poll)
        self._sync_buttons()
        self._connect_link()
        self.refresh_status()

    # ---------------------------------------------------------------- 도우미
    def _path_row(self, label, edit, picker):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addWidget(edit, 1)
        b = QPushButton("찾아보기")
        b.clicked.connect(picker)
        row.addWidget(b)
        return row

    def _pick_blender(self):
        p, _ = QFileDialog.getOpenFileName(self, "Blender 실행 파일 선택", self.ed_blender.text(), "blender.exe (blender.exe);;모든 파일 (*)")
        if p:
            self.ed_blender.setText(p)

    def _pick_rig(self):
        p, _ = QFileDialog.getOpenFileName(self, "리그가 들어 있는 .blend 파일", self.ed_rig.text(), "Blender 파일 (*.blend)")
        if p:
            self.ed_rig.setText(p)

    def _save_prefs(self):
        s = self.main.s
        s["blender_exe"] = self.ed_blender.text().strip()
        s["rig_blend"] = self.ed_rig.text().strip()
        s["mocap_in_place"] = self.ck_inplace.isChecked()
        s["mocap_markers"] = self.ck_markers.isChecked()
        s["mocap_reduce"] = float(self.cb_reduce.currentData() or 0.0)
        s["mocap_video_bg"] = self.ck_video.isChecked()
        s["mocap_character"] = self._char_name()
        s["mocap_crop_subject"] = self.ck_crop.isChecked()
        s["mocap_use_vlm"] = self.ck_vlm.isChecked()
        s["tool_cmd"] = self.ed_tool.text().strip()
        s["tool_process"] = self.ed_tool_proc.text().strip()
        s["tool_open_video"] = self.ck_tool_open.isChecked()
        s["tool_copy_video"] = self.ck_tool_copy.isChecked()
        self.main.save_settings()

    # ---------------------------------------------------------------- 캐릭터 / 대상
    def _char_name(self):
        return self.cb_char.currentData() or ""

    def _fill_chars(self, select=""):
        self.cb_char.blockSignals(True)
        self.cb_char.clear()
        self.cb_char.addItem("(고르지 않음 — 가장 오래 나온 사람)", "")
        try:
            chars = characters.list_characters(self.main.s)
        except Exception:  # noqa: BLE001
            chars = []
        for c in chars:
            self.cb_char.addItem(f"{c['name']}  ({characters.KINDS.get(c.get('kind'), '')[:3]})", c["name"])
        i = self.cb_char.findData(select)
        self.cb_char.setCurrentIndex(max(0, i))
        self.cb_char.blockSignals(False)

    def manage_characters(self):
        dlg = CharactersDialog(self.main, select=self._char_name() or None)
        dlg.exec()
        self._fill_chars(self._char_name() if not dlg.changed else (dlg.cur or {}).get("name", self._char_name()))

    def find_targets(self):
        if self._running():
            return
        targets = self._target_metas()
        if not targets:
            return
        name = self._char_name()
        self._save_prefs()                       # 등록 캐릭터가 없어도 '직접 고르기'로 쓸 수 있다
        self._start("대상 찾기", characters.locate_batch, (targets, copy.deepcopy(self.main.s), name),
                    {"use_vlm": self.ck_vlm.isChecked()}, self._located)

    def _located(self, res):
        self._refresh_rows()
        auto, pending, failed = res["auto"], list(res["pending"]), res["failed"]
        entries = [(library.load_clip(mm["_json"]) or mm, r) for mm, r in res.get("auto_entries", [])]
        kept = len(auto)
        if entries:                                    # 자동으로 정한 결과를 눈으로 확인
            rv = SubjectReviewDialog(self.main, entries, self._char_name())
            rv.exec()
            pending = list(rv.redo) + pending
            kept -= len(rv.redo)
            self._refresh_rows()
        picked = 0
        if pending:
            dlg = SubjectPickerDialog(self.main, pending, self._char_name())
            dlg.exec()
            picked = dlg.saved
            self._refresh_rows()
        total = kept + picked
        who = self._char_name() or "직접 고른 사람"
        msg = f"🎯 대상 {total}개 정했어요 ({who})"
        left = len(pending) - picked
        if left > 0:
            msg += f" · 못 정한 클립 {left}개 (이 클립은 가장 오래 나온 사람을 써요)"
        if failed:
            msg += "\n실패: " + "; ".join(f"{n} ({w[:60]})" for n, w in failed[:3])
        self.lbl_prog.setText(msg.splitlines()[0])
        if failed:
            QMessageBox.warning(self, "대상 찾기", msg)
        elif total:
            QMessageBox.information(self, "대상 찾기", msg + "\n\n목록에 🎯 표시가 붙은 클립은 그 사람의 모션만 뽑아요.")

    def clear_targets(self):
        rows = [m for m in self._target_metas() if m.get("subject")]
        if not rows:
            return
        for m in rows:
            characters.clear_subject(m)
        self._refresh_rows()
        self.lbl_prog.setText(f"대상 {len(rows)}개를 해제했어요.")

    def _running(self):
        return self.job is not None and self.job.isRunning()

    def _sync_buttons(self):
        busy = self._running()
        ready = bool(self.st) and self.st.get("level") == "ok"
        self.btn_extract.setEnabled(ready and not busy and bool(self.metas))
        self.btn_blender.setEnabled(not busy)
        self.btn_install.setEnabled(not busy)
        self.btn_find.setEnabled(not busy and bool(self.metas))
        self.btn_unsub.setEnabled(not busy)
        self.btn_chars.setEnabled(not busy)
        self.btn_ext_out.setEnabled(not busy and bool(self.metas))
        self.btn_ext_in.setEnabled(not busy and bool(self.metas))
        self.btn_tool_send.setEnabled(not busy and bool(self.metas))
        self.btn_tool_get.setEnabled(not busy and bool(self.metas))
        self.btn_cancel.setVisible(busy)
        self.bar.setVisible(busy)

    def _target_metas(self):
        rows = sorted({i.row() for i in self.list.selectedIndexes()})
        return [self.metas[r] for r in rows] if rows else list(self.metas)

    def _current_meta(self):
        r = self.list.currentRow()
        return self.metas[r] if 0 <= r < len(self.metas) else None

    def _refresh_rows(self):
        for i, m in enumerate(self.metas):
            fresh = library.load_clip(m["_json"])
            if fresh:
                self.metas[i] = fresh
            it = self.list.item(i)
            if it is not None:
                it.setText(row_text(self.metas[i]))

    def _start(self, title, fn, args, kwargs, done):
        self.job = Job(title, fn, args, kwargs)
        self.job.log.connect(self.main.log)
        self.job.progress.connect(self._on_progress)
        self.job.succeeded.connect(done)
        self.job.failed.connect(self._failed)
        self.job.finished.connect(self._sync_buttons)
        self.bar.setRange(0, 0)
        self.lbl_prog.setText(f"{title} 준비 중...")
        self.job.start()
        self._sync_buttons()

    def _on_progress(self, frac, text):
        if frac < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
        if text:
            self.lbl_prog.setText(text)

    def _failed(self, msg, cancelled):
        self.lbl_prog.setText(("⏹ " if cancelled else "⚠ ") + msg.splitlines()[0])
        if not cancelled:
            QMessageBox.warning(self, "모션 AI", msg + "\n\n자세한 내용은 메인 창의 [로그 보기]에서 볼 수 있어요.")
        self._refresh_rows()

    # ---------------------------------------------------------------- ① 상태/설치
    def refresh_status(self, quiet=False):
        if self.sjob is not None and self.sjob.isRunning():
            return
        if not quiet:
            self.lbl_status.setText("상태를 확인하는 중...")
        self.sjob = Job("모션 AI 상태", _status_job)
        self.sjob.succeeded.connect(self._status_done)
        self.sjob.failed.connect(lambda msg, c: self.lbl_status.setText(f"⚠ 상태를 확인하지 못했어요: {msg}"))
        self.sjob.start()

    def _status_done(self, st):
        self.st = st
        self.lbl_status.setText(status_html(st))
        parts = []
        if st.get("gpu"):
            parts.append(f"그래픽카드: {st['gpu']}")
        if st.get("gpu_warn"):
            parts.append("⚠ " + st["gpu_warn"])
        self.lbl_gpu.setText("\n".join(parts))
        self.btn_install.setText("✅ 설치됨 (다시 확인/복구)" if st.get("level") == "ok" else "⚙ 설치 시작 (원클릭)")
        if st.get("level") == "ok":
            self.poll.stop()
        self._sync_buttons()

    def install(self):
        if self.st and self.st.get("level") == "ok":
            if QMessageBox.question(self, "모션 AI", "이미 설치돼 있어요. 그래도 설치 창을 다시 열어 점검/복구할까요?") != QMessageBox.StandardButton.Yes:
                return
        try:
            mocap.launch_installer()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "모션 AI 설치", str(e))
            return
        self.installer_started = True
        self.poll.start()
        self.lbl_status.setText("설치 창이 열렸어요. 창의 안내를 따라 진행하세요. 끝나면 이 화면이 자동으로 확인해요.")

    def _poll(self):
        if self.installer_started and not self._running():
            self.refresh_status(quiet=True)

    # ---------------------------------------------------------------- ② 모션 뽑기
    def extract(self):
        if not (self.st and self.st.get("level") == "ok"):
            QMessageBox.information(self, "모션 AI", "먼저 ①에서 모션 AI 설치를 끝내 주세요.")
            return
        targets = self._target_metas()
        if not targets:
            return
        again = [clip_name(m) for m in targets if motion_ok(m)]
        if again and QMessageBox.question(
                self, "모션 다시 뽑기", f"이미 모션이 있는 클립 {len(again)}개를 다시 뽑아요 (기존 결과는 덮어써요). 계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        cname = self._char_name()
        if cname:
            lack = [clip_name(m) for m in targets if not (m.get("subject") or {}).get("boxes")]
            if lack:
                ans = QMessageBox.question(
                    self, "대상이 아직 없어요",
                    f"'{cname}' 캐릭터를 골랐지만 대상을 정하지 않은 클립이 {len(lack)}개 있어요.\n"
                    "먼저 [🎯 대상 찾기]를 하면 그 캐릭터의 모션만 뽑아요.\n\n"
                    "대상 없이(가장 오래 나온 사람으로) 그냥 뽑을까요?")
                if ans != QMessageBox.StandardButton.Yes:
                    return
        self._save_prefs()
        self._start("모션 뽑기", mocap.run_batch, (targets, copy.deepcopy(self.main.s)),
                    {"in_place": self.ck_inplace.isChecked(), "markers": self.ck_markers.isChecked(),
                     "use_subject": True, "crop_subject": self.ck_crop.isChecked()}, self._extracted)

    def _extracted(self, res):
        self._refresh_rows()
        ok, failed = res["ok"], res["failed"]
        self.changed = self.changed or bool(ok)
        msg = f"모션 {len(ok)}개를 만들었어요."
        if ok:
            msg += "\n아래 ③에서 Blender나 3ds Max로 보내세요."
            notes = sorted({n for r in ok for n in r.get("notes", []) if "SMPL" in n})
            if notes:
                msg += "\n\n참고: " + notes[0]
        if failed:
            msg += "\n\n실패한 클립:\n" + "\n".join(f"· {n}: {why[:120]}" for n, why in failed[:5])
        self.lbl_prog.setText("✅ " + msg.splitlines()[0])
        QMessageBox.information(self, "모션 AI", msg)

    # ---------------------------------------------------------------- 외부 AI 서비스
    def export_external(self):
        if self._running():
            return
        targets = self._target_metas()
        if not targets:
            return
        self._start("서비스용 영상 만들기", _export_batch, (targets, copy.deepcopy(self.main.s)),
                    {"follow": self.ck_ext_follow.isChecked()}, self._exported)

    def _exported(self, res):
        ok, failed = res["ok"], res["failed"]
        msg = f"서비스에 올릴 영상 {len(ok)}개를 만들었어요."
        if ok:
            kinds = {r["kind"] for r in ok}
            if kinds == {"original"}:
                msg += "\n(대상이 정해지지 않아 원본 그대로예요. 작은 캐릭터면 [🎯 대상 찾기] 뒤에 다시 만들면 더 잘 돼요)"
            msg += "\n폴더를 열었어요. 서비스 사이트에 올린 뒤, 결과를 BVH/FBX 로 받아 [📥 받은 모션 가져오기]를 누르세요."
            open_path(ok[0]["folder"])
        if failed:
            msg += "\n\n실패:\n" + "\n".join(f"· {n}: {why[:120]}" for n, why in failed[:5])
        self.lbl_prog.setText("✅ " + msg.splitlines()[0])
        QMessageBox.information(self, "외부 AI 서비스", msg)

    def import_external(self):
        if self._running():
            return
        rows = sorted({i.row() for i in self.list.selectedIndexes()})
        meta = self.metas[rows[0]] if len(rows) == 1 else (self._current_meta() if not rows else None)
        if meta is None:
            QMessageBox.information(self, "외부 AI 서비스", "모션을 붙일 클립을 목록에서 한 개만 골라 주세요.")
            return
        p, _ = QFileDialog.getOpenFileName(self, f"'{clip_name(meta)}' 에 붙일 모션 파일", self.main.s.get("last_dir", ""),
                                           "모션 파일 (*.bvh *.fbx);;BVH (*.bvh);;FBX (*.fbx);;모든 파일 (*)")
        if not p:
            return
        if motion_ok(meta) and QMessageBox.question(
                self, "모션 바꾸기", "이 클립에는 이미 모션이 있어요. 가져온 모션으로 바꿀까요? (이전 파일은 폴더에 남아요)") != QMessageBox.StandardButton.Yes:
            return
        self._save_prefs()
        self._start("외부 모션 가져오기", external.import_motion, (meta, copy.deepcopy(self.main.s), p),
                    {"in_place": self.ck_inplace.isChecked(), "markers": self.ck_markers.isChecked()}, self._imported)

    def _imported(self, rec):
        self._refresh_rows()
        self.changed = True
        msg = f"외부 모션을 가져왔어요 ({rec['frames']}프레임, {rec['fps']:.0f}fps)."
        msg += "\n아래 ③에서 Blender나 3ds Max로 보내세요."
        extra = [n for n in rec.get("notes", []) if "달라요" in n or "없는 관절" in n or "쇄골" in n]
        if extra:
            msg += "\n\n참고:\n" + "\n".join("· " + n for n in extra[:3])
        self.lbl_prog.setText("✅ " + msg.splitlines()[0])
        QMessageBox.information(self, "외부 AI 서비스", msg)

    # ---------------------------------------------------------------- 추출 툴 연동
    def _tool_hint_changed(self):
        """실행 명령/확인 이름을 고치면 바로 저장하고 상태를 다시 확인한다."""
        self.main.s["tool_cmd"] = self.ed_tool.text().strip()
        self.main.s["tool_process"] = self.ed_tool_proc.text().strip()
        self.main.save_settings()
        if hasattr(self.main, "_poll_tool"):
            self.main._poll_tool()

    def _connect_link(self):
        """추출 툴이 결과를 보내면 목록을 바로 갱신한다 (창이 닫히면 연결을 푼다)."""
        m = self.main
        if not hasattr(m, "link_result"):
            return
        m.link_result.connect(self._on_link_result)
        m.link_state.connect(self._on_link_state)
        self.finished.connect(self._disconnect_link)
        self._on_link_state(getattr(m, "tool_online", "unknown"))

    def _disconnect_link(self, *_):
        m = self.main
        for sig, fn in ((m.link_result, self._on_link_result), (m.link_state, self._on_link_state)):
            try:
                sig.disconnect(fn)
            except (RuntimeError, TypeError):
                pass

    def _on_link_state(self, state):
        if getattr(self.main, "link_server", None) is None and state != "live":
            extra = "  (실시간 연결 기능이 꺼져 있어 파일 방식으로 주고받아요)"
        else:
            extra = ""
        txt = {"live": "추출 툴: 🟢 연결됨 — 바로 보낼 수 있어요",
               "running": "추출 툴: 🟡 켜져 있지만 AnimRef 와 연결하는 창구가 없어요 — 보내기는 폴더 방식, 결과는 [📥 결과 가져오기]",
               "off": "추출 툴: ⚪ 꺼져 있음 (보내면 아래 실행 명령으로 켜요)",
               }.get(state, "추출 툴: ❔ 켜졌는지 확인 안 됨 — 아래 '켜졌는지 확인할 이름'을 적어 주세요")
        self.lbl_tool_state.setText(txt + extra)

    def _on_link_result(self, clip_name, text):
        self._refresh_rows()
        self.changed = True
        self.lbl_prog.setText(f"🔗 추출 툴에서 결과가 왔어요: {clip_name} → {text}")

    def _pick_tool(self):
        p, _ = QFileDialog.getOpenFileName(self, "추출 툴 실행 파일", "", "실행 파일 (*.exe *.bat *.cmd *.py);;모든 파일 (*)")
        if p:
            self.ed_tool.setText(f'"{p}" --handoff {{query}}')

    def send_tool(self):
        if self._running():
            return
        targets = self._target_metas()
        if len(targets) != 1:
            QMessageBox.information(self, "추출 툴", "보낼 클립을 목록에서 한 개만 골라 주세요. (추출 툴에서 클립 하나씩 후보를 고르는 방식이에요)")
            return
        meta = targets[0]
        self._save_prefs()
        try:
            qp = handoff.build_query(meta, self.main.s, copy_video=self.ck_tool_copy.isChecked(), log=self.main.log)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "추출 툴", str(e))
            return
        self._refresh_rows()
        self._tool_qp, self._tool_meta = qp, meta
        self._start("추출 툴로 보내기", _send_tool_job, (self.main.s, qp), {}, self._tool_sent)

    def _tool_sent(self, mode):
        qp, meta = self._tool_qp, self._tool_meta
        if mode == "manual":
            open_path(Path(qp).parent)
            msg = (f"연동 파일을 만들었어요:\n{qp}\n\n추출 툴이 켜져 있지 않고 실행 명령도 비어 있어서 폴더를 열었어요. "
                   "툴에서 query.json 을 열어 후보를 고르면 되고, 툴이 실시간 연결을 지원하면 결과가 자동으로 와요. 안 오면 [📥 결과 가져오기]를 누르세요.")
        else:
            how = {"live": "켜져 있는 추출 툴로 바로 보냈어요.", "launched": "추출 툴을 실행하면서 넘겼어요.",
                   "launched+live": "추출 툴을 켜고 연결해서 보냈어요."}[mode]
            msg = how + "\n툴에서 후보를 고르고 '확정'하면 결과가 이 창에 자동으로 나타나요."
        if self.ck_tool_open.isChecked():
            try:
                open_path(library.file_of(meta, "video"))
            except Exception:  # noqa: BLE001
                pass
        self.lbl_prog.setText("📨 " + msg.splitlines()[0])
        QMessageBox.information(self, "추출 툴", msg)

    def get_tool_result(self):
        targets = self._target_metas()
        rows = [m for m in targets if m.get("handoff")] or targets
        got, errs, waiting = [], [], 0
        for m in rows:
            try:
                ga = handoff.read_result(m, self.main.s)
            except ValueError as e:
                errs.append(f"{clip_name(m)}: {e}")
                continue
            if ga is None:
                waiting += 1
            else:
                got.append((m, ga))
        self._refresh_rows()
        lines = []
        for m, ga in got:
            lines.append(f"· {clip_name(m)} → {handoff.describe(ga)}")
        if got:
            self.changed = True
        if waiting and not got:
            lines.append("아직 추출 툴의 결과(result.json)가 없어요. 툴에서 후보를 고르고 '확정'했는지 확인해 주세요.")
        lines += errs
        self.lbl_prog.setText(("🔗 " if got else "") + (lines[0] if lines else "결과가 없어요"))
        QMessageBox.information(self, "추출 툴", "\n".join(lines) or "결과가 없어요.")

    def cancel(self):
        if self._running():
            self.job.cancel.set()
            self.btn_cancel.setEnabled(False)
            self.lbl_prog.setText("취소하는 중... (현재 단계가 끝나면 멈춰요)")

    # ---------------------------------------------------------------- ③ 보내기
    def _meta_with_motion(self):
        m = self._current_meta()
        if m is None or not motion_ok(m):
            QMessageBox.information(self, "모션 AI", "목록에서 모션이 있는(✅) 클립을 골라 주세요.\n없다면 먼저 ②에서 [모션 뽑기]를 해 주세요.")
            return None
        return m

    def send_blender(self):
        m = self._meta_with_motion()
        if m is None:
            return
        exe, rig = self.ed_blender.text().strip(), self.ed_rig.text().strip()
        if not exe or not Path(exe).exists():
            QMessageBox.information(self, "Blender", "Blender 실행 파일(blender.exe)을 찾지 못했어요. [찾아보기]로 골라 주세요.\n"
                                    "보통 C:\\Program Files\\Blender Foundation\\Blender 4.x\\blender.exe 에 있어요.")
            return
        if not rig or not Path(rig).exists() or not rig.lower().endswith(".blend"):
            QMessageBox.information(self, "Blender", "모션을 넣을 리그가 들어 있는 .blend 파일을 골라 주세요.")
            return
        self._save_prefs()
        video = str(library.file_of(m, "video")) if self.ck_video.isChecked() else None
        kw = {"reduce_deg": float(self.cb_reduce.currentData() or 0.0), "markers": self.ck_markers.isChecked(), "video": video}
        self._start("Blender에 적용", mocap.apply_with_blender, (exe, rig, m["motion"]), kw, self._blender_done)

    def _blender_done(self, save):
        self.lbl_prog.setText(f"✅ 적용 완료: {Path(save).name}")
        QMessageBox.information(self, "Blender", f"모션을 넣어서 저장했어요:\n{save}\n\nBlender 창이 열립니다. "
                                "원래 리그 파일은 그대로예요. 뼈가 엉뚱하게 움직이면 메인 창 [로그 보기]의 '뼈 대응'을 확인하세요.")

    def open_max(self):
        m = self._meta_with_motion()
        if m is None:
            return
        try:
            folder = mocap.prepare_max_folder(self.main.s, m["motion"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "3ds Max", str(e))
            return
        open_path(folder)
        open_path(folder.parent)
        QMessageBox.information(self, "3ds Max", "폴더를 열었어요.\n\n1) 리그를 기본 자세·0프레임에 두고 뼈 하나 선택\n"
                                "2) Max에서 Scripting > Run Script... 로 max_apply.ms 실행 (모션 폴더 안에 있어요)\n"
                                "3) [모션 파일 열기] → .motion.txt 선택 → [적용]\n\n자세한 설명은 'Max_사용법.txt'에 있어요.")

    def open_bvh(self):
        m = self._meta_with_motion()
        if m is not None:
            open_path(m["motion"]["dir"])

    def done(self, r):
        self.poll.stop()
        for j in (self.job, self.sjob):
            if j is not None and j.isRunning():
                j.cancel.set()
                j.wait(15000)
        super().done(r)
