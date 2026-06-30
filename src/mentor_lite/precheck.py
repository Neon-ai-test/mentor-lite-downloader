from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from mentor_lite.models import Candidate as DiscoveredVideo

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "precheck_rules.yaml"
DEFAULT_MAX_DURATION_SECONDS = 15 * 60
_PARTICLES = re.compile(r"(的定义|定义是什么|是什么|怎么求|如何求|怎么学|知识点|讲解|教学|课程)$")
_PUNCTUATION = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
_BVID_PATTERN = re.compile(r"(BV[0-9A-Za-z]+)", re.IGNORECASE)
_EPISODE_MARKER_RE = re.compile(
    r"(?i)(?:^|[\s:：#_\-])p\s*\d+|第\s*\d+\s*(?:集|讲|节|课)|^\s*\d+\s*[.、:：_\-]"
)
_COLLECTION_MARKER_RE = re.compile(
    r"(?i)(全\s*\d+\s*集|全集|合集|大合集|全册|全套|系统课|动画版|爆笑|趣味动画|collection|full\s*\d*|episode)"
)
_RULE_CACHE: dict[Path, tuple[tuple[tuple[Path, int], ...], dict[str, Any]]] = {}
SUBJECT_TERMS = (
    "语文",
    "数学",
    "英语",
    "物理",
    "化学",
    "生物",
    "科学",
    "历史",
    "地理",
    "道德与法治",
    "政治",
)


@dataclass(frozen=True, slots=True)
class KnowledgeContext:
    knowledge_point_id: str
    keyword: str
    entity: str
    entity_terms: tuple[str, ...]
    intent: str
    intent_terms: tuple[str, ...]
    subject: str
    stage: str
    grade: str
    related_terms: tuple[str, ...]
    reject_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrecheckResult:
    accepted: bool
    relevance_score: float
    engagement_score: float
    total_score: float
    reason: str
    score_breakdown: dict[str, float] = field(default_factory=dict)


def load_rules(path: Path | str | None = None) -> dict[str, Any]:
    rules_path = Path(path or DEFAULT_RULES_PATH).resolve()
    if not rules_path.exists():
        raise FileNotFoundError(f"Precheck rules file was not found: {rules_path}")
    cached = _RULE_CACHE.get(rules_path)
    if cached:
        signature, cached_rules = cached
        if all(file_signature(file_path) == modified for file_path, modified in signature):
            return cached_rules
    with rules_path.open("r", encoding="utf-8") as stream:
        rules = yaml.safe_load(stream) or {}
    if not isinstance(rules, dict) or "scoring" not in rules:
        raise ValueError("Precheck rules must be a mapping containing 'scoring'")
    catalog: list[dict[str, Any]] = []
    catalog_paths: list[Path] = []
    for configured_path in rules.get("catalog_files") or []:
        catalog_path = (rules_path.parent / str(configured_path)).resolve()
        if not catalog_path.exists():
            raise FileNotFoundError(f"Knowledge catalog was not found: {catalog_path}")
        with catalog_path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream) or {}
        catalog.extend(payload.get("knowledge_points") or [])
        catalog_paths.append(catalog_path)
    for configured_path in rules.get("dynamic_catalog_files") or []:
        catalog_path = (rules_path.parent / str(configured_path)).resolve()
        if not catalog_path.exists():
            catalog_paths.append(catalog_path)
            continue
        with catalog_path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream) or {}
        catalog.extend(payload.get("knowledge_points") or [])
        catalog_paths.append(catalog_path)
    catalog = deduplicate_catalog(catalog)
    rules["_knowledge_catalog"] = catalog
    signature = tuple(
        (file_path, file_signature(file_path))
        for file_path in [rules_path, *catalog_paths]
    )
    _RULE_CACHE[rules_path] = (signature, rules)
    return rules


def get_knowledge_catalog(path: Path | str | None = None) -> list[dict[str, Any]]:
    return list(load_rules(path).get("_knowledge_catalog") or [])


def file_signature(file_path: Path) -> int:
    return file_path.stat().st_mtime_ns if file_path.exists() else -1


