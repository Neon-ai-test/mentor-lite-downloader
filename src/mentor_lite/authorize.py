from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from mentor_lite.settings import DEFAULT_USER_AGENT, Settings


def has_login_cookie(cookies: list[dict[str, object]]) -> bool:
    names = {str(cookie.get("name") or "") for cookie in cookies}
    return {"SESSDATA", "DedeUserID"}.issubset(names)


def authorize(output: Path, timeout_seconds: int = 600) -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_seconds
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(user_agent=DEFAULT_USER_AGENT, locale="zh-CN")
        page = context.new_page()
        page.goto("https://passport.bilibili.com/login", wait_until="domcontentloaded")
        while time.time() < deadline:
            cookies = context.cookies("https://www.bilibili.com")
            if has_login_cookie(cookies):
                context.storage_state(path=str(output))
                browser.close()
                return {"ok": True, "path": str(output), "cookie_count": len(cookies)}
            time.sleep(2)
        context.storage_state(path=str(output))
        browser.close()
    return {"ok": False, "path": str(output), "message": "等待登录超时，已保存当前浏览器状态"}


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(settings.auth_state_path))
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    try:
        summary = authorize(Path(args.output), args.timeout_seconds)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"授权失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
