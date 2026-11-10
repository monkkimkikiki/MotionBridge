"""처음 쓰는 사람을 위한 '지금 뭘 하면 되는지' 안내 (Qt 없음).

작업은 네 단계다: ① 모션 열기 → ② 내 모델 열기 → ③ 확인하고 다듬기 → ④ 내보내기. 지금 상태에서 어느 단계인지, 쉬운 말로 무엇을 하면 되는지,
눌러서 바로 갈 수 있는 동작이 무엇인지 계산한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

STEPS: List[Tuple[str, str]] = [("motion", "① 모션 열기"), ("model", "② 내 모델 열기"), ("check", "③ 확인하고 다듬기"), ("export", "④ 내보내기")]


@dataclass
class Guide:
    states: List[Tuple[str, str, str]]   # (키, 이름, "done" | "now" | "todo")
    hint: str                            # 지금 할 일 (쉬운 말)
    action: str                          # 버튼 동작 키: open_source | open_target | goto_mapping | goto_export | open_output | ""
    button: str                          # 버튼 글자 ("" 이면 버튼 없음)
    step: str                            # 지금 단계 키


def compute(has_src: bool, has_tgt: bool, ready: bool, n_issues: int = 0, exported: bool = False, same_rig: bool = False) -> Guide:
    """has_src/has_tgt: 모션·모델을 열었는가 · ready: 본이 연결되어 리타겟 결과가 나오는가 · n_issues: 자동 검수가 찾은 문제 수 · exported: 이번에 내보냈는가."""
    if not has_src:
        step, hint, action, button = "motion", "먼저 게임에서 뽑은 모션 파일(FBX · GLB · BVH)을 여세요. 파일을 이 창에 끌어다 놓아도 됩니다. 아직 파일이 없으면 '추출' 탭에서 게임 파일을 뽑을 수 있어요.", "open_source", "모션 파일 열기…"
    elif not has_tgt:
        step, hint, action, button = "model", "모션을 입힐 내 캐릭터 모델(FBX · GLB)을 여세요. 모델 없이 모션만 내보내려면 아래 ④ 내보내기에서 '소스 그대로'를 고르면 됩니다.", "open_target", "내 모델 열기…"
    elif not ready:
        step, hint, action, button = "check", ("두 파일의 뼈 이름이 달라서 자동으로 짝지어지지 않았어요. '파일·매핑' 탭에서 같은 부위끼리(골반·팔·다리…) 맞춰 주세요. "
                                            "사람 모양이 아닌 캐릭터(몬스터)는 같은 스켈레톤이어야 그대로 적용됩니다."), "goto_mapping", "본 연결 화면 열기"
    elif not exported:
        extra = " 자동 검수가 확인해 볼 곳 %d군데를 찾았어요(‘검수’ 탭)." % n_issues if n_issues else ""
        step, hint, action, button = "check", ("화면에서 모션이 자연스러운지 재생해 보세요.%s 발이 미끄러지거나 방향·크기가 이상하면 '리타겟' 탭에서 고치고, 팔·다리처럼 한 군데만 틀리면 "
                                            "🦴 본 편집으로 직접 끌어 고치거나 '보정 레이어'로 키를 찍어 고치세요. 괜찮으면 내보내기로 가요." % extra), "goto_export", "다 됐어요 → 내보내기"
    else:
        step, hint, action, button = "export", "내보내기가 끝났어요! 출력 폴더의 ‘캐릭터 / Animations / 클립 이름’ 폴더에 파일들과 '사용법.txt'가 있습니다 (3ds Max · Blender 파일이 한곳에 들어 있어요).", "open_output", "출력 폴더 열기"
    idx = [k for k, _ in STEPS].index(step)
    states = [(k, n, "done" if i < idx or (step == "export" and k == "export") else ("now" if i == idx else "todo")) for i, (k, n) in enumerate(STEPS)]
    return Guide(states, hint, action, button, step)
