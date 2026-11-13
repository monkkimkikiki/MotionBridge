"""생김새 검증 시험 (Qt·Blender·cv2 불필요): 썸네일 캐시 · 시트 질문/답 해석 · 한 줄 검증."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import bridge
bridge.CONFIG_DIR = tempfile.mkdtemp(prefix="mb_test_vfy_")   # 실제 사용자 폴더를 건드리지 않게

from motionbridge.core.identify import verify as vf  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


d = tempfile.mkdtemp()
A = "Game/Content/Characters/Bosses/Swordman/Art/Mesh/SK_Swordman.uasset"
B = "Game/Content/Characters/Enemies/Wolf/Art/Mesh/SK_Wolf.uasset"

# ---- 썸네일 경로: 같은 메시는 항상 같은 파일, 다른 메시와 겹치지 않는다
check(vf.thumb_path("오공", A) == vf.thumb_path("오공", A.replace("/", "\\")), "경로 구분자 달라도 같은 캐시")
check(vf.thumb_path("오공", A) != vf.thumb_path("오공", B), "다른 메시는 다른 캐시")

# ---- ensure_thumbs: glb 추출 -> 렌더 호출, 이미 있으면 재사용, 실패는 None
made = []


def fake_glb(p):
    if p == B:
        return None                      # 추출 실패 흉내
    g = os.path.join(d, "m.glb")
    open(g, "w").write("glb")
    return g


def fake_render(glb, out, blender=None, size=512):
    made.append(out)
    open(out, "w").write("png")
    return True


t = vf.ensure_thumbs("오공", [A, A, B], fake_glb, render=fake_render)
check(t[A] and os.path.isfile(t[A]) and t[B] is None, "성공/실패 썸네일: %s" % t)
check(len(made) == 1, "같은 메시는 한 번만 렌더: %d" % len(made))
t2 = vf.ensure_thumbs("오공", [A], fake_glb, render=fake_render)
check(len(made) == 1 and t2[A] == t[A], "두 번째는 캐시 재사용")

# ---- 질문/답 해석
p = vf.build_prompt(3)
check("Image 1" in p and "2..4" in p and "JSON" in p, "질문에 번호 규약이 있다")
check(vf.parse_verdict('{"match": 3, "confidence": 0.8, "why": "same armor"}', 3) == (1, 0.8, "same armor"), "답: 3번 -> 후보 1 (0-기준)")
check(vf.parse_verdict('{"match": null, "confidence": 0.2}', 3)[0] is None, "답: 없음")
check(vf.parse_verdict('앞말 {"match": "2", "confidence": "0.9"} 뒷말', 3) == (0, 0.9, ""), "글자 섞인 답도 해석")
check(vf.parse_verdict('{"match": 9, "confidence": 0.9}', 3)[0] is None, "범위 밖 번호는 무시")
check(vf.parse_verdict("json 아님", 3) == (None, 0.0, ""), "깨진 답은 안전하게")

# ---- verify_one: 시트를 만들고 VLM 답대로 메시를 고른다
frame = os.path.join(d, "scene.png")
open(frame, "w").write("img")
sheets = []


def fake_sheet(paths, out, cols=4, cell_w=480, max_frames=12):
    sheets.append(list(paths))
    open(out, "w").write("sheet")
    return out


def fake_ask(image, prompt):
    return '{"match": 2, "confidence": 0.85, "why": "같은 갑옷"}'   # 첫 후보


v = vf.verify_one(fake_ask, "오공", frame, None, [A, B], t, os.path.join(d, "wk"), fake_sheet)
check(v and v["path"] == A and v["confidence"] == 0.85, "검증 결과: %s" % v)
check(sheets and sheets[0][0] == frame and len(sheets[0]) == 2, "시트 = 장면 + 썸네일 있는 후보만: %s" % sheets)
v2 = vf.verify_one(lambda i, p: '{"match": null, "confidence": 0.3}', "오공", frame, None, [A], t, os.path.join(d, "wk"), fake_sheet)
check(v2 and v2["path"] is None, "일치 없음이면 path=None")
check(vf.verify_one(fake_ask, "오공", frame, None, [B], t, os.path.join(d, "wk"), fake_sheet) is None, "썸네일이 하나도 없으면 건너뜀")
check(vf.verify_one(fake_ask, "오공", os.path.join(d, "없는그림.png"), None, [A], t, os.path.join(d, "wk"), fake_sheet) is None, "장면 파일이 없으면 건너뜀")

# ---- 크롭: box 없거나 cv2 없으면 원본 경로 그대로 (조용한 폴백)
check(vf.crop_query(frame, None, os.path.join(d, "q.png")) == frame, "box 없으면 원본")

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_identify_verify ALL OK")
