"""3ds Max 로 '일반 본'(Maya 식 관절, Biped 가 아닌 평범한 본/헬퍼 계층)에 모션을 굽는 내보내기.

스키닝된 .max 의 본 이름과 타깃 모델의 본 이름이 같으면(대소문자·공백·_·네임스페이스 접두어는 무시) 그 본에 직접 키를 넣는다.
회전은 Biped 와 같은 방식으로 '바인드 포즈 대비 월드 회전 변화(델타)'를 보낸다 — 본 로컬 축 규칙이 달라도 월드 델타는 같아서 맞는다.
  max 본의 월드 회전 = (.max 를 연 순간의 월드 회전) * 델타          (부모부터 순서대로)
위치는 루트·골반(Hips)·위치가 애니메이션되는 본만 월드 위치 델타로 넣는다 (나머지는 계층을 따라간다).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from .biped import _axis_angle_deg, fill_script, AUTO_HEADER
from .model import Animation, Skeleton, fk

FORMAT = "motionbridge-bones-1"


def compute(skel: Skeleton, anim: Animation, slots: Dict[str, int], frame_to_canon: np.ndarray, units_per_meter: float) -> dict:
    """스크립트에 보낼 값. 3ds Max 프레임(Z-up, 캐릭터는 -Y 를 바라봄), 길이는 미터."""
    from .export import PRESETS, preset_frame

    C = Q.mul(preset_frame(PRESETS["max"]), frame_to_canon)
    wr, wp = fk(skel, anim.rot, anim.pos)
    bwr, bwp = skel.bind_world()
    T, N = anim.n_frames, skel.n
    deltas = np.empty((T, N, 4))
    for j in range(N):
        d = Q.mul(wr[:, j], Q.conj(bwr[j])[None])
        dm = Q.mul(Q.mul(C, d), Q.conj(C))
        deltas[:, j] = _axis_angle_deg(Q.make_continuous(dm) if T > 1 else dm)
    hips = slots.get("Hips", -1)
    eps = 1e-5 * max(1.0, float(np.abs(skel.offsets).max()) if N else 1.0)
    moved = np.abs(anim.pos - skel.offsets[None]).max(axis=(0, 2)) > eps if T > 0 else np.zeros(N, bool)
    pos_bones = [j for j in range(N) if skel.parents[j] < 0 or j == hips or bool(moved[j])]
    posd = np.empty((T, len(pos_bones), 3))
    for k, j in enumerate(pos_bones):
        posd[:, k] = Q.rotate(C, wp[:, j] - bwp[j][None]) / units_per_meter
    dirs = np.zeros((N, 3))
    aim = [-1] * N
    for j in range(N):
        for c in skel.children[j]:
            v = Q.rotate(C, bwp[c] - bwp[j])
            n = float(np.linalg.norm(v))
            if n > 1e-9:
                dirs[j] = v / n
                aim[j] = c
                break
    allp = Q.rotate(C, bwp) / units_per_meter
    ground = float(min([allp[slots[s], 2] for s in ("L_Toe", "R_Toe", "L_Foot", "R_Foot") if s in slots] or [allp[:, 2].min()]))
    hip_h = float(allp[hips, 2] - ground) if hips >= 0 else 0.0
    return {"names": list(skel.names), "parents": [int(p) for p in skel.parents], "aim": aim, "dirs": dirs, "pos_bones": pos_bones,
            "deltas": deltas, "posd": posd, "hips": hips, "hip_height_m": hip_h, "fps": float(anim.fps), "frames": T, "name": anim.name}


def write_data(path: str, d: dict) -> None:
    names = d["names"]
    posk = {j: k for k, j in enumerate(d["pos_bones"])}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("%s\n" % FORMAT)
        f.write("name %s\n" % d["name"].replace(" ", "_"))
        f.write("fps %.6f\n" % d["fps"])
        f.write("frames %d\n" % d["frames"])
        f.write("hip_height_m %.6f\n" % d["hip_height_m"])
        f.write("hips %d\n" % (d["hips"] + 1))
        f.write("bones %d\n" % len(names))
        f.write("posbones %d\n" % len(d["pos_bones"]))
        for j, nm in enumerate(names):
            dr = d["dirs"][j]
            f.write("B\t%s\t%d\t%d\t%.6f\t%.6f\t%.6f\n" % (nm.replace("\t", " "), d["aim"][j] + 1, posk.get(j, -1) + 1, dr[0], dr[1], dr[2]))
        f.write("DATA\n")
        for t in range(d["frames"]):
            vals = [x for k in range(len(d["pos_bones"])) for x in d["posd"][t, k]] + [x for j in range(len(names)) for x in d["deltas"][t, j]]
            f.write(" ".join("%.5f" % v for v in vals) + "\n")


SCRIPT = r'''-- MotionBridge -> 3ds Max 일반 본(Maya 식 관절)  (자동 생성 파일, 리타깃 결과)
-- 사용법: 3ds Max 에서 [Scripting > Run Script...] 로 이 파일을 실행하세요.
--   * 같은 폴더의 "__DATA__" 를 읽어서, 스키닝된 템플릿 .max 를 열고 이름이 같은 본에 모션 키를 넣은 뒤
--     템플릿이 있는 폴더에 "<이 파일 이름>.max" 로 다른 이름 저장합니다 (원본은 그대로, 같은 이름이 있으면 _2, _3 …).
--   * 오류가 나면 [MAXScript Listener] 창(F11)의 내용을 알려 주세요.

__AUTOHEAD__
-- 본 이름 비교용: 소문자, 네임스페이스(: 와 | 앞부분) 제거, 공백/밑줄/점 제거
fn mbNorm s =
(
	local t = toLower s
	local p = filterString t ":|"
	if p.count > 0 do t = p[p.count]
	local r = ""
	for i = 1 to t.count do ( local c = t[i] ; if c != " " and c != "_" and c != "." do r += c )
	r
)

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

fn mbMat ang ax = ( if abs ang < 1e-4 then (matrix3 1) else ((angleaxis ang ax) as matrix3) )

fn mbRun =
(
	local dataFile = (getFilenamePath (getThisScriptFilename())) + "__DATA__"
	if mbDataAbs != "" do dataFile = mbDataAbs
	if not (doesFileExist dataFile) and not mbAuto do dataFile = getOpenFileName caption:"__DATA__ 파일을 고르세요" types:"MotionBridge bones data (*.mbbones)|*.mbbones"
	if dataFile == undefined or not (doesFileExist dataFile) do ( mbSay "데이터 파일(__DATA__)을 찾지 못했습니다. 이 스크립트와 같은 폴더에 두세요." ; return undefined )

	-- ---------------------------------------------------------------- 데이터 읽기
	local f = openFile dataFile mode:"r"
	local bName = #(), bAim = #(), bPk = #(), bDir = #()
	local lines = #()
	local fps = 30.0, nFrames = 0, hipM = 0.9, hipsIdx = 0, nPos = 0
	local ln = readLine f
	local inData = false
	while not (eof f) do
	(
		ln = readLine f
		if inData then ( append lines ln )
		else
		(
			local tk = filterString ln " \t"
			if tk.count > 0 do
			(
				case tk[1] of
				(
					"fps": fps = tk[2] as float
					"frames": nFrames = tk[2] as integer
					"hip_height_m": hipM = tk[2] as float
					"hips": hipsIdx = tk[2] as integer
					"posbones": nPos = tk[2] as integer
					"B": ( append bName ln ; append bAim (tk[tk.count - 4] as integer) ; append bPk (tk[tk.count - 3] as integer) ; append bDir [tk[tk.count - 2] as float, tk[tk.count - 1] as float, tk[tk.count] as float] )
					"DATA": inData = true
				)
			)
		)
	)
	close f
	local nB = bName.count
	if lines.count < 1 or nB < 1 do ( mbSay "데이터 파일이 비어 있습니다." ; return undefined )
	-- 본 이름: "B<탭>이름<탭>..." 줄에서 이름만 꺼낸다
	for i = 1 to nB do ( local tk = filterString bName[i] "\t" ; bName[i] = tk[2] )

	-- ---------------------------------------------------------------- 템플릿 열기
	local tmplFile = "__TEMPLATE__"
	if tmplFile == "" do ( mbSay "일반 본 방식은 스키닝된 .max 가 필요합니다 (템플릿 경로가 비어 있음)." ; return undefined )
	if not (doesFileExist tmplFile) do ( mbSay ("템플릿 .max 파일을 찾지 못했습니다:\n" + tmplFile) ; return undefined )
	local scriptFile = getThisScriptFilename()
	local baseName = if mbBaseAbs != "" then mbBaseAbs else if scriptFile != undefined then (getFilenameFile scriptFile) else "motion_retarget"
	local tdir = getFilenamePath tmplFile
	local stem = mbUniqueStem tdir baseName #(".max")
	local outMax = tdir + stem + ".max"
	if not mbAuto do ( if not (checkForSave()) do return undefined )
	loadMaxFile tmplFile quiet:true useFileUnits:true
	local U = units.decodeValue "1.0m"
	local msgs = #()

	-- ---------------------------------------------------------------- 이름으로 본 찾기
	local objNames = for o in objects collect (mbNorm o.name)
	local nodes = #()
	local missing = #()
	for i = 1 to nB do
	(
		local n = getNodeByName bName[i] exact:true
		if n == undefined do ( local k = findItem objNames (mbNorm bName[i]) ; if k > 0 do n = objects[k] )
		append nodes n
		if n == undefined do append missing bName[i]
	)
	local found = nB - missing.count
	if found < 1 do ( mbSay ("스키닝된 .max 안에서 이름이 같은 본을 하나도 찾지 못했습니다 (모델 본 이름 예: " + bName[1] + "). Biped 방식을 쓰거나 본 이름을 맞춰 주세요.") ; return undefined )

	-- ---------------------------------------------------------------- 기본 자세 기록 + 타깃 바인드 포즈와 비교
	local restM = for i = 1 to nB collect (if nodes[i] != undefined then ((nodes[i].transform.rotationpart) as matrix3) else (matrix3 1))
	local restP = for i = 1 to nB collect (if nodes[i] != undefined then nodes[i].transform.pos else [0,0,0])
	local offList = #()
	for i = 1 to nB do
	(
		local c = bAim[i]
		if c > 0 and nodes[i] != undefined and nodes[c] != undefined and length bDir[i] > 0.5 do
		(
			local db = restP[c] - restP[i]
			if length db > 1e-6 do
			(
				local e = acos (amax -1.0 (amin 1.0 (dot (normalize db) bDir[i])))
				if e > 10.0 do append offList (bName[i] + ":" + (((floor e) as integer) as string) + "도")
			)
		)
	)
	local figMsg = ""
	if offList.count > 0 then
	(
		figMsg = "⚠ .max 의 기본 자세가 타깃 모델의 바인드 포즈와 다른 본: "
		for k = 1 to (amin 12 offList.count) do figMsg += offList[k] + "  "
	)
	else figMsg = ".max 의 기본 자세가 타깃 모델의 바인드 포즈와 일치합니다."
	local r = U
	if hipsIdx > 0 and nodes[hipsIdx] != undefined and hipM > 0.05 do
	(
		local ph = restP[hipsIdx].z
		if ph > 0 do r = ph / hipM       -- 골반 높이 비율(데이터 미터 -> 이 .max 의 길이)
	)

	-- ---------------------------------------------------------------- 키 넣기 (부모부터: 데이터의 본 순서가 부모 먼저)
	frameRate = amax 1 ((floor (fps + 0.5)) as integer)
	animationRange = interval 0 (nFrames - 1)
	local failed = #()
	local t0 = timeStamp()
	local rotBase = 3 * nPos
	with animate on
	(
		for t = 1 to nFrames do
		(
			sliderTime = (t - 1)
			local tk = filterString lines[t] " "
			if tk.count >= rotBase + 4 * nB do
			(
				for i = 1 to nB do
				(
					if nodes[i] != undefined do
					(
						if bPk[i] > 0 do
						(
							local po = (bPk[i] - 1) * 3
							try ( in coordsys world (nodes[i].pos = restP[i] + [tk[po + 1] as float, tk[po + 2] as float, tk[po + 3] as float] * r) ) catch ( appendIfUnique failed (bName[i] + "(위치)") )
						)
						local o = rotBase + (i - 1) * 4
						local dm = mbMat (tk[o + 4] as float) [tk[o + 1] as float, tk[o + 2] as float, tk[o + 3] as float]
						try ( in coordsys world (nodes[i].rotation = ((restM[i] * dm) as quat)) ) catch ( appendIfUnique failed bName[i] )
					)
				)
			)
		)
	)
	sliderTime = 0

	-- ---------------------------------------------------------------- 자가 점검: 키를 넣은 뼈 방향 = 데이터가 말하는 방향?
	local worst = #()
	for t in #(1, (nFrames + 1) / 2, nFrames) do
	(
		if t >= 1 and t <= nFrames do
		(
			sliderTime = (t - 1)
			local tk = filterString lines[t] " "
			for i = 1 to nB do
			(
				local c = bAim[i]
				if c > 0 and nodes[i] != undefined and nodes[c] != undefined and tk.count >= rotBase + 4 * nB do
				(
					local db = restP[c] - restP[i]
					if length db > 1e-6 do
					(
						local o = rotBase + (i - 1) * 4
						local want = normalize ((normalize db) * (mbMat (tk[o + 4] as float) [tk[o + 1] as float, tk[o + 2] as float, tk[o + 3] as float]))
						local got = nodes[c].transform.pos - nodes[i].transform.pos
						if length got > 1e-6 do
						(
							got = normalize got
							local e = acos (amax -1.0 (amin 1.0 (dot want got)))
							if e > 15.0 do append worst (bName[i] + "@" + (t as string) + ":" + (((floor e) as integer) as string) + "도")
						)
					)
				)
			)
		)
	)
	sliderTime = 0
	local checkMsg = ""
	if worst.count == 0 then checkMsg = "자가 점검: 뼈 방향이 데이터와 일치합니다 (프레임 1·중간·끝, 오차 15도 이하)"
	else
	(
		checkMsg = "⚠ 자가 점검: 뼈 방향이 데이터와 다른 본 (본@프레임:오차) — "
		for k = 1 to (amin 12 worst.count) do checkMsg += worst[k] + "  "
	)

	-- ---------------------------------------------------------------- 저장
	local savedMax = false
	try ( saveMaxFile outMax quiet:true ; savedMax = true ) catch ( append msgs (".max 저장 실패: " + (getCurrentException())) )
	if savedMax do mbSay ("SAVED: " + outMax)
	local secs = (timeStamp() - t0) / 1000.0
	local msg = "MotionBridge: 일반 본 모션 " + (nFrames as string) + "프레임 적용 (" + (secs as string) + "초), 본 " + (found as string) + "/" + (nB as string) + "개 일치\n"
	if savedMax do msg += "저장: " + outMax + "\n"
	msg += figMsg + "\n"
	msg += checkMsg + "\n"
	msg += "위치 배율 " + (r as string) + "\n"
	if missing.count > 0 do
	(
		msg += "이름이 같은 본이 없어 키를 못 넣은 본 (" + (missing.count as string) + "개): "
		for k = 1 to (amin 15 missing.count) do msg += missing[k] + "  "
		msg += "\n"
	)
	if failed.count > 0 do msg += "키를 넣지 못한 본: " + (failed as string) + "\n"
	for l in msgs do msg += l + "\n"
	mbSay msg
)
mbRun()
if mbQuitAfter do quitMax #noPrompt
'''.replace("__AUTOHEAD__\n", AUTO_HEADER)


def write_script(path: str, data_name: str, template: str = "", auto: Optional[dict] = None) -> None:
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(fill_script(SCRIPT, data_name, template, auto))


def export_bones(out_dir: str, base: str, skel: Skeleton, anim: Animation, slots: Dict[str, int], frame_to_canon: np.ndarray,
                 units_per_meter: float, template: str = "", auto: Optional[dict] = None) -> Tuple[List[str], List[str]]:
    """(만든 파일 목록, 경고 목록)"""
    warns: List[str] = []
    if not template:
        raise ValueError("일반 본 방식은 모션을 넣을 스키닝된 .max 가 필요합니다. '스키닝된 .max' 를 고르세요.")
    d = compute(skel, anim, slots, frame_to_canon, units_per_meter)
    ms = os.path.join(out_dir, base + "_retarget.ms")
    data = os.path.join(out_dir, base + "_retarget.mbbones")
    write_data(data, d)
    if auto is not None:
        auto = dict(auto, data=data, base=base + "_retarget")
    write_script(ms, os.path.basename(data), template, auto)
    if "Hips" not in slots:
        warns.append("일반 본: Hips(골반) 슬롯이 매핑되지 않아 골반 위치 이동은 루트 본만 따릅니다.")
    return [ms, data], warns
