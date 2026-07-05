"""参照元URL・公式サイトの URL バリデーションのオフライン検証（ネットワーク/DB 不要）。

example.com / dummy / sample / test / localhost / 127.0.0.1 / githubusercontent や
kickstarter.com/projects/example/... のようなダミー URL を弾き、実在しうる
ビジネス URL は残すことを確認する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_url_validation.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.url_validation import (  # noqa: E402
    business_url_reason,
    filter_business_urls,
    first_valid_url,
    is_valid_business_url,
)

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_dummy_urls_rejected() -> None:
    print("test_dummy_urls_rejected")
    dummies = [
        "https://www.kickstarter.com/projects/example/super-widget",
        "https://example.com/foo",
        "https://example.org/",
        "https://example.net/team",
        "https://xxxx.example.com",
        "http://localhost:3000/",
        "http://127.0.0.1:8000/",
        "https://raw.githubusercontent.com/user/repo/main/x.png",
        "https://dummy.io/contact",
        "https://sample.com/",
        "https://test.co/about",
        "ftp://example.com/file",
        "not-a-url",
        "",
    ]
    for u in dummies:
        check(f"{u!r} は無効", is_valid_business_url(u) is False)
        check(f"{u!r} は理由あり", business_url_reason(u) is not None)


def test_real_urls_kept() -> None:
    print("test_real_urls_kept")
    reals = [
        "https://www.kickstarter.com/projects/vitesy/natede-smart",
        "https://vitesy.com/",
        "https://www.example-brand.com/contact",  # example-brand は正規ラベル
        "https://my-company.io/about",
        "https://shop.realbrand.co.uk/",
        "https://indiegogo.com/projects/cool-thing",
    ]
    for u in reals:
        check(f"{u!r} は有効", is_valid_business_url(u) is True)
        check(f"{u!r} は理由なし", business_url_reason(u) is None)


def test_reasons_machine_readable() -> None:
    print("test_reasons_machine_readable")
    check("example.com の理由は dummy_host",
          business_url_reason("https://example.com/x") == "dummy_host:example")
    check("127.0.0.1 の理由は dummy_host",
          business_url_reason("http://127.0.0.1/") == "dummy_host:127.0.0.1")
    check("KS example slug は placeholder_path",
          (business_url_reason(
              "https://www.kickstarter.com/projects/example/x") or "").startswith(
              "placeholder_path"))
    check("ftp は scheme 理由",
          (business_url_reason("ftp://brand.com") or "").startswith("scheme"))


def test_filter_and_first() -> None:
    print("test_filter_and_first")
    urls = [
        "https://example.com/a",
        "https://vitesy.com/",
        "https://vitesy.com/",  # 重複
        "http://127.0.0.1/",
        "https://realbrand.com/contact",
        "(no external page available)",
    ]
    out = filter_business_urls(urls)
    check("有効のみ残る", out == ["https://vitesy.com/", "https://realbrand.com/contact"])
    check("first_valid はダミーを飛ばす",
          first_valid_url("https://example.com/x", None, "https://real.com/")
          == "https://real.com/")
    check("first_valid 全部ダミーなら None",
          first_valid_url("https://example.com/x", "http://localhost/") is None)


def main() -> int:
    test_dummy_urls_rejected()
    test_real_urls_kept()
    test_reasons_machine_readable()
    test_filter_and_first()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
