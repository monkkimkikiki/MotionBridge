"""같은 이름 파일 처리: 덮어쓰기 / 이전 파일 보관 / 다른 이름 — 단위, 추출 이동, 내보내기."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import extractor, layout, safesave as S  # noqa: E402
from motionbridge.core.pipeline import ExportOptions, Project  # noqa: E402
from test_pipeline import make_inputs  # noqa: E402


def check(c, m):
    if not c:
        raise AssertionError(m)
    print("  ok:", m)


def w(p, t):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(t)


def r(p):
    return open(p, encoding="utf-8").read()


with tempfile.TemporaryDirectory() as d:
    f = os.path.join(d, "a", "Walk.psa")
    # 없으면 그대로
    check(S.claim(f, S.KEEP_OLD) == (f, None), "없으면 그대로")
    w(f, "old")
    # 덮어쓰기
    check(S.claim(f, S.OVERWRITE) == (f, None) and r(f) == "old", "덮어쓰기는 손대지 않음(쓰는 쪽이 덮어씀)")
    # 다른 이름
    p2, bk = S.claim(f, S.NEW_NAME)
    check(p2.endswith("Walk_2.psa") and bk is None and r(f) == "old", "새 파일은 _2: %s" % os.path.basename(p2))
    w(p2, "x")
    check(S.claim(f, S.NEW_NAME)[0].endswith("Walk_3.psa"), "_3")
    # 이전 파일 보관
    p3, bk = S.claim(f, S.KEEP_OLD, now=0)
    check(p3 == f and bk and "_old_" in os.path.basename(bk) and r(bk) == "old" and not os.path.exists(f), "이전 파일은 _old_ 로 보관: %s" % os.path.basename(bk))
    w(f, "new")
    _, bk2 = S.claim(f, S.KEEP_OLD, now=0)    # 같은 초에 또 해도 겹치지 않는다
    check(bk2 != bk and r(bk2) == "new" and r(bk) == "old", "같은 시각이어도 보관 이름이 겹치지 않음")
    check(S.norm("엉뚱") == S.OVERWRITE and S.norm(None) == S.OVERWRITE, "모르는 값은 기본(덮어쓰기: _old_ 파일을 만들지 않음)")
    check(S.choice(S.KEEP_OLD) == S.OVERWRITE and S.choice(S.NEW_NAME) == S.NEW_NAME and S.choice("keep_old") == S.OVERWRITE, "예전에 저장된 keep_old 는 화면에서 덮어쓰기로")
    check(S.KEEP_OLD not in S.CHOICES, "이전 파일 보관은 화면 선택지에서 뺌")

with tempfile.TemporaryDirectory() as d:
    # 추출 이동(_move_into): 정책별
    for pol, want in ((S.OVERWRITE, "overwrite"), (S.KEEP_OLD, "keep"), (S.NEW_NAME, "new")):
        out = os.path.join(d, pol)
        lay = layout.Layout(out, "G", force_char="Boss", on_exist=pol)
        dest = lay.anim_dest("x")
        w(os.path.join(dest, "A_Hit.psa"), "OLD")
        src = os.path.join(d, "stage_%s.psa" % pol)
        os.rename(w(src, "NEW") or src, src) if False else None
        w(os.path.join(d, "s_%s" % pol, "A_Hit.psa"), "NEW")
        got = extractor._move_into(os.path.join(d, "s_%s" % pol, "A_Hit.psa"), dest, set(), lay)
        names = sorted(os.listdir(dest))
        if want == "overwrite":
            check(names == ["A_Hit.psa"] and r(got) == "NEW" and not lay.backups, "추출 덮어쓰기: %s" % names)
        elif want == "keep":
            check(len(names) == 2 and r(got) == "NEW" and os.path.basename(got) == "A_Hit.psa" and len(lay.backups) == 1 and r(lay.backups[0]) == "OLD", "추출 이전 보관: %s" % names)
        else:
            check(names == ["A_Hit.psa", "A_Hit_2.psa"] and r(got) == "NEW" and got.endswith("A_Hit_2.psa") and r(os.path.join(dest, "A_Hit.psa")) == "OLD", "추출 새 이름: %s" % names)

with tempfile.TemporaryDirectory() as d:
    # 내보내기
    src, _s2, tgt, _, _ = make_inputs(d)
    pr = Project()
    pr.load_source(src)
    pr.load_target(tgt)
    pr.automap()
    o = os.path.join(d, "o")

    def exp(pol, name=""):
        return pr.export(ExportOptions(out_dir=o, presets=["blender"], formats=["bvh"], on_exist=pol, name=name))[0]

    f1 = exp(S.OVERWRITE)
    sig1 = os.path.getsize(f1[0])
    open(f1[0], "w").write("MARK")
    f2 = exp(S.OVERWRITE)
    check(f2 == f1 and os.path.getsize(f2[0]) == sig1 and not pr.backups, "내보내기 덮어쓰기: 같은 파일이 새로 써짐")
    open(f2[0], "w").write("MARK")
    f3 = exp(S.KEEP_OLD)
    kept = [n for n in os.listdir(o) if "_old_" in n]
    check(f3 == f1 and len(kept) == 1 and r(os.path.join(o, kept[0])) == "MARK" and os.path.getsize(f3[0]) == sig1 and len(pr.backups) == 1,
          "내보내기 이전 파일 보관: %s" % sorted(os.listdir(o)))
    check(not any(n.startswith("mb_exp_") for n in os.listdir(o)), "임시 폴더는 지워짐")
    f4 = exp(S.NEW_NAME)
    check(f4 != f1 and os.path.basename(f4[0]).startswith(os.path.basename(f1[0]).split("_blender")[0] + "_2") and os.path.exists(f1[0]), "내보내기 새 이름: %s" % os.path.basename(f4[0]))
    # 다른 이름으로 저장
    f5 = exp(S.KEEP_OLD, name="내가정한이름")
    check(os.path.basename(f5[0]).startswith("내가정한이름_") and os.path.exists(f1[0]), "다른 이름으로 저장: %s" % os.path.basename(f5[0]))
print("test_safesave ALL OK")
