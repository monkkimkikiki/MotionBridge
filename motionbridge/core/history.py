"""실행 취소 / 다시 실행. 편집 상태(설정 + 본 매핑)의 스냅샷을 쌓아 둔다. Qt 에 의존하지 않는다."""
from __future__ import annotations

import copy
from typing import Any, List, Optional


class History:
    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self.undo_stack: List[Any] = []
        self.redo_stack: List[Any] = []
        self.current: Any = None  # 마지막으로 확정한 상태

    def reset(self, state: Any) -> None:
        """새 파일을 열었을 때처럼 기록을 지우고 이 상태를 시작점으로 삼는다."""
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.current = copy.deepcopy(state)

    def commit(self, state: Any) -> bool:
        """현재 상태가 마지막 확정 상태와 다르면 이전 상태를 취소 목록에 넣는다. 바뀌었으면 True."""
        if self.current is None:
            self.current = copy.deepcopy(state)
            return False
        if state == self.current:
            return False
        self.undo_stack.append(self.current)
        if len(self.undo_stack) > self.limit:
            del self.undo_stack[0]
        self.redo_stack.clear()
        self.current = copy.deepcopy(state)
        return True

    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self, state: Any) -> Optional[Any]:
        """아직 확정하지 않은 변경이 있으면 먼저 확정한 뒤, 한 단계 되돌린 상태를 돌려준다."""
        self.commit(state)
        if not self.undo_stack:
            return None
        self.redo_stack.append(self.current)
        self.current = self.undo_stack.pop()
        return copy.deepcopy(self.current)

    def redo(self) -> Optional[Any]:
        if not self.redo_stack:
            return None
        self.undo_stack.append(self.current)
        self.current = self.redo_stack.pop()
        return copy.deepcopy(self.current)
