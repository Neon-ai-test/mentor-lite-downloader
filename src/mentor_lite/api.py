from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from threading import Lock, Thread
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mentor_lite.bilibili import auth_summary, search_bilibili
from mentor_lite.downloader import download_bilibili_flat
from mentor_lite.knowledge import (
    catalog_item_to_knowledge_point,
    import_knowledge_points,
    knowledge_point_to_catalog_item,
    load_catalog_payload,
    make_knowledge_point,
    preview_upload,
    save_catalog_payload,
    save_upload,
)
from mentor_lite.settings import Settings
from mentor_lite.storage import Repository

settings = Settings.from_env()
settings.ensure_dirs()
repository = Repository(settings.db_path)
repository.initialize()

active_task_ids: set[str] = set()
active_candidate_ids: set[int] = set()
active_lock = Lock()

app = FastAPI(
    title="MENTOR Lite Downloader",
    version="0.1.0",
    description="Standalone rough-screening and Bilibili download tool",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_static_assets(request: Any, call_next: Any) -> Any:
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response

STATIC_DIR = settings.root / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class KnowledgePayload(BaseModel):
    subject: str = Field(min_length=1, max_length=80)
    stage: str = Field(default="", max_length=80)
    grade: str = Field(default="", max_length=80)
    textbook: str = Field(default="", max_length=120)
    chapter: str = Field(default="", max_length=200)
    group: str = Field(default="", max_length=200)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    aliases: list[str] = Field(default_factory=list, max_length=30)


class TaskPayload(BaseModel):
    knowledge_point_id: str = Field(min_length=1, max_length=120)
    keyword: str = Field(default="", max_length=200)
    target_count: int = Field(default=100, ge=1, le=500)
    max_duration_minutes: int | None = Field(default=None, ge=0, le=240)


class DownloadPayload(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=100)


class IdsPayload(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class KnowledgeImportPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=260)
    content_base64: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=80)
    stage: str = Field(default="", max_length=80)
    grade: str = Field(default="", max_length=80)
    textbook: str = Field(default="", max_length=120)


class KnowledgeImportPreviewPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=260)
    content_base64: str = Field(min_length=1)


class KnowledgeImportCommitPayload(BaseModel):
    upload_id: str = Field(min_length=32, max_length=32)
    sheet_name: str | None = Field(default=None, max_length=120)
    header_row: int | None = Field(default=None, ge=1)
    field_mapping: dict[str, str | None] = Field(default_factory=dict)
    defaults: dict[str, str] = Field(default_factory=dict)
    mode: str = Field(default="append")


def model_data(model: BaseModel) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    return model.dict()


def task_max_duration_seconds(payload: TaskPayload) -> int | None:
    if payload.max_duration_minutes is None:
        return settings.max_duration_seconds
    return max(0, int(payload.max_duration_minutes) * 60)


def upload_dir() -> Path:
    path = settings.runtime_dir / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_upload_path(upload_id: str) -> Path:
    if not upload_id or any(char not in "0123456789abcdef" for char in upload_id.lower()):
        raise HTTPException(status_code=400, detail="上传编号无效")
    matches = list(upload_dir().glob(f"{upload_id}_*"))
    if not matches:
        raise HTTPException(status_code=404, detail="上传文件不存在或已被清理")
    return matches[0]


def load_catalog_payload_or_seed() -> dict[str, Any]:
    if settings.knowledge_catalog_path.exists():
        return load_catalog_payload(settings.knowledge_catalog_path)
    items = [knowledge_point_to_catalog_item(knowledge_row_to_point(row)) for row in repository.list_knowledge()]
    payload = {
        "version": 1,
        "source": "mentor_lite",
        "knowledge_points": items,
        "imports": [],
    }
    save_catalog_payload(settings.knowledge_catalog_path, payload)
    return payload


def knowledge_row_to_point(row: dict[str, Any]) -> Any:
    return make_knowledge_point(
        {
            "subject": row.get("subject", ""),
            "stage": row.get("stage", ""),
            "grade": row.get("grade", ""),
            "textbook": row.get("textbook", ""),
            "chapter": row.get("chapter", ""),
            "group": row.get("group", ""),
            "name": row.get("name", ""),
            "description": row.get("description", ""),
            "aliases": row.get("aliases", []),
        },
        point_id=str(row["id"]),
    )


def sync_repository_from_catalog() -> list[dict[str, Any]]:
    payload = load_catalog_payload(settings.knowledge_catalog_path)
    points = [
        catalog_item_to_knowledge_point(item)
        for item in payload.get("knowledge_points") or []
        if isinstance(item, dict)
    ]
    repository.replace_knowledge(points)
    return repository.list_knowledge()


