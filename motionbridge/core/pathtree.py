"""경로 목록 -> 폴더 트리 (Qt 없이 계산). 에셋 검색 결과를 폴더 구조대로 접고 펼치기 위한 데이터."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Node:
    name: str  # 표시 이름 (폴더 체인은 'a/b/c' 로 합쳐짐)
    path: Optional[str] = None  # 파일(잎)이면 원래 경로, 폴더면 None
    children: List["Node"] = field(default_factory=list)
    count: int = 0  # 이 노드 아래 파일 수 (파일이면 1)

    @property
    def is_leaf(self) -> bool:
        return self.path is not None

    def leaves(self) -> List[str]:
        if self.is_leaf:
            return [self.path]
        out: List[str] = []
        for c in self.children:
            out += c.leaves()
        return out


def _split(p: str) -> List[str]:
    return [s for s in re.split(r"[\\/]+", p) if s != ""]


def build_tree(paths: List[str], strip_ext: bool = False, compress: bool = True) -> List[Node]:
    """paths -> 최상위 노드 목록. 폴더는 이름순, 그다음 파일은 이름순. 자식이 폴더 하나뿐인 폴더는 합친다."""
    root = Node("")
    seen = set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        parts = _split(p)
        if not parts:
            continue
        cur = root
        for part in parts[:-1]:
            nxt = next((c for c in cur.children if not c.is_leaf and c.name == part), None)
            if nxt is None:
                nxt = Node(part)
                cur.children.append(nxt)
            cur = nxt
        leaf = parts[-1]
        if strip_ext:
            leaf = os.path.splitext(leaf)[0] or leaf
        cur.children.append(Node(leaf, path=p, count=1))

    def finish(n: Node) -> int:
        for c in n.children:
            if not c.is_leaf:
                finish(c)
        n.children.sort(key=lambda c: (c.is_leaf, c.name.lower()))
        n.count = sum(c.count for c in n.children) if not n.is_leaf else 1
        if compress:
            for i, c in enumerate(n.children):
                while not c.is_leaf and len(c.children) == 1 and not c.children[0].is_leaf:
                    g = c.children[0]
                    c = Node(c.name + "/" + g.name, None, g.children, g.count)
                n.children[i] = c
        return n.count

    finish(root)
    # 최상위도 한 폴더뿐이면 합쳐서 보여준다 (finish 가 자식 단위로만 합치므로 루트 쪽 별도 처리)
    return root.children
