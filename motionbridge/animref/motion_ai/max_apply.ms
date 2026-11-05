-- ============================================================================
--  AnimRef -> 3ds Max : 모션(.motion.txt)을 '이미 세팅된 뼈 리그'에 키프레임으로 입력
--
--  사용법
--   1) 리그를 '기본 자세(T자 또는 A자)'로 두고, 시간 슬라이더를 0프레임에 둡니다. (이 자세를 기준으로 계산해요)
--   2) 리그의 아무 뼈나 하나 선택합니다 (맨 위 부모까지 자동으로 올라가 전체 계층을 씁니다).
--   3) 이 파일을 Max 에서 실행합니다 (Scripting > Run Script...)
--   4) [모션 파일 열기] 로 AnimRef 가 만든 <이름>.motion.txt 를 고르고 [적용] 을 누릅니다.
--
--  지원: Bone / Dummy / CAT 부품 / 일반 노드 계층 (뼈 이름은 자동 인식: Mixamo, Unreal, UpperArm_L 식 이름 등)
--  Biped 도 지원해요(실험): Figure Mode 를 켜 둔 기본 자세에서 [적용]을 누르면 스크립트가 알아서 끄고 키를 넣어요.
--    Biped 의 손·발은 IK 로 움직이니 결과가 어색하면 Motion 패널에서 손발 IK Blend 를 조절하거나, 같은 폴더의 .bvh 를 Motion Capture 로 불러오세요.
--  이름이 달라 못 알아보면, motion.txt 와 같은 폴더에 animref_map.txt 를 만들어
--      upperarm_L=내뼈이름
--  처럼 한 줄에 하나씩 적으면 그 값이 우선합니다. (역할 이름은 아래 AR_ROLES 참고)
--
--  !! 이 스크립트는 3ds Max 가 없는 환경에서 작성해서 실행해 보지 못했습니다.
--  !! Blender 용 blender_apply.py 와 같은 수식을 쓰며, 수식은 시험을 거쳤지만 MAXScript 문법/동작은 Max 에서 처음 돌려 보는 것입니다.
--  !! 오류가 나면 Listener 창(F11)의 빨간 글씨를 그대로 알려 주세요.
-- ============================================================================

global AR_ROLES = #("hips","neck","head","shoulder_L","shoulder_R","upperarm_L","upperarm_R","forearm_L","forearm_R","hand_L","hand_R","thigh_L","thigh_R","shin_L","shin_R","foot_L","foot_R","toe_L","toe_R")
-- 위 역할에 대응하는 SMPL 관절 번호 (0부터)
global AR_SMPLJ = #(0,12,15,13,14,16,17,18,19,20,21,1,2,4,5,7,8,10,11)
-- 방향을 맞출 '다음 뼈' 역할과, 그에 대응하는 SMPL 관절 (없으면 "" / -1). AR_ROLES 와 같은 순서(19개)
global AR_NEXTR = #("spine_0","head","","upperarm_L","upperarm_R","forearm_L","forearm_R","hand_L","hand_R","","","shin_L","shin_R","foot_L","foot_R","toe_L","toe_R","","")
global AR_NEXTJ = #(3,15,-1,16,17,18,19,20,21,22,23,4,5,7,8,10,11,-1,-1)
-- 다음 뼈가 없어도 자식 노드 방향을 쓸 수 있는 역할: 손, 발
global AR_TAILOK = #("hand_L","hand_R","foot_L","foot_R")

global AR_TOKEXCL = #("ik","end","top","ear","eye","ring","tail","hair","pole","target","mch","roll","nub","tip")
global AR_SUBEXCL = #("twist","tweak","helper","dummy","proxy","thumb","index","middle","pinky","little","finger","jaw","cloth","skirt","breast","prop","weapon","corrective","stretch")
global AR_IGNTOK = #("bip","bone","c","cc","mixamorig")

