from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from mentor_lite.models import Candidate, KnowledgePoint
from mentor_lite.precheck import (
    build_query_variants as build_precheck_query_variants,
    deduplicate_videos,
    diversify_candidates,
    extract_core_terms,
    refresh_candidate_precheck,
)
from mentor_lite.settings import Settings

BV_RE = re.compile(r"/video/(BV[0-9A-Za-z]+)", re.IGNORECASE)
BILIBILI_VIDEO_RE = re.compile(
    r"((?:https?:)?//(?:www\.)?bilibili\.com/video/BV[0-9A-Za-z]+[^\s]*)"
    r"|((?:www\.)?bilibili\.com/video/BV[0-9A-Za-z]+[^\s]*)"
    r"|(/video/BV[0-9A-Za-z]+[^\s]*)",
    re.IGNORECASE,
)
PUNCT_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
GENERIC_TERMS = {
    "知识点",
    "讲解",
    "教学",
    "课程",
    "视频",
    "合集",
    "复习",
    "初中",
    "高中",
    "小学",
}

ProgressCallback = Callable[[str, int, str, int, int], None]


def compact_text(value: object) -> str:
    return PUNCT_RE.sub("", str(value or "").lower())


def extract_bilibili_video_text(value: str) -> str:
    candidate = value.strip()
    match = BILIBILI_VIDEO_RE.search(candidate)
    if match:
        candidate = match.group(0).rstrip(")]），。；;、")
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    elif candidate.startswith("www."):
        candidate = "https://" + candidate
    elif candidate.startswith("bilibili.com"):
        candidate = "https://" + candidate
    elif candidate.startswith("/"):
        candidate = "https://www.bilibili.com" + candidate
    return candidate


def normalize_bilibili_url(url: str) -> tuple[str, str] | None:
    candidate = extract_bilibili_video_text(url)
    match = BV_RE.search(candidate)
    if not match:
        return None
    bvid = match.group(1)
    return bvid, urlunsplit(("https", "www.bilibili.com", f"/video/{bvid}", "", ""))


def extract_page_number(url: str) -> int | None:
    query = parse_qs(urlsplit(extract_bilibili_video_text(url)).query)
    raw = next(iter(query.get("p") or []), None)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 1 else None


def parse_duration(value: str | None) -> int | None:
    if not value:
        return None
    chunks = value.strip().split(":")
    if not all(chunk.isdigit() for chunk in chunks):
        return None
    numbers = [int(chunk) for chunk in chunks]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return None


def _safe_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _selection_terms(keyword: str, search_query: str) -> list[str]:
    terms: list[str] = []
    for text in [keyword, search_query]:
        for term in extract_core_terms(text):
            cleaned = compact_text(term)
            if len(cleaned) < 2 or cleaned in GENERIC_TERMS:
                continue
            terms.append(cleaned)
    return list(dict.fromkeys(terms))


def select_bilibili_page(
    data: dict[str, object],
    keyword: str,
    search_query: str,
    requested_page: int | None = None,
) -> dict[str, object]:
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    typed_pages = [page for page in pages if isinstance(page, dict)]
    if not typed_pages:
        return {
            "page": None,
            "is_multipart": False,
            "selected_page_relevant": True,
            "selected_by": "no_page_metadata",
            "matched_terms": [],
        }

    is_multipart = len(typed_pages) > 1
    if requested_page is not None:
        for page in typed_pages:
            if _safe_int(page.get("page")) == requested_page:
                return {
                    "page": page,
                    "is_multipart": is_multipart,
                    "selected_page_relevant": True,
                    "selected_by": "requested_page",
                    "matched_terms": [],
                }

    terms = _selection_terms(keyword, search_query)
    ranked: list[tuple[int, int, dict[str, object], list[str]]] = []
    for page in typed_pages:
        part_text = compact_text(str(page.get("part") or ""))
        matched_terms = [term for term in terms if term in part_text]
        score = sum(10 + min(len(term), 8) for term in matched_terms)
        page_number = _safe_int(page.get("page")) or 1
        ranked.append((score, -page_number, page, matched_terms))

    best_score, _, best_page, matched_terms = max(ranked, key=lambda item: (item[0], item[1]))
    if is_multipart and best_score <= 0:
        return {
            "page": best_page,
            "is_multipart": True,
            "selected_page_relevant": False,
            "selected_by": "first_page_unmatched",
            "matched_terms": [],
        }
    return {
        "page": best_page,
        "is_multipart": is_multipart,
        "selected_page_relevant": True,
        "selected_by": "title_match" if is_multipart else "single_page",
        "matched_terms": matched_terms,
    }


