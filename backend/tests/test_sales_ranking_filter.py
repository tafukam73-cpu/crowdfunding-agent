"""営業ランキングの営業状況フィルターのオフライン検証（ネットワーク不要）。

「今日営業すべき案件ランキング」は既定で未営業（not_started / ready）だけを返し、
営業アクション済み（contacted / awaiting_reply / replied / negotiating / won /
rejected）を除外することを検証する。status_filter でフォローアップ/商談中/すべて表示
に切り替えられること、ステータス変更後に再取得するとランキングから消えることも確認する。

実行（backend ディレクトリで）:
    python tests/test_sales_ranking_filter.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# SessionLocal 束縛前に隔離した file sqlite を指定（dev DB を汚さない）
_DBFILE = os.path.join(tempfile.gettempdir(), "sales_ranking_filter_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.project import Project, SalesStatus  # noqa: E402
from app.services import workflow_service as wf  # noqa: E402

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


_seq = 0


def _mk(db, status: str, score: int = 80) -> Project:
    """営業対象サイトの案件を 1 件作る（sales_status 指定・URL は毎回一意）。"""
    global _seq
    _seq += 1
    p = Project(
        title=f"proj-{status}",
        source_site="kickstarter",
        source_url=f"https://kck.st/{status}-{_seq}",
        sales_status=status,
        latest_score=score,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _rank_ids(db, **kwargs) -> set[int]:
    """ランキング結果の project_id 集合。"""
    items = wf.ranking(db, **kwargs)
    return {it["project_id"] for it in items}


def test_default_excludes_actioned():
    print("test_default_excludes_actioned")
    db = SessionLocal()
    not_started = _mk(db, SalesStatus.not_started.value)
    ready = _mk(db, SalesStatus.ready.value)
    contacted = _mk(db, SalesStatus.contacted.value)
    awaiting = _mk(db, SalesStatus.awaiting_reply.value)
    replied = _mk(db, SalesStatus.replied.value)
    negotiating = _mk(db, SalesStatus.negotiating.value)
    won = _mk(db, SalesStatus.won.value)
    rejected = _mk(db, SalesStatus.rejected.value)

    ids = _rank_ids(db)  # 既定 = status_filter="not_started"
    check("未営業(not_started)は表示される", not_started.id in ids)
    check("準備完了(ready)は表示される", ready.id in ids)
    check("メール送信済み(contacted)は除外", contacted.id not in ids)
    check("返事待ち(awaiting_reply)は除外", awaiting.id not in ids)
    check("返信あり(replied)は除外", replied.id not in ids)
    check("商談中(negotiating)は除外", negotiating.id not in ids)
    check("契約(won)は除外", won.id not in ids)
    check("見送り(rejected)は除外", rejected.id not in ids)
    db.close()


def test_all_filter_shows_everything():
    print("test_all_filter_shows_everything")
    db = SessionLocal()
    ns = _mk(db, SalesStatus.not_started.value)
    contacted = _mk(db, SalesStatus.contacted.value)
    negotiating = _mk(db, SalesStatus.negotiating.value)

    ids = _rank_ids(db, status_filter="all")
    check("すべて表示では未営業も表示", ns.id in ids)
    check("すべて表示では営業済みも表示", contacted.id in ids)
    check("すべて表示では商談中も表示", negotiating.id in ids)
    db.close()


def test_named_filters():
    print("test_named_filters")
    db = SessionLocal()
    ns = _mk(db, SalesStatus.not_started.value)
    contacted = _mk(db, SalesStatus.contacted.value)
    awaiting = _mk(db, SalesStatus.awaiting_reply.value)
    negotiating = _mk(db, SalesStatus.negotiating.value)

    aw_ids = _rank_ids(db, status_filter="awaiting_reply")
    check("返事待ちフィルターは awaiting_reply のみ",
          awaiting.id in aw_ids and ns.id not in aw_ids
          and contacted.id not in aw_ids)

    fu_ids = _rank_ids(db, status_filter="followup")
    check("フォローアップは営業済み＋返信待ち",
          contacted.id in fu_ids and awaiting.id in fu_ids
          and ns.id not in fu_ids and negotiating.id not in fu_ids)

    ng_ids = _rank_ids(db, status_filter="negotiating")
    check("商談中フィルターは negotiating のみ",
          negotiating.id in ng_ids and ns.id not in ng_ids)

    # 不正な値は既定（未営業のみ）に丸める
    bad_ids = _rank_ids(db, status_filter="__bogus__")
    check("不正フィルターは未営業のみに丸める",
          ns.id in bad_ids and contacted.id not in bad_ids)
    db.close()


def test_status_change_drops_from_ranking():
    print("test_status_change_drops_from_ranking")
    db = SessionLocal()
    p = _mk(db, SalesStatus.not_started.value)
    check("変更前は未営業ランキングに載る", p.id in _rank_ids(db))

    # 営業アクション（営業済みに変更）→ 再取得でランキングから消える
    p.sales_status = SalesStatus.contacted.value
    db.commit()
    check("ステータス変更後は再取得で消える", p.id not in _rank_ids(db))
    check("すべて表示なら引き続き見える", p.id in _rank_ids(db, status_filter="all"))
    db.close()


def test_legacy_not_started_only():
    print("test_legacy_not_started_only")
    db = SessionLocal()
    ns = _mk(db, SalesStatus.not_started.value)
    contacted = _mk(db, SalesStatus.contacted.value)
    # 後方互換：not_started_only=True は status_filter に関わらず未営業のみ
    ids = _rank_ids(db, not_started_only=True, status_filter="all")
    check("not_started_only=True は未営業のみに強制", ns.id in ids and contacted.id not in ids)
    db.close()


def main():
    test_default_excludes_actioned()
    test_all_filter_shows_everything()
    test_named_filters()
    test_status_change_drops_from_ranking()
    test_legacy_not_started_only()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
