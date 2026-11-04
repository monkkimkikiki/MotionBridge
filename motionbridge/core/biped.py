"""3ds Max Biped 로 바로 넣는 내보내기.

FBX 는 Biped 에 직접 들어가지 않는다(Biped 는 자기 전용 .bip 포맷만 읽고, .bip 는 비공개 바이너리라 이 프로그램이 직접 쓸 수 없다).
그래서 두 파일을 만든다.
  <이름>_biped.ms     : 3ds Max 에서 실행하면 Biped 를 만들고, 키를 넣고, .bip 로 저장하는 MaxScript
  <이름>_biped.mbbip  : 프레임별 회전(데이터). 스크립트와 같은 폴더에 두면 된다.

회전은 '바인드 포즈 대비 월드 회전 변화(델타)'로 보낸다. 본 로컬 축 규칙이 달라도 월드 델타는 같아서
내 모델의 본 축과 Biped 의 본 축이 달라도 맞는다. 바인드 포즈가 서로 다른 것(예: Biped 는 팔이 아래로 내려가 있고 내 모델은 T 포즈)은
스크립트가 Biped 를 Figure 모드에서 내 모델 바인드 포즈의 팔다리 방향에 맞춘 뒤 키를 넣는다.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from .model import Animation, Skeleton, fk

FORMAT = "motionbridge-biped-1"
FINGERS = ["Thumb", "Index", "Middle", "Ring", "Pinky"]


def build_parts(slots: Dict[str, int]) -> Tuple[List[dict], dict]:
    """매핑된 슬롯 -> Biped 부위 목록(부모가 먼저). 각 부위: slot, arr(Biped 배열 이름), link, aim(방향 기준 자식 부위 인덱스 또는 -1)."""
    if "Hips" not in slots:
        raise ValueError("Biped 로 내보내려면 Hips(골반) 슬롯이 매핑되어 있어야 합니다.")
    parts: List[dict] = []

    def add(slot: str, arr: str, link: int) -> None:
        if slot in slots:
            parts.append({"slot": slot, "arr": arr, "link": link, "aim": -1})

    add("Hips", "pelvis", 1)
    spine = [s for s in ("Spine", "Spine1", "Spine2") if s in slots]
    for i, s in enumerate(spine):
        add(s, "spine", i + 1)
    add("Neck", "neck", 1)
    add("Head", "head", 1)
    for side, a in (("L", "larm"), ("R", "rarm")):
        for k, name in enumerate(("Shoulder", "UpperArm", "LowerArm", "Hand")):
            add("%s_%s" % (side, name), a, k + 1)
    for side, a in (("L", "lleg"), ("R", "rleg")):
        for k, name in enumerate(("UpLeg", "LowLeg", "Foot", "Toe")):
            add("%s_%s" % (side, name), a, k + 1)
    n_fingers = 0
    for side, a in (("L", "lfingers"), ("R", "rfingers")):
        for fi, f in enumerate(FINGERS):
            for j in (1, 2, 3):
                s = "%s_%s%d" % (side, f, j)
                if s in slots:
                    add(s, a, fi * 3 + j)
                    n_fingers = 5
    idx = {p["slot"]: i for i, p in enumerate(parts)}

    def aim(slot: str, *cands: str) -> None:
        if slot not in idx:
            return
        for c in cands:
            if c in idx:
                parts[idx[slot]]["aim"] = idx[c]
                return

    for i, s in enumerate(spine):
        rest = list(spine[i + 1:]) + ["Neck", "Head"]
        aim(s, *rest)
    aim("Neck", "Head")
    for side in ("L", "R"):
        aim(side + "_Shoulder", side + "_UpperArm")
        aim(side + "_UpperArm", side + "_LowerArm")
        aim(side + "_LowerArm", side + "_Hand")
        aim(side + "_UpLeg", side + "_LowLeg")
        aim(side + "_LowLeg", side + "_Foot")
        aim(side + "_Foot", side + "_Toe")
        for f in FINGERS:
            for j in (1, 2):
                if "%s_%s%d" % (side, f, j) in idx:
                    aim("%s_%s%d" % (side, f, j), "%s_%s%d" % (side, f, j + 1))
    info = {"spine_links": max(len(spine), 1), "fingers": n_fingers, "toes": int(any(p["slot"].endswith("_Toe") for p in parts))}
    return parts, info


def _axis_angle_deg(q: np.ndarray) -> np.ndarray:
    """(...,4 wxyz) -> (...,4) [ax, ay, az, 각도(도)]"""
    q = Q.normalize(q)
    q = np.where(q[..., :1] < 0, -q, q)
    w = np.clip(q[..., 0], -1.0, 1.0)
    ang = 2.0 * np.arccos(w)
    s = np.linalg.norm(q[..., 1:], axis=-1)
    ax = np.where(s[..., None] > 1e-9, q[..., 1:] / np.maximum(s[..., None], 1e-12), np.array([0.0, 0.0, 1.0]))
    return np.concatenate([ax, np.degrees(ang)[..., None]], axis=-1)


def compute(skel: Skeleton, anim: Animation, slots: Dict[str, int], frame_to_canon: np.ndarray, units_per_meter: float) -> dict:
    """스크립트에 보낼 값을 계산한다. 모든 벡터/회전은 3ds Max 프레임(Z-up, 캐릭터는 -Y 를 바라봄), 길이는 미터."""
    from .export import PRESETS, preset_frame

    parts, info = build_parts(slots)
    C = Q.mul(preset_frame(PRESETS["max"]), frame_to_canon)  # 파일 프레임 -> 3ds Max 프레임
    wr, wp = fk(skel, anim.rot, anim.pos)
    bwr, bwp = skel.bind_world()
    T = anim.n_frames
    deltas = np.empty((T, len(parts), 4))
    for i, p in enumerate(parts):
        j = slots[p["slot"]]
        d = Q.mul(wr[:, j], Q.conj(bwr[j])[None])  # 파일 프레임의 월드 델타
        dm = Q.mul(Q.mul(C, d), Q.conj(C))  # 3ds Max 프레임으로 옮긴 델타
        deltas[:, i] = _axis_angle_deg(Q.make_continuous(dm) if T > 1 else dm)
    hips = slots["Hips"]
    com = Q.rotate(C, (wp[:, hips] - bwp[hips][None])) / units_per_meter  # (T,3) 바인드 대비 이동
    dirs = np.zeros((len(parts), 3))
    for i, p in enumerate(parts):
        if p["aim"] >= 0:
            v = Q.rotate(C, bwp[slots[parts[p["aim"]]["slot"]]] - bwp[slots[p["slot"]]])
            n = float(np.linalg.norm(v))
            dirs[i] = v / n if n > 1e-9 else 0.0
    allp = Q.rotate(C, bwp) / units_per_meter
    height = float(np.ptp(allp[:, 2]))
    hip_h = float(Q.rotate(C, bwp[hips])[2] / units_per_meter - allp[:, 2].min())
    return {"parts": parts, "info": info, "deltas": deltas, "com": com, "dirs": dirs, "height_m": height, "hip_height_m": hip_h,
            "fps": float(anim.fps), "frames": T, "name": anim.name}


def write_data(path: str, d: dict) -> None:
    parts = d["parts"]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("%s\n" % FORMAT)
        f.write("name %s\n" % d["name"].replace(" ", "_"))
        f.write("fps %.6f\n" % d["fps"])
        f.write("frames %d\n" % d["frames"])
        f.write("height_m %.6f\n" % d["height_m"])
        f.write("hip_height_m %.6f\n" % d["hip_height_m"])
        f.write("spine_links %d\n" % d["info"]["spine_links"])
        f.write("fingers %d\n" % d["info"]["fingers"])
        f.write("toes %d\n" % d["info"]["toes"])
        f.write("parts %d\n" % len(parts))
        for i, p in enumerate(parts):
            dr = d["dirs"][i]
            f.write("P %s %s %d %d %.6f %.6f %.6f\n" % (p["slot"], p["arr"], p["link"], p["aim"], dr[0], dr[1], dr[2]))
        f.write("DATA\n")
        for t in range(d["frames"]):
            vals = list(d["com"][t]) + [x for i in range(len(parts)) for x in d["deltas"][t, i]]
            f.write(" ".join("%.5f" % v for v in vals) + "\n")


# ---------------------------------------------------------------------------------------------------- MaxScript
SCRIPT = r'''-- MotionBridge -> 3ds Max Biped  (자동 생성 파일)
-- 사용법: 3ds Max 에서 [Scripting > Run Script...] 로 이 파일을 실행하세요.
--   * 같은 폴더의 "__DATA__" 를 읽어서 Biped 를 만들고, 모션 키를 넣고, .bip 로 저장합니다.
--   * 장면에 있던 Biped 는 건드리지 않고 새로 만듭니다. 저장 위치는 실행할 때 물어봅니다.
--   * 오류가 나면 [MAXScript Listener] 창(F11)의 내용을 알려 주세요.
fn mbRun =
(
	local dataFile = (getFilenamePath (getThisScriptFilename())) + "__DATA__"
	if not (doesFileExist dataFile) do dataFile = getOpenFileName caption:"__DATA__ 파일을 고르세요" types:"MotionBridge Biped data (*.mbbip)|*.mbbip"
	if dataFile == undefined or not (doesFileExist dataFile) do ( messageBox "데이터 파일(__DATA__)을 찾지 못했습니다. 이 스크립트와 같은 폴더에 두세요." ; return undefined )

	-- ---------------------------------------------------------------- 데이터 읽기
	local f = openFile dataFile mode:"r"
	local hdr = #()
	local pSlot = #(), pArr = #(), pLink = #(), pAim = #(), pDir = #()
	local lines = #()
	local fps = 30.0, nFrames = 0, heightM = 1.7, hipM = 0.9, spineN = 3, fingN = 0, toesN = 0, clipName = "clip"
	local line = readLine f   -- 형식 이름
	local inData = false
	while not (eof f) do
	(
		line = readLine f
		if inData then ( append lines line )
		else
		(
			local tk = filterString line " "
			if tk.count > 0 do
			(
				case tk[1] of
				(
					"name": clipName = tk[2]
					"fps": fps = tk[2] as float
					"frames": nFrames = tk[2] as integer
					"height_m": heightM = tk[2] as float
					"hip_height_m": hipM = tk[2] as float
					"spine_links": spineN = tk[2] as integer
					"fingers": fingN = tk[2] as integer
					"toes": toesN = tk[2] as integer
					"P": ( append pSlot tk[2] ; append pArr (execute ("#" + tk[3])) ; append pLink (tk[4] as integer) ; append pAim ((tk[5] as integer) + 1) ; append pDir [tk[6] as float, tk[7] as float, tk[8] as float] )
					"DATA": inData = true
				)
			)
		)
	)
	close f
	local nParts = pSlot.count
	if lines.count < 1 or nParts < 1 do ( messageBox "데이터 파일이 비어 있습니다." ; return undefined )

	local outBip = getSaveFileName caption:"저장할 .bip 파일" filename:(clipName + ".bip") types:"Biped (*.bip)|*.bip"
	if outBip == undefined do return undefined

	local U = units.decodeValue "1.0m"      -- 3ds Max 시스템 단위로 1 미터
	local log = #()
	fn tryGetNode b arr link = ( local n = undefined ; try ( n = biped.getNode b arr link:link ) catch () ; if n == undefined do ( try ( n = biped.getNode b.transform.controller arr link:link ) catch () ) ; n )

	-- ---------------------------------------------------------------- Biped 만들기
	fn makeBiped h ang sN fN tN =
	(
		local b = undefined
		try ( b = biped.createNew h ang [0,0,0] arms:true neckLinks:1 spineLinks:sN legLinks:3 fingers:fN fingerLinks:3 toes:tN toeLinks:1 ankleAttach:0.2 ) catch ()
		if b == undefined do ( try ( b = biped.createNew h ang [0,0,0] arms:true neckLinks:1 spineLinks:sN legLinks:3 fingers:fN fingerLinks:3 ) catch () )
		if b == undefined do ( try ( b = biped.createNew h ang [0,0,0] ) catch () )
		b
	)
	local bip = makeBiped (heightM * U) 0 spineN fingN toesN
	if bip == undefined do ( messageBox "Biped 를 만들지 못했습니다." ; return undefined )
	local ctrl = bip.transform.controller
	ctrl.figureMode = true

	-- 정면 방향 맞추기: 이 프로그램의 데이터는 캐릭터가 -Y 를 바라보는 것을 기준으로 한다.
	local fi = findItem pSlot "L_Foot"
	local ti = findItem pSlot "L_Toe"
	if fi > 0 and ti > 0 do
	(
		local nf = tryGetNode bip pArr[fi] pLink[fi]
		local nt = tryGetNode bip pArr[ti] pLink[ti]
		if nf != undefined and nt != undefined do
		(
			local v = nt.transform.pos - nf.transform.pos
			local a = atan2 v.y v.x
			local need = -90.0 - a
			if abs (mod (need + 540.0) 360.0 - 180.0) > 5.0 do
			(
				local old = for o in objects where (matchPattern o.name pattern:(bip.name + "*")) collect o
				delete old
				bip = makeBiped (heightM * U) need spineN fingN toesN
				ctrl = bip.transform.controller
				ctrl.figureMode = true
			)
		)
	)

	local nodes = #()
	for i = 1 to nParts do
	(
		local n = tryGetNode bip pArr[i] pLink[i]
		append nodes n
		if n == undefined do append log ("Biped 에서 찾지 못한 부위: " + pSlot[i])
	)

	-- ---------------------------------------------------------------- Figure 모드에서 내 모델의 바인드 포즈 방향에 맞추기
	for i = 1 to nParts do
	(
		local c = pAim[i]
		if c > 0 and nodes[i] != undefined and nodes[c] != undefined and length pDir[i] > 0.5 do
		(
			local db = nodes[c].transform.pos - nodes[i].transform.pos
			if length db > 1e-6 do
			(
				db = normalize db
				local axs = cross db pDir[i]
				local s = length axs
				local cs = dot db pDir[i]
				if s > 1e-5 do
				(
					local rm = (angleaxis (atan2 s cs) (normalize axs)) as matrix3
					local cur = (nodes[i].transform.rotationpart) as matrix3
					try ( biped.setTransform nodes[i] #rotation ((cur * rm) as quat) false ) catch ( append log ("자세 맞추기 실패: " + pSlot[i]) )
				)
			)
		)
	)
	local restM = for i = 1 to nParts collect (if nodes[i] != undefined then ((nodes[i].transform.rotationpart) as matrix3) else (matrix3 1))
	local comRest = bip.transform.pos
	local pelvisIdx = findItem pSlot "Hips"
	local r = U
	if pelvisIdx > 0 and nodes[pelvisIdx] != undefined and hipM > 0.05 do
	(
		local ph = nodes[pelvisIdx].transform.pos.z
		if ph > 0 do r = ph / hipM       -- 골반 높이 비율(Biped 비율 -> 내 모델 비율)
	)

	-- ---------------------------------------------------------------- 키 넣기
	ctrl.figureMode = false
	frameRate = amax 1 ((floor (fps + 0.5)) as integer)
	animationRange = interval 0 (nFrames - 1)
	local failed = #()
	local t0 = timeStamp()
	with animate on
	(
		for t = 1 to nFrames do
		(
			sliderTime = (t - 1)
			local tk = filterString lines[t] " "
			if tk.count >= 3 + 4 * nParts do
			(
				try ( biped.setTransform bip #pos (comRest + [tk[1] as float, tk[2] as float, tk[3] as float] * r) true ) catch ( appendIfUnique failed "COM" )
				for i = 1 to nParts do
				(
					if nodes[i] != undefined do
					(
						local o = 3 + (i - 1) * 4
						local ang = tk[o + 4] as float
						local dm = if abs ang < 1e-4 then (matrix3 1) else ((angleaxis ang [tk[o + 1] as float, tk[o + 2] as float, tk[o + 3] as float]) as matrix3)
						try ( biped.setTransform nodes[i] #rotation ((restM[i] * dm) as quat) true ) catch ( appendIfUnique failed pSlot[i] )
					)
				)
			)
		)
	)
	sliderTime = 0

	-- ---------------------------------------------------------------- 저장
	local saved = false
	try ( biped.saveBipFile ctrl outBip ; saved = true ) catch ( append log ("저장 실패: " + (getCurrentException())) )
	local msg = "MotionBridge: Biped 모션 " + nFrames as string + "프레임 적용 (" + ((timeStamp() - t0) / 1000.0) as string + "초)\n"
	if saved do msg += "저장: " + outBip + "\n"
	if failed.count > 0 do msg += "키를 넣지 못한 부위: " + (failed as string) + "\n"
	for l in log do msg += l + "\n"
	format "%\n" msg
	messageBox msg title:"MotionBridge"
)
mbRun()
'''


def write_script(path: str, data_name: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(SCRIPT.replace("__DATA__", data_name))


def export_biped(out_dir: str, base: str, skel: Skeleton, anim: Animation, slots: Dict[str, int], frame_to_canon: np.ndarray,
                 units_per_meter: float) -> Tuple[List[str], List[str]]:
    """(만든 파일 목록, 경고 목록)"""
    d = compute(skel, anim, slots, frame_to_canon, units_per_meter)
    ms = os.path.join(out_dir, base + "_biped.ms")
    data = os.path.join(out_dir, base + "_biped.mbbip")
    write_data(data, d)
    write_script(ms, os.path.basename(data))
    warns = []
    missing = [s for s in ("Hips", "Spine", "Head", "L_UpperArm", "R_UpperArm", "L_UpLeg", "R_UpLeg") if s not in slots]
    if missing:
        warns.append("Biped: 매핑되지 않은 주요 부위가 있어 그 부분은 Biped 기본 자세로 남습니다: %s" % ", ".join(missing))
    return [ms, data], warns