def card_to_candidate(card: dict[str, Any]) -> Candidate | None:
    normalized = normalize_bilibili_url(str(card.get("url") or ""))
    title = str(card.get("title") or "").strip()
    if normalized is None or not title:
        return None
    bvid, canonical = normalized
    page_number = extract_page_number(str(card.get("url") or ""))
    external_id = f"{bvid}:p{page_number}" if page_number else bvid
    canonical_url = f"{canonical}?p={page_number}" if page_number else canonical
    return Candidate(
        source="bilibili",
        external_id=external_id,
        canonical_url=canonical_url,
        title=title,
        author=str(card.get("author") or "").strip(),
        duration_seconds=parse_duration(card.get("duration")),
        description=str(card.get("description") or "").strip(),
        cover_url=str(card.get("cover_url") or "").strip(),
        raw={
            "search_card": card,
            "bvid": bvid,
            "bilibili_bvid": bvid,
            "search_page_number": page_number,
        },
    )


def enrich_candidate(context: Any, candidate: Candidate, settings: Settings, point: KnowledgePoint, query: str) -> None:
    request_context = context.request
    bvid = str(candidate.raw.get("bvid") or candidate.external_id).split(":p", 1)[0]
    try:
        response = request_context.get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            timeout=settings.browser_timeout_ms,
        )
        payload = response.json() if response.ok else {}
        data = payload.get("data") or {}
    except Exception:
        data = {}
    if data:
        collection_title = str(data.get("title") or candidate.title)
        requested_page = candidate.raw.get("search_page_number")
        selected = select_bilibili_page(
            data,
            point.name,
            query,
            int(requested_page) if isinstance(requested_page, int) else None,
        )
        selected_page = selected.get("page") if isinstance(selected.get("page"), dict) else None
        page_number = _safe_int(selected_page.get("page")) if selected_page else None
        part_title = str(selected_page.get("part") or "").strip() if selected_page else ""
        page_duration = _safe_int(selected_page.get("duration")) if selected_page else None
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        if selected.get("is_multipart") and page_number is not None:
            candidate.external_id = f"{bvid}:p{page_number}"
            candidate.canonical_url = f"https://www.bilibili.com/video/{bvid}?p={page_number}"
            candidate.title = part_title or collection_title
            candidate.duration_seconds = page_duration or _safe_int(data.get("duration")) or candidate.duration_seconds
        else:
            candidate.external_id = bvid
            candidate.canonical_url = f"https://www.bilibili.com/video/{bvid}"
            candidate.title = collection_title or candidate.title
            candidate.duration_seconds = _safe_int(data.get("duration")) or candidate.duration_seconds
        candidate.author = str(owner.get("name") or candidate.author)
        candidate.description = str(data.get("desc") or candidate.description)
        candidate.cover_url = str(data.get("pic") or candidate.cover_url)
        candidate.published_at = str(data.get("pubdate") or candidate.published_at)
        candidate.view_count = int(stat.get("view") or 0)
        candidate.like_count = int(stat.get("like") or 0)
        candidate.comment_count = int(stat.get("reply") or 0)
        candidate.share_count = int(stat.get("share") or 0)
        candidate.favorite_count = int(stat.get("favorite") or 0)
        candidate.danmaku_count = int(stat.get("danmaku") or 0)
        pages = [item for item in data.get("pages") or [] if isinstance(item, dict)]
        candidate.raw["bilibili_view"] = {
            "bvid": bvid,
            "aid": data.get("aid"),
            "cid": selected_page.get("cid") if selected_page else data.get("cid"),
            "collection_title": collection_title,
            "collection_duration_seconds": _safe_int(data.get("duration")),
            "is_multipart": bool(selected.get("is_multipart")),
            "selected_page_number": page_number,
            "selected_page_title": part_title or None,
            "selected_page_duration_seconds": page_duration,
            "selected_page_relevant": bool(selected.get("selected_page_relevant")),
            "selected_by": selected.get("selected_by"),
            "matched_terms": selected.get("matched_terms") or [],
            "page_count": len(pages),
            "stat": stat,
        }
        aid = data.get("aid")
        if aid:
            candidate.comments = fetch_comments(request_context, aid, settings)


