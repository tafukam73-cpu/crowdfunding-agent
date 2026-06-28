"""日本販売状況チェックのオフライン検証（ネットワーク不要 / DB は in-memory SQLite）。

検証対象：
- 営業価値（★1〜5）の判定ロジック（compute_stars）
- 各チャネルの検索 URL（Amazon / 楽天 / Yahoo / 代理店 / 法人 / Makuake / GREEN FUNDING）
- モックチェッカー → service.run_check（end-to-end・SQLite）
- メール生成への反映（日本未上陸なら参入機会の一文が本文に入る）

pytest 非依存で単体実行できる。
実行（backend ディレクトリで）:
    python tests/test_japan_sales_check.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai.japan_sales_checker import (  # noqa: E402
    CHANNEL_KEYS,
    build_search_queries,
    compute_stars,
    search_url,
)
from app.ai.mock_email_generator import MockEmailGenerator  # noqa: E402
from app.ai.mock_japan_sales_checker import MockJapanSalesChecker  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import japan_sales_service  # noqa: E402

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


def _nf() -> dict[str, str]:
    return {k: "not_found" for k in CHANNEL_KEYS}


def test_compute_stars() -> None:
    print("compute_stars（営業価値★の判定）")
    nf = _nf()
    check("全未検出 → ★5", compute_stars(nf) == 5)
    check("クラファン掲載のみ → ★4", compute_stars({**nf, "makuake": "found"}) == 4)
    check("限定的な痕跡のみ → ★4", compute_stars({**nf, "amazon": "limited"}) == 4)
    check("EC 1サイト販売 → ★3", compute_stars({**nf, "amazon": "found"}) == 3)
    check("代理店あり → ★2", compute_stars({**nf, "distributor": "found"}) == 2)
    check("日本法人あり → ★2", compute_stars({**nf, "subsidiary": "found"}) == 2)
    check(
        "EC 2サイト以上（広く販売） → ★1",
        compute_stars({**nf, "amazon": "found", "rakuten": "found"}) == 1,
    )
    check(
        "代理店あり＋広くEC販売 → ★1",
        compute_stars(
            {**nf, "distributor": "found", "amazon": "found", "yahoo": "found"}
        )
        == 1,
    )


def test_search_urls() -> None:
    print("各チャネルの検索 URL")
    u = {c: search_url(c, product="Foo Bar", maker="Acme") for c in CHANNEL_KEYS}
    check("Amazon.co.jp 検索", u["amazon"].startswith("https://www.amazon.co.jp/s?k="))
    check("楽天市場 検索", u["rakuten"].startswith("https://search.rakuten.co.jp/search/mall/"))
    check("Yahoo!ショッピング 検索", u["yahoo"].startswith("https://shopping.yahoo.co.jp/search?p="))
    check("日本代理店 検索（Google）", "google.com/search" in u["distributor"] and "%E4%BB%A3%E7%90%86%E5%BA%97" in u["distributor"])
    check("日本法人 検索（Google）", "google.com/search" in u["subsidiary"] and "%E6%B3%95%E4%BA%BA" in u["subsidiary"])
    check("Makuake 比較（site:makuake.com）", "site%3Amakuake.com" in u["makuake"])
    check("GREEN FUNDING 比較（site:greenfunding.jp）", "site%3Agreenfunding.jp" in u["greenfunding"])
    # メーカー名が無くても商品名でフォールバックできる
    check(
        "メーカー名なしでも EC 検索可",
        search_url("amazon", product="Foo", maker=None).endswith("k=Foo"),
    )
    q = build_search_queries("Foo Bar", "Acme")
    check("検索クエリ候補に商品名を含む", "Foo Bar" in q)


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_service_end_to_end() -> None:
    print("service.run_check（モック・SQLite end-to-end）")
    db = _make_session()
    project = Project(
        title="SuperWidget",
        source_site="kickstarter",
        maker_name="Acme Inc",
        currency="USD",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    row = japan_sales_service.run_check(db, project, checker=MockJapanSalesChecker())
    check("status=completed", row.status == "completed")
    check("★は 5（モックは全未検出）", row.sales_value_stars == 5)
    check("channels は 7 チャネル", isinstance(row.channels, list) and len(row.channels) == 7)
    check(
        "全チャネルに search_url が付く",
        all(c.get("search_url", "").startswith("http") for c in (row.channels or [])),
    )
    check("AI コメントあり", bool(row.ai_comment))
    check("検索クエリ保存あり", bool(row.search_queries))

    ctx = japan_sales_service.to_email_context(row)
    check("email context: 日本未上陸", ctx is not None and ctx["no_japan_presence"] is True)
    check("email context: 代理店なし", ctx is not None and ctx["has_distributor"] is False)

    # get_latest / get_latest_completed
    check("get_latest が取得できる", japan_sales_service.get_latest(db, project.id) is not None)
    check(
        "get_latest_completed が取得できる",
        japan_sales_service.get_latest_completed(db, project.id) is not None,
    )
    db.close()


def test_email_integration() -> None:
    print("メール生成への反映")
    project = Project(
        title="SuperWidget",
        source_site="kickstarter",
        maker_name="Acme Inc",
        currency="USD",
    )
    gen = MockEmailGenerator()

    no_presence = {
        "stars": 5,
        "summary": "未確認",
        "ai_comment": "",
        "has_distributor": False,
        "sold_in_japan": False,
        "no_japan_presence": True,
    }
    drafts = gen.generate(project, japan_sales=no_presence)
    sentence = "we believe this creates an exciting opportunity"
    check(
        "日本未上陸 → 参入機会の一文が本文に入る",
        all(sentence in d.body for d in drafts),
    )

    # 日本販売状況が無い場合は従来どおり（参入機会の一文は入らない）
    drafts_none = gen.generate(project)
    check(
        "japan_sales 無し → 参入機会の一文は入らない",
        all(sentence not in d.body for d in drafts_none),
    )

    # 代理店ありの場合は参入機会の一文を入れない
    has_dist = {**no_presence, "has_distributor": True, "no_japan_presence": False}
    drafts_dist = gen.generate(project, japan_sales=has_dist)
    check(
        "代理店あり → 参入機会の一文は入らない",
        all(sentence not in d.body for d in drafts_dist),
    )


def main() -> int:
    test_compute_stars()
    test_search_urls()
    test_service_end_to_end()
    test_email_integration()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
