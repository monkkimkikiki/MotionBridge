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
    mj = [slots[p["slot"]] for p in parts]                    # 매핑된 관절만: 무기·소품·IK 본이 키를 부풀리지 않게
    z = allp[mj, 2]
    ground = float(min([allp[slots[s], 2] for s in ("L_Toe", "R_Toe", "L_Foot", "R_Foot") if s in slots] or [z.min()]))
    top = float(allp[slots["Head"], 2]) if "Head" in slots else float(z.max())
    height = max((top - ground) * 1.12, 1e-3)                 # 머리 관절은 정수리보다 낮다
    hip_h = float(allp[hips, 2] - ground)
    lr = np.zeros(3)                                          # 왼쪽 -> 오른쪽이 아니라 '오른쪽 허벅지 -> 왼쪽 허벅지' 방향 (3ds Max 프레임)
    if "L_UpLeg" in slots and "R_UpLeg" in slots:
        lr = allp[slots["L_UpLeg"]] - allp[slots["R_UpLeg"]]
        lr[2] = 0.0
        n = float(np.linalg.norm(lr))
        lr = lr / n if n > 1e-9 else np.zeros(3)
    return {"parts": parts, "info": info, "deltas": deltas, "com": com, "dirs": dirs, "height_m": height, "hip_height_m": hip_h,
            "fps": float(anim.fps), "frames": T, "name": anim.name, "lr": lr}


def write_data(path: str, d: dict) -> None:
    parts = d["parts"]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("%s\n" % FORMAT)
        f.write("name %s\n" % d["name"].replace(" ", "_"))
        f.write("fps %.6f\n" % d["fps"])
        f.write("frames %d\n" % d["frames"])
        f.write("height_m %.6f\n" % d["height_m"])
        f.write("hip_height_m %.6f\n" % d["hip_height_m"])
        lr = d.get("lr", (0.0, 0.0, 0.0))
        f.write("lr %.6f %.6f %.6f\n" % (lr[0], lr[1], lr[2]))
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
AUTO_HEADER = r'''-- 자동 실행(MotionBridge 가 3ds Max 를 직접 띄울 때): 메시지 창 없이 로그 파일에 기록하고, 경로를 스크립트에 박아 둔다
global mbAuto = __AUTO__
global mbLogFile = "__LOG__"
global mbDataAbs = "__DATAABS__"
global mbBaseAbs = "__BASEABS__"
global mbQuitAfter = __QUIT__
fn mbSay msg =
(
	local lf = undefined
	format "%\n" msg
	if mbLogFile != "" do
	(
		try ( lf = openFile mbLogFile mode:"a" ) catch ()
		if lf == undefined do ( try ( lf = createFile mbLogFile ) catch () )
		if lf != undefined do ( format "%\n" msg to:lf ; close lf )
	)
	if not mbAuto do messageBox msg title:"MotionBridge"
)
'''

