"""httpx-first / Playwright フォールバック判定のオフライン検証（ネットワーク不要）。

Contact Discovery の取得は httpx を優先し、次の場合のみ Playwright にフォールバックする：
  - HTTP 403 / 429
  - Cloudflare / bot チャレンジ・JS 必須マーカー
  - 本文が空 or 極端に短い（重要 URL：TOP / contact / support / about のとき）
通常サイト（十分な本文・200）では Playwright を使わないことを確認する。

実行（backend ディレクトリで）:
    python tests/test_contact_fetcher_fallback.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(tempfile.gettempdir(), 'contact_fetcher_test.sqlite')}",
)
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


_LONG = "<html><body>" + ("x" * 2000) + "</body></html>"


def test_important_url():
    print("test_important_url")
    check("TOP は重要", cds._is_important_url("https://brand.com/"))
    check("contact は重要", cds._is_important_url("https://brand.com/contact"))
    check("support は重要", cds._is_important_url("https://brand.com/support"))
    check("about は重要", cds._is_important_url("https://brand.com/about-us"))
    check("privacy は重要でない", not cds._is_important_url("https://brand.com/privacy"))
    check("terms は重要でない", not cds._is_important_url("https://brand.com/terms"))


def test_needs_playwright():
    print("test_needs_playwright")
    # 通常サイト（十分な本文・200）→ Playwright 不要
    check("通常200は httpx のみ", not cds._needs_playwright(_LONG, 200, False))
    check("通常200（重要URL）も httpx のみ", not cds._needs_playwright(_LONG, 200, True))
    # ボット対策 → フォールバック
    check("403 は fallback", cds._needs_playwright(_LONG, 403, False))
    check("429 は fallback", cds._needs_playwright(_LONG, 429, False))
    # チャレンジ / JS 必須マーカー → フォールバック
    check("Just a moment は fallback",
          cds._needs_playwright("<html>Just a moment...</html>", 200, False))
    check("challenge-platform は fallback",
          cds._needs_playwright('<script src="/cdn-cgi/challenge-platform/x"></script>', 200, False))
    check("enable javascript は fallback",
          cds._needs_playwright("<noscript>Please enable JavaScript</noscript>", 200, False))
    # 空 / 極端に短い本文
    check("空本文＋重要URLは fallback", cds._needs_playwright("", 200, True))
    check("空本文＋非重要はスキップ", not cds._needs_playwright("", 200, False))
    check("短い本文＋重要URLは fallback",
          cds._needs_playwright("<html>hi</html>", 200, True))
    check("短い本文＋非重要はスキップ",
          not cds._needs_playwright("<html>hi</html>", 200, False))
    check("None本文＋非重要はスキップ", not cds._needs_playwright(None, None, False))


def main():
    test_important_url()
    test_needs_playwright()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
