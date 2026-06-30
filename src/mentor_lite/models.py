from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class KnowledgePoint:
    id: str
    subject: str
    stage: str
    grade: str
    textbook: str
    chapter: str
    group: str
    name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)

    def context_text(self) -> str:
        return " ".join(
            value
            for value in (
                self.subject,
                self.stage,
                self.grade,
                self.textbook,
                self.chapter,
                self.group,
                self.name,
                self.description,
                *self.aliases,
            )
            if value
        )

    def path_label(self) -> str:
        return " / ".join(
            value
            for value in (
                self.subject,
                self.stage,
                self.grade,
                self.textbook,
                self.chapter,
                self.group,
                self.name,
            )
            if value
        )


@dataclass(slots=True)
class Candidate:
    source: str
    external_id: str
    canonical_url: str
    title: str
    author: str = ""
    duration_seconds: int | None = None
    description: str = ""
    cover_url: str = ""
    published_at: str = ""
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    favorite_count: int = 0
    danmaku_count: int = 0
    precheck_score: float = 0.0
    precheck_reason: str = ""
    score_breakdown: dict[str, float] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "author": self.author,
            "duration_seconds": self.duration_seconds,
            "description": self.description,
            "cover_url": self.cover_url,
            "published_at": self.published_at,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "favorite_count": self.favorite_count,
            "danmaku_count": self.danmaku_count,
            "precheck_score": self.precheck_score,
            "precheck_reason": self.precheck_reason,
            "score_breakdown": self.score_breakdown,
            "comments": self.comments,
            "raw": self.raw,
        }