SCRIPT = r'''-- MotionBridge -> 3ds Max Biped  (자동 생성 파일, 리타깃 결과)
-- 사용법: 3ds Max 에서 [Scripting > Run Script...] 로 이 파일을 실행하세요.
--   * 같은 폴더의 "__DATA__" 를 읽어서 Biped 에 리타깃한 모션 키를 넣고 .bip 로 저장합니다 (저장 위치를 묻지 않습니다).
--   * 템플릿 .max 가 지정되어 있으면 그 파일을 열어 안의 Biped 에 모션을 적용하고, 템플릿이 있는 폴더에
--     "<이 파일 이름>.max" (+ .bip) 로 다른 이름 저장합니다. 템플릿 원본은 건드리지 않고, 같은 이름이 있으면 _2, _3 … 을 붙입니다.
--   * 템플릿이 없으면 새 Biped 를 만들어 이 스크립트와 같은 폴더에 .bip 로 저장합니다.
--   * 오류가 나면 [MAXScript Listener] 창(F11)의 내용을 알려 주세요.

__AUTOHEAD__
-- 주의: MaxScript 는 함수 안에서 바깥 함수의 지역변수/지역함수를 쓸 수 없어서(컴파일 오류), 도우미 함수는 모두 여기(최상위)에 두고 필요한 값은 인자로 받는다.
fn mbTryGetNode b arr link = ( local n = undefined ; try ( n = biped.getNode b arr link:link ) catch () ; if n == undefined do ( try ( n = biped.getNode b.transform.controller arr link:link ) catch () ) ; n )

fn mbMakeBiped h ang spN fgN toN =
(
	local b = undefined
	try ( b = biped.createNew h ang [0,0,0] arms:true neckLinks:1 spineLinks:spN legLinks:3 fingers:fgN fingerLinks:3 toes:toN toeLinks:1 ankleAttach:0.2 ) catch ()
	if b == undefined do ( try ( b = biped.createNew h ang [0,0,0] arms:true neckLinks:1 spineLinks:spN legLinks:3 fingers:fgN fingerLinks:3 ) catch () )
	if b == undefined do ( try ( b = biped.createNew h ang [0,0,0] ) catch () )
	b
)

-- 오른쪽 허벅지 -> 왼쪽 허벅지 방향(좌우 축)이 XY 평면에서 몇 도인지 (없으면 undefined)
fn mbLrAngle b slotNames arrs links =
(
	local li = findItem slotNames "L_UpLeg"
	local ri = findItem slotNames "R_UpLeg"
	if li == 0 or ri == 0 then undefined else
	(
		local nl = mbTryGetNode b arrs[li] links[li]
		local nr = mbTryGetNode b arrs[ri] links[ri]
		if nl == undefined or nr == undefined then undefined else
		(
			local v = nl.transform.pos - nr.transform.pos
			atan2 v.y v.x
		)
	)
)

fn mbAngDiff a b = ( (mod (a - b + 540.0) 360.0) - 180.0 )

-- 같은 이름의 파일이 없는 이름(stem)을 찾는다: base, base_2, base_3 ... (확장자 목록 전부 비어 있어야 함)
fn mbUniqueStem dir base exts =
(
	local stem = base
	local i = 2
	local clash = true
	while clash do
	(
		clash = false
		for e in exts do ( if doesFileExist (dir + stem + e) do clash = true )
		if clash do ( stem = base + "_" + (i as string) ; i += 1 )
	)
	stem
)

fn mbRun =
(
	local dataFile = (getFilenamePath (getThisScriptFilename())) + "__DATA__"
	if mbDataAbs != "" do dataFile = mbDataAbs
	if not (doesFileExist dataFile) and not mbAuto do dataFile = getOpenFileName caption:"__DATA__ 파일을 고르세요" types:"MotionBridge Biped data (*.mbbip)|*.mbbip"
	if dataFile == undefined or not (doesFileExist dataFile) do ( mbSay "데이터 파일(__DATA__)을 찾지 못했습니다. 이 스크립트와 같은 폴더에 두세요." ; return undefined )

	-- ---------------------------------------------------------------- 데이터 읽기
	local f = openFile dataFile mode:"r"
	local pSlot = #(), pArr = #(), pLink = #(), pAim = #(), pDir = #()
	local lines = #()
	local lrDir = [0,0,0]
	local fps = 30.0, nFrames = 0, heightM = 1.7, hipM = 0.9, spineN = 3, fingN = 0, toesN = 0, clipName = "clip"
	local ln = readLine f   -- 형식 이름
	local inData = false
	while not (eof f) do
	(
		ln = readLine f
		if inData then ( append lines ln )
		else
		(
			local tk = filterString ln " "
			if tk.count > 0 do
			(
				case tk[1] of
				(
					"name": clipName = tk[2]
					"fps": fps = tk[2] as float
					"frames": nFrames = tk[2] as integer
					"height_m": heightM = tk[2] as float
					"hip_height_m": hipM = tk[2] as float
					"lr": lrDir = [tk[2] as float, tk[3] as float, tk[4] as float]
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
	if lines.count < 1 or nParts < 1 do ( mbSay "데이터 파일이 비어 있습니다." ; return undefined )

	-- 템플릿 모드(__TEMPLATE__ 가 비어 있지 않을 때): 스키닝된 .max 를 열어 그 안의 Biped 에 키를 굽고 .max 로 저장한다 (Biped 를 새로 만들지 않음)
	local tmplFile = "__TEMPLATE__"
	local scriptFile = getThisScriptFilename()
	local scriptDir = if scriptFile != undefined then (getFilenamePath scriptFile) else (getFilenamePath dataFile)
	local baseName = if mbBaseAbs != "" then mbBaseAbs else if scriptFile != undefined then (getFilenameFile scriptFile) else (clipName + "_retarget")
	local outBip = undefined, outMax = undefined
	if tmplFile != "" then
	(
		if not (doesFileExist tmplFile) do ( mbSay ("템플릿 .max 파일을 찾지 못했습니다:\n" + tmplFile) ; return undefined )
		local tdir = getFilenamePath tmplFile
		local stem = mbUniqueStem tdir baseName #(".max", ".bip")
		outMax = tdir + stem + ".max"
		outBip = tdir + stem + ".bip"
	)
	else
	(
		local stem = mbUniqueStem scriptDir baseName #(".bip")
		outBip = scriptDir + stem + ".bip"
	)

	local U = units.decodeValue "1.0m"      -- 3ds Max 시스템 단위로 1 미터
	local msgs = #()

	-- ---------------------------------------------------------------- Biped 준비
	local bip = undefined
	if tmplFile != "" then
	(
		if not mbAuto do ( if not (checkForSave()) do return undefined )      -- 지금 장면에 저장 안 한 변경이 있으면 먼저 물어본다 (자동 실행은 묻지 않음)
		loadMaxFile tmplFile quiet:true useFileUnits:true
		U = units.decodeValue "1.0m"
		-- Biped 의 중심(COM, Bip01): 컨트롤러가 Vertical_Horizontal_Turn 인 노드
		for o in objects where bip == undefined do ( try ( if classof o.controller == Vertical_Horizontal_Turn do bip = o ) catch () )
		if bip == undefined do ( mbSay "템플릿 .max 안에서 Biped 를 찾지 못했습니다." ; return undefined )
	)
	else bip = mbMakeBiped (heightM * U) 0 spineN fingN toesN
	if bip == undefined do ( mbSay "Biped 를 만들지 못했습니다." ; return undefined )
	local ctrl = bip.transform.controller
	ctrl.figureMode = true

	-- 정면 방향 맞추기: 오른쪽 허벅지 -> 왼쪽 허벅지 방향(좌우 축)이 데이터와 같아지게 Biped 를 돌려서 다시 만든다.
	-- (발끝이 없는 모델에서도 되도록 발끝 대신 허벅지를 쓴다. 만든 뒤 실제로 재서 확인하고, 틀리면 반대 방향으로 한 번 더 시도한다.)
	local facingMsg = ""
	if tmplFile == "" and length lrDir > 0.5 do
	(
		local target = atan2 lrDir.y lrDir.x
		local cur = mbLrAngle bip pSlot pArr pLink
		if cur != undefined and abs (mbAngDiff target cur) > 5.0 do
		(
			local need = mbAngDiff target cur
			local tryAng = #(need, -need)
			local okAng = false
			for k = 1 to 2 while not okAng do
			(
				local old = for o in objects where (matchPattern o.name pattern:(bip.name + "*")) collect o
				delete old
				bip = mbMakeBiped (heightM * U) tryAng[k] spineN fingN toesN
				ctrl = bip.transform.controller
				ctrl.figureMode = true
				local now = mbLrAngle bip pSlot pArr pLink
				if now != undefined and abs (mbAngDiff target now) <= 5.0 do okAng = true
			)
			if okAng then facingMsg = "정면 방향 맞춤 완료" else append msgs "⚠ Biped 정면 방향을 데이터와 맞추지 못했습니다 (좌우 축 불일치) — 캐릭터가 돌아가 보일 수 있습니다."
		)
	)

	local nodes = #()
	for i = 1 to nParts do
	(
		local n = mbTryGetNode bip pArr[i] pLink[i]
		append nodes n
		if n == undefined do append msgs ("Biped 에서 찾지 못한 부위: " + pSlot[i])
	)

	-- ---------------------------------------------------------------- Figure 모드에서 내 모델의 바인드 포즈 방향에 맞추기
	-- (템플릿 모드에서는 이미 스키닝된 기본 자세를 건드리지 않고, 타깃 바인드 포즈와 얼마나 다른지만 알려 준다)
	local figMsg = ""
	if tmplFile != "" do
	(
		local offList = #()
		for i = 1 to nParts do
		(
			local c = pAim[i]
			if c > 0 and nodes[i] != undefined and nodes[c] != undefined and length pDir[i] > 0.5 do
			(
				local db = nodes[c].transform.pos - nodes[i].transform.pos
				if length db > 1e-6 do
				(
					local e = acos (amax -1.0 (amin 1.0 (dot (normalize db) pDir[i])))
					if e > 10.0 do append offList (pSlot[i] + ":" + (((floor e) as integer) as string) + "도")
				)
			)
		)
		if offList.count > 0 then
		(
			figMsg = "⚠ 템플릿 Biped 의 기본(Figure) 자세가 타깃 모델의 바인드 포즈와 다른 부위: "
			for k = 1 to (amin 12 offList.count) do figMsg += offList[k] + "  "
			figMsg += "\n   (타깃 모델과 템플릿의 기본 자세가 같아야 모션이 정확합니다)"
		)
		else figMsg = "템플릿 Biped 의 기본 자세가 타깃 모델의 바인드 포즈와 일치합니다."
	)
	for i = 1 to nParts do
	(
		local c = pAim[i]
		if tmplFile == "" and c > 0 and nodes[i] != undefined and nodes[c] != undefined and length pDir[i] > 0.5 do
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
					try ( biped.setTransform nodes[i] #rotation ((cur * rm) as quat) false ) catch ( append msgs ("자세 맞추기 실패: " + pSlot[i]) )
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

	-- ---------------------------------------------------------------- 자가 점검: 키를 넣은 Biped 의 뼈 방향 = 데이터가 말하는 방향?
	local checkMsg = ""
	local worst = #()
	local chkFrames = #(1, (nFrames + 1) / 2, nFrames)
	for t in chkFrames do
	(
		if t >= 1 and t <= nFrames do
		(
			sliderTime = (t - 1)
			local tk = filterString lines[t] " "
			for i = 1 to nParts do
			(
				local c = pAim[i]
				if c > 0 and nodes[i] != undefined and nodes[c] != undefined and length pDir[i] > 0.5 and tk.count >= 3 + 4 * nParts do
				(
					local o = 3 + (i - 1) * 4
					local ang = tk[o + 4] as float
					local dm = if abs ang < 1e-4 then (matrix3 1) else ((angleaxis ang [tk[o + 1] as float, tk[o + 2] as float, tk[o + 3] as float]) as matrix3)
					local want = normalize (pDir[i] * dm)
					local got = nodes[c].transform.pos - nodes[i].transform.pos
					if length got > 1e-6 do
					(
						got = normalize got
						local e = acos (amax -1.0 (amin 1.0 (dot want got)))
						if e > 15.0 do append worst (pSlot[i] + "@" + (t as string) + ":" + (((floor e) as integer) as string) + "도")
					)
				)
			)
		)
	)
	sliderTime = 0
	if worst.count == 0 then checkMsg = "자가 점검: 뼈 방향이 데이터와 일치합니다 (프레임 1·중간·끝, 오차 15도 이하)"
	else
	(
		checkMsg = "⚠ 자가 점검: 뼈 방향이 데이터와 다른 부위 (부위@프레임:오차) — "
		for k = 1 to (amin 12 worst.count) do checkMsg += worst[k] + "  "
	)

	-- ---------------------------------------------------------------- 저장
	local saved = false
	try ( biped.saveBipFile ctrl outBip ; saved = true ) catch ( append msgs ("저장 실패: " + (getCurrentException())) )
	if saved do mbSay ("SAVED: " + outBip)
	local savedMax = false
	if outMax != undefined do
	(
		try ( saveMaxFile outMax quiet:true ; savedMax = true ) catch ( append msgs (".max 저장 실패: " + (getCurrentException())) )
		if savedMax do mbSay ("SAVED: " + outMax)
	)
	local secs = (timeStamp() - t0) / 1000.0
	local msg = "MotionBridge: Biped 모션 " + (nFrames as string) + "프레임 적용 (" + (secs as string) + "초)\n"
	if saved do msg += "저장: " + outBip + "\n"
	if savedMax do msg += "저장: " + outMax + "\n"
	if figMsg != "" do msg += figMsg + "\n"
	if facingMsg != "" do msg += facingMsg + "\n"
	msg += checkMsg + "\n"
	msg += "Biped 키 " + (heightM as string) + "m · 골반 높이 비율 " + (r as string) + "\n"
	if failed.count > 0 do msg += "키를 넣지 못한 부위: " + (failed as string) + "\n"
	for l in msgs do msg += l + "\n"
	mbSay msg
)
mbRun()
if mbQuitAfter do quitMax #noPrompt
'''


