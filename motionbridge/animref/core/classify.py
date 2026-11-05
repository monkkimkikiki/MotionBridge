"""CLIP으로 클립을 자동 분류(공격/피격/스킬...)하고 비슷한 모션끼리 묶는다.
임베딩은 .emb.npy 로 캐시되므로 분류 항목을 바꿔도 다시 분류하는 데 몇 초면 된다."""
import numpy as np

from . import Cancelled, media, noop
from .library import OTHER_GROUP, file_of, final_label, game_dir, load_clips, save_clip
from .settings import group_threshold

_CLIP = {}
N_FRAMES = 8


def get_clip(log=noop):
    if not _CLIP:
        log("분류 AI(CLIP)를 불러오는 중... (처음 한 번은 모델을 내려받아요)")
        import clip
        import torch

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        # YOLO-World가 쓰는 것과 같은 폴더를 써서 같은 모델을 두 번 내려받지 않게 한다
        import os

        model, pre = clip.load("ViT-B/32", device=dev, download_root=os.path.expanduser("~/.cache/clip"))
        model.eval()
        _CLIP.update(model=model, pre=pre, dev=dev, torch=torch, clip=clip)
    return _CLIP


def _pad_square(img):
    from PIL import Image

    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2))
    return canvas


def embed_clip(meta, log=noop):
    from PIL import Image

    c = get_clip(log)
    frames = media.sample_frames(file_of(meta, "video"), N_FRAMES, 320)
    if not frames:
        return None
    media.write_image(file_of(meta, "sheet"), media.contact_sheet(frames))
    torch = c["torch"]
    with torch.no_grad():
        x = torch.stack([c["pre"](_pad_square(Image.fromarray(f))) for f in frames]).to(c["dev"])
        fe = c["model"].encode_image(x).float()
        fe = fe / fe.norm(dim=-1, keepdim=True)
    fe = fe.cpu().numpy().astype(np.float32)
    np.save(file_of(meta, "emb"), fe)
    return fe


def text_features(labels, log=noop):
    c = get_clip(log)
    names, prompts, owner = [], [], []
    for li, l in enumerate(labels):
        ps = [p.strip() for p in l.get("prompts", []) if p.strip()] or [f"a character {l['name']}"]
        names.append(l["name"])
        prompts += ps
        owner += [li] * len(ps)
    torch = c["torch"]
    with torch.no_grad():
        tf = c["model"].encode_text(c["clip"].tokenize(prompts, truncate=True).to(c["dev"])).float()
        tf = tf / tf.norm(dim=-1, keepdim=True)
    return names, tf.cpu().numpy(), np.array(owner)


def score(fe, names, tf, owner):
    sims = fe @ tf.T
    per = np.stack([sims[:, owner == li].max(axis=1) for li in range(len(names))], axis=1).mean(axis=0)
    e = np.exp(100.0 * (per - per.max()))
    probs = e / e.sum()
    order = np.argsort(-probs)
    return [(names[i], float(probs[i])) for i in order[:3]]


def _normalize(x):
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-8)


def leader_cluster(X, thr, iters=3):
    """임계값 기반 단순 군집화 (외부 라이브러리 없이)."""
    cents, assign = [], np.zeros(len(X), int)
    for i, x in enumerate(X):
        if cents:
            sims = np.stack(cents) @ x
            j = int(sims.argmax())
            if sims[j] >= thr:
                assign[i] = j
                continue
        cents.append(x.copy())
        assign[i] = len(cents) - 1
    for _ in range(iters):
        used = sorted(set(assign.tolist()))
        C = _normalize(np.stack([X[assign == j].mean(0) for j in used]))
        assign = (X @ C.T).argmax(1)
    return assign


