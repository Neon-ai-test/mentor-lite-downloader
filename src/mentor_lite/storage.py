from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from mentor_lite.models import Candidate, KnowledgePoint, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_point (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    grade TEXT NOT NULL DEFAULT '',
    textbook TEXT NOT NULL DEFAULT '',
    chapter TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task (
    id TEXT PRIMARY KEY,
    knowledge_point_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    target_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    processed_count INTEGER NOT NULL DEFAULT 0,
    qualified_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    duration_seconds INTEGER,
    description TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    view_count INTEGER NOT NULL DEFAULT 0,
    like_count INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    share_count INTEGER NOT NULL DEFAULT 0,
    favorite_count INTEGER NOT NULL DEFAULT 0,
    danmaku_count INTEGER NOT NULL DEFAULT 0,
    precheck_score REAL NOT NULL DEFAULT 0,
    precheck_reason TEXT NOT NULL DEFAULT '',
    score_json TEXT NOT NULL DEFAULT '{}',
    comments_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}',
    download_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
    download_stage TEXT NOT NULL DEFAULT '',
    download_progress_percent INTEGER NOT NULL DEFAULT 0,
    download_message TEXT NOT NULL DEFAULT '',
    media_file TEXT NOT NULL DEFAULT '',
    downloaded_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_task ON candidate(task_id, rank);
CREATE INDEX IF NOT EXISTS idx_candidate_download ON candidate(download_status);
"""


class Repository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(candidate)").fetchall()
            }
            if "share_count" not in columns:
                connection.execute("ALTER TABLE candidate ADD COLUMN share_count INTEGER NOT NULL DEFAULT 0")
            connection.commit()
            connection.execute(
                "UPDATE task SET status = 'FAILED', stage = 'FAILED', message = ? WHERE status = 'RUNNING'",
                ("上次运行中断，任务已恢复为失败状态",),
            )
            connection.execute(
                """UPDATE candidate
                   SET download_status = 'FAILED',
                       download_stage = 'FAILED',
                       download_message = '上次运行中断，下载已恢复为失败状态',
                       download_progress_percent = 0
                   WHERE download_status = 'DOWNLOADING'""",
            )
            connection.commit()

    def upsert_knowledge(self, point: KnowledgePoint) -> None:
        now = utc_now()
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(
                    """INSERT INTO knowledge_point
                       (id, subject, stage, grade, textbook, chapter, group_name, name,
                        description, aliases_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                        subject = excluded.subject,
                        stage = excluded.stage,
                        grade = excluded.grade,
                        textbook = excluded.textbook,
                        chapter = excluded.chapter,
                        group_name = excluded.group_name,
                        name = excluded.name,
                        description = excluded.description,
                        aliases_json = excluded.aliases_json,
                        updated_at = excluded.updated_at""",
                    (
                        point.id,
                        point.subject,
                        point.stage,
                        point.grade,
                        point.textbook,
                        point.chapter,
                        point.group,
                        point.name,
                        point.description,
                        json.dumps(point.aliases, ensure_ascii=False),
                        now,
                        now,
                    ),
                )

    def list_knowledge(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_point
                   ORDER BY subject, stage, grade, textbook, chapter, group_name, name"""
            ).fetchall()
        return [knowledge_row_to_dict(row) for row in rows]

    def get_knowledge(self, point_id: str) -> KnowledgePoint | None:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM knowledge_point WHERE id = ?", (point_id,)).fetchone()
        return knowledge_row_to_model(row) if row else None

    def delete_knowledge(self, point_id: str) -> bool:
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute("DELETE FROM knowledge_point WHERE id = ?", (point_id,))
                return cursor.rowcount > 0

    def delete_knowledge_many(self, point_ids: list[str]) -> int:
        unique_ids = list(dict.fromkeys(point_id for point_id in point_ids if point_id))
        if not unique_ids:
            return 0
        placeholders = ",".join("?" for _ in unique_ids)
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute(
                    f"DELETE FROM knowledge_point WHERE id IN ({placeholders})",
                    tuple(unique_ids),
                )
                return cursor.rowcount

    def create_task(self, task_id: str, knowledge_point_id: str, keyword: str, target_count: int) -> None:
        now = utc_now()
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(
                    """INSERT INTO task
                       (id, knowledge_point_id, keyword, target_count, status, stage,
                        progress_percent, message, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'RUNNING', 'PREPARING', 0, '任务已创建', ?, ?)""",
                    (task_id, knowledge_point_id, keyword, target_count, now, now),
                )

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        percent: int | None = None,
        message: str | None = None,
        processed_count: int | None = None,
        qualified_count: int | None = None,
    ) -> None:
        updates = ["updated_at = ?"]
        values: list[Any] = [utc_now()]
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if stage is not None:
            updates.append("stage = ?")
            values.append(stage)
        if percent is not None:
            updates.append("progress_percent = ?")
            values.append(max(0, min(100, int(percent))))
        if message is not None:
            updates.append("message = ?")
            values.append(message)
        if processed_count is not None:
            updates.append("processed_count = ?")
            values.append(processed_count)
        if qualified_count is not None:
            updates.append("qualified_count = ?")
            values.append(qualified_count)
        values.append(task_id)
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(f"UPDATE task SET {', '.join(updates)} WHERE id = ?", values)

    def list_tasks(self, limit: int = 30) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT t.*,
                          kp.name AS knowledge_name,
                          kp.chapter AS knowledge_chapter,
                          kp.group_name AS knowledge_group,
                          COUNT(c.id) AS candidate_count
                   FROM task t
                   LEFT JOIN knowledge_point kp ON kp.id = t.knowledge_point_id
                   LEFT JOIN candidate c ON c.task_id = t.id
                   GROUP BY t.id
                   ORDER BY t.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT t.*,
                          kp.name AS knowledge_name,
                          kp.chapter AS knowledge_chapter,
                          kp.group_name AS knowledge_group,
                          COUNT(c.id) AS candidate_count
                   FROM task t
                   LEFT JOIN knowledge_point kp ON kp.id = t.knowledge_point_id
                   LEFT JOIN candidate c ON c.task_id = t.id
                   WHERE t.id = ?
                   GROUP BY t.id""",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_task(self, task_id: str) -> int:
        with closing(self.connect()) as connection:
            with connection:
                candidate_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM candidate WHERE task_id = ?",
                    (task_id,),
                ).fetchone()["count"]
                task_cursor = connection.execute("DELETE FROM task WHERE id = ?", (task_id,))
                if task_cursor.rowcount:
                    connection.execute("DELETE FROM candidate WHERE task_id = ?", (task_id,))
                    return int(candidate_count)
                return 0

    def clear_tasks(self, statuses: set[str] | None = None) -> dict[str, int]:
        with closing(self.connect()) as connection:
            with connection:
                if statuses:
                    placeholders = ",".join("?" for _ in statuses)
                    task_rows = connection.execute(
                        f"SELECT id FROM task WHERE status IN ({placeholders})",
                        tuple(statuses),
                    ).fetchall()
                else:
                    task_rows = connection.execute("SELECT id FROM task").fetchall()
                task_ids = [str(row["id"]) for row in task_rows]
                if not task_ids:
                    return {"tasks": 0, "candidates": 0}
                placeholders = ",".join("?" for _ in task_ids)
                candidate_count = connection.execute(
                    f"SELECT COUNT(*) AS count FROM candidate WHERE task_id IN ({placeholders})",
                    tuple(task_ids),
                ).fetchone()["count"]
                connection.execute(
                    f"DELETE FROM candidate WHERE task_id IN ({placeholders})",
                    tuple(task_ids),
                )
                cursor = connection.execute(
                    f"DELETE FROM task WHERE id IN ({placeholders})",
                    tuple(task_ids),
                )
                return {"tasks": cursor.rowcount, "candidates": int(candidate_count)}

    def save_candidates(self, task_id: str, candidates: list[Candidate]) -> int:
        now = utc_now()
        saved = 0
        with closing(self.connect()) as connection:
            with connection:
                connection.execute("DELETE FROM candidate WHERE task_id = ?", (task_id,))
                for rank, candidate in enumerate(candidates, start=1):
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO candidate
                           (task_id, rank, source, external_id, canonical_url, title, author,
                            duration_seconds, description, cover_url, published_at, view_count,
                            like_count, comment_count, share_count, favorite_count, danmaku_count,
                            precheck_score, precheck_reason, score_json, comments_json, raw_json,
                            created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            task_id,
                            rank,
                            candidate.source,
                            candidate.external_id,
                            candidate.canonical_url,
                            candidate.title,
                            candidate.author,
                            candidate.duration_seconds,
                            candidate.description,
                            candidate.cover_url,
                            candidate.published_at,
                            candidate.view_count,
                            candidate.like_count,
                            candidate.comment_count,
                            candidate.share_count,
                            candidate.favorite_count,
                            candidate.danmaku_count,
                            candidate.precheck_score,
                            candidate.precheck_reason,
                            json.dumps(candidate.score_breakdown, ensure_ascii=False),
                            json.dumps(candidate.comments, ensure_ascii=False),
                            json.dumps(candidate.raw, ensure_ascii=False),
                            now,
                        ),
                    )
                    saved += cursor.rowcount
        return saved

    def list_candidates(self, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where = "WHERE c.task_id = ?" if task_id else ""
        params: tuple[Any, ...] = (task_id, limit) if task_id else (limit,)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""SELECT c.*, t.keyword AS task_keyword, t.knowledge_point_id,
                           kp.name AS knowledge_name,
                           kp.chapter AS knowledge_chapter,
                           kp.group_name AS knowledge_group
                    FROM candidate c
                    JOIN task t ON t.id = c.task_id
                    LEFT JOIN knowledge_point kp ON kp.id = t.knowledge_point_id
                    {where}
                    ORDER BY c.task_id DESC, c.rank ASC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [candidate_row_to_dict(row) for row in rows]

    def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT c.*, t.keyword AS task_keyword, t.knowledge_point_id,
                          kp.name AS knowledge_name,
                          kp.chapter AS knowledge_chapter,
                          kp.group_name AS knowledge_group
                   FROM candidate c
                   JOIN task t ON t.id = c.task_id
                   LEFT JOIN knowledge_point kp ON kp.id = t.knowledge_point_id
                   WHERE c.id = ?""",
                (candidate_id,),
            ).fetchone()
        return candidate_row_to_dict(row) if row else None

    def delete_candidate(self, candidate_id: int) -> bool:
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute("DELETE FROM candidate WHERE id = ?", (candidate_id,))
                return cursor.rowcount > 0

    def clear_candidates(self, task_id: str | None = None) -> int:
        with closing(self.connect()) as connection:
            with connection:
                if task_id:
                    cursor = connection.execute("DELETE FROM candidate WHERE task_id = ?", (task_id,))
                else:
                    cursor = connection.execute("DELETE FROM candidate")
                return cursor.rowcount

    def update_download(
        self,
        candidate_id: int,
        *,
        status: str,
        stage: str,
        percent: int,
        message: str,
        media_file: str | None = None,
    ) -> None:
        updates = [
            "download_status = ?",
            "download_stage = ?",
            "download_progress_percent = ?",
            "download_message = ?",
        ]
        values: list[Any] = [status, stage, max(0, min(100, int(percent))), message]
        if media_file is not None:
            updates.append("media_file = ?")
            values.append(media_file)
        if status in {"COMPLETED", "FAILED"}:
            updates.append("downloaded_at = ?")
            values.append(utc_now())
        values.append(candidate_id)
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(f"UPDATE candidate SET {', '.join(updates)} WHERE id = ?", values)


def knowledge_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["group"] = data.pop("group_name")
    try:
        data["aliases"] = json.loads(str(data.pop("aliases_json") or "[]"))
    except json.JSONDecodeError:
        data["aliases"] = []
    return data


def knowledge_row_to_model(row: sqlite3.Row) -> KnowledgePoint:
    data = knowledge_row_to_dict(row)
    return KnowledgePoint(
        id=str(data["id"]),
        subject=str(data["subject"]),
        stage=str(data.get("stage") or ""),
        grade=str(data.get("grade") or ""),
        textbook=str(data.get("textbook") or ""),
        chapter=str(data.get("chapter") or ""),
        group=str(data.get("group") or ""),
        name=str(data["name"]),
        description=str(data.get("description") or ""),
        aliases=[str(item) for item in data.get("aliases") or []],
    )


def candidate_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for source_key, target_key, default in (
        ("score_json", "score_breakdown", {}),
        ("comments_json", "comments", []),
        ("raw_json", "raw", {}),
    ):
        raw = data.pop(source_key, "")
        try:
            data[target_key] = json.loads(str(raw or json.dumps(default)))
        except json.JSONDecodeError:
            data[target_key] = default
    return data
