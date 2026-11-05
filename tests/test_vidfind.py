"""이미지 인식 연결 / 프레임 추출 / AnimRef 클립 읽기 시험 (Qt 불필요, 네트워크는 가짜)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import animref, frames, vision

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


# ---------------------------------------------------------------- 질문 만들기
p = vision.build_prompt(["Dwarf", "Hexapod"], "Mortal Shell 2")
check("[Dwarf, Hexapod]" in p and "Mortal Shell 2" in p and "ONLY JSON" in p, "프롬프트에 이름 목록/게임/JSON 지시")

# ---------------------------------------------------------------- 답 해석 (모델 답은 지저분할 수 있다)
d = vision.parse_response('{"characters":[{"name":"Dwarf","confidence":0.9,"description":"armored","box":[0.1,0.2,0.5,0.9]},{"name":"unknown","confidence":0.3,"description":"red blob","box":null}]}', "f.jpg")
check(len(d) == 2 and d[0].name == "Dwarf" and d[0].box == (0.1, 0.2, 0.5, 0.9) and d[0].frame == "f.jpg", "정상 JSON")
d = vision.parse_response('```json\n{"characters":[{"name":"Hexapod","confidence":85}]}\n```')
check(d and d[0].confidence == 0.85, "코드블록 + 백분율 확신도: %s" % d)
d = vision.parse_response('Here you go: {"characters":[{"name":"Dwarf"}]} hope it helps')
check(d and d[0].name == "Dwarf", "앞뒤 잡담 허용")
d = vision.parse_response('[{"name":"A"},"B",{"description":"x"},5]')
check([x.name for x in d] == ["A", "B", "unknown"], "목록/문자열 항목: %s" % [x.name for x in d])
check(vision.parse_response('{"characters":[]}') == [], "빈 결과")
d = vision.parse_response('{"characters":[{"name":"X","box":[100,200,500,900]}]}')
check(d[0].box == (0.1, 0.2, 0.5, 0.9), "0~1000 눈금 상자 변환: %s" % (d[0].box,))
d = vision.parse_response('{"characters":[{"name":"X","box":[0.5,0.5,0.5,0.5]}]}')
check(d[0].box is None, "너무 작은 상자는 버림")
try:
    vision.parse_response("sorry I cannot")
    check(False, "해석 불가는 오류")
except vision.VisionError:
    pass

# ---------------------------------------------------------------- 공급자 (가짜 네트워크)
tmp = tempfile.mkdtemp()
img = os.path.join(tmp, "a.jpg")
with open(img, "wb") as f:
    f.write(b"\xff\xd8\xff fake jpeg")

calls = []


def fake_post(url, payload, headers, timeout):
    calls.append((url, payload, headers))
    if url.endswith("/api/tags"):
        return {"models": [{"name": "qwen2.5vl:7b"}]}
    if url.endswith("/api/chat"):
        return {"message": {"content": '{"characters":[{"name":"Dwarf","confidence":0.8}]}'}}
    if "anthropic" in url:
        return {"content": [{"type": "text", "text": '{"characters":[{"name":"Hexapod","confidence":0.7}]}'}]}
    raise vision.VisionError("HTTP 404")


o = vision.OllamaProvider("qwen2.5vl:7b", post=fake_post)
ok, msg, names = o.check()
check(ok and names == ["qwen2.5vl:7b"], "Ollama 확인: %s" % msg)
r = o.analyze(img, ["Dwarf"])
pl = calls[-1][1]
check(r and r[0].name == "Dwarf" and pl["format"] == "json" and pl["stream"] is False and pl["messages"][0]["images"], "Ollama 요청/응답")
ok, msg, _ = vision.OllamaProvider("other-model", post=fake_post).check()
check(not ok and "ollama pull other-model" in msg, "없는 모델 안내: %s" % msg)


def down(url, payload, headers, timeout):
    raise vision.VisionError("연결할 수 없습니다: refused")


ok, msg, _ = vision.OllamaProvider(post=down).check()
check(not ok and "실행 중" in msg, "Ollama 꺼짐 안내")

c = vision.ClaudeProvider("sk-test-123", "claude-sonnet-5-5", post=fake_post)
check(c.check()[0], "Claude 키 있음")
check(not vision.ClaudeProvider("").check()[0], "Claude 키 없음")
check(not vision.ClaudeProvider("abc").check()[0], "Claude 키 형식 이상")
r = c.analyze(img, ["Hexapod"])
url, pl, hd = calls[-1]
blk = pl["messages"][0]["content"]
check(r and r[0].name == "Hexapod", "Claude 응답")
check(hd["x-api-key"] == "sk-test-123" and hd["anthropic-version"] == "2023-06-01", "Claude 헤더")
check(blk[0]["type"] == "image" and blk[0]["source"]["media_type"] == "image/jpeg" and blk[1]["type"] == "text", "Claude 이미지+글 구성")
check(pl["model"] == "claude-sonnet-5-5", "모델 이름 전달")


def err401(url, payload, headers, timeout):
    raise vision.VisionError("HTTP 401 {}")


try:
    vision.ClaudeProvider("sk-x", post=err401).analyze(img, [])
    check(False, "401 오류")
except vision.VisionError as e:
    check("키" in str(e), "401 안내: %s" % e)

# 이름 목록은 공급자별 한도까지만
many = ["N%d" % i for i in range(1000)]
calls.clear()
o.analyze(img, many)
check(calls[-1][1]["messages"][0]["content"].count("N") < 400, "Ollama 는 이름 목록을 줄여서 보냄")

# make_provider
check(isinstance(vision.make_provider("ollama", {}), vision.OllamaProvider), "make ollama")
mp = vision.make_provider("claude", {"vf_claude_key": "sk-1", "vf_claude_model": "claude-haiku-5-5"})
check(isinstance(mp, vision.ClaudeProvider) and mp.model == "claude-haiku-5-5", "make claude")
os.environ["ANTHROPIC_API_KEY"] = "sk-env"
check(vision.make_provider("claude", {}).api_key == "sk-env", "환경변수 키")
del os.environ["ANTHROPIC_API_KEY"]

# ---------------------------------------------------------------- 묶기
ds = [vision.Detection("Dwarf", 0.6, "a", None, "f1"), vision.Detection("dwarf", 0.9, "b", (0.1, 0.1, 0.5, 0.5), "f2"),
      vision.Detection("Hexapod", 0.5, "c", None, "f1"), vision.Detection("unknown", 0.4, "grey goblin with club", None, "f3"),
      vision.Detection("unknown", 0.7, "Grey goblin, club", None, "f4"), vision.Detection("", 0.1, "", None, "f5")]
g = vision.aggregate(ds)
check(len(g) == 3, "3종으로 묶임: %s" % [(x.name, x.seen) for x in g])
check(g[0].name.lower() == "dwarf" and g[0].seen == 2 and g[0].frame == "f2" and g[0].box == (0.1, 0.1, 0.5, 0.5), "최고 확신도 장면을 대표로: %s" % g[0])
unk = [x for x in g if x.name == ""]
check(len(unk) == 1 and unk[0].seen == 2 and unk[0].frame == "f4", "모름은 설명으로 묶음")

# ---------------------------------------------------------------- 프레임 추출 (진짜 ffmpeg 가 있을 때만)
ff = frames.find_ffmpeg()
if ff:
    vid = os.path.join(tmp, "t.mp4")
    r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=640x360:rate=10", "-pix_fmt", "yuv420p", vid])
    check(r.returncode == 0 and os.path.isfile(vid), "시험 영상 생성")
    check(abs((frames.video_duration(ff, vid) or 0) - 4.0) < 0.2, "길이 읽기: %s" % frames.video_duration(ff, vid))
    fr = frames.extract_frames(ff, vid, os.path.join(tmp, "o"), 4, width=320)
    check(len(fr) == 4 and all(os.path.getsize(x.path) > 500 for x in fr), "프레임 4장")
    check([round(x.t, 1) for x in fr] == [0.5, 1.5, 2.5, 3.5], "고르게 뽑힘: %s" % [x.t for x in fr])
    # 이미지 + 영상 섞어서, 전체 한도
    png = os.path.join(tmp, "s.png")
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", fr[0].path, png])
    allf = frames.sample_sources(ff, [vid, png], os.path.join(tmp, "o2"), per_source=5, cap=6)
    check(len(allf) <= 6 and any(x.source == png for x in allf), "여러 원본 한도 적용: %d" % len(allf))
    # 원본이 한도보다 많으면 고르게 솎는다
    many = []
    for i in range(10):
        q = os.path.join(tmp, "m%d.png" % i)
        shutil.copy(png, q)
        many.append(q)
    sub = frames.sample_sources(ff, many, os.path.join(tmp, "o4"), per_source=3, cap=4)
    check([os.path.basename(x.source) for x in sub] == ["m0.png", "m2.png", "m5.png", "m7.png"], "원본 솎기: %s" % [os.path.basename(x.source) for x in sub])
    # ffmpeg 없이: 이미지는 그대로, 영상은 안내
    raw = frames.sample_sources(None, [png], os.path.join(tmp, "o3"))
    check(len(raw) == 1 and raw[0].path == png, "ffmpeg 없이 이미지는 그대로")
    try:
        frames.sample_sources(None, [vid], os.path.join(tmp, "o3"))
        check(False, "ffmpeg 없이 영상은 오류")
    except frames.FrameError as e:
        check("ffmpeg" in str(e), "ffmpeg 안내")
    try:
        frames.sample_sources(ff, [os.path.join(tmp, "x.txt")], tmp)
        check(False, "지원 안 하는 파일")
    except frames.FrameError:
        pass
else:
    print("(ffmpeg 없음 - 프레임 추출 시험 건너뜀)")

# ---------------------------------------------------------------- AnimRef 클립 읽기
root = os.path.join(tmp, "AnimRef", "Sparta", "clips")
os.makedirs(os.path.join(root, "타격", "묶음 1"))
os.makedirs(os.path.join(root, "피격"))
os.makedirs(os.path.join(root, "all"))
for rel in ("타격/묶음 1/c1.mp4", "타격/c2_[2_3타].mp4", "피격/h1.mp4", "all/x.mp4", "all/y.mp4", "all/z_스킬_01.mp4", "all/note.txt"):
    pth = os.path.join(root, rel)
    os.makedirs(os.path.dirname(pth), exist_ok=True)
    open(pth, "wb").write(b"0")
with open(os.path.join(root, "all", "x.json"), "w", encoding="utf-8") as f:
    json.dump({"ai": "스킬"}, f)
with open(os.path.join(root, "all", "library.json"), "w", encoding="utf-8") as f:
    json.dump([{"name": "y.mp4", "label": "회피"}], f)
cl = {os.path.basename(c.path): c for c in animref.scan_clips(root)}
check(set(cl) == {"c1.mp4", "c2_[2_3타].mp4", "h1.mp4", "x.mp4", "y.mp4", "z_스킬_01.mp4"}, "영상 파일만: %s" % sorted(cl))
check(cl["c1.mp4"].label == "타격" and cl["c1.mp4"].group == "묶음 1", "폴더 이름으로 분류+묶음")
check(cl["h1.mp4"].label == "피격", "피격 폴더")
check(cl["x.mp4"].label == "스킬", "옆 json 우선")
check(cl["y.mp4"].label == "회피", "목록 파일")
check(cl["z_스킬_01.mp4"].label == "스킬", "파일 이름 속 분류")
one = animref.scan_clips(os.path.join(root, "피격", "h1.mp4"))
check(len(one) == 1 and one[0].label == "", "단일 파일(폴더 단서 없음)은 분류 없음")
sm = animref.summarize(list(cl.values()))
check("클립 6개" in sm and "타격 2" in sm and "스킬 2" in sm, "요약: %s" % sm)
check(cl["c2_[2_3타].mp4"].label == "타격" and (cl["c2_[2_3타].mp4"].hit_index, cl["c2_[2_3타].mp4"].hit_total) == (2, 3), "타수 표시 읽기")
check(animref.summarize([]) == "영상 클립이 없습니다.", "빈 요약")

shutil.rmtree(tmp, ignore_errors=True)
print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
