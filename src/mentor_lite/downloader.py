from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from mentor_lite.bilibili import (
    parse_bilibili_reference,
    playurl_for_quality,
    request_json,
    select_download_page,
    storage_cookie_header,
)
from mentor_lite.settings import Settings

ProgressCallback = Callable[[int, str, str], None]
QUALITY_LADDER = (127, 126, 125, 120, 116, 112, 80, 74, 64, 32, 16, 6)
QUALITY_LABELS = {
    127: "8K",
    126: "Dolby Vision",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
    6: "240P",
}
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_name(value: object, fallback: str = "untitled", max_length: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = text.strip(" ._")
    if not text:
        text = fallback
    if text.upper() in RESERVED_NAMES:
        text = f"{text}_"
    return text[:max_length].strip(" ._") or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}_{int(time.time())}{path.suffix}")


def quality_label(value: object) -> str:
    try:
        return QUALITY_LABELS.get(int(value), str(value))
    except (TypeError, ValueError):
        return str(value or "")


def quality_ladder(max_qn: int) -> list[int]:
    values = [quality for quality in QUALITY_LADDER if quality <= max_qn]
    if max_qn not in values:
        values.insert(0, max_qn)
    return values


def stream_rank(stream: dict[str, Any]) -> tuple[int, int]:
    return int(stream.get("id") or stream.get("quality") or 0), int(stream.get("bandwidth") or 0)


def select_streams(play_data: dict[str, Any]) -> list[dict[str, Any]]:
    dash = play_data.get("dash") or {}
    streams: list[dict[str, Any]] = []
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    if videos:
        streams.append({"kind": "video", **sorted(videos, key=stream_rank, reverse=True)[0]})
    if audios:
        streams.append({"kind": "audio", **sorted(audios, key=stream_rank, reverse=True)[0]})
    if streams:
        return streams
    durl = play_data.get("durl") or []
    if durl:
        return [{"kind": "combined", **durl[0]}]
    return []


def stream_url(stream: dict[str, Any]) -> str:
    value = stream.get("baseUrl") or stream.get("base_url") or stream.get("url")
    if not value:
        raise RuntimeError(f"媒体流缺少下载地址：{stream.get('kind')}")
    return str(value)


def backup_urls(stream: dict[str, Any]) -> list[str]:
    values = stream.get("backupUrl") or stream.get("backup_url") or []
    if isinstance(values, str):
        return [values]
    if isinstance(values, list):
        return [str(value) for value in values if value]
    return []


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "...", ""))


def download_stream(
    urls: list[str],
    output_path: Path,
    *,
    referer: str,
    cookie_header: str | None,
    user_agent: str,
    max_bytes: int,
    progress: ProgressCallback | None,
    base_percent: int,
    span_percent: int,
    stage: str,
    message: str,
) -> dict[str, Any]:
    errors: list[str] = []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for index, url in enumerate(urls):
        try:
            headers = {
                "User-Agent": user_agent,
                "Referer": referer,
                "Origin": "https://www.bilibili.com",
                "Accept": "*/*",
            }
            if cookie_header:
                headers["Cookie"] = cookie_header
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                content_length = response.headers.get("Content-Length")
                total_bytes = int(content_length) if content_length and content_length.isdigit() else None
                if total_bytes and total_bytes > max_bytes:
                    raise RuntimeError(f"媒体流超过安全上限：{total_bytes} > {max_bytes}")
                bytes_written = 0
                last_emit = -1
                with output_path.open("wb") as output:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        bytes_written += len(chunk)
                        if bytes_written > max_bytes:
                            raise RuntimeError(f"媒体流超过安全上限：{bytes_written} > {max_bytes}")
                        if progress and total_bytes:
                            percent = base_percent + int(bytes_written * span_percent / max(total_bytes, 1))
                            percent = min(99, percent)
                            if percent != last_emit:
                                progress(percent, stage, message)
                                last_emit = percent
                return {
                    "file": str(output_path),
                    "bytes_written": bytes_written,
                    "url": redact_url(url),
                    "url_index": index,
                    "fallback_errors": errors,
                }
        except Exception as exc:
            errors.append(f"{redact_url(url)} -> {exc}")
            if output_path.exists():
                output_path.unlink()
    raise RuntimeError("; ".join(errors) if errors else "媒体流下载失败")


