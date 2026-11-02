"""소재 카드와 초안 데이터 모델."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class Card:
    """사연 소재 카드. facts는 숫자/짧은 문자열만, subject는 사람이 쓴 자유 텍스트(가명화 대상)."""
    source: str            # nagger / pet / cooloff / kakao / journal
    kind: str              # dish_streak, regret_item ...
    facts: dict = field(default_factory=dict)
    subject: str = ""
    priority: int = 50     # 0~100, 클수록 먼저
    consent: str = "auto"  # auto | user-specified (voice-journal)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            raw = json.dumps([self.source, self.kind, self.facts, self.subject], ensure_ascii=False, sort_keys=True)
            self.id = "%s-%s-%s" % (self.source, self.kind.replace("_", "-"),
                                    hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6])

    def text_blob(self) -> str:
        """필터/가명화 검사용 전체 텍스트."""
        return " ".join([self.subject] + [str(v) for v in self.facts.values() if isinstance(v, str)])

    def to_dict(self) -> dict:
        return {"id": self.id, "source": self.source, "kind": self.kind, "facts": self.facts,
                "subject": self.subject, "priority": self.priority, "consent": self.consent}


@dataclass
class Draft:
    card_id: str
    title: str
    title_candidates: list
    lines: list
    thumbnail_text: str
    claims: list
    ai_used: dict
    used_llm: bool = False
    model: str = ""
    kind: str = ""
    source: str = ""

    @property
    def script(self) -> str:
        return "\n".join(self.lines) + "\n"

    def all_text(self) -> str:
        return "\n".join([self.title, self.thumbnail_text, *self.title_candidates, *self.claims, *self.lines])

    def meta(self, seed: int = 0) -> dict:
        m = {
            "title": self.title,
            "title_candidates": list(self.title_candidates),
            "claims": list(self.claims),
            "thumbnail_text": self.thumbnail_text,
            "hashtags": ["#shorts", "#사연"],
            "ai_used": dict(self.ai_used),
            "ai_disclosure": any(self.ai_used.values()),
            "transcript": self.card_id + ".txt",
            "story_factory": {"card": self.card_id, "source": self.source, "kind": self.kind,
                              "seed": seed, "anonymized": True},
        }
        if self.used_llm and self.model:
            m["story_factory"]["llm_model"] = self.model
        return m
