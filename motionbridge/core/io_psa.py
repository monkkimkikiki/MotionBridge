"""ActorX PSA(애니메이션) 읽기/쓰기. FModel/CUE4Parse 가 내보내는 형식.

PSA 한 파일에 스켈레톤(본 이름, 부모, 기준 포즈)과 애니메이션 키가 모두 들어 있어서 PSK 없이 소스 모션으로 쓸 수 있다.

좌표계: 언리얼은 왼손 좌표계(X 앞, Y 오른쪽, Z 위). 읽을 때 Y 를 뒤집어서 오른손 좌표계(Z-up)로 바꾼다.
  위치 (x,y,z) -> (x,-y,z) / 쿼터니언 (x,y,z,w) -> (-x, y, -z, w)   [ S·R·S, S=diag(1,-1,1) ]

쿼터니언 켤레(conjugate) 관례: ActorX 는 본마다 회전을 뒤집어 저장하는 경우가 있어서(도구/버전마다 다름)
  루트/비루트 각각 켤레를 적용할지 네 가지 조합을 모두 만들어 보고, 사람 모양(좌우 대칭, 위/아래 일관)이 가장 잘 나오는 것을 고른다.
  (이 방식은 '일반 PSA' 용 대비책이다.)

CUE4Parse(FModel)가 쓰는 PSA 는 관례가 고정돼 있다(ActorXAnim.cs 확인, 실제 파일로 검증):
  - BONENAMES 의 기준 포즈: 언리얼 원본 값(왼손 좌표, 변환 없음)
  - ANIMKEYS: 이미 Y 반전된 위치, 쿼터니언은 (x,-y,z,w) 이고 루트만 w 부호가 뒤집힘
  그래서 키 위치는 그대로 쓰고, 키 회전은 비루트는 켤레(conjugate), 루트는 그대로(부호만 다름) 쓰면 오른손 좌표 값이 된다.
"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

import numpy as np

from . import quat as Q
from .mapping import auto_map, detect_frame
from .model import Animation, Model, Skeleton, fk

_HDR = struct.Struct("<20siii")  # name, type_flag, data_size, data_count


class PSAError(Exception):
    pass


def _chunks(buf: bytes):
    pos = 0
    while pos + _HDR.size <= len(buf):
        name, flag, size, count = _HDR.unpack_from(buf, pos)
        name = name.split(b"\0")[0].decode("ascii", "replace")
        pos += _HDR.size
        body = buf[pos:pos + size * count]
        pos += size * count
        yield name, size, count, body


def parse_psa(path: str) -> dict:
    """원본 값 그대로(언리얼 좌표) 읽는다: bones[(name,parent,pos,quat)], anims[(name,fps,frames,start)], keys (N,7)."""
    with open(path, "rb") as f:
        buf = f.read()
    if not buf.startswith(b"ANIMHEAD"):
        raise PSAError("PSA 파일이 아닙니다 (ANIMHEAD 없음): " + path)
    bones, infos, keys = [], [], None
    for name, size, count, body in _chunks(buf):
        if name == "BONENAMES":
            if size != 120:
                raise PSAError("BONENAMES 레코드 크기가 120 이 아닙니다 (%d)" % size)
            for i in range(count):
                r = body[i * 120:(i + 1) * 120]
                nm = r[:64].split(b"\0")[0].decode("utf-8", "replace")
                nchild, parent = struct.unpack_from("<ii", r, 68)
                qx, qy, qz, qw, px, py, pz = struct.unpack_from("<7f", r, 76)
                bones.append((nm, parent, np.array([px, py, pz]), np.array([qw, qx, qy, qz])))
        elif name == "ANIMINFO":
            if size != 168:
                raise PSAError("ANIMINFO 레코드 크기가 168 이 아닙니다 (%d)" % size)
            for i in range(count):
                r = body[i * 168:(i + 1) * 168]
                nm = r[:64].split(b"\0")[0].decode("utf-8", "replace")
                total_bones = struct.unpack_from("<i", r, 128)[0]
                track_time, rate = struct.unpack_from("<ff", r, 148)
                start_bone, first, nframes = struct.unpack_from("<iii", r, 156)
                infos.append((nm, float(rate), int(nframes), int(first), total_bones))
        elif name == "ANIMKEYS":
            if size != 32:
                raise PSAError("ANIMKEYS 레코드 크기가 32 가 아닙니다 (%d)" % size)
            arr = np.frombuffer(body, dtype="<f4").reshape(count, 8)
            # px,py,pz, qx,qy,qz,qw, time
            keys = np.concatenate([arr[:, 0:3], arr[:, 6:7], arr[:, 3:6]], axis=1).astype(float)  # px py pz qw qx qy qz
    if not bones or not infos or keys is None:
        raise PSAError("PSA 에 본/애니메이션/키 중 일부가 없습니다.")
    return {"bones": bones, "infos": infos, "keys": keys}


def _to_right_handed(pos: np.ndarray, q: np.ndarray):
    """왼손(UE) -> 오른손: Y 반전. q 는 (...,4) wxyz."""
    p = pos.copy()
    p[..., 1] *= -1
    r = q.copy()
    r[..., 1] *= -1  # x
    r[..., 3] *= -1  # z
    return p, r


def _build(raw: dict, conj_root: bool, conj_other: bool):
    bones = raw["bones"]
    J = len(bones)
    names = [b[0] for b in bones]
    parents = [b[1] if b[1] >= 0 and b[1] != i else -1 for i, b in enumerate(bones)]
    # ActorX 는 루트의 부모 인덱스를 0(자기 자신)으로 쓰기도 한다
    parents[0] = -1
    conj = np.array([conj_root if p < 0 else conj_other for p in parents])

    def fix_q(q):
        q = q.copy()
        m = np.broadcast_to(conj, q.shape[:-1])
        q[m] = Q.conj(q[m])
        return q

    off = np.stack([b[2] for b in bones])
    rq = fix_q(np.stack([b[3] for b in bones]))
    off, rq = _to_right_handed(off, rq)
    rq = Q.normalize(rq)
    skel = Skeleton(names, parents, off, rq, name=names[0])
    anims: List[Animation] = []
    keys = raw["keys"]
    for nm, rate, nframes, first, _tb in raw["infos"]:
        k = keys[first * J:(first + nframes) * J].reshape(nframes, J, 7)
        pos = k[..., 0:3]
        q = fix_q(k[..., 3:7])
        pos, q = _to_right_handed(pos, q)
        q = Q.make_continuous(Q.normalize(q))
        anims.append(Animation(nm, round(rate, 3) or 30.0, q, pos))
    return skel, anims


def _score(skel: Skeleton) -> float:
    """사람 모양인 정도(0~1). 좌우 대칭 + 위/아래 일관성 + 팔다리 길이 비율. 높을수록 좋음."""
    slots = auto_map(skel)
    need = ["Hips", "Head", "L_UpLeg", "R_UpLeg", "L_Foot", "R_Foot", "L_Hand", "R_Hand"]
    if any(n not in slots for n in need):
        return 0.0
    _, wp = skel.bind_world()
    P = {n: wp[slots[n]] for n in need}
    up = P["Head"] - P["Hips"]
    lat = P["L_UpLeg"] - P["R_UpLeg"]
    if np.linalg.norm(up) < 1e-9 or np.linalg.norm(lat) < 1e-9:
        return 0.0
    u, l = up / np.linalg.norm(up), lat / np.linalg.norm(lat)
    s = 1.0 - abs(float(u @ l))  # 위축과 좌우축이 직교해야 한다
    # 발은 골반보다 아래, 손 좌우는 좌우축으로 대칭 (같은 높이, 같은 거리)
    s *= 1.0 if float((P["L_Foot"] - P["Hips"]) @ u) < 0 and float((P["R_Foot"] - P["Hips"]) @ u) < 0 else 0.2
    hl, hr = P["L_Hand"] - P["Hips"], P["R_Hand"] - P["Hips"]
    s *= 1.0 / (1.0 + abs(float(hl @ u) - float(hr @ u)) / max(np.linalg.norm(up), 1e-9))
    s *= 1.0 / (1.0 + abs(abs(float(hl @ l)) - abs(float(hr @ l))) / max(np.linalg.norm(up), 1e-9))
    # 왼손/왼발은 같은 쪽(좌우축 +)에, 오른손/오른발은 반대쪽에 있어야 한다
    fl, fr = P["L_Foot"] - P["Hips"], P["R_Foot"] - P["Hips"]
    side_ok = (float(hl @ l) > 0) and (float(hr @ l) < 0)
    s *= 1.0 if side_ok else 0.2
    s *= 1.0 / (1.0 + abs(float(fl @ u) - float(fr @ u)) / max(np.linalg.norm(up), 1e-9))
    # 루트 켤레 여부는 몸 전체의 강체 회전만 바꿔서 모양 점수로는 구분되지 않는다.
    # UE 캐릭터는 보통 Z-up 으로 서 있으므로 '위'가 +Z 에 가까운 쪽을 약간 선호한다.
    return float(s * (0.9 + 0.1 * max(0.0, float(u[2]))))


def _build_cue4parse(raw: dict):
    """CUE4Parse 가 쓴 PSA 를 오른손 좌표 값으로 복원한다."""
    bones = raw["bones"]
    J = len(bones)
    names = [b[0] for b in bones]
    parents = [b[1] if b[1] >= 0 and b[1] != i else -1 for i, b in enumerate(bones)]
    parents[0] = -1
    is_root = np.array([p < 0 for p in parents])
    off = np.stack([b[2] for b in bones])
    rq = np.stack([b[3] for b in bones])
    off, rq = _to_right_handed(off, rq)  # 기준 포즈는 원본(왼손) 값이다
    skel = Skeleton(names, parents, off, Q.normalize(rq), name=names[0])
    anims: List[Animation] = []
    keys = raw["keys"]
    for nm, rate, nframes, first, _tb in raw["infos"]:
        k = keys[first * J:(first + nframes) * J].reshape(nframes, J, 7)
        pos = k[..., 0:3].copy()  # 이미 오른손 좌표
        q = Q.normalize(k[..., 3:7].copy())
        qc = Q.conj(q)
        q = np.where(is_root[None, :, None], q, qc)
        q = Q.make_continuous(q)
        anims.append(Animation(nm, round(rate, 3) or 30.0, q, pos))
    return skel, anims


def read_psa(path: str, conj: Optional[Tuple[bool, bool]] = None, fmt: str = "cue4parse") -> Model:
    """PSA 읽기.

    기본(fmt="cue4parse"): FModel/CUE4Parse 형식으로 해석. 결과가 사람 모양이 아니면 일반 방식으로 다시 시도한다.
    fmt="generic": 4가지 켤레 조합을 시험해 가장 사람 같은 것을 고른다. conj=(루트, 비루트) 를 주면 그 조합(일반 방식)을 쓴다.
    """
    raw = parse_psa(path)
    notes: List[str] = []
    if conj is None and fmt == "cue4parse":
        skel, anims = _build_cue4parse(raw)
        sc = _score(skel)
        if sc >= 0.5:
            notes.append("PSA: CUE4Parse 형식으로 해석 (사람 모양 점수 %.2f)" % sc)
            return Model(path=path, skeleton=skel, animations=anims, meshes=[], notes=notes)
        notes.append("⚠ CUE4Parse 형식으로는 사람 모양이 아니어서(점수 %.2f) 일반 방식으로 다시 해석합니다." % sc)
    if conj is None:
        best, best_s = None, -1.0
        scores = []
        for cr in (False, True):
            for co in (False, True):
                sk, _ = _build(raw, cr, co)
                s = _score(sk)
                scores.append(((cr, co), s))
                if s > best_s + 1e-9:
                    best, best_s = (cr, co), s
        conj = best
        notes.append("PSA 회전 관례 자동 선택: root켤레=%s 비루트켤레=%s (점수 %.2f; 후보: %s)" % (
            conj[0], conj[1], best_s, ", ".join("%s%s=%.2f" % ("R" if a else "r", "O" if b else "o", s) for (a, b), s in scores)))
        if best_s < 0.5:
            notes.append("⚠ 사람 모양으로 인식되지 않았습니다. 본 이름이 특이하거나 회전 관례가 다를 수 있습니다.")
    skel, anims = _build(raw, *conj)
    return Model(path=path, skeleton=skel, animations=anims, meshes=[], notes=notes)


def read_psa_exact(path: str) -> Model:
    """사람 모양 점수와 상관없이 CUE4Parse 배치로만 읽는다(몬스터처럼 사람 모양이 아닌 PSA 용).

    write_psa(path, skel, anims)(기본 배치)와 짝이라 읽고-고치고-다시 쓰는 용도에 맞다.
    """
    raw = parse_psa(path)
    skel, anims = _build_cue4parse(raw)
    return Model(path=path, skeleton=skel, animations=anims, meshes=[], notes=["PSA: CUE4Parse 배치로 읽음"])


# ----------------------------------------------------------------------------- 쓰기 (테스트/내보내기용)
def write_psa(path: str, skel: Skeleton, anims: List[Animation], conj: Optional[Tuple[bool, bool]] = None, to_left_handed: bool = True) -> None:
    """Model(오른손) -> PSA. conj 를 안 주면 CUE4Parse 와 같은 배치로, 주면 일반 방식(read_psa 의 conj 와 짝)으로 쓴다."""
    if conj is None:
        return _write_psa_cue4parse(path, skel, anims)
    J = skel.n
    par = np.array([max(p, 0) for p in skel.parents])
    is_root = np.array([p < 0 for p in skel.parents])
    cj = np.where(is_root, conj[0], conj[1])

    def enc_q(q):
        q = np.asarray(q, float).copy()
        if to_left_handed:
            q[..., 1] *= -1
            q[..., 3] *= -1
        m = np.broadcast_to(cj, q.shape[:-1])
        q[m] = Q.conj(q[m])
        return q

    def enc_p(p):
        p = np.asarray(p, float).copy()
        if to_left_handed:
            p[..., 1] *= -1
        return p

    out = bytearray()

    def chunk(name, size, count, body=b""):
        out.extend(_HDR.pack(name.encode("ascii"), 1999801, size, count))
        out.extend(body)

    chunk("ANIMHEAD", 0, 0)
    nchild = [len(skel.children[j]) for j in range(J)]
    body = bytearray()
    rq = enc_q(skel.rest_rot)
    rp = enc_p(skel.offsets)
    for j in range(J):
        r = bytearray(120)
        r[:64] = skel.names[j].encode("utf-8")[:63].ljust(64, b"\0")
        struct.pack_into("<Iii", r, 64, 0, nchild[j], int(par[j]))
        w, x, y, z = rq[j]
        struct.pack_into("<7f", r, 76, x, y, z, w, *rp[j])
        body += r
    chunk("BONENAMES", 120, J, bytes(body))
    info = bytearray()
    first = 0
    keys = bytearray()
    for a in anims:
        T = a.n_frames
        r = bytearray(168)
        r[:64] = a.name.encode("utf-8")[:63].ljust(64, b"\0")
        struct.pack_into("<i", r, 128, J)
        struct.pack_into("<ff", r, 148, T / a.fps, a.fps)
        struct.pack_into("<iii", r, 156, 0, first, T)
        info += r
        q = enc_q(a.rot)
        p = enc_p(a.pos)
        k = np.zeros((T, J, 8), dtype="<f4")
        k[..., 0:3] = p
        k[..., 3] = q[..., 1]
        k[..., 4] = q[..., 2]
        k[..., 5] = q[..., 3]
        k[..., 6] = q[..., 0]
        k[..., 7] = np.arange(T)[:, None] / a.fps
        keys += k.tobytes()
        first += T
    chunk("ANIMINFO", 168, len(anims), bytes(info))
    chunk("ANIMKEYS", 32, first * J, bytes(keys))
    with open(path, "wb") as f:
        f.write(out)


def _write_psa_cue4parse(path: str, skel: Skeleton, anims: List[Animation]) -> None:
    """CUE4Parse ActorXAnim.cs 와 같은 값 배치로 쓴다(시험·내보내기용)."""
    J = skel.n
    par = np.array([max(p, 0) for p in skel.parents])
    is_root = np.array([p < 0 for p in skel.parents])
    out = bytearray()

    def chunk(name, size, count, body=b""):
        out.extend(_HDR.pack(name.encode("ascii"), 20100422 if name == "ANIMHEAD" else 0, size, count))
        out.extend(body)

    chunk("ANIMHEAD", 0, 0)
    nchild = [len(skel.children[j]) for j in range(J)]
    rp, rq = _to_right_handed(skel.offsets.copy(), skel.rest_rot.copy())  # 오른손 -> 원본(왼손), 같은 변환이 자기 역변환
    body = bytearray()
    for j in range(J):
        r = bytearray(120)
        r[:64] = skel.names[j].encode("utf-8")[:63].ljust(64, b"\0")
        struct.pack_into("<Iii", r, 64, 0, nchild[j], int(par[j]))
        w, x, y, z = rq[j]
        struct.pack_into("<7f", r, 76, x, y, z, w, *rp[j])
        body += r
    chunk("BONENAMES", 120, J, bytes(body))
    info = bytearray()
    keys = bytearray()
    first = 0
    for a in anims:
        T = a.n_frames
        r = bytearray(168)
        r[:64] = a.name.encode("utf-8")[:63].ljust(64, b"\0")
        r[64:68] = b"None"
        struct.pack_into("<i", r, 128, J)
        struct.pack_into("<ff", r, 148, float(T), a.fps)
        struct.pack_into("<iii", r, 156, 0, first, T)
        info += r
        q = np.asarray(a.rot, float)
        qf = np.where(is_root[None, :, None], q, Q.conj(q))  # 파일 값 = 비루트 켤레, 루트 그대로
        k = np.zeros((T, J, 8), dtype="<f4")
        k[..., 0:3] = a.pos
        k[..., 3] = qf[..., 1]
        k[..., 4] = qf[..., 2]
        k[..., 5] = qf[..., 3]
        k[..., 6] = qf[..., 0]
        k[..., 7] = 1.0
        keys += k.tobytes()
        first += T
    chunk("ANIMINFO", 168, len(anims), bytes(info))
    chunk("ANIMKEYS", 32, first * J, bytes(keys))
    with open(path, "wb") as f:
        f.write(out)