struct AR_MotionData ( fps = 30.0, frames = 0, rest = #(), markers = #(), rootPos = #(), W = #() )
global AR_motion = undefined
global AR_motionPath = ""
global AR_dlg

-- ---------------------------------------------------------------- 3x3 행렬 도구 (열 벡터 규칙, 9개 배열 = 행 우선)
fn AR_ident = ( #(1.0,0.0,0.0, 0.0,1.0,0.0, 0.0,0.0,1.0) )

fn AR_mmul a b = (
	local r = #(0.0,0.0,0.0, 0.0,0.0,0.0, 0.0,0.0,0.0)
	for i = 0 to 2 do (
		for j = 0 to 2 do (
			local s = 0.0
			for k = 0 to 2 do ( s += a[i*3+k+1] * b[k*3+j+1] )
			r[i*3+j+1] = s
		)
	)
	r
)
fn AR_mt a = ( #(a[1],a[4],a[7], a[2],a[5],a[8], a[3],a[6],a[9]) )
fn AR_mvec a v = ( [ a[1]*v.x + a[2]*v.y + a[3]*v.z, a[4]*v.x + a[5]*v.y + a[6]*v.z, a[7]*v.x + a[8]*v.y + a[9]*v.z ] )
fn AR_fromCols c1 c2 c3 = ( #(c1.x,c2.x,c3.x, c1.y,c2.y,c3.y, c1.z,c2.z,c3.z) )
fn AR_col a j = ( [ a[j], a[j+3], a[j+6] ] )
fn AR_skew v = ( #(0.0, -v.z, v.y, v.z, 0.0, -v.x, -v.y, v.x, 0.0) )
fn AR_madd a b = ( local r = #(); for i = 1 to 9 do append r (a[i] + b[i]); r )
fn AR_mscale a k = ( local r = #(); for i = 1 to 9 do append r (a[i] * k); r )
fn AR_sub9 arr j = ( local r = #(); for i = 1 to 9 do append r arr[j*9+i]; r )

-- 노드의 현재 회전 (크기/기울임 제거) -> 3x3
fn AR_nodeRot n = (
	local t = n.transform
	local x = normalize t.row1
	local z = normalize (cross x t.row2)
	local y = cross z x
	AR_fromCols x y z
)

-- a 방향을 b 방향으로 돌리는 최소 회전
fn AR_rotBetween a b = (
	local av = normalize a
	local bv = normalize b
	local v = cross av bv
	local c = dot av bv
	local s = length v
	local r = AR_ident()
	if s < 1e-9 then (
		if c < 0 then (
			local ax = cross av [1,0,0]
			if (length ax) < 1e-6 then ax = cross av [0,1,0]
			ax = normalize ax
			local K = AR_skew ax
			r = AR_madd (AR_ident()) (AR_mscale (AR_mmul K K) 2.0)
		)
	) else (
		local K = AR_skew v
		r = AR_madd (AR_madd (AR_ident()) K) (AR_mscale (AR_mmul K K) ((1.0 - c) / (s * s)))
	)
	r
)

-- (위 시작, 위 끝, 왼쪽 a, 왼쪽 b) -> 열 = (왼쪽, 위, 앞) 인 3x3, 만들 수 없으면 undefined
fn AR_frameFrom up0 up1 la lb = (
	local res = undefined
	local up = up1 - up0
	local lf = la - lb
	if (length up) > 1e-6 and (length lf) > 1e-6 then (
		up = normalize up
		lf = lf - up * (dot lf up)
		if (length lf) > 1e-6 then (
			lf = normalize lf
			res = AR_fromCols lf up (cross lf up)
		)
	)
	res
)

-- ---------------------------------------------------------------- 이름 인식 (blender_apply.py 의 classify_bone 과 같은 규칙)
fn AR_splitName nm = (
	-- ':' 또는 '|' 뒤쪽만 사용. 영문자 덩어리로 나누고(숫자·기호는 구분자) 소문자→대문자 경계에서도 자른다
	local start = 1
	for i = 1 to nm.count do ( if nm[i] == ":" or nm[i] == "|" then start = i + 1 )
	local toks = #()
	local cur = ""
	local prevLower = false
	for i = start to nm.count do (
		local ch = nm[i]
		local code = bit.charAsInt ch
		local isUp = (code >= 65 and code <= 90)
		local isLo = (code >= 97 and code <= 122)
		if isUp or isLo then (
			if isUp and prevLower and cur.count > 0 then ( append toks (toLower cur); cur = "" )
			cur += ch
			prevLower = isLo
		) else (
			if cur.count > 0 then ( append toks (toLower cur); cur = "" )
			prevLower = false
		)
	)
	if cur.count > 0 then append toks (toLower cur)
	toks
)

fn AR_has key s = ( (findString key s) != undefined )

-- 이름 -> #(역할, 점수) 또는 undefined
fn AR_classify nm = (
	local toks = AR_splitName nm
	local side = ""
	local keep = #()
	for t in toks do (
		if (t == "l" or t == "left" or t == "lf") and side == "" then ( side = "L" )
		else if (t == "r" or t == "right" or t == "rt") and side == "" then ( side = "R" )
		else if (findItem AR_IGNTOK t) == 0 then ( append keep t )
	)
	local key = ""
	for t in keep do key += t
	local result = undefined
	if key != "" then (
		local excluded = false
		for t in keep do ( if (findItem AR_TOKEXCL t) != 0 then excluded = true )
		for e in AR_SUBEXCL do ( if AR_has key e then excluded = true )
		if not excluded then (
			local role = ""
			if (AR_has key "forearm") or (AR_has key "lowerarm") or ((findItem keep "fore") != 0 and (findItem keep "arm") != 0) or ((findItem keep "lower") != 0 and (findItem keep "arm") != 0) then role = "forearm"
			else if (AR_has key "upperarm") or ((findItem keep "upper") != 0 and (findItem keep "arm") != 0) or key == "arm" or key == "armfk" or key == "armdef" or key == "armorg" then role = "upperarm"
			else if (AR_has key "hand") or (AR_has key "wrist") then role = "hand"
			else if (AR_has key "shoulder") or (AR_has key "clavicle") or (AR_has key "collar") then role = "shoulder"
			else if (AR_has key "thigh") or (AR_has key "upleg") or (AR_has key "upperleg") or ((findItem keep "up") != 0 and (findItem keep "leg") != 0) or ((findItem keep "upper") != 0 and (findItem keep "leg") != 0) then role = "thigh"
			else if (AR_has key "shin") or (AR_has key "calf") or (AR_has key "lowerleg") or ((findItem keep "lower") != 0 and (findItem keep "leg") != 0) or key == "leg" or key == "legfk" or key == "legdef" or key == "legorg" then role = "shin"
			else if (AR_has key "foot") or (AR_has key "ankle") then role = "foot"
			else if (AR_has key "toe") or ((findString key "ball") == 1) then role = "toe"
			else if AR_has key "neck" then role = "neck"
			else if AR_has key "head" then role = "head"
			else if (AR_has key "spine") or (AR_has key "chest") or (AR_has key "abdomen") then role = "spine"
			else if (AR_has key "hips") or (AR_has key "pelvis") or key == "hip" or key == "hipfk" or key == "hipsfk" then role = "hips"
			if role != "" then (
				local limb = (role == "forearm" or role == "upperarm" or role == "hand" or role == "shoulder" or role == "thigh" or role == "shin" or role == "foot" or role == "toe")
				if not (limb and side == "") then (
					local score = 0.0
					if (findItem keep "def") != 0 then score += 5.0
					if (findItem keep "org") != 0 then score += 6.0
					if (findItem keep "fk") == 0 then score += 0.5
					score += 0.01 * key.count
					local rname = role
					if limb then rname = role + "_" + side
					result = #(rname, score)
				)
			)
		)
	)
	result
)

fn AR_depth n = ( local d = 0; local p = n.parent; while p != undefined do ( d += 1; p = p.parent ); d )
fn AR_collect n acc = ( append acc n; for c in n.children do AR_collect c acc )

-- 노드 목록 -> 역할별 노드 (배열 두 개: 역할 이름들, 노드들)
fn AR_autoMap nodes = (
	local roles = #()
	local pick = #()
	local scores = #()
	local spines = #()
	local hasFk = false
	for n in nodes do (
		local r = AR_classify n.name
		if r != undefined then (
			if (AR_has (toLower n.name) "fk") then hasFk = true
		)
	)
	for n in nodes do (
		local r = AR_classify n.name
		if r != undefined then (
			local sc = r[2]
			if hasFk and not (AR_has (toLower n.name) "fk") then sc += 3.0
			if r[1] == "spine" then (
				append spines n
			) else (
				local idx = findItem roles r[1]
				if idx == 0 then ( append roles r[1]; append pick n; append scores sc )
				else if sc < scores[idx] then ( pick[idx] = n; scores[idx] = sc )
			)
		)
	)
	-- 척추: 계층 깊이 순서대로 (같은 깊이는 하나만)
	local depthSeen = #()
	local sorted = #()
	local pairs = #()
	for n in spines do append pairs #((AR_depth n), n)
	local cnt = pairs.count
	for i = 1 to cnt do (
		for j = i + 1 to cnt do (
			if pairs[j][1] < pairs[i][1] then ( local tmp = pairs[i]; pairs[i] = pairs[j]; pairs[j] = tmp )
		)
	)
	local k = 0
	for pr in pairs do (
		if (findItem depthSeen pr[1]) == 0 and k < 6 then (
			append depthSeen pr[1]
			append roles ("spine_" + (k as string))
			append pick pr[2]
			append scores 0.0
			k += 1
		)
	)
	#(roles, pick)
)

fn AR_spineSmpl k = (
	local r = #()
	if k == 1 then ( r = #(6) )
	else if k == 2 then ( r = #(3, 9) )
	else if k == 3 then ( r = #(3, 6, 9) )
	else if k >= 4 then (
		local base = #(3, 6, 9)
		for i = 0 to (k - 1) do (
			local idx = (floor (i * 2.0 / (k - 1) + 0.5)) as integer
			append r base[idx + 1]
		)
	)
	r
)

-- ---------------------------------------------------------------- 모션 파일 읽기
fn AR_loadMotion path = (
	local m = undefined
	local f = openFile path mode:"r"
	if f != undefined then (
		m = AR_MotionData()
		local gotHeader = false
		while not (eof f) do (
			local line = readLine f
			if line.count > 2 and (substring line 1 2) == "F " then (
				local tk = filterString line " "
				local v = #()
				for q = 3 to tk.count do append v (tk[q] as float)
				append m.rootPos [v[1], v[2], v[3]]
				local w = #()
				for q = 4 to v.count do append w v[q]
				append m.W w
			) else if line.count > 4 and (substring line 1 4) == "fps " then (
				m.fps = (substring line 5 -1) as float
			) else if line.count > 5 and (substring line 1 5) == "rest " then (
				local tk = filterString line " "
				m.rest = #()
				for j = 0 to 23 do append m.rest [ tk[2 + j*3] as float, tk[3 + j*3] as float, tk[4 + j*3] as float ]
			) else if line.count > 8 and (substring line 1 8) == "markers " then (
				local tk = filterString line " "
				for q = 2 to tk.count do append m.markers (tk[q] as float)
			)
		)
		close f
		m.frames = m.W.count
		gotHeader = (m.rest.count == 24 and m.frames > 0)
		if not gotHeader then m = undefined
	)
	m
)

-- animref_map.txt (역할=뼈이름) 읽기 -> #(역할들, 이름들)
fn AR_readMapFile path = (
	local roles = #()
	local names = #()
	if (doesFileExist path) then (
		local f = openFile path mode:"r"
		while not (eof f) do (
			local line = readLine f
			local p = findString line "="
			if p != undefined and p > 1 then (
				append roles (trimRight (trimLeft (substring line 1 (p - 1))))
				append names (trimRight (trimLeft (substring line (p + 1) -1)))
			)
		)
		close f
	)
	#(roles, names)
)

-- ---------------------------------------------------------------- 적용
-- 문제가 있으면 throw "메시지" 로 알린다 (창의 버튼이 try/catch 로 받아서 보여 준다)
fn AR_apply motion startFrame noMove = (
	local sel = selection as array
	if sel.count == 0 then throw "리그의 뼈를 하나 선택해 주세요."
	local top = sel[1]
	while top.parent != undefined do top = top.parent
	-- Biped: 몸 중심(COM, 'Bip001') 노드를 찾는다. Biped 가 다른 노드에 링크돼 있어도 COM 부터 쓴다
	local isBip = false
	local com = undefined
	local c0 = sel[1]
	while c0 != undefined and (classOf c0) != Biped_Object do c0 = c0.parent
	if c0 != undefined then (
		isBip = true
		com = c0
		while com.parent != undefined and (classOf com.parent) == Biped_Object do com = com.parent
		top = com
	)
	local nodes = #()
	AR_collect top nodes
	if isBip then format "AnimRef: Biped 로 인식했어요 (COM: %). 실험 기능이에요.\n" com.name
	format "AnimRef: 리그 노드 % 개 (최상위: %)\n" nodes.count top.name

	local am = AR_autoMap nodes
	local roles = am[1]
	local picks = am[2]
	-- 맵 파일(animref_map.txt)이 있으면 그 값이 우선
	local mf = AR_readMapFile ((getFilenamePath AR_motionPath) + "animref_map.txt")
	for i = 1 to mf[1].count do (
		local nd = getNodeByName mf[2][i]
		if nd == undefined then throw ("맵 파일의 뼈 이름을 못 찾았어요: " + mf[2][i])
		local idx = findItem roles mf[1][i]
		if idx == 0 then ( append roles mf[1][i]; append picks nd )
		else ( picks[idx] = nd )
	)
	for i = 1 to roles.count do format "AnimRef: 뼈 대응 % = %\n" roles[i] picks[i].name
	local hipsIdx = findItem roles "hips"
	if hipsIdx == 0 or roles.count < 6 then throw "뼈 이름을 거의 못 알아봤어요. animref_map.txt 로 직접 알려 주세요. (Listener 창에 찾은 목록이 있어요)"

	-- 역할 -> SMPL 관절
	local nspine = 0
	for r in roles do ( if (substring r 1 6) == "spine_" then nspine += 1 )
	local spj = AR_spineSmpl nspine
	local rj = #()
	for r in roles do (
		local j = -1
		local ri0 = findItem AR_ROLES r
		if ri0 != 0 then ( j = AR_SMPLJ[ri0] )
		else if (substring r 1 6) == "spine_" then (
			local sn = (substring r 7 -1) as integer
			j = spj[sn + 1]
		)
		append rj j
	)

	local rest = motion.rest
	-- 리그 방향(왼쪽·위·앞): 리그와 SMPL 양쪽에서 같은 부위로 틀을 재서 맞춘다. 못 알아내면 Max 기본(+X 왼쪽, +Z 위, -Y 앞)
	local C = #(1.0,0.0,0.0, 0.0,0.0,-1.0, 0.0,1.0,0.0)
	local found = false
	local topRole = "neck"
	local topJ = 12
	if (findItem roles "neck") == 0 then ( topRole = "head"; topJ = 15 )
	local ti = findItem roles topRole
	if ti != 0 then (
		local hipsPos = picks[hipsIdx].transform.row4
		local pa = undefined
		local pb = undefined
		local ja = 0
		local jb = 0
		local pairsR = #(#("thigh_L","thigh_R",1,2), #("upperarm_L","upperarm_R",16,17), #("shoulder_L","shoulder_R",13,14))
		for pr in pairsR do (
			if pa == undefined then (
				local ia = findItem roles pr[1]
				local ib = findItem roles pr[2]
				if ia != 0 and ib != 0 then ( pa = picks[ia].transform.row4; pb = picks[ib].transform.row4; ja = pr[3]; jb = pr[4] )
			)
		)
		if pa != undefined then (
			local Fr = AR_frameFrom hipsPos picks[ti].transform.row4 pa pb
			local Fs = AR_frameFrom rest[1] rest[topJ + 1] rest[ja + 1] rest[jb + 1]
			if Fr != undefined and Fs != undefined then ( C = AR_mmul Fr (AR_mt Fs); found = true )
		)
	)
	if found then format "AnimRef: 방향 자동 인식 = 성공\n" else format "AnimRef: 방향 자동 인식 = 실패 (Max 기본 방향 가정)\n"
	local CT = AR_mt C

	-- 부모 먼저 순서 (키를 넣는 순서). 부모를 먼저 정해야 자식의 절대 회전이 덮이지 않는다
	local order = #()
	for i = 1 to roles.count do ( if rj[i] >= 0 then append order #((AR_depth picks[i]), i) )
	local cnt = order.count
	for i = 1 to cnt do (
		for j = i + 1 to cnt do (
			if order[j][1] < order[i][1] then ( local tmp = order[i]; order[i] = order[j]; order[j] = tmp )
		)
	)

	-- 뼈마다 CtA = C^T · Rshort · R_rest  (Rshort: 기본 자세에서 뼈 방향을 SMPL 방향에 맞추는 최소 회전)
	local CtA = #()
	for i = 1 to roles.count do append CtA undefined
	for o in order do (
		local i = o[2]
		local nd = picks[i]
		local role = roles[i]
		local j = rj[i]
		local childJ = -1
		local childNode = undefined
		local tailPt = undefined
		local ri = findItem AR_ROLES role
		if (substring role 1 6) == "spine_" then (
			local nn = ((substring role 7 -1) as integer) + 1
			local ci = findItem roles ("spine_" + (nn as string))
			if ci != 0 then ( childNode = picks[ci]; childJ = rj[ci] )
			else (
				ci = findItem roles "neck"
				if ci != 0 then ( childNode = picks[ci]; childJ = 12 )
			)
		) else if role == "hips" then (
			local ci = findItem roles "spine_0"
			if ci != 0 then ( childNode = picks[ci]; childJ = rj[ci] )
		) else if ri != 0 then (
			childJ = AR_NEXTJ[ri]
			if AR_NEXTR[ri] != "" then (
				local ci = findItem roles AR_NEXTR[ri]
				if ci != 0 then childNode = picks[ci]
			)
			if childNode == undefined and (findItem AR_TAILOK role) != 0 and nd.children.count > 0 then (
				local sum = [0,0,0]
				for c in nd.children do sum += c.transform.row4
				tailPt = sum / nd.children.count
			)
		)
		local Rshort = AR_ident()
		local dRig = undefined
		if childNode != undefined then ( dRig = childNode.transform.row4 - nd.transform.row4 )
		else if tailPt != undefined then ( dRig = tailPt - nd.transform.row4 )
		if dRig != undefined and childJ >= 0 and childJ != j then (
			local dS = rest[childJ + 1] - rest[j + 1]
			if (length dRig) > 1e-9 and (length dS) > 1e-9 then ( Rshort = AR_rotBetween dRig (AR_mvec C dS) )
		)
		CtA[i] = AR_mmul CT (AR_mmul Rshort (AR_nodeRot nd))
	)

	-- 골반 이동: 다리 길이 비율로 크기를 맞춘다
	local scl = 1.0
	local li = findItem roles "thigh_L"
	local si = findItem roles "shin_L"
	local fi = findItem roles "foot_L"
	if li != 0 and si != 0 and fi != 0 then (
		local legRig = (distance picks[li].transform.row4 picks[si].transform.row4) + (distance picks[si].transform.row4 picks[fi].transform.row4)
		local legS = (distance rest[2] rest[5]) + (distance rest[5] rest[8])
		if legS > 1e-6 and legRig > 1e-6 then scl = legRig / legS
	)
	local hip0 = picks[hipsIdx].transform.row4
	local com0 = [0,0,0]
	if isBip then com0 = com.transform.row4
	local hStand = -(amin rest[8].y rest[9].y) + 0.08
	format "AnimRef: 크기 비율 %\n" scl

	-- 장면 설정 + 키 입력 (각 프레임, 부모 먼저, 세계 좌표로 회전 지정)
	frameRate = (floor (motion.fps + 0.5)) as integer
	animationRange = interval startFrame (startFrame + motion.frames - 1)
	if isBip then ( try ( com.controller.figureMode = false ) catch () )   -- 기준 자세는 위에서 이미 읽었으니 Figure Mode 를 끄고 애니메이션 모드로
	local bipErr = undefined
	animate on (
		for fr = 0 to (motion.frames - 1) do (
			at time (startFrame + fr) (
				if isBip and not noMove then (
					local rp0 = motion.rootPos[fr + 1]
					local d0 = [rp0.x, rp0.y - hStand, rp0.z] * scl
					try ( biped.setTransform com #pos (com0 + (AR_mvec C d0)) true ) catch ( if bipErr == undefined then bipErr = getCurrentException() )
				)
				for o in order do (
					local i = o[2]
					local nd = picks[i]
					local Wj = AR_sub9 motion.W[fr + 1] rj[i]
					local Rt = AR_mmul (AR_mmul C Wj) CtA[i]
					local old = nd.transform
					local sx = length old.row1
					local sy = length old.row2
					local sz = length old.row3
					local pos = old.row4
					if roles[i] == "hips" and not noMove then (
						local rp = motion.rootPos[fr + 1]
						local d = [rp.x, rp.y - hStand, rp.z] * scl
						pos = hip0 + (AR_mvec C d)
					)
					if isBip then (
						local qb = (matrix3 (AR_col Rt 1) (AR_col Rt 2) (AR_col Rt 3) [0,0,0]).rotation
						try ( biped.setTransform nd #rotation qb true ) catch ( if bipErr == undefined then bipErr = getCurrentException() )
					) else (
						nd.transform = matrix3 ((AR_col Rt 1) * sx) ((AR_col Rt 2) * sy) ((AR_col Rt 3) * sz) pos
					)
				)
			)
		)
	)
	sliderTime = startFrame
	if bipErr != undefined then throw ("Biped 키 입력 중 오류가 났어요: " + bipErr + "\n같은 폴더의 .bvh 를 Biped 의 Motion Capture 로 불러오는 방법을 써 보세요.")
	format "AnimRef: 키 입력 완료 (뼈 % 개 x % 프레임)\n" order.count motion.frames
	if motion.markers.count > 0 then (
		local ms = ""
		for mk in motion.markers do ( ms += ((startFrame + ((floor (mk * motion.fps + 0.5)) as integer)) as string) + " " )
		format "AnimRef: 타격 프레임: %\n" ms
	)
	true
)

-- ---------------------------------------------------------------- 창
try ( destroyDialog AR_dlg ) catch ()
rollout AR_dlg "AnimRef 모션 적용" width:380 (
	label lbl1 "1) 리그를 기본 자세 · 0프레임에 둔다 (Biped 는 Figure Mode 켠 채로)" align:#left
	label lbl2 "2) 리그의 뼈를 하나 선택한다" align:#left
	label lbl3 "3) 모션 파일을 열고 [적용]" align:#left
	button btnOpen "모션 파일 열기 (.motion.txt)" width:360
	label lblInfo "(파일을 아직 안 열었어요)" align:#left
	spinner spStart "시작 프레임:" range:[-1000,100000,0] type:#integer fieldwidth:60
	checkbox ckNoMove "제자리 (골반 이동 안 넣기)" checked:false
	button btnApply "선택한 리그에 적용" width:360 height:32 enabled:false
	on btnOpen pressed do (
		local p = getOpenFileName caption:"AnimRef 모션 파일" types:"AnimRef 모션 (*.motion.txt)|*.motion.txt|모든 파일 (*.*)|*.*"
		if p != undefined then (
			local m = AR_loadMotion p
			if m == undefined then (
				lblInfo.text = "읽지 못했어요. AnimRef 가 만든 .motion.txt 가 맞나요?"
				btnApply.enabled = false
			) else (
				AR_motion = m
				AR_motionPath = p
				lblInfo.text = ((filenameFromPath p) + "  /  " + (m.frames as string) + "프레임  /  " + (m.fps as string) + " fps")
				btnApply.enabled = true
			)
		)
	)
	on btnApply pressed do (
		if AR_motion != undefined then (
			try (
				AR_apply AR_motion spStart.value ckNoMove.checked
				messageBox "키프레임을 넣었어요.\n리그가 이상하게 접히면 Listener(F11)의 뼈 대응 목록을 확인하고 animref_map.txt 로 고쳐 보세요." title:"AnimRef"
			) catch (
				messageBox ("적용하지 못했어요:\n" + (getCurrentException())) title:"AnimRef"
			)
		)
	)
)
createDialog AR_dlg
