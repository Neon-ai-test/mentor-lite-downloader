from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def tool_root() -> Path:
    configured = os.getenv("MENTOR_LITE_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    runtime_dir: Path
    data_dir: Path
    download_dir: Path
    temp_dir: Path
    db_path: Path
    auth_state_path: Path
    rules_path: Path
    user_agent: str = DEFAULT_USER_AGENT
    headless: bool = True
    browser_timeout_ms: int = 30_000
    crawl_delay_seconds: float = 1.2
    max_search_pages: int = 20
    precheck_target: int = 100
    max_duration_seconds: int = 15 * 60
    qn: int = 127
    max_stream_mb: int = 2048

    @classmethod
    def from_env(cls) -> "Settings":
        root = tool_root()
        runtime_dir = root / ".runtime"
        data_dir = root / "data"
        return cls(
            root=root,
            runtime_dir=runtime_dir,
            data_dir=data_dir,
            download_dir=root / "downloads",
            temp_dir=runtime_dir / "tmp",
            db_path=data_dir / "mentor_lite.db",
            auth_state_path=runtime_dir / "auth" / "bilibili.storage_state.json",
            rules_path=root / "config" / "precheck_rules.yaml",
            user_agent=os.getenv("MENTOR_LITE_USER_AGENT", DEFAULT_USER_AGENT),
            headless=os.getenv("MENTOR_LITE_HEADLESS", "true").lower() in {"1", "true", "yes", "on"},
            browser_timeout_ms=int(os.getenv("MENTOR_LITE_BROWSER_TIMEOUT_MS", "30000")),
            crawl_delay_seconds=float(os.getenv("MENTOR_LITE_CRAWL_DELAY_SECONDS", "1.2")),
            max_search_pages=int(os.getenv("MENTOR_LITE_MAX_SEARCH_PAGES", "20")),
            precheck_target=int(os.getenv("MENTOR_LITE_PRECHECK_TARGET", "100")),
            max_duration_seconds=int(os.getenv("MENTOR_LITE_MAX_DURATION_SECONDS", str(15 * 60))),
            qn=int(os.getenv("MENTOR_LITE_QN", "127")),
            max_stream_mb=int(os.getenv("MENTOR_LITE_MAX_STREAM_MB", "2048")),
        )

    def ensure_dirs(self) -> None:
        for path in (
            self.runtime_dir,
            self.data_dir,
            self.download_dir,
            self.temp_dir,
            self.auth_state_path.parent,
            self.db_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
