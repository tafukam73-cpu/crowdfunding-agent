"""発掘商品候補（Discovery Engine v1-1）のオフライン検証（ネットワーク不要）。

商品候補の作成・source_url 重複時の再利用・一覧/詳細取得・status/min_score
フィルター・スコア順ソートを sqlite で検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_discovery.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "discovery_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.services import discovery_service as svc  # noqa: E402

Base.metadata.create_all(engine)

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


def _mk(db, *, url, title="Prod", platform="kickstarter", status="live",
        score=None, category="gadget"):
    return svc.create(db, {
        "source_platform": platform,
        "source_url": url,
        "project_title": title,
        "product_name": title,
        "category": category,
        "status": status,
        "overall_discovery_score": score,
    })


def test_create():
    print("test_create")
    db = SessionLocal()
    product, created = _mk(db, url="https://kck.st/aaa", title="Cool Lamp", score=80)
    check("作成できる", created is True and product.id is not None)
    check("タイトルを保存", product.project_title == "Cool Lamp")
    check("プラットフォームを保存", product.source_platform == "kickstarter")
    check("ステータスを保存", product.status == "live")
    check("スコアを保存", product.overall_discovery_score == 80)
    db.close()


def test_duplicate_source_url():
    print("test_duplicate_source_url")
    db = SessionLocal()
    url = "https://kck.st/dup"
    p1, c1 = _mk(db, url=url, title="First")
    p2, c2 = _mk(db, url=url, title="Second")
    check("1回目は作成", c1 is True)
    check("2回目は既存を再利用（二重作成しない）", c2 is False and p2.id == p1.id)
    check("再利用時は既存データを返す", p2.project_title == "First")
    same_url = [p for p in svc.list_products(db) if p.source_url == url]
    check("同一 source_url は1件のみ", len(same_url) == 1)
    db.close()


def test_get_and_list():
    print("test_get_and_list")
    db = SessionLocal()
    p, _ = _mk(db, url="https://kck.st/getlist", title="G")
    got = svc.get(db, p.id)
    check("id で詳細取得できる", got is not None and got.id == p.id)
    check("存在しない id は None", svc.get(db, 987654) is None)
    check("一覧取得できる", len(svc.list_products(db)) >= 1)
    db.close()


def test_status_filter():
    print("test_status_filter")
    db = SessionLocal()
    _mk(db, url="https://kck.st/live1", status="live")
    _mk(db, url="https://kck.st/succ1", status="successful")
    only_succ = svc.list_products(db, status="successful")
    check("status フィルター", len(only_succ) >= 1
          and all(p.status == "successful" for p in only_succ))
    db.close()


def test_min_score_filter():
    print("test_min_score_filter")
    db = SessionLocal()
    _mk(db, url="https://kck.st/sc-lo", score=20)
    _mk(db, url="https://kck.st/sc-hi", score=90)
    ge50 = svc.list_products(db, min_score=50)
    check("min_score フィルター", all((p.overall_discovery_score or 0) >= 50 for p in ge50))
    check("min_score で下限未満は除外",
          all(p.source_url != "https://kck.st/sc-lo" for p in ge50))
    db.close()


def test_score_sort():
    print("test_score_sort")
    db = SessionLocal()
    _mk(db, url="https://kck.st/s1", score=30)
    _mk(db, url="https://kck.st/s2", score=95)
    _mk(db, url="https://kck.st/s3", score=60)
    ranked = svc.list_products(db, sort="score")
    scored = [p.overall_discovery_score for p in ranked
              if p.overall_discovery_score is not None]
    check("スコア降順に並ぶ", scored == sorted(scored, reverse=True))
    check("最上位は最高スコア", ranked[0].overall_discovery_score == 95)
    db.close()


def test_update():
    print("test_update")
    db = SessionLocal()
    p, _ = _mk(db, url="https://kck.st/upd", status="live")
    updated = svc.update(db, p.id, {"status": "successful",
                                    "overall_discovery_score": 77})
    check("ステータス更新できる", updated.status == "successful")
    check("スコア更新できる", updated.overall_discovery_score == 77)
    try:
        svc.update(db, p.id, {"status": "bogus"})
        check("不正 status で ValueError", False)
    except ValueError:
        check("不正 status で ValueError", True)
    db.close()


def main():
    test_create()
    test_duplicate_source_url()
    test_get_and_list()
    test_status_filter()
    test_min_score_filter()
    test_score_sort()
    test_update()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