def save_point_to_catalog(point: Any) -> None:
    payload = load_catalog_payload_or_seed()
    items = [item for item in payload.get("knowledge_points") or [] if isinstance(item, dict)]
    catalog_item = knowledge_point_to_catalog_item(point)
    for index, item in enumerate(items):
        if str(item.get("id") or "") == point.id:
            items[index] = catalog_item
            break
    else:
        items.append(catalog_item)
    payload["knowledge_points"] = items
    save_catalog_payload(settings.knowledge_catalog_path, payload)
    sync_repository_from_catalog()


def remove_points_from_catalog(point_ids: set[str]) -> None:
    if not settings.knowledge_catalog_path.exists():
        return
    payload = load_catalog_payload(settings.knowledge_catalog_path)
    items = [item for item in payload.get("knowledge_points") or [] if isinstance(item, dict)]
    payload["knowledge_points"] = [
        item for item in items if str(item.get("id") or "") not in point_ids
    ]
    save_catalog_payload(settings.knowledge_catalog_path, payload)
    sync_repository_from_catalog()


def task_running(task_id: str) -> bool:
    with active_lock:
        return task_id in active_task_ids


def candidate_downloading(candidate_id: int) -> bool:
    with active_lock:
        return candidate_id in active_candidate_ids


def run_discovery_task(
    task_id: str,
    point_id: str,
    keyword: str,
    target_count: int,
    max_duration_seconds: int | None,
) -> None:
    point = repository.get_knowledge(point_id)
    if point is None:
        repository.update_task(
            task_id,
            status="FAILED",
            stage="FAILED",
            percent=100,
            message="知识点不存在",
        )
        return

    def progress(stage: str, percent: int, message: str, processed: int, qualified: int) -> None:
        repository.update_task(
            task_id,
            stage=stage,
            percent=percent,
            message=message,
            processed_count=processed,
            qualified_count=qualified,
        )

    try:
        candidates = search_bilibili(
            settings,
            point,
            keyword=keyword,
            target_count=target_count,
            max_duration_seconds=max_duration_seconds,
            progress=progress,
        )
        saved = repository.save_candidates(task_id, candidates)
        status = "COMPLETED" if saved >= target_count else "PARTIAL"
        repository.update_task(
            task_id,
            status=status,
            stage=status,
            percent=100,
            message=f"粗筛完成，保存 {saved}/{target_count} 条候选",
            qualified_count=saved,
        )
    except Exception as exc:
        repository.update_task(
            task_id,
            status="FAILED",
            stage="FAILED",
            percent=100,
            message=str(exc)[:500],
        )
    finally:
        with active_lock:
            active_task_ids.discard(task_id)


def run_download_batch(candidate_ids: list[int]) -> None:
    for candidate_id in candidate_ids:
        row = repository.get_candidate(candidate_id)
        if row is None:
            continue
        with active_lock:
            if candidate_id in active_candidate_ids:
                continue
            active_candidate_ids.add(candidate_id)
        try:
            repository.update_download(
                candidate_id,
                status="DOWNLOADING",
                stage="QUEUED",
                percent=0,
                message="下载已排队",
            )

            def progress(percent: int, stage: str, message: str) -> None:
                repository.update_download(
                    candidate_id,
                    status="DOWNLOADING",
                    stage=stage,
                    percent=percent,
                    message=message,
                )

            report = download_bilibili_flat(
                settings,
                str(row["canonical_url"] or row["external_id"]),
                knowledge_name=str(row["knowledge_name"] or row["task_keyword"] or "未命名知识点"),
                video_title=str(row["title"] or row["external_id"]),
                progress=progress,
            )
            repository.update_download(
                candidate_id,
                status="COMPLETED",
                stage="COMPLETED",
                percent=100,
                message=f"下载完成：{report.get('selected_label') or ''}".strip(),
                media_file=str(report.get("main_file") or ""),
            )
        except Exception as exc:
            repository.update_download(
                candidate_id,
                status="FAILED",
                stage="FAILED",
                percent=0,
                message=str(exc)[:500],
            )
        finally:
            with active_lock:
                active_candidate_ids.discard(candidate_id)


def background_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return options


def open_system_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        command = ["explorer", str(path)]
    elif sys.platform == "darwin":
        command = ["open", str(path)]
    else:
        command = ["xdg-open", str(path)]
    try:
        subprocess.Popen(command, **background_options())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开目录失败：{exc}") from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "root": str(settings.root),
        "db": str(settings.db_path),
        "downloads": str(settings.download_dir),
    }


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    tasks = repository.list_tasks(200)
    candidates = repository.list_candidates(limit=500)
    return {
        "knowledge_count": len(repository.list_knowledge()),
        "task_count": len(tasks),
        "candidate_count": len(candidates),
        "running_count": sum(1 for item in tasks if item["status"] == "RUNNING"),
        "downloading_count": sum(1 for item in candidates if item["download_status"] == "DOWNLOADING"),
        "download_root": str(settings.download_dir),
        "max_duration_seconds": settings.max_duration_seconds,
    }