def regroup(clips, embs, s):
    """최종 분류별로 비슷한 모션끼리 묶음 번호를 매긴다."""
    if not clips:
        return
    X = np.stack(embs)
    X = _normalize(X - X.mean(0))  # 같은 게임 안에서의 차이를 강조
    thr, min_size = group_threshold(s), int(s.get("min_group", 3))
    by_label = {}
    for i, m in enumerate(clips):
        by_label.setdefault(final_label(m), []).append(i)
    for label, idxs in by_label.items():
        names = {i: OTHER_GROUP for i in idxs}
        if len(idxs) >= min_size:
            a = leader_cluster(X[idxs], thr)
            sizes = {}
            for c in a:
                sizes[c] = sizes.get(c, 0) + 1
            ranked = [c for c in sorted(sizes, key=lambda c: -sizes[c]) if sizes[c] >= min_size]
            num = {c: k + 1 for k, c in enumerate(ranked)}
            for i, c in zip(idxs, a):
                if c in num:
                    names[i] = f"{label} {num[c]}"
        for i in idxs:
            clips[i]["group"] = names[i]


class Stats:
    def __init__(self):
        self.kept = 0
        self.discarded = 0
        self.engine = "clip"
        self.vlm_done = 0
        self.vlm_failed = 0

    def __int__(self):
        return self.kept


CLIP_MIN_CONF = 0.35  # 기본 분류(CLIP)가 이보다 확신이 낮으면 '미분류'


