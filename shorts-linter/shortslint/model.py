"""검사 결과 모델."""
from dataclasses import dataclass, field
from typing import List

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌", SKIP: "⏭️"}
_RANK = {OK: 0, SKIP: 0, WARN: 1, FAIL: 2}


@dataclass
class Result:
    name: str
    status: str
    message: str = ""
    category: str = ""

    def to_dict(self):
        return {"name": self.name, "status": self.status,
                "message": self.message, "category": self.category}


def worst(results) -> str:
    st = OK
    for r in results:
        if _RANK[r.status] > _RANK[st]:
            st = r.status
    return st


@dataclass
class VideoReport:
    path: str
    results: List[Result] = field(default_factory=list)

    @property
    def status(self) -> str:
        return worst(self.results)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    def to_dict(self):
        return {"path": self.path, "status": self.status,
                "results": [r.to_dict() for r in self.results]}