def deduplicate_catalog(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            identity_fields = ("subject", "stage", "grade", "textbook", "source", "chapter", "group", "name")
            item_id = "|".join(str(item.get(field) or "") for field in identity_fields)
        if not item_id:
            continue
        if item_id not in merged:
            order.append(item_id)
        merged[item_id] = item
    return [merged[item_id] for item_id in order]


def compact_text(value: str) -> str:
    return _PUNCTUATION.sub("", value.lower())


def extract_core_terms(keyword: str) -> list[str]:
    pieces = [piece for piece in re.split(r"[\s,，、/]+", keyword.strip()) if piece]
    terms: list[str] = []
    for piece in pieces:
        cleaned = _PARTICLES.sub("", piece).strip()
        if cleaned:
            terms.append(cleaned)
    return terms or [keyword.strip()]


def _unique_texts(values: list[object]) -> tuple[str, ...]:
    unique: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if text and text not in unique:
            unique.append(text)
    return tuple(unique)


def _snapshot_terms(knowledge_context: dict[str, object]) -> tuple[str, ...]:
    values: list[object] = []
    name = str(knowledge_context.get("name") or "").strip()
    values.extend(knowledge_context.get("aliases") or [])
    values.extend(extract_core_terms(name) if name else [])
    return _unique_texts(values)


def analyze_keyword(
    keyword: str,
    rules: dict[str, Any],
    knowledge_context: dict[str, object] | None = None,
) -> KnowledgeContext:
    if knowledge_context:
        name = str(knowledge_context.get("name") or keyword).strip()
        matching_text = " ".join(
            str(knowledge_context.get(field) or "")
            for field in ("name", "group", "chapter", "description")
        )
        configured_points = rules.get("knowledge_points") or {}
        point_rules: dict[str, Any] = {}
        entity = name or keyword.strip()
        for configured_entity, candidate_rules in configured_points.items():
            aliases = candidate_rules.get("aliases") or [configured_entity]
            if any(str(alias).lower() in matching_text.lower() for alias in aliases):
                entity = str(configured_entity)
                point_rules = candidate_rules
                break
        entity_terms = tuple(str(term) for term in (point_rules.get("aliases") or []))
        if not entity_terms:
            entity_terms = _snapshot_terms(knowledge_context) or tuple(extract_core_terms(entity))

        intent = ""
        intent_terms: tuple[str, ...] = ()
        intent_source = " ".join([keyword, name, str(knowledge_context.get("group") or "")])
        for configured_intent, intent_rules in (rules.get("intents") or {}).items():
            aliases = tuple(str(alias) for alias in (intent_rules.get("aliases") or []))
            if any(alias.lower() in intent_source.lower() for alias in aliases):
                intent = str(configured_intent)
                intent_terms = aliases
                break

        related_terms = _unique_texts(
            [
                *(point_rules.get("related_terms") or []),
                knowledge_context.get("chapter"),
                knowledge_context.get("group"),
            ]
        )
        return KnowledgeContext(
            knowledge_point_id=str(knowledge_context.get("id") or ""),
            keyword=keyword.strip() or name,
            entity=entity,
            entity_terms=entity_terms or (entity,),
            intent=intent,
            intent_terms=intent_terms,
            subject=str(knowledge_context.get("subject") or "").strip(),
            stage=str(knowledge_context.get("stage") or "").strip(),
            grade=str(knowledge_context.get("grade") or "").strip(),
            related_terms=related_terms,
            reject_terms=tuple(str(term) for term in (point_rules.get("reject_terms") or [])),
        )

    catalog_match: dict[str, Any] = {}
    catalog_candidates: list[tuple[int, dict[str, Any]]] = []
    for item in rules.get("_knowledge_catalog") or []:
        names = [str(item.get("name") or ""), *[str(alias) for alias in item.get("aliases") or []]]
        longest_match = max((len(name) for name in names if name and name in keyword), default=0)
        if longest_match:
            catalog_candidates.append((longest_match, item))
    if catalog_candidates:
        catalog_match = max(catalog_candidates, key=lambda candidate: candidate[0])[1]

    configured_points = rules.get("knowledge_points") or {}
    entity = ""
    point_rules: dict[str, Any] = {}
    matching_text = " ".join(
        [
            keyword,
            str(catalog_match.get("name") or ""),
            str(catalog_match.get("group") or ""),
            str(catalog_match.get("description") or ""),
        ]
    )
    for configured_entity, candidate_rules in configured_points.items():
        aliases = candidate_rules.get("aliases") or [configured_entity]
        if any(str(alias).lower() in matching_text.lower() for alias in aliases):
            entity = str(configured_entity)
            point_rules = candidate_rules
            break

    entity_terms = tuple(str(term) for term in (point_rules.get("aliases") or []))
    if not entity:
        fallback = extract_core_terms(str(catalog_match.get("name") or keyword))
        entity = fallback[0]
        entity_terms = tuple(fallback)

    intent = ""
    intent_terms: tuple[str, ...] = ()
    for configured_intent, intent_rules in (rules.get("intents") or {}).items():
        aliases = tuple(str(alias) for alias in (intent_rules.get("aliases") or []))
        if any(alias.lower() in keyword.lower() for alias in aliases):
            intent = str(configured_intent)
            intent_terms = aliases
            break

    grades = [str(grade) for grade in (point_rules.get("grades") or [])]
    global_context = rules.get("context") or {}
    subject = str(catalog_match.get("subject") or global_context.get("subject") or "")
    stage = str(catalog_match.get("stage") or global_context.get("stage") or "")
    catalog_grade = str(catalog_match.get("grade") or "")
    grade = catalog_grade or next((grade for grade in grades if grade in keyword), grades[0] if grades else "")
    return KnowledgeContext(
        knowledge_point_id=str(catalog_match.get("id") or ""),
        keyword=keyword.strip(),
        entity=entity,
        entity_terms=entity_terms or (entity,),
        intent=intent,
        intent_terms=intent_terms,
        subject=subject,
        stage=stage,
        grade=grade,
        related_terms=tuple(str(term) for term in (point_rules.get("related_terms") or [])),
        reject_terms=tuple(str(term) for term in (point_rules.get("reject_terms") or [])),
    )


def build_education_query(
    keyword: str,
    rules_path: Path | str | None = None,
    knowledge_context: dict[str, object] | None = None,
) -> str:
    return build_query_variants(keyword, rules_path, knowledge_context)[0]


def build_query_variants(
    keyword: str,
    rules_path: Path | str | None = None,
    knowledge_context: dict[str, object] | None = None,
) -> list[str]:
    rules = load_rules(rules_path)
    context = analyze_keyword(keyword, rules, knowledge_context)
    values = {
        "keyword": context.keyword,
        "entity": context.entity,
        "intent": context.intent,
        "grade": context.grade,
        "subject": context.subject,
        "stage": context.stage,
    }
    variants: list[str] = []
    if knowledge_context:
        name = str(knowledge_context.get("name") or keyword).strip()
        group = str(knowledge_context.get("group") or "").strip()
        chapter = str(knowledge_context.get("chapter") or "").strip()
        explicit_values = [
            f"{context.stage}{context.subject} {context.grade} {name} 知识点 讲解",
            f"{context.grade}{context.subject} {name} 讲解",
            f"{context.subject} {name} {group} 讲解",
            f"{context.subject} {chapter} {name}",
        ]
        variants.extend(re.sub(r"\s+", " ", value).strip() for value in explicit_values)
    variants.extend(
        re.sub(r"\s+", " ", str(template).format(**values)).strip()
        for template in (rules.get("query_templates") or ["{keyword}"])
    )
    return [query for query in dict.fromkeys(variants) if query]


def engagement_score(video: DiscoveredVideo, rules: dict[str, Any]) -> float:
    weights = rules.get("engagement") or {}
    score = (
        min(math.log10(video.view_count + 1) / 6, 1) * float(weights.get("view_weight", 45))
        + min(math.log10(video.like_count + 1) / 5, 1) * float(weights.get("like_weight", 25))
        + min(math.log10(video.comment_count + 1) / 4, 1)
        * float(weights.get("comment_weight", 15))
        + min(math.log10(video.share_count + 1) / 4, 1)
        * float(weights.get("share_weight", 15))
    )
    return round(min(score, 100), 2)


def cover_ocr_text(video: DiscoveredVideo) -> str:
    payload = video.raw_metadata.get("cover_ocr")
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("status") or "").lower() != "ok":
        return ""
    return str(payload.get("text") or "")


