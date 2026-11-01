"""Shared data model."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Item:
    title: str
    url: str
    source: str
    summary: str = ""              # plain text from the feed / page
    published: Optional[str] = None  # ISO 8601 (UTC) or None
    comments: Optional[int] = None
    score: float = 0.0
    topic: str = ""
    matched: List[str] = field(default_factory=list)
    links: List[dict] = field(default_factory=list)  # [{"source", "url"}], one per source
    brief: str = ""                # 1-2 sentence Korean summary
    brief_by: str = ""             # "llm" | "extractive"

    def __post_init__(self):
        if not self.links:
            self.links = [{"source": self.source, "url": self.url}]

    @property
    def sources(self) -> List[str]:
        return [link["source"] for link in self.links]

    def to_dict(self) -> dict:
        return {
            "title": self.title, "url": self.url, "sources": self.sources, "links": self.links,
            "summary": self.brief, "summary_by": self.brief_by, "published": self.published,
            "comments": self.comments, "score": round(self.score, 2), "topic": self.topic,
            "matched": self.matched,
        }
