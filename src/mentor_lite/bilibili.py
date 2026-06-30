from __future__ import annotations

import json
import math
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from mentor_lite.models import Candidate, KnowledgePoint
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


def build_query_variants(point: KnowledgePoint, keyword: str = "") -> list[str]:
    name = point.name.strip()
    seed = keyword.strip() or name
    values = [
        f"{point.stage}{point.subject} {point.grade} {name} 知识点 讲解",
        f"{point.grade}{point.subject} {name} 讲解",
        f"{point.subject} {point.chapter} {name}",
        f"{point.subject} {point.group} {name} 讲解",
        seed,
    ]
    return [re.sub(r"\s+", " ", item).strip() for item in dict.fromkeys(values) if item.strip()]


def score_candidate(candidate: Candidate, point: KnowledgePoint, keyword: str = "") -> None:
    terms = [point.name, *point.aliases, point.group, point.chapter, keyword]
    terms = [compact_text(term) for term in terms if compact_text(term) and compact_text(term) not in GENERIC_TERMS]
    terms = list(dict.fromkeys(terms))
    title = compact_text(candidate.title)
    description = compact_text(candidate.description)
    comments = compact_text(" ".join(candidate.comments[:10]))
    evidence = title + description + comments
    subject_terms = [compact_text(point.subject), compact_text(point.grade), compact_text(point.stage)]
    subject_terms = [term for term in subject_terms if term]

    title_hits = sum(1 for term in terms if term in title)
    evidence_hits = sum(1 for term in terms if term in evidence)
    subject_hits = sum(1 for term in subject_terms if term in evidence)
    title_score = min(45, title_hits * 22)
    evidence_score = min(24, evidence_hits * 8)
    subject_score = min(12, subject_hits * 4)
    engagement_score = min(math.log10(candidate.view_count + 1) / 6, 1) * 12
    interaction_score = min(math.log10(candidate.like_count + candidate.favorite_count + 1) / 5, 1) * 7
    duration_penalty = 0
    if candidate.duration_seconds and candidate.duration_seconds > 15 * 60:
        duration_penalty = min(18, (candidate.duration_seconds - 15 * 60) / 60)
    score = max(0.0, min(100.0, title_score + evidence_score + subject_score + engagement_score + interaction_score - duration_penalty))
    candidate.precheck_score = round(score, 2)
    candidate.score_breakdown = {
        "title_score": round(title_score, 2),
        "evidence_score": round(evidence_score, 2),
        "subject_score": round(subject_score, 2),
        "engagement_score": round(engagement_score, 2),
        "interaction_score": round(interaction_score, 2),
        "duration_penalty": round(duration_penalty, 2),
    }
    if title_hits:
        candidate.precheck_reason = "标题命中知识点关键词"
    elif evidence_hits:
        candidate.precheck_reason = "简介或评论命中知识点关键词"
    elif subject_hits:
        candidate.precheck_reason = "存在学段/学科语境，建议人工复核"
    else:
        candidate.precheck_reason = "相关性较弱"


def is_candidate_accepted(candidate: Candidate) -> bool:
    return candidate.precheck_score >= 48


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
        raw={"search_card": card, "bvid": bvid, "search_page_number": page_number},
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
        pages = [item for item in data.get("pages") or [] if isinstance(item, dict)]
        selected_page = select_page(pages, point, query, candidate.raw.get("search_page_number"))
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        if selected_page:
            page_number = int(selected_page.get("page") or 1)
            candidate.external_id = f"{bvid}:p{page_number}" if len(pages) > 1 else bvid
            candidate.canonical_url = (
                f"https://www.bilibili.com/video/{bvid}?p={page_number}"
                if len(pages) > 1
                else f"https://www.bilibili.com/video/{bvid}"
            )
            candidate.title = str(selected_page.get("part") or data.get("title") or candidate.title)
            candidate.duration_seconds = int(selected_page.get("duration") or data.get("duration") or 0) or candidate.duration_seconds
            candidate.raw["selected_page"] = selected_page
        else:
            candidate.title = str(data.get("title") or candidate.title)
            candidate.duration_seconds = int(data.get("duration") or 0) or candidate.duration_seconds
        candidate.author = str(owner.get("name") or candidate.author)
        candidate.description = str(data.get("desc") or candidate.description)
        candidate.cover_url = str(data.get("pic") or candidate.cover_url)
        candidate.published_at = str(data.get("pubdate") or candidate.published_at)
        candidate.view_count = int(stat.get("view") or 0)
        candidate.like_count = int(stat.get("like") or 0)
        candidate.comment_count = int(stat.get("reply") or 0)
        candidate.favorite_count = int(stat.get("favorite") or 0)
        candidate.danmaku_count = int(stat.get("danmaku") or 0)
        candidate.raw["view"] = {
            "aid": data.get("aid"),
            "cid": selected_page.get("cid") if selected_page else data.get("cid"),
            "page_count": len(pages),
        }
        aid = data.get("aid")
        if aid:
            candidate.comments = fetch_comments(request_context, aid, settings)


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


def near_duplicate(left: Candidate, right: Candidate) -> bool:
    left_bvid = left.external_id.split(":p", 1)[0].lower()
    right_bvid = right.external_id.split(":p", 1)[0].lower()
    if left_bvid == right_bvid:
        return True
    left_title = compact_text(left.title)
    right_title = compact_text(right.title)
    if left_title and right_title and SequenceMatcher(None, left_title, right_title).ratio() >= 0.9:
        if not left.author or not right.author or left.author == right.author:
            return True
    return False


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    result: list[Candidate] = []
    for candidate in sorted(candidates, key=lambda item: item.precheck_score, reverse=True):
        if not any(near_duplicate(candidate, kept) for kept in result):
            result.append(candidate)
    return result


def search_bilibili(
    settings: Settings,
    point: KnowledgePoint,
    *,
    keyword: str = "",
    target_count: int = 100,
    progress: ProgressCallback | None = None,
) -> list[Candidate]:
    from playwright.sync_api import sync_playwright

    queries = build_query_variants(point, keyword)
    accepted: list[Candidate] = []
    seen: set[str] = set()
    processed = 0

    def emit(stage: str, percent: int, message: str) -> None:
        if progress:
            progress(stage, percent, message, processed, len(dedupe_candidates(accepted)))

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
                if len(dedupe_candidates(accepted)) >= target_count:
                    break
                for page_number in range(1, settings.max_search_pages + 1):
                    if len(dedupe_candidates(accepted)) >= target_count:
                        break
                    emit("QUERYING", min(90, 8 + len(dedupe_candidates(accepted)) * 75 // max(target_count, 1)), f"搜索：{query} / 第 {page_number} 页")
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
                        emit("ENRICHING", min(92, 10 + len(dedupe_candidates(accepted)) * 76 // max(target_count, 1)), f"补充视频信息：{candidate.title[:32]}")
                        enrich_candidate(context, candidate, settings, point, query)
                        score_candidate(candidate, point, keyword or query)
                        if is_candidate_accepted(candidate):
                            accepted.append(candidate)
                            accepted = dedupe_candidates(accepted)
                            emit("PRECHECKING", min(94, 12 + len(accepted) * 78 // max(target_count, 1)), f"通过粗筛 {len(accepted)}/{target_count}")
        finally:
            context.close()
            browser.close()
    accepted = dedupe_candidates(accepted)[:target_count]
    emit("COMPLETED", 100, f"粗筛完成，共 {len(accepted)} 条候选")
    return accepted


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
