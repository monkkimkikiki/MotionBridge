"""같은 이름의 파일이 이미 있을 때 어떻게 저장할지: 덮어쓰기 / 이전 파일 보관 / 새 파일을 다른 이름으로.

- keep_old (기본): 이미 있는 파일을 `이름_old_YYYYMMDD-HHMMSS.확장자` 로 바꿔 두고, 새 파일이 원래 이름을 쓴다. 이전 결과가 사라지지 않는다.
- new_name: 이미 있는 파일은 그대로 두고, 새 파일은 `이름_2`, `이름_3` … 으로 저장한다.
- overwrite: 이미 있는 파일을 지우고 덮어쓴다 (예전 동작).
Qt 에 의존하지 않는다.
"""
from __future__ import annotations

import os
import time
from typing import Optional, Tuple

OVERWRITE, KEEP_OLD, NEW_NAME = "overwrite", "keep_old", "new_name"
DEFAULT = KEEP_OLD
LABELS = {
    KEEP_OLD: "이미 있으면 이전 파일을 다른 이름(_old_날짜)으로 보관하고 새로 저장",
    NEW_NAME: "이미 있으면 새 파일을 다른 이름(_2, _3 …)으로 저장",
    OVERWRITE: "이미 있으면 덮어쓰기 (이전 파일은 사라짐)",
}
SHORT = {KEEP_OLD: "이전 파일 보관", NEW_NAME: "새 이름으로", OVERWRITE: "덮어쓰기"}
EXPLAIN = {
    KEEP_OLD: "같은 이름의 파일이 있으면 그 파일을 '이름_old_날짜' 로 바꿔 보관하고, 새 파일이 원래 이름으로 저장됩니다.",
    NEW_NAME: "같은 이름의 파일이 있으면 그대로 두고, 새 파일을 '이름_2', '이름_3' … 으로 저장합니다.",
    OVERWRITE: "같은 이름의 파일이 있으면 덮어씁니다 — 이전 파일은 사라집니다.",
}


def norm(policy: Optional[str]) -> str:
    return policy if policy in LABELS else DEFAULT


def next_free(path: str) -> str:
    """path 가 비어 있으면 그대로, 아니면 이름_2, 이름_3 … 중 비어 있는 첫 경로."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists("%s_%d%s" % (stem, n, ext)):
        n += 1
    return "%s_%d%s" % (stem, n, ext)


def backup_path(path: str, now: Optional[float] = None) -> str:
    stem, ext = os.path.splitext(path)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    return next_free("%s_old_%s%s" % (stem, stamp, ext))


def claim(path: str, policy: Optional[str] = DEFAULT, now: Optional[float] = None) -> Tuple[str, Optional[str]]:
    """path 에 새 파일을 쓰기 전에 부른다. (실제로 쓸 경로, 보관해 둔 이전 파일 경로 또는 None).

    keep_old 면 이미 있는 파일을 여기서 옮겨 둔다 — 호출한 쪽은 돌려받은 경로에 쓰기만 하면 된다."""
    policy = norm(policy)
    if not os.path.lexists(path) or policy == OVERWRITE:
        return path, None
    if policy == NEW_NAME:
        return next_free(path), None
    if os.path.isdir(path):                       # 폴더는 건드리지 않는다
        return next_free(path), None
    bk = backup_path(path, now)
    os.replace(path, bk)
    return path, bk
