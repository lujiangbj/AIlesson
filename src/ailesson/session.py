"""三层集级会话：勾选 → 打包 → 上课 → 报告。"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import Any

from ailesson.course.assessment import SelfAssessment, build_assessment
from ailesson.contract.episode import Episode
from ailesson.classroom.runtime import LessonRuntime
from ailesson.infra.llm import BaseLLM
from ailesson.contract.lesson_spec import CoursePlan, LessonSpec
from ailesson.course.planner import TARGET_POINTS, pack_course
from ailesson.learner.progress import Progress
from ailesson.classroom.report import LessonReport, build_report


@dataclass
class CourseSession:
    episode: Episode
    llm: BaseLLM
    progress: Progress = field(default_factory=Progress)
    assessment: SelfAssessment | None = None
    plan: CoursePlan | None = None
    completed_lessons: list[int] = field(default_factory=list)
    # 动态挑出的 chunk/句子及打分理由（selector.build_pool 的结果）。
    # 只用于展示和调参，真正的待学池仍在 assessment 里
    selection: dict[str, list[dict]] = field(default_factory=dict)
    # 听力探测：抽样实测 chunk/句子掌握度的过程与校准结果（probe.py）
    probe: dict[str, Any] = field(default_factory=dict)

    # ---- 勾选 ----

    def all_items(self) -> dict[str, list[str]]:
        return {
            "words": [w.lemma for w in self.episode.words],
            "chunks": [c.id for c in self.episode.chunks],
            "sentences": [s.id for s in self.episode.sentences],
        }

    def submit_checklist(self, known: dict[str, list[str]]) -> SelfAssessment:
        self.assessment = build_assessment(self.episode.id, self.all_items(), known)
        return self.assessment

    def max_lessons_hint(self) -> int:
        total = sum(len(v) for v in self.all_items().values())
        return max(1, ceil(total / TARGET_POINTS))

    def actual_lessons(self) -> int:
        return len(self.plan.lessons) if self.plan else 0

    # ---- 上课 ----

    def spec_for(self, index: int) -> LessonSpec | None:
        if not self.plan:
            return None
        return next((l for l in self.plan.lessons if l.index == index), None)

    def _spot_check(self, spec: LessonSpec) -> dict[str, list[str]]:
        """挑本节要抽检的已会条目：优先和本节相关的。"""
        if not self.assessment:
            return {}
        a = self.assessment
        out: dict[str, list[str]] = {"words": [], "chunks": []}
        # 本节句子牵连到的已会词，抽检最有意义（马上要在语流里听到）
        in_scene: list[str] = []
        for sid in spec.sentence_ids:
            for w in sorted(self.episode.words_covered_by_sentence(sid)):
                if w in set(a.known_words) and w not in in_scene:
                    in_scene.append(w)
        out["words"] = (in_scene + [w for w in a.known_words if w not in in_scene])[:3]
        out["chunks"] = a.known_chunks[:3]
        return out

    def start_lesson(self, index: int) -> LessonRuntime | None:
        spec = self.spec_for(index)
        if spec is None:
            return None
        return LessonRuntime.build(
            self.episode, spec, self.progress, known=self._spot_check(spec)
        )

    def label_of(self, domain: str, item_id: str) -> str:
        """把内部 id 换成人看得懂的内容。

        报告里写 "s16 guess_what weve_been_doing" 用户看不懂，
        要写 "guess what / 趁妈妈没看见赶紧打扫"。
        """
        try:
            if domain == "words":
                return item_id
            if domain == "chunks":
                return self.episode.chunk(item_id).text
            return self.episode.sentence(item_id).text
        except KeyError:
            return item_id

    def finish_lesson(self, rt: LessonRuntime) -> LessonReport:
        if self.assessment:
            for dom, item_id in rt.demoted:
                self.assessment.demote(dom, item_id)   # type: ignore[arg-type]
        if rt.lesson_index not in self.completed_lessons:
            self.completed_lessons.append(rt.lesson_index)

        spec = self.spec_for(rt.lesson_index)
        nxt = self.spec_for(rt.lesson_index + 1)
        return build_report(
            episode_title=self.episode.title,
            lesson_index=rt.lesson_index,
            theme=spec.theme if spec else "",
            n_words=len(spec.focus_words) if spec else 0,
            n_chunks=len(spec.chunk_ids) if spec else 0,
            n_sentences=len(spec.sentence_ids) if spec else 0,
            stats=rt.stats,
            wrong_words=[self.label_of(d, i) for d, i in rt.wrong_items],
            shadow_scores=[],
            shadow_issues=[],
            blind_listen_score=rt.blind_listen_score,
            demoted=[self.label_of(d, i) for d, i in rt.demoted],
            next_theme=nxt.theme if nxt else "",
        )

    # ---- 落盘 ----

    def to_dict(self, lesson_runtime: LessonRuntime | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "episode_id": self.episode.id,
            "progress": self.progress.to_dict()["progress"],
            "assessment": self.assessment.to_dict() if self.assessment else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "completed_lessons": self.completed_lessons,
            # 这两项早先没落盘，重启后 selection/probe 全变 None，
            # 排查"动态挑选有没有生效"时看不到任何痕迹
            "selection": self.selection,
            "probe": self.probe,
        }
        if lesson_runtime is not None:
            out["lesson"] = lesson_runtime.to_dict()
        return out

    @classmethod
    def restore(
        cls, ep: Episode, llm: BaseLLM, snap: dict[str, Any]
    ) -> tuple[CourseSession, LessonRuntime | None]:
        s = cls(
            episode=ep, llm=llm,
            progress=Progress.from_dict({"progress": snap.get("progress", {})}),
            assessment=(
                SelfAssessment.from_dict(snap["assessment"])
                if snap.get("assessment") else None
            ),
            plan=CoursePlan.from_dict(snap["plan"]) if snap.get("plan") else None,
            completed_lessons=list(snap.get("completed_lessons", [])),
            selection=dict(snap.get("selection") or {}),
            probe=dict(snap.get("probe") or {}),
        )
        rt = None
        lsnap = snap.get("lesson")
        if lsnap:
            spec = s.spec_for(lsnap.get("lesson_index", 0))
            if spec:
                rt = LessonRuntime.restore(
                    ep, spec, s.progress, lsnap, known=s._spot_check(spec)
                )
        return s, rt
