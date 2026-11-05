"""전체 흐름: 영상 -> 후보 구간(움직임) -> 전투/연출 단서로 거르기 -> 클립 저장
-> AI 판정(영상 이해 AI 또는 CLIP) + 비전투 자동 삭제 -> 원본 정리."""
from pathlib import Path

from . import Cancelled, bookmarks, classify, cleanup, media, noop
from .segment import cinematic_segments, merge_padded, segment_series, track_video, write_clips
from .signals import (audio_onsets, energy_peaks, hit_windows, is_combat_candidate, merge_for_full,
                      merge_hits, segment_hints)

RECORDED, DROPPED = "recorded", "dropped"


class Result(list):
    """새 클립 json 경로 목록 + 정리한 원본 / 자동 삭제한 클립 수."""
    cleaned = ()
    discarded = 0


def policy_for(s, origin):
    key = "source_policy_recorded" if origin == RECORDED else "source_policy_dropped"
    return s.get(key, cleanup.KEEP)


def find_segments(p, s, log=noop, progress=noop, cancel=None, f0=0.0, f1=1.0, want_weak=False):
    """한 영상에서 저장할 구간을 찾는다. (보기 종류별 구간 dict, 영상 정보[, 약한 후보])"""
    series, fps, info, cues = track_video(p, s, log, progress, cancel, f0, f1)
    segs, thr = segment_series(series, s, fps)
    progress(f1, "효과음 분석 중...")
    onsets, strengths = audio_onsets(p, with_strength=True) or (None, None)
    if onsets is None:
        log("  (오디오가 없어 효과음 필터는 건너뜀)")

    weak = []
    combat = bool(s.get("combat_only", True)) and not s.get("include_move", False)

    def keep(lst):
        kept = []
        for sg in lst:
            sg["hints"] = segment_hints(sg["t0"], sg["t1"], cues, onsets)
            if not combat or is_combat_candidate(sg["hints"]):
                kept.append(sg)
            else:
                weak.append(sg)  # 효과음/번쩍임이 없어 클립은 안 만들지만 책갈피로는 남김
        return kept

    n0 = len(segs)
    segs = keep(segs)
    if combat and n0 != len(segs):
        log(f"  효과음·번쩍임이 없는 구간 {n0 - len(segs)}개 제외 (이동/카메라 움직임 등)")
    cine = keep(cinematic_segments(cues, info, max_s=float(s.get("max_s", 8.0))))
    log(f"  캐릭터 {len(series)}개 추적 · 액션 후보 {len(segs)}개" + (f" · 연출 후보 {len(cine)}개" if cine else ""))

    pre, post, max_s = float(s.get("pre_pad", 1.0)), float(s.get("post_pad", 1.2)), float(s.get("max_s", 8.0))
    out = {}
    if s.get("save_full", True):
        full = merge_for_full(segs + cine, max_s=max_s)
        full = merge_padded(full, pre, post, max_s)
        for sg in full:  # 합친 구간의 단서 다시 계산
            sg["hints"] = segment_hints(sg["t0"], sg["t1"], cues, onsets)
        out["full"] = full
    if s.get("save_crop", False):
        crop = []
        for tid in sorted({sg["tid"] for sg in segs}):  # 캐릭터별로 겹치는 구간 합치기
            crop += merge_padded([sg for sg in segs if sg["tid"] == tid], pre, post, max_s)
        out["crop"] = crop
    if not out:
        out["full"] = merge_for_full(segs + cine)

    # 한 타씩 나누기
    mode = s.get("split_mode", "both")
    if mode in ("hit", "both"):
        base = out.get("full") or merge_padded(merge_for_full(segs + cine, max_s=max_s), pre, post, max_s)
        c_t, c_flash = cues[0], cues[1]
        flash_t = c_t[c_flash] if len(c_t) else []
        hits_out, keep_full = [], []
        for sg in base:
            a, b = sg["t0"] - 0.3, sg["t1"] + 0.3
            ts, ws = [], []
            if onsets is not None:
                sel = (onsets >= a) & (onsets <= b)
                ts += list(onsets[sel])
                ws += list(strengths[sel])
            ts += [float(x) for x in flash_t if a <= x <= b]
            ws += [0.5] * (len(ts) - len(ws))
            hs = merge_hits(ts, ws, float(s.get("hit_min_gap", 0.3)))
            if not hs and sg.get("tid", -1) >= 0:
                hs = energy_peaks(series.get(sg["tid"]), sg["t0"], sg["t1"], thr)
            if len(hs) >= (2 if mode == "both" else 1):  # '둘 다'일 때 한 타짜리는 긴 클립과 같으므로 생략
                for w in hit_windows(sg, hs, float(s.get("hit_pre", 0.8)), float(s.get("hit_post", 0.8)),
                                     float(s.get("hit_min_len", 1.0)), info.duration):
                    w["hints"] = segment_hints(w["t0"], w["t1"], cues, onsets)
                    hits_out.append(w)
            elif mode == "hit":
                keep_full.append(sg)  # 타격을 못 찾은 구간(연출 등)은 긴 클립으로 남김
        out["hit"] = hits_out
        if hits_out:
            log(f"  한 타씩 나눈 클립 후보 {len(hits_out)}개")
        if mode == "hit":
            out["full"] = keep_full
            if not keep_full:
                out.pop("full", None)
    if want_weak:
        return out, info, merge_padded(merge_for_full(weak, max_s=max_s), pre, post, max_s)
    return out, info