def base_external_id(video: DiscoveredVideo) -> str:
    external_id = str(video.external_id or "")
    match = _BVID_PATTERN.search(external_id) or _BVID_PATTERN.search(video.canonical_url or "")
    if match:
        return match.group(1).lower()
    return external_id.split(":p", 1)[0].lower()


def normalize_dedup_text(value: str) -> str:
    text = _EPISODE_MARKER_RE.sub(" ", value)
    text = _COLLECTION_MARKER_RE.sub(" ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    return compact_text(text)


def cover_signature(video: DiscoveredVideo) -> str:
    return normalize_dedup_text(cover_ocr_text(video))


def has_collection_signal(video: DiscoveredVideo) -> bool:
    text = " ".join([video.title or "", video.description or "", cover_ocr_text(video)])
    return bool(_COLLECTION_MARKER_RE.search(text) or _EPISODE_MARKER_RE.search(text))


def series_key(video: DiscoveredVideo) -> str:
    text = " ".join([video.title or "", cover_ocr_text(video)])
    if not has_collection_signal(video):
        return ""
    normalized = normalize_dedup_text(text)
    if len(normalized) >= 4:
        author = compact_text(video.author or "")
        return f"{video.source}:series:{author}:{normalized[:32]}"
    base_id = base_external_id(video)
    if base_id:
        return f"{video.source}:bvid:{base_id}"
    author = compact_text(video.author or "")
    return f"{video.source}:series:{author}"


def duration_close(left: DiscoveredVideo, right: DiscoveredVideo) -> bool:
    if left.duration_seconds is None or right.duration_seconds is None:
        return True
    return abs(left.duration_seconds - right.duration_seconds) <= max(
        10,
        int(min(left.duration_seconds, right.duration_seconds) * 0.03),
    )


def candidate_rank_score(video: DiscoveredVideo) -> float:
    precheck = video.raw_metadata.get("precheck") or {}
    if not isinstance(precheck, dict):
        return 0.0
    score = precheck.get("secondary_score")
    if score is None:
        score = (precheck.get("score_breakdown") or {}).get("secondary_score")
    if score is None:
        score = precheck.get("total_score")
    try:
        return float(score or 0)
    except (TypeError, ValueError):
        return 0.0


def configured_max_duration_seconds(rules: dict[str, Any]) -> int:
    duration_rules = rules.get("duration") or {}
    raw_value = duration_rules.get("max_seconds", DEFAULT_MAX_DURATION_SECONDS)
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DURATION_SECONDS


def resolve_max_duration_seconds(
    rules: dict[str, Any],
    max_duration_seconds: int | None,
) -> int:
    if max_duration_seconds is None:
        return configured_max_duration_seconds(rules)
    return max(0, int(max_duration_seconds))


def format_duration_minutes(seconds: int) -> str:
    minutes = seconds / 60
    if seconds % 60 == 0:
        return str(int(minutes))
    return f"{minutes:.1f}"


def evaluate_candidate(
    video: DiscoveredVideo,
    keyword: str,
    comments: list[str],
    rules_path: Path | str | None = None,
    max_duration_seconds: int | None = None,
    knowledge_context: dict[str, object] | None = None,
) -> PrecheckResult:
    rules = load_rules(rules_path)
    context = analyze_keyword(keyword, rules, knowledge_context)
    scoring = rules.get("scoring") or {}
    duration_limit_seconds = resolve_max_duration_seconds(rules, max_duration_seconds)
    duration_over_limit = (
        duration_limit_seconds > 0
        and video.duration_seconds is not None
        and video.duration_seconds > duration_limit_seconds
    )
    title = compact_text(video.title)
    description = compact_text(video.description or "")
    comment_text = compact_text(" ".join(comments[:10]))
    cover_text = compact_text(cover_ocr_text(video))
    metadata_content = title + description
    content = metadata_content + cover_text
    full_evidence = content + comment_text

    entity_terms = [compact_text(term) for term in context.entity_terms]
    entity_title_hits = sum(term in title for term in entity_terms)
    entity_metadata_hits = sum(term in metadata_content for term in entity_terms)
    entity_cover_hits = sum(term in cover_text for term in entity_terms)
    entity_all_in_title = entity_title_hits == len(entity_terms)
    entity_all_in_content = all(term in content for term in entity_terms)
    entity_match = any(term in content for term in entity_terms)
    metadata_entity_match = any(term in metadata_content for term in entity_terms)
    text_entity_match = any(term in metadata_content + comment_text for term in entity_terms)
    ocr_only_match = bool(entity_cover_hits and not text_entity_match)

    intent_terms = [compact_text(term) for term in context.intent_terms]
    intent_in_title = bool(intent_terms) and any(term in title for term in intent_terms)
    intent_in_description = bool(intent_terms) and any(term in description for term in intent_terms)
    intent_in_cover = bool(intent_terms) and any(term in cover_text for term in intent_terms)
    intent_in_comments = bool(intent_terms) and any(term in comment_text for term in intent_terms)
    text_intent_signal = bool(intent_in_title or intent_in_description or intent_in_comments)

    education_terms = [
        compact_text(str(term)) for term in (rules.get("context") or {}).get("education_terms", [])
    ]
    education_terms.extend(
        compact_text(term)
        for term in (context.subject, context.stage, context.grade)
        if str(term or "").strip()
    )
    education_context = any(term in metadata_content for term in education_terms)
    cover_education_context = bool(cover_text) and any(term in cover_text for term in education_terms)
    related_hits = sum(compact_text(term) in content for term in context.related_terms)
    target_subject = compact_text(context.subject)
    subject_hits = {
        subject
        for subject in SUBJECT_TERMS
        if compact_text(subject) and compact_text(subject) in full_evidence
    }
    other_subject_hits = sorted(
        subject for subject in subject_hits if compact_text(subject) != target_subject
    )
    target_subject_hit = bool(target_subject and target_subject in full_evidence)
    subject_mismatch = bool(target_subject and other_subject_hits and not target_subject_hit)

    reject_terms = [
        str(term) for term in (rules.get("global_reject_terms") or [])
    ] + list(context.reject_terms)
    matched_reject = next(
        (term for term in reject_terms if compact_text(term) in full_evidence),
        None,
    )

    entity_score = 0.0
    if entity_all_in_title:
        entity_score += float(scoring.get("entity_all_in_title", 42))
    else:
        entity_score += min(
            entity_title_hits * float(scoring.get("entity_term_in_title", 22)),
            float(scoring.get("entity_all_in_title", 42)),
        )
    if entity_all_in_content:
        entity_score += float(scoring.get("entity_all_in_content", 16))
    cover_entity_score = 0.0
    if cover_text:
        if entity_cover_hits == len(entity_terms):
            cover_entity_score = float(scoring.get("cover_entity_all", 5))
        else:
            cover_entity_score = min(
                entity_cover_hits * float(scoring.get("cover_entity_term", 2)),
                float(scoring.get("cover_entity_all", 5)),
            )
        cover_entity_score = min(cover_entity_score, float(scoring.get("cover_entity_cap", 8)))
        entity_score += cover_entity_score

    intent_score = 0.0
    if intent_in_title:
        intent_score += float(scoring.get("intent_in_title", 22))
    elif intent_in_description:
        intent_score += float(scoring.get("intent_in_description", 12))
    elif intent_in_cover:
        intent_score += min(float(scoring.get("cover_intent", 3)), float(scoring.get("cover_intent_cap", 3)))
    elif intent_in_comments:
        intent_score += float(scoring.get("intent_in_comments", 5))

    education_score = 0.0
    if education_context:
        education_score += float(scoring.get("education_context", 16))
    cover_education_score = 0.0
    if cover_education_context:
        cover_education_score = min(
            float(scoring.get("cover_education_context", 2)),
            float(scoring.get("cover_education_cap", 2)),
        )
        education_score += cover_education_score

    related_score = min(
        related_hits * float(scoring.get("related_term_each", 3)),
        float(scoring.get("related_term_cap", 9)),
    )
    risk_penalty = float(scoring.get("reject_penalty", 100)) if matched_reject else 0.0
    duration_penalty = float(scoring.get("duration_over_limit_penalty", 100)) if duration_over_limit else 0.0
    subject_mismatch_penalty = float(scoring.get("subject_mismatch_penalty", 100)) if subject_mismatch else 0.0
    relevance = entity_score + intent_score + education_score + related_score
    if matched_reject:
        relevance -= risk_penalty
    if subject_mismatch:
        relevance -= subject_mismatch_penalty
    relevance = round(max(0, min(relevance, 100)), 2)

    engagement = engagement_score(video, rules)
    total = round(
        relevance * float(scoring.get("relevance_weight", 0.8))
        + engagement * float(scoring.get("engagement_weight", 0.2)),
        2,
    )
    if duration_over_limit:
        total = 0.0
    strict_threshold = float(scoring.get("acceptance_threshold", 55))
    relaxed_entity_threshold = float(
        scoring.get("relaxed_entity_threshold", strict_threshold)
    )
    context_recall_threshold = float(
        scoring.get("context_recall_threshold", strict_threshold)
    )
    hard_blocked = bool(matched_reject or subject_mismatch or duration_over_limit)
    cover_support_score = min(
        5.0,
        cover_entity_score
        + (1.0 if cover_education_context else 0.0)
        + (1.0 if intent_in_cover else 0.0),
    )
    primary_relevance = max(
        0.0,
        relevance
        - cover_entity_score
        - cover_education_score
        - (min(float(scoring.get("cover_intent", 3)), float(scoring.get("cover_intent_cap", 3))) if intent_in_cover and not text_intent_signal else 0.0),
    )
    if duration_over_limit:
        duration_fit_score = 0.0
    elif video.duration_seconds is None or duration_limit_seconds <= 0:
        duration_fit_score = 70.0
    else:
        ratio = min(video.duration_seconds / max(duration_limit_seconds, 1), 1.0)
        duration_fit_score = max(45.0, 100.0 - ratio * 35.0)
    secondary_score = round(
        min(
            100.0,
            primary_relevance * 0.65
            + engagement * 0.15
            + duration_fit_score * 0.10
            + cover_support_score
            + (5.0 if text_entity_match and entity_cover_hits else 0.0),
        ),
        2,
    )
    if ocr_only_match:
        secondary_score = min(secondary_score, 45.0)
    if has_collection_signal(video):
        secondary_score = max(0.0, secondary_score - 5.0)
    if hard_blocked:
        secondary_score = 0.0
    context_signal = bool(
        target_subject_hit
        or education_context
        or (cover_education_context and (metadata_entity_match or entity_cover_hits > 0))
        or related_hits > 0
        or text_intent_signal
    )
    strict_accept = (
        text_entity_match
        and not hard_blocked
        and relevance >= strict_threshold
    )
    relaxed_entity_accept = (
        text_entity_match
        and not hard_blocked
        and relevance >= relaxed_entity_threshold
    )
    context_recall_accept = (
        context_signal
        and not hard_blocked
        and relevance >= context_recall_threshold
    )
    accepted = bool(strict_accept or relaxed_entity_accept or context_recall_accept)
    if strict_accept:
        recall_tier = 3.0
    elif relaxed_entity_accept:
        recall_tier = 2.0
    elif context_recall_accept:
        recall_tier = 1.0
    else:
        recall_tier = 0.0
    if duration_over_limit and video.duration_seconds is not None:
        reason = (
            f"视频时长 {format_duration_minutes(video.duration_seconds)} 分钟"
            f"超过粗选上限 {format_duration_minutes(duration_limit_seconds)} 分钟"
        )
    elif matched_reject:
        reason = f"命中跨领域冲突词：{matched_reject}"
    elif subject_mismatch:
        reason = f"目标学科为“{context.subject}”，但候选内容明确指向：{'、'.join(other_subject_hits)}"
    elif not entity_match:
        if context_recall_accept:
            reason = "宽松召回：未完整命中核心知识点，但存在学科/教育语境信号，留待 OCR 或 AI 复核"
        else:
            reason = "标题和简介未命中核心知识点实体"
    elif ocr_only_match:
        reason = "仅封面 OCR 命中知识点，标题/简介/评论证据不足，进入待复核层"
    elif context.intent and not (intent_in_title or intent_in_description or intent_in_comments):
        if accepted:
            reason = f"宽松召回：实体相关，但缺少“{context.intent}”意图证据，留待后续复核"
        else:
            reason = f"实体相关，但缺少“{context.intent}”意图证据"
    elif strict_accept:
        reason = "通过实体、意图、教育语境与互动质量粗选"
    elif relaxed_entity_accept:
        reason = "宽松召回：命中知识点实体，但相关性证据不足，留待 OCR 或 AI 复核"
    elif context_recall_accept:
        reason = "宽松召回：存在学科/教育语境信号，留待 OCR 或 AI 复核"
    else:
        reason = "相关性分未达到候选阈值"
    return PrecheckResult(
        accepted,
        relevance,
        engagement,
        total,
        reason,
        {
            "entity_score": round(entity_score, 2),
            "entity_metadata_hits": float(entity_metadata_hits),
            "entity_cover_hits": float(entity_cover_hits),
            "intent_score": round(intent_score, 2),
            "education_score": round(education_score, 2),
            "related_score": round(related_score, 2),
            "cover_entity_score": round(cover_entity_score, 2),
            "cover_support_score": round(cover_support_score, 2),
            "primary_relevance_score": round(primary_relevance, 2),
            "secondary_score": round(secondary_score, 2),
            "ocr_only": 1.0 if ocr_only_match else 0.0,
            "collection_signal": 1.0 if has_collection_signal(video) else 0.0,
            "risk_penalty": round(risk_penalty, 2),
            "subject_mismatch": 1.0 if subject_mismatch else 0.0,
            "subject_mismatch_penalty": round(subject_mismatch_penalty, 2),
            "strict_accept": 1.0 if strict_accept else 0.0,
            "relaxed_entity_accept": 1.0 if relaxed_entity_accept else 0.0,
            "context_recall_accept": 1.0 if context_recall_accept else 0.0,
            "recall_tier": recall_tier,
            "strict_threshold": round(strict_threshold, 2),
            "relaxed_entity_threshold": round(relaxed_entity_threshold, 2),
            "context_recall_threshold": round(context_recall_threshold, 2),
            "duration_seconds": float(video.duration_seconds or 0),
            "duration_limit_seconds": float(duration_limit_seconds),
            "duration_over_limit": 1.0 if duration_over_limit else 0.0,
            "duration_penalty": round(duration_penalty, 2),
            "relevance_score": relevance,
            "engagement_score": engagement,
            "total_score": total,
        },
    )


def precheck_result_metadata(result: PrecheckResult) -> dict[str, Any]:
    payload = {
        "accepted": result.accepted,
        "relevance_score": result.relevance_score,
        "engagement_score": result.engagement_score,
        "total_score": result.total_score,
        "reason": result.reason,
        "score_breakdown": result.score_breakdown,
    }
    if "secondary_score" in result.score_breakdown:
        payload["secondary_score"] = result.score_breakdown["secondary_score"]
    return payload


def refresh_candidate_precheck(
    video: DiscoveredVideo,
    keyword: str,
    rules_path: Path | str | None = None,
    max_duration_seconds: int | None = None,
    knowledge_context: dict[str, object] | None = None,
) -> PrecheckResult:
    raw_comments = video.raw_metadata.get("comments") or []
    comments = [str(comment) for comment in raw_comments if str(comment).strip()]
    result = evaluate_candidate(
        video,
        keyword,
        comments,
        rules_path,
        max_duration_seconds,
        knowledge_context,
    )
    video.raw_metadata["precheck"] = precheck_result_metadata(result)
    return result


def rerank_candidates(
    videos: list[DiscoveredVideo],
    keyword: str,
    rules_path: Path | str | None = None,
    max_duration_seconds: int | None = None,
    knowledge_context: dict[str, object] | None = None,
    target_limit: int | None = None,
) -> list[DiscoveredVideo]:
    accepted: list[DiscoveredVideo] = []
    for video in videos:
        result = refresh_candidate_precheck(
            video,
            keyword,
            rules_path,
            max_duration_seconds,
            knowledge_context,
        )
        if result.accepted:
            accepted.append(video)
    deduped = deduplicate_videos(accepted)
    return diversify_candidates(deduped, target_limit)


def content_key(video: DiscoveredVideo) -> str:
    normalized_title = normalize_dedup_text(video.title)
    duration_bucket = round((video.duration_seconds or 0) / 10)
    return f"{normalized_title}:{duration_bucket}"


def is_near_duplicate(video: DiscoveredVideo, kept: DiscoveredVideo) -> bool:
    same_source = video.source == kept.source
    if same_source and base_external_id(video) and base_external_id(video) == base_external_id(kept):
        return True
    if video.canonical_url and kept.canonical_url and video.canonical_url == kept.canonical_url:
        return True

    duration_ok = duration_close(video, kept)
    same_author = not video.author or not kept.author or compact_text(video.author) == compact_text(kept.author)
    title = normalize_dedup_text(video.title)
    kept_title = normalize_dedup_text(kept.title)
    if (
        duration_ok
        and same_author
        and title
        and kept_title
        and SequenceMatcher(None, title, kept_title).ratio() >= 0.88
    ):
        return True

    signature = cover_signature(video)
    kept_signature = cover_signature(kept)
    if (
        duration_ok
        and signature
        and kept_signature
        and SequenceMatcher(None, signature, kept_signature).ratio() >= 0.9
        and (same_author or has_collection_signal(video) or has_collection_signal(kept))
    ):
        return True
    return False


def deduplicate_videos(videos: list[DiscoveredVideo]) -> list[DiscoveredVideo]:
    ranked = sorted(
        videos,
        key=candidate_rank_score,
        reverse=True,
    )
    unique: list[DiscoveredVideo] = []
    for video in ranked:
        duplicate = False
        for kept in unique:
            if is_near_duplicate(video, kept):
                duplicate = True
                break
        if not duplicate:
            dedup_meta = video.raw_metadata.setdefault("dedup", {})
            dedup_meta["content_key"] = content_key(video)
            dedup_meta["series_key"] = series_key(video)
            dedup_meta["cover_signature"] = cover_signature(video)
            unique.append(video)
    return unique


def diversify_candidates(
    videos: list[DiscoveredVideo],
    target_limit: int | None = None,
) -> list[DiscoveredVideo]:
    if not target_limit or target_limit <= 1:
        return videos

    primary: list[DiscoveredVideo] = []
    overflow: list[DiscoveredVideo] = []
    series_counts: dict[str, int] = {}
    author_counts: dict[str, int] = {}
    ocr_only_count = 0

    def can_take(video: DiscoveredVideo) -> bool:
        nonlocal ocr_only_count
        precheck = video.raw_metadata.get("precheck") or {}
        breakdown = precheck.get("score_breakdown") if isinstance(precheck, dict) else {}
        is_ocr_only = bool((breakdown or {}).get("ocr_only"))
        if is_ocr_only and ocr_only_count >= 1:
            return False
        key = series_key(video)
        if key and series_counts.get(key, 0) >= 1:
            return False
        author = compact_text(video.author or "")
        if author and author_counts.get(author, 0) >= 2:
            return False
        return True

    def take(video: DiscoveredVideo) -> None:
        nonlocal ocr_only_count
        primary.append(video)
        key = series_key(video)
        if key:
            series_counts[key] = series_counts.get(key, 0) + 1
        author = compact_text(video.author or "")
        if author:
            author_counts[author] = author_counts.get(author, 0) + 1
        precheck = video.raw_metadata.get("precheck") or {}
        breakdown = precheck.get("score_breakdown") if isinstance(precheck, dict) else {}
        if bool((breakdown or {}).get("ocr_only")):
            ocr_only_count += 1

    for video in videos:
        if len(primary) < target_limit and can_take(video):
            take(video)
        else:
            overflow.append(video)

    if len(primary) < target_limit:
        for video in overflow[:]:
            if len(primary) >= target_limit:
                break
            take(video)
            overflow.remove(video)

    selected_ids = {id(video) for video in primary}
    remainder = [video for video in videos if id(video) not in selected_ids]
    return primary + remainder
