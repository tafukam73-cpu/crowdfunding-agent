"""送信後営業ワークフロー（sales_outreach 0045）のオフライン検証（ネットワーク不要）。

検証範囲:
- update_draft: 下書きの編集保存・user_edited による AI 再生成保護
- mark_sent: 送信済み記録・スナップショット凍結・5 営業日後フォロー期日・冪等
- add_business_days: 土日除外
- followup: 適格判定（返信/終端は対象外）・最大 2 回・背景ジョブ重複防止
- reply_preview / reply_confirm: preview は DB 非更新 / confirm のみ保存・intent 解析
- CRM（sales_opportunity）反映・タイムライン（SalesActivity）記録
- execution_tasks: 今日フォロー / 期限超過 / 返信対応の抽出

実行（backend ディレクトリで）:
    python tests/test_post_send_outreach.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "post_send_outreach_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.ai.mock_reply_assistant import MockReplyAssistant  # noqa: E402
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.crm import Maker, SalesActivity  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.sales_outreach import OutreachStatus, SalesOutreach  # noqa: E402
from app.services import sales_outreach_service as svc  # noqa: E402

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


_MOCK = MockReplyAssistant()


def _mk_project(db, *, site="wadiz", maker="올음", title="Cool Gadget", with_maker=True):
    p = Project(title=title, source_site=site, maker_name=maker, sales_status="not_started")
    db.add(p)
    db.commit()
    db.refresh(p)
    if with_maker:
        m = Maker(name=maker or "Maker", country="KR")
        db.add(m)
        db.commit()
        db.refresh(m)
        p.maker_id = m.id
        db.commit()
        db.refresh(p)
    return p


def _mk_cd(db, project_id, *, email="maker@example.com", form=None):
    cd = ContactDiscovery(
        project_id=project_id, status="completed", primary_email=email,
        primary_contact_form_url=form, official_site_url="https://maker.example.com",
        recommended_channel="email",
    )
    db.add(cd)
    db.commit()
    db.refresh(cd)
    return cd


def _gen_draft(db, project):
    """初回 4 言語生成（同期）で draft を用意する。"""
    return svc.run_generation(db, project)


db = SessionLocal()

# ---------------- 0. add_business_days（土日除外） ----------------
print("[add_business_days]")
fri = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)   # 金曜
due = svc.add_business_days(fri, 5)
check("金曜 + 5 営業日 = 翌週金曜（土日スキップ）",
      due.date() == datetime(2026, 7, 17).date())
mon = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)   # 月曜
check("月曜 + 5 営業日 = 翌週月曜",
      svc.add_business_days(mon, 5).date() == datetime(2026, 7, 20).date())
check("+1 営業日は必ず平日", svc.add_business_days(fri, 1).weekday() < 5)

# ---------------- 1. 下書き編集保存 ----------------
print("[update_draft]")
p1 = _mk_project(db, site="wadiz", maker="EditCo", title="Editable Gadget")
_mk_cd(db, p1.id)
_gen_draft(db, p1)
row = svc.update_draft(db, p1, subject="手動件名", body="手動本文です", language="ja")
check("編集後 generated_subject が反映", row.generated_subject == "手動件名")
check("編集後 generated_body が反映", row.generated_body == "手動本文です")
check("variants[ja] も更新される", row.generated_variants["ja"]["subject"] == "手動件名")
check("user_edited=True になる", row.user_edited is True)
check("edited_at が入る", row.edited_at is not None)

# AI 再生成で編集を上書きしない
svc.run_generation(db, p1)
row = svc.get_by_project(db, p1.id)
check("ユーザー編集は AI 再生成で上書きされない（件名保持）",
      row.generated_subject == "手動件名")
check("ユーザー編集は AI 再生成で上書きされない（本文保持）",
      row.generated_body == "手動本文です")

# ---------------- 2. Gmail open だけでは sent にならない ----------------
print("[gmail-open-not-sent]")
p2 = _mk_project(db, site="wadiz", maker="SendCo", title="Send Gadget")
_mk_cd(db, p2.id, email="send@example.com")
_gen_draft(db, p2)
# Gmail compose URL は「送信導線」なので、enforce モードでは営業対象判定
# （pre_outreach）が clear の案件にだけ出す。observe（既定）は従来どおり出す。
from app.config import settings as _settings  # noqa: E402
from app.services import lead_qualification_service as _lqs  # noqa: E402

_orig_mode = _settings.outreach_gate_mode
_settings.outreach_gate_mode = "observe"
ser = svc.serialize(db, svc.get_by_project(db, p2.id))
check("observe では未判定でも Gmail compose URL を出す",
      bool(ser["gmail_compose_url"]))
check("判定値をレスポンスで説明できる", "qualification_decision" in ser)

_settings.outreach_gate_mode = "enforce"
ser = svc.serialize(db, svc.get_by_project(db, p2.id))
check("enforce では未判定なら Gmail compose URL を出さない",
      ser["gmail_compose_url"] is None)
_lqs.run(db, p2, _lqs.STAGE_PRE_OUTREACH)
_dec = _lqs.get_latest(db, p2.id, stage=_lqs.STAGE_PRE_OUTREACH).decision
ser = svc.serialize(db, svc.get_by_project(db, p2.id))
if _dec == "clear":
    check("enforce: clear なら Gmail compose URL が出る",
          bool(ser["gmail_compose_url"]))
else:
    check("enforce: clear 以外は Gmail compose URL を出さない",
          ser["gmail_compose_url"] is None)
_settings.outreach_gate_mode = _orig_mode

ser = svc.serialize(db, svc.get_by_project(db, p2.id))
check("URL 生成後も status は draft のまま", ser["outreach_status"] == "draft")
check("URL 生成後も sent_at は None", ser["sent_at"] is None)

# ---------------- 3. mark_sent で sent + スナップショット ----------------
print("[mark_sent]")
out = svc.mark_sent(db, p2, language="ko")
row = out["outreach"]
check("mark_sent で status=sent", row.outreach_status == OutreachStatus.sent.value)
check("already_sent=False（初回）", out["already_sent"] is False)
check("sent_at が入る", row.sent_at is not None)
check("送信件名スナップショット保持", bool(row.sent_subject))
check("送信本文スナップショット保持", bool(row.sent_body_snapshot))
check("送信言語スナップショット=ko", row.sent_language == "ko")
check("宛先スナップショット保持", row.recipient_email == "send@example.com")
check("followup_due_at が設定される", row.followup_due_at is not None)
check("followup_due_at は sent_at より後", row.followup_due_at > row.sent_at)
check("followup_due_at は平日", row.followup_due_at.weekday() < 5)

# スナップショットは以後の編集で変わらない
snap_subject = row.sent_subject
svc.update_draft(db, p2, subject="送信後に書き換えた件名", language="ko")
row = svc.get_by_project(db, p2.id)
check("送信後に下書きを編集してもスナップショットは不変",
      row.sent_subject == snap_subject)

# ---------------- 4. 二重 mark_sent は冪等 ----------------
print("[mark_sent idempotent]")
first_sent_at = row.sent_at
out2 = svc.mark_sent(db, p2, language="en")
row = out2["outreach"]
check("二重 mark_sent は already_sent=True", out2["already_sent"] is True)
check("二重 mark_sent で sent_at は変わらない", row.sent_at == first_sent_at)
check("二重 mark_sent で言語スナップショットは変わらない", row.sent_language == "ko")

# ---------------- 5. CRM・タイムライン反映（送信時） ----------------
print("[CRM / timeline on send]")
from app.models.sales_opportunity import SalesOpportunity  # noqa: E402

opp = db.query(SalesOpportunity).join(
    ContactDiscovery, ContactDiscovery.id == SalesOpportunity.contact_discovery_id
).filter(ContactDiscovery.project_id == p2.id).first()
check("送信で CRM（sales_opportunity）が作成/更新される", opp is not None)
acts = db.query(SalesActivity).filter(SalesActivity.project_id == p2.id).all()
check("送信でタイムライン（SalesActivity）が記録される", len(acts) >= 1)

# ---------------- 6. フォローアップ適格判定・最大2回 ----------------
print("[followup eligibility / max 2]")
p3 = _mk_project(db, site="zeczec", maker="FollowCo", title="Follow Gadget")
_mk_cd(db, p3.id, email="follow@example.com")
_gen_draft(db, p3)

# 未送信はフォロー不可
ok, reason = svc.followup_eligibility(svc.get_by_project(db, p3.id))
check("未送信はフォロー対象外", ok is False)

svc.mark_sent(db, p3, language="zh")
ok, _ = svc.followup_eligibility(svc.get_by_project(db, p3.id))
check("送信済みはフォロー適格", ok is True)

# 1 回目フォロー生成（同期実行）
svc.run_followup_generation(db, p3)
row = svc.get_by_project(db, p3.id)
check("フォロー1回目で followup_count=1", row.followup_count == 1)
check("フォロー1回目で last_followup_at 記録", row.last_followup_at is not None)
check("フォロー後も status は sent のまま", row.outreach_status == OutreachStatus.sent.value)
check("フォロー本文がフォローアップ文面に差し替わる",
      row.generated_subject and ("再次" in row.generated_subject or "再" in row.generated_subject
                                 or "Following up" in row.generated_subject
                                 or "再문의" in row.generated_subject
                                 or "재문의" in row.generated_subject))
first_due = row.followup_due_at

# 2 回目フォロー生成
svc.run_followup_generation(db, p3)
row = svc.get_by_project(db, p3.id)
check("フォロー2回目で followup_count=2", row.followup_count == 2)
ok, reason = svc.followup_eligibility(row)
check("2回で上限到達→フォロー対象外", ok is False and "2" in (reason or ""))

# 上限到達後に生成しても回数は増えない
svc.run_followup_generation(db, p3)
row = svc.get_by_project(db, p3.id)
check("上限到達後は followup_count が増えない", row.followup_count == 2)

# ---------------- 7. 返信/終端はフォロー対象外 ----------------
print("[followup excluded states]")
for st in ("replied", "negotiating", "contract", "lost"):
    r = SalesOutreach(project_id=99000 + hash(st) % 1000,
                      outreach_status=st, followup_count=0)
    ok, _ = svc.followup_eligibility(r)
    check(f"{st} はフォロー対象外", ok is False)

# ---------------- 8. フォロー生成ジョブの重複禁止 ----------------
print("[followup job dedup]")
p4 = _mk_project(db, site="wadiz", maker="JobCo", title="Job Gadget")
_mk_cd(db, p4.id, email="job@example.com")
_gen_draft(db, p4)
svc.mark_sent(db, p4, language="ko")
# 動作中ジョブを模擬
active = ContactIntelligenceJob(
    project_id=p4.id, job_type=CIJobType.followup_generation.value,
    status=CIJobStatus.running.value, progress=10,
)
db.add(active)
db.commit()
out = svc.request_followup_generation(db, p4, runner=lambda job_id: None)
check("動作中フォロージョブがあれば duplicate=True",
      out["duplicate"] is True and out["created"] is False)
job_cnt = db.query(ContactIntelligenceJob).filter(
    ContactIntelligenceJob.project_id == p4.id,
    ContactIntelligenceJob.job_type == CIJobType.followup_generation.value,
).count()
check("重複フォロージョブを作らない（1 件のまま）", job_cnt == 1)

# ---------------- 9. reply preview は DB 非更新 ----------------
print("[reply preview]")
p5 = _mk_project(db, site="wadiz", maker="ReplyCo", title="Reply Gadget")
_mk_cd(db, p5.id, email="reply@example.com")
_gen_draft(db, p5)
svc.mark_sent(db, p5, language="ko")

prev = svc.reply_preview(
    db, p5, incoming_subject="Re: proposal",
    incoming_body="We are very interested! Sounds great, we'd love to talk.",
    incoming_from="ceo@replyco.com", assistant=_MOCK,
)
check("preview は intent を返す", prev["intent"] == "interested")
check("preview は confidence を返す", prev["confidence"] in ("high", "medium", "low"))
check("preview は日本語サマリを返す", bool(prev["summary"]))
row = svc.get_by_project(db, p5.id)
check("preview は DB を更新しない（reply_intent 未設定）", row.reply_intent is None)
check("preview は status を変えない（sent のまま）",
      row.outreach_status == OutreachStatus.sent.value)
check("preview は last_reply_at を更新しない", row.last_reply_at is None)

# ---------------- 10. reply confirm のみ保存・intent 解析・状態遷移 ----------------
print("[reply confirm]")
res = svc.reply_confirm(
    db, p5, incoming_subject="Re: proposal",
    incoming_body="We are very interested! Sounds great, we'd love to talk.",
    incoming_from="ceo@replyco.com", assistant=_MOCK,
)
row = res["outreach"]
check("confirm で reply_intent 保存", row.reply_intent == "interested")
check("confirm で reply_summary 保存", bool(row.reply_summary))
check("confirm で reply_confidence 保存", row.reply_confidence in ("high", "medium", "low"))
check("confirm で last_reply_at 記録", row.last_reply_at is not None)
check("confirm で status=replied に遷移", row.outreach_status == OutreachStatus.replied.value)
# confirm 後はフォロー対象外
ok, _ = svc.followup_eligibility(row)
check("返信登録後はフォロー対象外", ok is False)

# intent 解析：not_interested
p6 = _mk_project(db, site="wadiz", maker="PassCo", title="Pass Gadget")
_mk_cd(db, p6.id, email="pass@example.com")
_gen_draft(db, p6)
svc.mark_sent(db, p6, language="ko")
res2 = svc.reply_confirm(
    db, p6, incoming_body="Thanks but we are not interested at this time.",
    incoming_from="x@passco.com", assistant=_MOCK,
)
check("not interested 本文は intent=not_interested",
      res2["outreach"].reply_intent == "not_interested")

# CRM・タイムライン（返信時）
acts5 = db.query(SalesActivity).filter(SalesActivity.project_id == p5.id).all()
check("返信登録でタイムラインが記録される",
      any("返信" in a.summary for a in acts5))

# ---------------- 11. execution_tasks 抽出 ----------------
print("[execution_tasks]")
# p3: 送信済み・フォロー2回・返信なし（followup_due 未到来 → awaiting か follow_today）
# p5/p6: replied
# 期限超過を作る：p7 を送信し followup_due_at を過去に
p7 = _mk_project(db, site="wadiz", maker="OverdueCo", title="Overdue Gadget")
_mk_cd(db, p7.id, email="over@example.com")
_gen_draft(db, p7)
svc.mark_sent(db, p7, language="ko")
row7 = svc.get_by_project(db, p7.id)
row7.followup_due_at = svc._now() - timedelta(days=3)  # 期限超過
db.commit()

tasks = svc.execution_tasks(db, limit=50)
follow_ids = [t["project_id"] for t in tasks["follow_today"]]
overdue_ids = [t["project_id"] for t in tasks["overdue"]]
replied_ids = [t["project_id"] for t in tasks["replied"]]
check("期限到来案件が今日フォローに入る", p7.id in follow_ids)
check("期限超過案件が overdue に入る", p7.id in overdue_ids)
check("overdue の days_overdue が正の値",
      any(t["project_id"] == p7.id and (t["days_overdue"] or 0) >= 1
          for t in tasks["overdue"]))
check("返信済み案件が replied に入る", p5.id in replied_ids and p6.id in replied_ids)
check("返信済み案件は follow_today に入らない", p5.id not in follow_ids)

db.close()

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