def ffmpeg_executable() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def merge_streams(video_path: Path, audio_path: Path | None, output_path: Path) -> dict[str, Any]:
    command = [ffmpeg_executable(), "-y", "-i", str(video_path)]
    if audio_path is not None:
        command.extend(["-i", str(audio_path)])
    command.extend(["-c", "copy", str(output_path)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    return {
        "merged": result.returncode == 0,
        "file": str(output_path) if result.returncode == 0 else "",
        "returncode": result.returncode,
        "stderr_tail": result.stderr[-1000:],
    }


def download_bilibili_flat(
    settings: Settings,
    value: str,
    *,
    knowledge_name: str,
    video_title: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    def emit(percent: int, stage: str, message: str) -> None:
        if progress:
            progress(percent, stage, message)

    bvid, page_url, page_number = parse_bilibili_reference(value)
    max_bytes = max(1, int(settings.max_stream_mb * 1024 * 1024))
    view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    view_cookie, auth = storage_cookie_header(settings.auth_state_path if settings.auth_state_path.exists() else None, view_url)

    emit(4, "METADATA", "正在解析视频信息")
    view_payload = request_json(
        view_url,
        referer=page_url,
        cookie_header=view_cookie,
        user_agent=settings.user_agent,
        delay_seconds=settings.crawl_delay_seconds,
    )
    if view_payload.get("code") != 0:
        raise RuntimeError(f"视频信息接口失败：{view_payload.get('message')}")
    view_data = view_payload.get("data") or {}
    selected_page = select_download_page(view_data, page_number)
    cid = selected_page.get("cid")
    if not cid:
        raise RuntimeError("未取得视频 cid")

    temp_dir = settings.temp_dir / safe_name(bvid, bvid)
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "source": "bilibili",
        "bvid": bvid,
        "page_url": page_url,
        "title": selected_page.get("part") or view_data.get("title") or video_title,
        "collection_title": view_data.get("title"),
        "duration_seconds": selected_page.get("duration") or view_data.get("duration"),
        "auth": auth,
        "quality_attempts": [],
    }
    last_error = "未找到可下载媒体流"
    try:
        for qn in quality_ladder(settings.qn):
            label = quality_label(qn)
            emit(10, "QUALITY", f"尝试清晰度 {label}")
            play_url = playurl_for_quality(bvid, cid, qn)
            play_cookie, play_auth = storage_cookie_header(
                settings.auth_state_path if settings.auth_state_path.exists() else None,
                play_url,
            )
            attempt: dict[str, Any] = {"requested_qn": qn, "requested_label": label, "auth": play_auth}
            try:
                play_payload = request_json(
                    play_url,
                    referer=page_url,
                    cookie_header=play_cookie,
                    user_agent=settings.user_agent,
                    delay_seconds=settings.crawl_delay_seconds,
                )
                if play_payload.get("code") != 0:
                    raise RuntimeError(str(play_payload.get("message") or "playurl 接口异常"))
                play_data = play_payload.get("data") or {}
                streams = select_streams(play_data)
                if not streams:
                    raise RuntimeError("接口未返回媒体流")
                downloaded_video: Path | None = None
                downloaded_audio: Path | None = None
                stream_reports: list[dict[str, Any]] = []
                for stream in streams:
                    kind = str(stream.get("kind"))
                    suffix = "m4s" if kind in {"video", "audio"} else "mp4"
                    stream_path = temp_dir / f"{kind}.{suffix}"
                    base, span = (18, 46) if kind == "video" else ((66, 24) if kind == "audio" else (18, 72))
                    stream_cookie, _ = storage_cookie_header(
                        settings.auth_state_path if settings.auth_state_path.exists() else None,
                        stream_url(stream),
                    )
                    stream_reports.append(
                        download_stream(
                            [stream_url(stream), *backup_urls(stream)],
                            stream_path,
                            referer=page_url,
                            cookie_header=stream_cookie,
                            user_agent=settings.user_agent,
                            max_bytes=max_bytes,
                            progress=progress,
                            base_percent=base,
                            span_percent=span,
                            stage=f"DOWNLOAD_{kind.upper()}",
                            message=f"正在下载 {kind} 流",
                        )
                    )
                    if kind in {"video", "combined"}:
                        downloaded_video = stream_path
                    if kind == "audio":
                        downloaded_audio = stream_path
                if downloaded_video is None:
                    raise RuntimeError("没有下载到视频流")
                emit(92, "MERGING", "正在合并音视频")
                merged_path = temp_dir / "main.mp4"
                merge = merge_streams(downloaded_video, downloaded_audio, merged_path)
                if not merge.get("merged"):
                    raise RuntimeError(str(merge.get("stderr_tail") or "FFmpeg 合并失败"))
                knowledge_dir = settings.download_dir / safe_name(knowledge_name, "未命名知识点")
                knowledge_dir.mkdir(parents=True, exist_ok=True)
                final_path = unique_path(knowledge_dir / f"{safe_name(video_title or report['title'], bvid)}.mp4")
                emit(98, "ORGANIZING", "正在整理到知识点目录")
                shutil.move(str(merged_path), final_path)
                attempt.update({"status": "completed", "streams": stream_reports, "merge": merge})
                report.update(
                    {
                        "selected_qn": qn,
                        "selected_label": label,
                        "streams": stream_reports,
                        "merge": {**merge, "file": str(final_path)},
                        "main_file": str(final_path),
                        "download_dir": str(knowledge_dir),
                    }
                )
                return report
            except Exception as exc:
                last_error = str(exc)
                attempt.update({"status": "failed", "error": last_error[:500]})
                report["quality_attempts"].append(attempt)
                continue
        raise RuntimeError(f"所有清晰度下载失败：{last_error}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