def process_videos(paths, s, log=noop, progress=noop, cancel=None, origins=None):
    paths = [str(p) for p in paths]
    origins = origins or {}
    share = 0.7 if s.get("auto_classify", True) else 1.0
    new = Result()
    per_source = []
    for k, p in enumerate(paths):
        a, b = share * k / len(paths), share * (k + 1) / len(paths)
        log(f"[{k + 1}/{len(paths)}] {Path(p).name} 분석 시작")
        by_view, info, weak = find_segments(p, s, log, progress, cancel, a, a + (b - a) * 0.75, want_weak=True)
        if policy_for(s, origins.get(p, DROPPED)) == cleanup.KEEP:  # 원본이 남는 영상만 타임라인 책갈피
            try:
                strong = by_view.get("full") or by_view.get("crop") or []
                bookmarks.add_from_segments(s, p, strong, weak, log, progress, cancel,
                                            a + (b - a) * 0.75, a + (b - a) * 0.8)
            except Cancelled:
                raise
            except Exception as e:
                log(f"  책갈피 저장 실패: {e}")
        clips = []
        views = list(by_view.items())
        for vi, (view, segs) in enumerate(views):
            va = a + (b - a) * (0.8 + 0.2 * vi / len(views))
            vb = a + (b - a) * (0.8 + 0.2 * (vi + 1) / len(views))
            clips += write_clips(p, segs, info, s, view, log, progress, cancel, va, vb)
        log(f"  클립 {len(clips)}개 저장")
        new += clips
        per_source.append((p, clips))

    if s.get("auto_classify", True) and new:
        stats = classify.run(s, log, progress, cancel, share, 0.98, discard_ok=set(new))
        new.discarded = getattr(stats, "discarded", 0)
        alive = [j for j in new if Path(j).exists()]
        new[:] = alive

    # 원본 정리는 모든 처리가 성공한 뒤에만 (중간에 취소/오류면 여기까지 오지 않음)
    for p, clips in per_source:
        policy = policy_for(s, origins.get(p, DROPPED))
        if policy != cleanup.KEEP and cleanup.apply(p, policy, clips, log):
            new.cleaned = tuple(new.cleaned) + ((p, policy),)
    progress(1.0, f"완료! 새 클립 {len(new)}개")
    return new


def process_recording(path, s, log=noop, progress=noop, cancel=None):
    progress(-1, "OBS가 녹화 파일을 마무리하는 중...")
    media.wait_until_stable(path)
    return process_videos([path], s, log, progress, cancel, origins={str(path): RECORDED})
