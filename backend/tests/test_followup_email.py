"""フォローアップ営業メール生成のオフライン検証（ネットワーク/AI 不要）。

- 経過日数 → 段階（light/repropose/final）判定
- 段階別の文面生成（3日後の軽い確認 / 7日後の再提案 / 14日後の最終フォロー）
- Gmail 下書き URL 生成
- generate_followup：下書き保存・営業活動タイムライン記録・状況更新
- 3日未満は ValueError

実行（backend ディレクトリで）:
    python tests/test_followup_email.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "followup_email_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.ai import followup as fu  # noqa: E402
from app.models.crm import Maker, SalesActivity  # noqa: E402
from app.models.email_draft import EmailDraft  # noqa: E402
from app.models.project import Project, SalesStatus  # noqa: E402
from app.services import email_service  # noqa: E402

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


def _mk(db, *, status=SalesStatus.contacted.value, email="maker@example.com") -> Project:
    global _seq
    _seq += 1
    p = Project(
        title=f"Aurora Lamp {_seq}",
        source_site="kickstarter",
        source_url=f"https://kck.st/fu-{_seq}",
        sales_status=status,
        contact_info=email,
        raised_amount=300000,
        goal_amount=10000,
        backers_count=1200,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_stage_for_days():
    print("test_stage_for_days")
    check("2日はフォロー対象外(None)", fu.stage_for_days(2) is None)
    check("3日は light", fu.stage_for_days(3) == "light")
    check("6日は light", fu.stage_for_days(6) == "light")
    check("7日は repropose", fu.stage_for_days(7) == "repropose")
    check("13日は repropose", fu.stage_for_days(13) == "repropose")
    check("14日は final", fu.stage_for_days(14) == "final")
    check("30日は final", fu.stage_for_days(30) == "final")


def test_build_message_per_stage():
    print("test_build_message_per_stage")
    db = SessionLocal()
    p = _mk(db)
    light = fu.build_followup_message(p, stage="light", days=4)
    repro = fu.build_followup_message(p, stage="repropose", days=8)
    final = fu.build_followup_message(p, stage="final", days=15)

    check("3種の件名は互いに異なる",
          len({light["subject"], repro["subject"], final["subject"]}) == 3)
    check("light 本文に商品名が入る", p.title in light["body"])
    check("light は「軽い」再確認の趣旨", "follow up" in light["body"].lower())
    check("repropose は打ち合わせ/資料を提案",
          "call" in repro["body"].lower() or "overview" in repro["body"].lower())
    check("final は最後の連絡である旨", "last note" in final["body"].lower())
    check("stage_label が日本語で入る",
          final["stage_label"] == "最終フォロー" and light["stage_label"] == "軽い確認")
    check("日本語要約が入る", bool(final["japanese_summary"]))
    db.close()


def test_gmail_compose_url():
    print("test_gmail_compose_url")
    u = fu.gmail_compose_url("maker@example.com", "Hello & Hi", "Line1\nLine2 & more")
    check("Gmail 作成URLである", u.startswith("https://mail.google.com/mail/?"))
    check("宛先が URL エンコードされる", "to=maker%40example.com" in u)
    check("件名が入る", "su=Hello%20%26%20Hi" in u)
    check("本文（改行・記号）がエンコードされる", "body=Line1%0ALine2%20%26%20more" in u)
    check("宛先なしでも生成できる", fu.gmail_compose_url(None, "S", "B").startswith("https://"))


def _stage_of(db, project, days):
    res = email_service.generate_followup(db, project, days=days)
    return res


def test_generate_followup_stages_and_record():
    print("test_generate_followup_stages_and_record")
    db = SessionLocal()

    p3 = _mk(db)
    r3 = _stage_of(db, p3, 4)
    check("4日 → light 段階", r3["stage"] == "light")
    check("follow_up_level=normal", r3["follow_up_level"] == "normal")

    p7 = _mk(db)
    r7 = _stage_of(db, p7, 8)
    check("8日 → repropose 段階", r7["stage"] == "repropose")
    check("follow_up_level=high", r7["follow_up_level"] == "high")

    p14 = _mk(db)
    r14 = _stage_of(db, p14, 15)
    check("15日 → final 段階", r14["stage"] == "final")
    check("follow_up_level=final", r14["follow_up_level"] == "final")

    # 下書きが保存され、種別は followup、段階が記録される
    draft = db.get(EmailDraft, r14["draft"].id)
    check("下書きが EmailDraft に保存される", draft is not None)
    check("email_type=followup", draft.email_type == "followup")
    check("段階が personalization_context に記録",
          draft.personalization_context.get("followup_stage") == "final")

    # Gmail 下書きURL・宛先
    check("Gmail 下書きURLを返す",
          r14["gmail_compose_url"].startswith("https://mail.google.com/mail/?"))
    check("宛先が解決される（contact_info）", r14["recipient"] == "maker@example.com")

    # 営業活動タイムラインに記録される
    acts = db.query(SalesActivity).filter(
        SalesActivity.project_id == p14.id
    ).all()
    followup_acts = [a for a in acts if "フォローアップメール作成" in a.summary]
    check("営業活動タイムラインにフォロー記録が残る", len(followup_acts) >= 1)

    # 営業状況が「返信待ち」に更新される
    db.refresh(p14)
    check("営業状況が返信待ちに更新される",
          p14.sales_status == SalesStatus.awaiting_reply.value)
    db.close()


def test_too_early_raises():
    print("test_too_early_raises")
    db = SessionLocal()
    p = _mk(db)
    try:
        email_service.generate_followup(db, p, days=2)
        check("3日未満は ValueError", False)
    except ValueError:
        check("3日未満は ValueError", True)
    db.close()


def test_auto_days_from_activity():
    print("test_auto_days_from_activity")
    db = SessionLocal()
    maker = Maker(name="Auto Maker")
    db.add(maker)
    db.commit()
    db.refresh(maker)
    p = _mk(db)
    p.maker_id = maker.id
    db.commit()
    # 10日前の最終営業（SalesActivity）
    ts = datetime.now(timezone.utc) - timedelta(days=10)
    db.add(SalesActivity(maker_id=maker.id, project_id=p.id, kind="email",
                         summary="initial outreach", occurred_at=ts, created_at=ts))
    db.commit()

    res = email_service.generate_followup(db, p)  # days 自動算出
    check("最終営業10日前 → 自動で repropose", res["stage"] == "repropose")
    check("経過日数が概ね10日", 9 <= res["days_since_last_outreach"] <= 11)
    db.close()


def main():
    test_stage_for_days()
    test_build_message_per_stage()
    test_gmail_compose_url()
    test_generate_followup_stages_and_record()
    test_too_early_raises()
    test_auto_days_from_activity()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