SCRIPT = SCRIPT.replace("__AUTOHEAD__\n", AUTO_HEADER)


def maxstr(p: str) -> str:
    """MaxScript 문자열 안에 넣을 경로: 슬래시, 따옴표 제거."""
    return (p or "").replace("\\", "/").replace('"', "")


def fill_script(text: str, data_name: str, template: str = "", auto: Optional[dict] = None) -> str:
    """스크립트 틀의 자리표시자를 채운다. auto = {"log","data","base","quit"} 이면 자동 실행용(메시지 창 없음, 경로를 박음)."""
    a = auto or {}
    return (text.replace("__AUTO__", "true" if auto else "false").replace("__LOG__", maxstr(a.get("log", "")))
            .replace("__DATAABS__", maxstr(a.get("data", ""))).replace("__BASEABS__", a.get("base", ""))
            .replace("__QUIT__", "true" if a.get("quit") else "false")
            .replace("__DATA__", data_name).replace("__TEMPLATE__", maxstr(template)))


def write_script(path: str, data_name: str, template: str = "", auto: Optional[dict] = None) -> None:
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(fill_script(SCRIPT, data_name, template, auto))


def export_biped(out_dir: str, base: str, skel: Skeleton, anim: Animation, slots: Dict[str, int], frame_to_canon: np.ndarray,
                 units_per_meter: float, template: str = "", auto: Optional[dict] = None) -> Tuple[List[str], List[str]]:
    """(만든 파일 목록, 경고 목록). auto 는 fill_script 참고(경로 키는 여기서 채운다)."""
    d = compute(skel, anim, slots, frame_to_canon, units_per_meter)
    ms = os.path.join(out_dir, base + "_retarget.ms")
    data = os.path.join(out_dir, base + "_retarget.mbbip")
    write_data(data, d)
    if auto is not None:
        auto = dict(auto, data=data, base=base + "_retarget")
    write_script(ms, os.path.basename(data), template, auto)
    warns = []
    missing = [s for s in ("Hips", "Spine", "Head", "L_UpperArm", "R_UpperArm", "L_UpLeg", "R_UpLeg") if s not in slots]
    if missing:
        warns.append("Biped: 매핑되지 않은 주요 부위가 있어 그 부분은 Biped 기본 자세로 남습니다: %s" % ", ".join(missing))
    return [ms, data], warns