@app.post("/api/downloads/open")
def open_downloads() -> dict[str, Any]:
    open_system_directory(settings.download_dir)
    return {"opened": True, "path": str(settings.download_dir)}


@app.get("/api/auth")
def get_auth(live: bool = Query(default=False)) -> dict[str, Any]:
    return auth_summary(settings, live=live)


@app.post("/api/auth/start")
def start_auth() -> dict[str, Any]:
    settings.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mentor_lite.authorize",
            "--output",
            str(settings.auth_state_path),
        ],
        cwd=str(settings.root),
        **background_options(),
    )
    return {"started": True, "message": "授权浏览器已打开，请在新窗口完成 B站登录"}


@app.delete("/api/auth")
def clear_auth() -> dict[str, Any]:
    removed = False
    if settings.auth_state_path.exists():
        settings.auth_state_path.unlink()
        removed = True
    return {"cleared": removed, "auth": auth_summary(settings)}


@app.post("/api/auth/clear")
def clear_auth_action() -> dict[str, Any]:
    return clear_auth()


@app.get("/api/knowledge")
def list_knowledge() -> list[dict[str, Any]]:
    return repository.list_knowledge()


@app.post("/api/knowledge")
def create_knowledge(payload: KnowledgePayload) -> dict[str, Any]:
    try:
        point = make_knowledge_point(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_point_to_catalog(point)
    saved = next((item for item in repository.list_knowledge() if item["id"] == point.id), None)
    return {"saved": True, "knowledge": saved}


@app.put("/api/knowledge/{point_id}")
def update_knowledge(point_id: str, payload: KnowledgePayload) -> dict[str, Any]:
    if repository.get_knowledge(point_id) is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    try:
        point = make_knowledge_point(model_data(payload), point_id=point_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_point_to_catalog(point)
    saved = next((item for item in repository.list_knowledge() if item["id"] == point.id), None)
    return {"saved": True, "knowledge": saved}


@app.delete("/api/knowledge/{point_id}")
def delete_knowledge(point_id: str) -> dict[str, Any]:
    deleted = repository.delete_knowledge(point_id)
    remove_points_from_catalog({point_id})
    return {"deleted": deleted}


@app.post("/api/knowledge/{point_id}/delete")
def delete_knowledge_action(point_id: str) -> dict[str, Any]:
    return delete_knowledge(point_id)


@app.post("/api/knowledge/delete")
def delete_knowledge_batch(payload: IdsPayload) -> dict[str, Any]:
    point_ids = [str(item) for item in payload.ids]
    deleted = repository.delete_knowledge_many(point_ids)
    remove_points_from_catalog(set(point_ids))
    return {"deleted": deleted}


@app.post("/api/knowledge/import")
def import_knowledge(payload: KnowledgeImportPayload) -> dict[str, Any]:
    try:
        upload = save_upload(upload_dir(), payload.filename, payload.content_base64)
        preview = preview_upload(Path(upload["file_path"]))
        load_catalog_payload_or_seed()
        result = import_knowledge_points(
            Path(upload["file_path"]),
            settings.knowledge_catalog_path,
            field_mapping=dict(preview.get("suggested_mapping") or {}),
            defaults={
                "subject": payload.subject,
                "stage": payload.stage,
                "grade": payload.grade,
                "textbook": payload.textbook,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = sync_repository_from_catalog()
    return {"imported_count": result["imported_count"], "knowledge_count": len(rows)}


@app.post("/api/knowledge/import/preview")
def preview_knowledge_import(payload: KnowledgeImportPreviewPayload) -> dict[str, Any]:
    try:
        upload = save_upload(upload_dir(), payload.filename, payload.content_base64)
        preview = preview_upload(Path(upload["file_path"]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    preview.update(
        {
            "upload_id": upload["upload_id"],
            "filename": upload["filename"],
            "file_path": upload["file_path"],
            "target_catalog_path": str(settings.knowledge_catalog_path),
        }
    )
    return preview


@app.post("/api/knowledge/import/commit")
def commit_knowledge_import(payload: KnowledgeImportCommitPayload) -> dict[str, Any]:
    upload_path = resolve_upload_path(payload.upload_id)
    if payload.mode not in {"append", "replace"}:
        raise HTTPException(status_code=400, detail="导入模式只能是 append 或 replace")
    try:
        if payload.mode != "replace":
            load_catalog_payload_or_seed()
        result = import_knowledge_points(
            upload_path,
            settings.knowledge_catalog_path,
            field_mapping=payload.field_mapping,
            defaults=payload.defaults,
            mode=payload.mode,
            sheet_name=payload.sheet_name,
            header_row=payload.header_row,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = sync_repository_from_catalog()
    return {
        **result,
        "catalog_count": len(rows),
    }


@app.post("/api/tasks", status_code=202)
def create_task(payload: TaskPayload) -> dict[str, Any]:
    point = repository.get_knowledge(payload.knowledge_point_id)
    if point is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    task_id = uuid.uuid4().hex
    keyword = payload.keyword.strip() or point.name
    max_duration_seconds = task_max_duration_seconds(payload)
    repository.create_task(task_id, point.id, keyword, payload.target_count, max_duration_seconds)
    with active_lock:
        active_task_ids.add(task_id)
    Thread(
        target=run_discovery_task,
        args=(task_id, point.id, keyword, payload.target_count, max_duration_seconds),
        daemon=True,
    ).start()
    return {"task_id": task_id, "status": "RUNNING"}


@app.get("/api/tasks")
def list_tasks(limit: int = Query(default=30, ge=1, le=200)) -> list[dict[str, Any]]:
    return repository.list_tasks(limit)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    task = repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    task["active"] = task_running(task_id)
    return task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, Any]:
    task = repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task_running(task_id) or task.get("status") == "RUNNING":
        raise HTTPException(status_code=409, detail="任务正在运行，不能删除")
    deleted_candidates = repository.delete_task(task_id)
    with active_lock:
        active_task_ids.discard(task_id)
    return {"deleted": True, "deleted_candidates": deleted_candidates}


@app.post("/api/tasks/{task_id}/delete")
def delete_task_action(task_id: str) -> dict[str, Any]:
    return delete_task(task_id)


@app.delete("/api/tasks")
def clear_tasks(status: str | None = Query(default="finished")) -> dict[str, Any]:
    if status == "all":
        running = [task for task in repository.list_tasks(500) if task_running(str(task["id"])) or task["status"] == "RUNNING"]
        if running:
            raise HTTPException(status_code=409, detail=f"仍有 {len(running)} 个运行中任务，不能清空全部")
        result = repository.clear_tasks()
    elif status in {None, "", "finished"}:
        result = repository.clear_tasks({"COMPLETED", "PARTIAL", "FAILED"})
    else:
        allowed = {item.strip().upper() for item in status.split(",") if item.strip()}
        allowed.discard("RUNNING")
        result = repository.clear_tasks(allowed or {"COMPLETED", "PARTIAL", "FAILED"})
    return {"deleted_tasks": result["tasks"], "deleted_candidates": result["candidates"]}


@app.post("/api/tasks/clear")
def clear_tasks_action(status: str | None = Query(default="finished")) -> dict[str, Any]:
    return clear_tasks(status)


@app.get("/api/candidates")
def list_candidates(
    task_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    rows = repository.list_candidates(task_id=task_id, limit=limit)
    for row in rows:
        row["downloading"] = candidate_downloading(int(row["id"]))
    return rows


@app.delete("/api/candidates/{candidate_id}")
def delete_candidate(candidate_id: int) -> dict[str, Any]:
    if candidate_downloading(candidate_id):
        raise HTTPException(status_code=409, detail="候选正在下载，不能删除")
    return {"deleted": repository.delete_candidate(candidate_id)}


@app.post("/api/candidates/{candidate_id}/delete")
def delete_candidate_action(candidate_id: int) -> dict[str, Any]:
    return delete_candidate(candidate_id)


@app.delete("/api/candidates")
def clear_candidates(task_id: str | None = None) -> dict[str, Any]:
    if active_candidate_ids:
        raise HTTPException(status_code=409, detail="仍有候选正在下载，不能清空候选库")
    deleted = repository.clear_candidates(task_id=task_id)
    return {"deleted": deleted}


@app.post("/api/candidates/clear")
def clear_candidates_action(task_id: str | None = None) -> dict[str, Any]:
    return clear_candidates(task_id)


@app.post("/api/candidates/download", status_code=202)
def download_candidates(payload: DownloadPayload) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(int(item) for item in payload.candidate_ids))
    Thread(target=run_download_batch, args=(unique_ids,), daemon=True).start()
    return {"started": len(unique_ids), "candidate_ids": unique_ids}


@app.post("/api/candidates/{candidate_id}/download", status_code=202)
def download_candidate(candidate_id: int) -> dict[str, Any]:
    Thread(target=run_download_batch, args=([candidate_id],), daemon=True).start()
    return {"started": 1, "candidate_ids": [candidate_id]}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run("mentor_lite.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
