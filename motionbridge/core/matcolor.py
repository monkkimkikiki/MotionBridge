"""텍스처가 없는 메시에서도 머티리얼(재질)을 눈으로 구분할 수 있게, 이름으로 정해지는 고정 색 (Qt 없이)."""
from __future__ import annotations

import colorsys
import zlib
from typing import Optional, Sequence, Tuple


def material_color(name: str, index: Optional[int] = None) -> Tuple[float, float, float]:
    """머티리얼 구분 색. index(메시/재질 번호)를 주면 번호 순서대로 서로 멀리 떨어진 색을 주므로 겹치지 않는다(황금각 색조).
    index 가 없으면 이름으로 정해 항상 같은 색."""
    if index is None:
        i = zlib.crc32(name.encode("utf-8", "replace")) % 997
    else:
        i = int(index)
    hue = (i * 0.61803398875) % 1.0
    sat = (0.50, 0.38, 0.62)[i % 3]
    val = (0.90, 0.80, 0.86)[(i // 3) % 3]
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return float(r), float(g), float(b)


def is_placeholder_color(color: Optional[Sequence[float]]) -> bool:
    """머티리얼 색이 '정해지지 않은 기본값'인가: 없음 / Blender 기본 회색(0.8) / 순백. 이런 색이면 이름 색으로 대신한다."""
    if color is None:
        return True
    c = [float(x) for x in color[:3]]
    if len(c) < 3:
        return True
    near = lambda v, t: abs(v - t) < 0.02
    return all(near(x, 0.8) for x in c) or all(near(x, 1.0) for x in c)
