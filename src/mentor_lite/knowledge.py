from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from mentor_lite.models import KnowledgePoint

HEADER_ALIASES = {
    "chapter": {"chapter", "章节", "章", "单元", "模块", "教材章节", "目录"},
    "group": {"group", "level_1", "一级知识点", "知识点组", "知识模块", "知识单元", "小节"},
    "name": {"name", "level_2", "knowledge_point", "知识点", "知识点名称", "二级知识点", "考点", "标题"},
}
REQUIRED_FIELDS = {"chapter", "group", "name"}
SPACE_RE = re.compile(r"\s+")
SAFE_ID_RE = re.compile(r"[^0-9a-zA-Z_\-]+")
ENUM_RE = re.compile(r"^\s*\d+(?:[.、]\d+)*[.、\s]*")


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return SPACE_RE.sub(" ", str(value)).strip()


def normalize_key(value: object) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", normalize_text(value)).lower()


def clean_heading(value: object) -> str:
    return ENUM_RE.sub("", normalize_text(value)).strip()


def stable_id(point: dict[str, str]) -> str:
    basis = "|".join(
        normalize_text(point.get(key))
        for key in ("subject", "stage", "grade", "textbook", "chapter", "group", "name")
    )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"kp_{digest}"


def make_knowledge_point(data: dict[str, str], point_id: str | None = None) -> KnowledgePoint:
    name = normalize_text(data.get("name"))
    aliases = []
    raw_aliases = data.get("aliases", "")
    if isinstance(raw_aliases, list):
        alias_values = raw_aliases
    else:
        alias_values = re.split(r"[\n,，、;；]+", str(raw_aliases or ""))
    for raw in alias_values:
        alias = normalize_text(raw)
        if alias and alias not in aliases:
            aliases.append(alias)
    if name and name not in aliases:
        aliases.insert(0, name)
    payload = {
        "subject": normalize_text(data.get("subject")),
        "stage": normalize_text(data.get("stage")),
        "grade": normalize_text(data.get("grade")),
        "textbook": normalize_text(data.get("textbook")),
        "chapter": clean_heading(data.get("chapter")),
        "group": clean_heading(data.get("group")),
        "name": name,
    }
    if not payload["subject"] or not payload["name"]:
        raise ValueError("知识点必须包含学科和名称")
    return KnowledgePoint(
        id=point_id or stable_id(payload),
        subject=payload["subject"],
        stage=payload["stage"],
        grade=payload["grade"],
        textbook=payload["textbook"],
        chapter=payload["chapter"],
        group=payload["group"],
        name=payload["name"],
        description=normalize_text(data.get("description")),
        aliases=aliases,
    )


def read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw = path.read_bytes().decode("utf-8-sig", errors="replace")
        matrix = [list(row) for row in csv.reader(raw.splitlines())]
    elif suffix in {".xlsx", ".xlsm"}:
        workbook = load_workbook(path, data_only=True, read_only=True)
        sheet = workbook.worksheets[0]
        matrix = [list(row) for row in sheet.iter_rows(values_only=True)]
    else:
        raise ValueError("仅支持 .xlsx、.xlsm 或 .csv")
    if not matrix:
        raise ValueError("文件为空")
    header_index = detect_header_row(matrix)
    headers = normalize_headers(matrix[header_index])
    rows: list[dict[str, str]] = []
    for values in matrix[header_index + 1 :]:
        row = {
            header: normalize_text(values[index] if index < len(values) else "")
            for index, header in enumerate(headers)
        }
        if any(row.values()):
            rows.append(row)
    return headers, rows


def detect_header_row(matrix: list[list[Any]]) -> int:
    best_index = 0
    best_score = -1
    for index, row in enumerate(matrix[:8]):
        cells = [normalize_text(cell) for cell in row]
        score = sum(1 for cell in cells if cell) + sum(3 for cell in cells if match_field(cell))
        if score > best_score:
            best_index = index
            best_score = score
    return best_index


def normalize_headers(values: list[Any]) -> list[str]:
    headers: list[str] = []
    used: dict[str, int] = {}
    for index, value in enumerate(values):
        header = normalize_text(value) or f"未命名列{index + 1}"
        count = used.get(header, 0)
        used[header] = count + 1
        if count:
            header = f"{header}_{count + 1}"
        headers.append(header)
    while headers and headers[-1].startswith("未命名列"):
        headers.pop()
    return headers


def match_field(header: str) -> str | None:
    normalized = normalize_key(header)
    for key, aliases in HEADER_ALIASES.items():
        normalized_aliases = {normalize_key(alias) for alias in aliases}
        if normalized in normalized_aliases:
            return key
    for key, aliases in HEADER_ALIASES.items():
        if any(normalize_key(alias) in normalized for alias in aliases):
            return key
    return None


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for header in headers:
        key = match_field(header)
        if key and key not in mapping:
            mapping[key] = header
    return mapping


def import_knowledge_file(
    path: Path,
    *,
    subject: str,
    stage: str,
    grade: str,
    textbook: str,
    mapping: dict[str, str] | None = None,
) -> list[KnowledgePoint]:
    headers, rows = read_table(path)
    resolved_mapping = mapping or suggest_mapping(headers)
    missing = [key for key in REQUIRED_FIELDS if not resolved_mapping.get(key)]
    if missing:
        raise ValueError(f"缺少必要字段映射：{', '.join(missing)}")
    points: list[KnowledgePoint] = []
    carry: dict[str, str] = {}
    for row in rows:
        values = {
            "subject": subject,
            "stage": stage,
            "grade": grade,
            "textbook": textbook,
            "chapter": clean_heading(row.get(resolved_mapping["chapter"], "")),
            "group": clean_heading(row.get(resolved_mapping["group"], "")),
            "name": clean_heading(row.get(resolved_mapping["name"], "")),
        }
        if not any(values[key] for key in ("chapter", "group", "name")):
            continue
        if values["chapter"]:
            carry["chapter"] = values["chapter"]
        elif carry.get("chapter"):
            values["chapter"] = carry["chapter"]
        if values["group"]:
            carry["group"] = values["group"]
        elif carry.get("group"):
            values["group"] = carry["group"]
        if not values["name"]:
            continue
        points.append(make_knowledge_point(values))
    if not points:
        raise ValueError("没有可导入的知识点")
    return points
