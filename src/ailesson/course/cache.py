"""LLM 结果缓存（NFR-3）。

分诊排序 35s、课程打包 100~180s，都是集级一次性的活。不缓存的话每次进这一集
都得重算，用户要在课前干等 3 分钟。

不缓存兜底结果（fallback=True）—— 那是 LLM 挂了的降级产物，下次该重试。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ailesson.course.assessment import SelfAssessment
from ailesson.course.checklist import (
    WordGroup,
    build_checklist,
    build_item_checklist,
    groups_from_dict,
    groups_to_dict,
)
from ailesson.contract.episode import Episode
from ailesson.infra.llm import BaseLLM
from ailesson.contract.lesson_spec import CoursePlan
from ailesson.course.planner import pack_course


class LLMCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    # ---- 词表分组（勾选式用）----

    def get_or_build_groups(
        self, ep: Episode, llm: BaseLLM, force: bool = False
    ) -> list[WordGroup]:
        p = self._path(f"groups-{ep.id}")
        if not force and p.exists():
            return groups_from_dict(json.loads(p.read_text())["groups"])

        groups = build_checklist(ep, llm)
        p.write_text(
            json.dumps(
                {"episode_id": ep.id, "groups": groups_to_dict(groups)},
                ensure_ascii=False, indent=1,
            )
        )
        return groups

    # ---- 短语 / 句子分组（勾选式用）----

    def get_or_build_item_groups(
        self, ep: Episode, llm: BaseLLM, domain: str, force: bool = False
    ) -> list[WordGroup]:
        p = self._path(f"groups-{domain}-{ep.id}")
        if not force and p.exists():
            return groups_from_dict(json.loads(p.read_text())["groups"])

        groups = build_item_checklist(ep, llm, domain)
        p.write_text(
            json.dumps(
                {"episode_id": ep.id, "domain": domain, "groups": groups_to_dict(groups)},
                ensure_ascii=False, indent=1,
            )
        )
        return groups

    # ---- 三层课程划分 ----

    def get_or_build_plan(
        self, ep: Episode, a: SelfAssessment, llm: BaseLLM,
        thinking: bool = True, force: bool = False,
    ) -> CoursePlan:
        key = self._pool_key(
            set(a.unknown_words)
            | {f"c:{x}" for x in a.unknown_chunks}
            | {f"s:{x}" for x in a.unknown_sentences}
        )
        p = self._path(f"plan3-{ep.id}-{key}")
        if not force and p.exists():
            return CoursePlan.from_dict(json.loads(p.read_text()))

        plan = pack_course(ep, a, llm, thinking=thinking)
        if not plan.fallback:
            p.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=1))
        return plan

    @staticmethod
    def _pool_key(pool: set[str]) -> str:
        """池内容决定 key，顺序无关。"""
        blob = ",".join(sorted(pool))
        return hashlib.sha1(blob.encode()).hexdigest()[:12]