def run(s, log=noop, progress=noop, cancel=None, f0=0.0, f1=1.0, force=None, discard_ok=None):
    """라이브러리 전체를 분류한다.
    1) CLIP 임베딩(묶음용) 계산  2) 영상 이해 AI(Ollama)로 판정 (없으면 CLIP 분류로 대체)
    3) 비전투 분류 자동 삭제  4) 비슷한 모션 묶기
    force: 다시 판정할 클립 json 경로 집합
    discard_ok: 자동 삭제를 허용할 클립 json 경로 집합 (방금 만든 클립만). None이면 아무것도 지우지 않는다."""
    from . import vlm
    from .library import UNLABELED, delete_clip
    from .settings import discard_names

    st = Stats()
    force = set(force or ())
    discard_ok = set(discard_ok or ())
    span = f1 - f0
    clips = load_clips(game_dir(s))
    if not clips:
        log("분류할 클립이 없습니다.")
        return st

    # 1) CLIP 임베딩
    todo = [m for m in clips if not file_of(m, "emb").exists()]
    if todo:
        log(f"새 클립 {len(todo)}개의 특징을 추출합니다.")
    embs = {}
    for n, m in enumerate(todo):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        progress(f0 + span * 0.3 * n / len(todo), f"클립 특징 추출 중 {n + 1}/{len(todo)}")
        try:
            fe = embed_clip(m, log)
            if fe is not None:
                embs[m["_json"]] = fe
        except Exception as e:
            log(f"  특징 추출 실패({file_of(m, 'video').name}): {e}")

    names, tf, owner = text_features(s["labels"], log)
    feats = {}
    for m in clips:
        fe = embs.get(m["_json"])
        if fe is None and file_of(m, "emb").exists():
            fe = np.load(file_of(m, "emb"))
        if fe is None:
            continue
        feats[m["_json"]] = fe
        top = score(fe, names, tf, owner)
        m["clip_top"] = [[n, round(p, 3)] for n, p in top]

    # 2) 영상 이해 AI
    engine_vlm = s.get("engine", "vlm") == "vlm"
    h = vlm.labels_hash(s) if engine_vlm else None
    use_vlm = engine_vlm
    if use_vlm:
        ok, installed, err = vlm.status(s)
        model = vlm.model_of(s)
        if not ok:
            log("⚠ Ollama가 실행 중이 아니라서 새 클립은 기본 분류(CLIP)를 사용합니다. [설정] → AI 판정을 확인하세요.")
            use_vlm = False
        elif not vlm.has_model(installed, model):
            log(f"⚠ 영상 이해 AI 모델({model})이 없어서 새 클립은 기본 분류를 사용합니다. [설정]에서 [모델 내려받기]를 눌러 주세요.")
            use_vlm = False
    if use_vlm:
        st.engine = "vlm"
        need = [m for m in clips if m["_json"] in feats and (
            m["_json"] in force or (m.get("vlm_hash") != h and m.get("vlm_fail_hash") != h))]
        # 다시 판정 요청 > 방금 만든 클립 > 최근 클립 순서
        need.sort(key=lambda m: m.get("created", ""), reverse=True)  # 최신 먼저
        need.sort(key=lambda m: (m["_json"] not in force, m["_json"] not in discard_ok))  # 안정 정렬
        if need:
            log(f"영상 이해 AI({vlm.model_of(s)})로 {len(need)}개 클립을 판정합니다.")
        for n, m in enumerate(need):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            progress(f0 + span * (0.3 + 0.62 * n / len(need)), f"영상 이해 AI 판정 중 {n + 1}/{len(need)}")
            try:
                label, conf, reason = vlm.judge(s, m, file_of(m, "video"), log)
                m.update(vlm_label=label, vlm_conf=round(conf, 3), vlm_reason=reason, vlm_hash=h)
                m.pop("vlm_fail_hash", None)
                st.vlm_done += 1
            except Cancelled:
                raise
            except vlm.OllamaUnavailable as e:
                log(f"⚠ 영상 이해 AI를 쓸 수 없어 나머지는 기본 분류를 사용합니다: {e}")
                break
            except Exception as e:
                st.vlm_failed += 1
                m["vlm_fail_hash"] = h  # 같은 설정에서는 다시 시도하지 않음 (다시 판정으로 강제 가능)
                log(f"  판정 실패({file_of(m, 'video').name}): {e}")
            save_clip(m)  # 판정 결과를 바로 저장 (취소/오류 시에도 유지)

    # 3) 최종 분류 결정 + 자동 삭제
    progress(f0 + span * 0.94, "분류 정리 중...")
    discard = discard_names(s) if s.get("auto_discard", True) else set()
    min_conf = float(s.get("discard_min_conf", 0.6))
    kept, kept_emb = [], []
    for m in clips:
        if m["_json"] not in feats:
            continue
        if engine_vlm and m.get("vlm_hash") == h and m.get("vlm_label"):
            m["ai_label"], m["ai_conf"] = m["vlm_label"], m["vlm_conf"]
            m["ai_reason"], m["ai_engine"] = m.get("vlm_reason"), "vlm"
            m["ai_top"] = [[m["vlm_label"], m["vlm_conf"]]]
        else:
            top = m.get("clip_top") or [[UNLABELED, 0.0]]
            m["ai_label"] = top[0][0] if top[0][1] >= CLIP_MIN_CONF else UNLABELED
            m["ai_conf"], m["ai_top"] = top[0][1], top
            m["ai_reason"], m["ai_engine"] = None, "clip"
        # 사용자가 직접 고른 분류(label_manual)가 있으면 절대 지우지 않는다
        if (m["_json"] in discard_ok and m["ai_engine"] == "vlm" and not m.get("label_manual")
                and m["ai_label"] in discard and float(m["ai_conf"] or 0) >= min_conf):
            if delete_clip(m):
                st.discarded += 1
                continue
        kept.append(m)
        v = feats[m["_json"]].mean(0)
        kept_emb.append(v / (np.linalg.norm(v) + 1e-8))
    regroup(kept, kept_emb, s)
    for m in kept:
        save_clip(m)
    st.kept = len(kept)
    if st.discarded:
        log(f"비전투 장면으로 판정된 클립 {st.discarded}개를 자동 삭제했어요.")
    progress(f1, f"분류 완료: {len(kept)}개")
    log(f"AI 분류 완료: {len(kept)}개 클립 (엔진: {'영상 이해 AI' if st.engine == 'vlm' else '기본 CLIP'})")
    return st