def prechecked_candidates(candidates: list[Candidate], target_count: int | None = None) -> list[Candidate]:
    unique = deduplicate_videos(candidates)
    return diversify_candidates(unique, target_count)


def should_skip_multipart_candidate(candidate: Candidate) -> bool:
    view = candidate.raw.get("bilibili_view")
    if not isinstance(view, dict):
        return False
    return bool(view.get("is_multipart")) and not bool(view.get("selected_page_relevant"))


def apply_precheck(candidate: Candidate, result: Any) -> None:
    candidate.precheck_score = result.total_score
    candidate.precheck_reason = result.reason
    candidate.score_breakdown = result.score_breakdown


def select_page(
    pages: list[dict[str, Any]],
    point: KnowledgePoint,
    query: str,
    requested_page: object,
) -> dict[str, Any] | None:
    if not pages:
        return None
    try:
        requested = int(requested_page) if requested_page else None
    except (TypeError, ValueError):
        requested = None
    if requested:
        for page in pages:
            if int(page.get("page") or 0) == requested:
                return page
    terms = [compact_text(point.name), compact_text(point.group), compact_text(query)]
    terms = [term for term in terms if term]
    ranked = []
    for page in pages:
        part = compact_text(page.get("part"))
        score = sum(1 for term in terms if term in part)
        ranked.append((score, -int(page.get("page") or 1), page))
    return max(ranked, key=lambda item: (item[0], item[1]))[2]


def fetch_comments(request_context: Any, aid: object, settings: Settings) -> list[str]:
    urls = [
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode=3&next=0&ps=10",
        f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&sort=2&pn=1&ps=10",
    ]
    for url in urls:
        try:
            response = request_context.get(url, timeout=settings.browser_timeout_ms)
            payload = response.json() if response.ok else {}
            replies = (payload.get("data") or {}).get("replies") or []
            comments = [
                str((reply.get("content") or {}).get("message") or "").strip()
                for reply in replies
                if isinstance(reply, dict)
            ]
            comments = [comment for comment in comments if comment]
            if comments:
                return comments[:10]
        except Exception:
            continue
    return []


