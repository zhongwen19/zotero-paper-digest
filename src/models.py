from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Paper:
    title: str
    abstract: str = ""
    doi: str = ""
    year: int | None = None
    venue: str = ""
    authors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    url: str = ""
    source: str = ""
    source_id: str = ""
    openalex_id: str = ""
    external_ids: dict[str, str] = field(default_factory=dict)
    concepts: list[str] = field(default_factory=list)
    cited_by_count: int = 0
    related_to_seed: bool = False
    relation_reason: str = ""
    score: float = 0.0
    category: str = "NEW"
    why_recommended: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Digest:
    new_papers: list[Paper]
    classic_papers: list[Paper]
    stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_papers": [paper.to_dict() for paper in self.new_papers],
            "classic_papers": [paper.to_dict() for paper in self.classic_papers],
            "stats": self.stats,
        }