def search_bilibili(
    settings: Settings,
    point: KnowledgePoint,
    *,
    keyword: str = "",
    target_count: int = 100,
    progress: ProgressCallback | None = None,
) -> list[Candidate]:
    from playwright.sync_api import sync_playwright

    scoring_keyword = keyword.strip() or point.name
    knowledge_context = point.to_context()
    queries = build_precheck_query_variants(scoring_keyword, settings.rules_path, knowledge_context)
    accepted: list[Candidate] = []
    seen: set[str] = set()
    processed = 0

    def emit(stage: str, percent: int, message: str) -> None:
        if progress:
            progress(stage, percent, message, processed, len(prechecked_candidates(accepted, target_count)))

    emit("PREPARING", 5, f"生成 {len(queries)} 组搜索词，目标粗筛 {target_count} 条")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.headless)
        context_kwargs: dict[str, Any] = {"user_agent": settings.user_agent, "locale": "zh-CN"}
        if settings.auth_state_path.exists():
            context_kwargs["storage_state"] = str(settings.auth_state_path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(settings.browser_timeout_ms)
        try:
            for query in queries:
                if len(prechecked_candidates(accepted, target_count)) >= target_count:
                    break
                for page_number in range(1, settings.max_search_pages + 1):
                    if len(prechecked_candidates(accepted, target_count)) >= target_count:
                        break
                    unique_count = len(prechecked_candidates(accepted, target_count))
                    emit("QUERYING", min(90, 8 + unique_count * 75 // max(target_count, 1)), f"搜索：{query} / 第 {page_number} 页")
                    page.goto(
                        f"https://search.bilibili.com/video?keyword={quote_plus(query)}&page={page_number}",
                        wait_until="domcontentloaded",
                    )
                    try:
                        page.wait_for_selector('a[href*="/video/BV"]')
                    except Exception:
                        break
                    time.sleep(settings.crawl_delay_seconds)
                    cards = page.locator('a[href*="/video/BV"]').evaluate_all(
                        """
                        (anchors) => anchors.map((a) => {
                          const card = a.closest('.bili-video-card, .video-list-item, li, article') || a.parentElement;
                          const text = (selector) => card?.querySelector(selector)?.textContent?.trim() || null;
                          const image = card?.querySelector('img');
                          return {
                            url: a.href,
                            title: a.getAttribute('title') || text('h3') || text('.title') || a.textContent?.trim(),
                            author: text('.bili-video-card__info--author') || text('.up-name') || text('.author'),
                            duration: text('.bili-video-card__stats__duration') || text('.duration'),
                            description: text('.desc') || text('.description'),
                            cover_url: image?.currentSrc || image?.src || image?.getAttribute('data-src') || null
                          };
                        })
                        """
                    )
                    new_candidates = []
                    for card in cards:
                        candidate = card_to_candidate(card)
                        if candidate is None:
                            continue
                        key = candidate.external_id.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        new_candidates.append(candidate)
                    if not new_candidates:
                        break
                    for candidate in new_candidates:
                        processed += 1
                        unique_count = len(prechecked_candidates(accepted, target_count))
                        emit("ENRICHING", min(92, 10 + unique_count * 76 // max(target_count, 1)), f"补充视频信息：{candidate.title[:32]}")
                        enrich_candidate(context, candidate, settings, point, query)
                        candidate.raw["search_query"] = query
                        candidate.raw["search_page"] = page_number
                        candidate.raw["comments"] = candidate.comments
                        if should_skip_multipart_candidate(candidate):
                            emit("PRECHECKING", min(94, 12 + unique_count * 78 // max(target_count, 1)), f"跳过合集：未定位到匹配分集 {candidate.title[:32]}")
                            continue
                        result = refresh_candidate_precheck(
                            candidate,
                            scoring_keyword,
                            settings.rules_path,
                            settings.max_duration_seconds,
                            knowledge_context,
                        )
                        apply_precheck(candidate, result)
                        if result.accepted:
                            accepted.append(candidate)
                            unique = prechecked_candidates(accepted, target_count)
                            emit("DEDUPING", min(94, 12 + len(unique) * 78 // max(target_count, 1)), f"通过粗筛并去重 {len(unique)}/{target_count}")
        finally:
            context.close()
            browser.close()
    unique = prechecked_candidates(accepted, target_count)[:target_count]
    emit("COMPLETED", 100, f"粗筛完成，共 {len(unique)} 条候选")
    return unique


def domain_matches(cookie_domain: str, host: str) -> bool:
    normalized = cookie_domain.lstrip(".").lower()
    host = host.lower()
    return host == normalized or host.endswith(f".{normalized}")


def storage_cookie_header(storage_state: Path | None, url: str) -> tuple[str | None, dict[str, Any]]:
    if storage_state is None or not storage_state.exists():
        return None, {"used": False, "path": str(storage_state) if storage_state else None}
    payload = json.loads(storage_state.read_text(encoding="utf-8"))
    parts = urlsplit(url)
    now = time.time()
    cookies: list[str] = []
    names: set[str] = set()
    for cookie in payload.get("cookies") or []:
        domain = str(cookie.get("domain") or "")
        path = str(cookie.get("path") or "/")
        expires = cookie.get("expires")
        if not domain_matches(domain, parts.hostname or ""):
            continue
        if not parts.path.startswith(path):
            continue
        if isinstance(expires, (int, float)) and expires > 0 and expires < now:
            continue
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if name and value:
            cookies.append(f"{name}={value}")
            names.add(name)
    return (
        "; ".join(cookies) if cookies else None,
        {
            "used": bool(cookies),
            "path": str(storage_state),
            "cookie_count": len(cookies),
            "has_sessdata": "SESSDATA" in names,
            "has_dedeuserid": "DedeUserID" in names,
        },
    )


def request_json(
    url: str,
    *,
    referer: str | None = None,
    cookie_header: str | None = None,
    user_agent: str,
    delay_seconds: float = 0.0,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    headers = {"User-Agent": user_agent, "Accept": "application/json,text/plain,*/*"}
    if referer:
        headers["Referer"] = referer
    if cookie_header:
        headers["Cookie"] = cookie_header
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def auth_summary(settings: Settings, *, live: bool = False) -> dict[str, Any]:
    path = settings.auth_state_path
    if not path.exists():
        return {
            "platform": "bilibili",
            "authorized": False,
            "state_path": str(path),
            "message": "未授权",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "platform": "bilibili",
            "authorized": False,
            "state_path": str(path),
            "message": "授权文件无法读取",
        }
    cookies = payload.get("cookies") or []
    names = {str(cookie.get("name") or "") for cookie in cookies}
    expires_values = [
        float(cookie.get("expires"))
        for cookie in cookies
        if cookie.get("name") == "SESSDATA" and isinstance(cookie.get("expires"), (int, float))
    ]
    expired = bool(expires_values and max(expires_values) > 0 and max(expires_values) < time.time())
    authorized = {"SESSDATA", "DedeUserID"}.issubset(names) and not expired
    result: dict[str, Any] = {
        "platform": "bilibili",
        "authorized": authorized,
        "expired": expired,
        "state_path": str(path),
        "cookie_count": len(cookies),
        "has_sessdata": "SESSDATA" in names,
        "has_dedeuserid": "DedeUserID" in names,
        "message": "已授权" if authorized else "授权已过期或不完整",
    }
    if live:
        nav_url = "https://api.bilibili.com/x/web-interface/nav"
        cookie_header, _ = storage_cookie_header(path, nav_url)
        if not cookie_header:
            result.update({"live_ok": False, "live_status": "NO_COOKIE"})
            return result
        try:
            payload = request_json(
                nav_url,
                referer="https://www.bilibili.com/",
                cookie_header=cookie_header,
                user_agent=settings.user_agent,
                timeout_seconds=8,
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            result.update(
                {
                    "live_ok": bool(data.get("isLogin")),
                    "live_status": "LOGGED_IN" if data.get("isLogin") else "NOT_LOGGED_IN",
                }
            )
        except Exception as exc:
            result.update({"live_ok": None, "live_status": "CHECK_FAILED", "live_message": str(exc)})
    return result


def parse_bilibili_reference(value: str) -> tuple[str, str, int | None]:
    raw = value.strip()
    direct = re.match(r"^(BV[0-9A-Za-z]+)(?::p(\d+))?$", raw, re.IGNORECASE)
    if direct:
        bvid = direct.group(1)
        page_number = int(direct.group(2)) if direct.group(2) else None
        url = f"https://www.bilibili.com/video/{bvid}"
        if page_number:
            url += f"?p={page_number}"
        return bvid, url, page_number
    normalized = normalize_bilibili_url(raw)
    if normalized is None:
        raise ValueError("请输入 B站 BV 号或视频页面链接")
    bvid, page_url = normalized
    page_number = extract_page_number(raw)
    if page_number:
        page_url += f"?p={page_number}"
    return bvid, page_url, page_number


def select_download_page(view_data: dict[str, Any], page_number: int | None) -> dict[str, Any]:
    pages = [page for page in (view_data.get("pages") or []) if isinstance(page, dict)]
    if page_number is not None:
        for page in pages:
            if int(page.get("page") or 0) == page_number:
                return page
        raise RuntimeError(f"未找到第 {page_number} 个分 P")
    if pages:
        return pages[0]
    return {
        "page": 1,
        "cid": view_data.get("cid"),
        "part": view_data.get("title"),
        "duration": view_data.get("duration"),
    }


def playurl_for_quality(bvid: str, cid: object, qn: int) -> str:
    return "https://api.bilibili.com/x/player/playurl?" + urlencode(
        {"bvid": bvid, "cid": cid, "qn": qn, "fnval": 16, "fnver": 0, "fourk": 1 if qn >= 120 else 0}
    )
