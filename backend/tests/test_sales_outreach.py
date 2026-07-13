"""営業実行パイプライン（sales_outreach）のオフライン検証（ネットワーク不要）。

- compute_priority: 優先度スコア（適性 × email/maker/新規）の妥当性
- build_multilang_outreach: 4 言語生成と推奨言語の導出
- today_priority: 優先度順・Contact Intelligence 未完了案件の除外
- run_generation: 4 言語 draft の作成・二重下書き防止・CRM 反映
- request_generation: 生成ジョブの重複起動防止

実行（backend ディレクトリで）:
    python tests/test_sales_outreach.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "sales_outreach_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.ai.sales_outreach import build_multilang_outreach  # noqa: E402
from app.models.contact_discovery import ContactDiscovery, DiscoveryStatus  # noqa: E402
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.models.sales_assessment import SalesAssessment  # noqa: E402
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


def _mk_project(db, *, site="wadiz", maker="올음", status="not_started", title="Cool Gadget"):
    p = Project(title=title, source_site=site, maker_name=maker, sales_status=status)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _mk_assessment(db, project_id, *, jm=78, ex=55, mk=69, overall=67, conf=75):
    row = SalesAssessment(
        project_id=project_id,
        japan_market_fit_score=jm,
        exclusivity_score=ex,
        makuake_fit_score=mk,
        overall_priority_score=overall,
        confidence=conf,
        engine="test",
        details_json={
            "japan_market_fit": {"score": jm},
            "exclusivity": {"score": ex},
            "makuake_fit": {"score": mk},
        },
    )
    db.add(row)
    db.commit()
    return row


def _mk_cd(db, project_id, *, status="completed", email="maker@example.com", form=None):
    cd = ContactDiscovery(
        project_id=project_id,
        status=status,
        primary_email=email,
        primary_contact_form_url=form,
        official_site_url="https://maker.example.com",
        recommended_channel="email",
    )
    db.add(cd)
    db.commit()
    db.refresh(cd)
    return cd


# ---------------- 1. compute_priority ----------------
print("[compute_priority]")
strong, sr = svc.compute_priority(
    japan_market_fit=80, exclusivity=75, makuake_fit=70,
    has_email=True, has_maker=True, is_new=True,
)
weak, wr = svc.compute_priority(
    japan_market_fit=20, exclusivity=15, makuake_fit=10,
    has_email=False, has_maker=False, is_new=False,
)
check("強い案件は高スコア", strong >= 80)
check("弱い案件は低スコア", weak <= 25)
check("強い案件 > 弱い案件", strong > weak)
# email/maker/new の加点が効く
base, _ = svc.compute_priority(japan_market_fit=50, exclusivity=50, makuake_fit=50,
                               has_email=False, has_maker=False, is_new=False)
plus, _ = svc.compute_priority(japan_market_fit=50, exclusivity=50, makuake_fit=50,
                               has_email=True, has_maker=True, is_new=True)
check("email/maker/新規で +20", plus - base == 20)
check("未評価(None)は 0 点相当に落ちる",
      svc.compute_priority(japan_market_fit=None, exclusivity=None, makuake_fit=None,
                           has_email=False, has_maker=False, is_new=False)[0] == 0)
check("理由が必ず 1 件以上", len(sr) >= 1 and len(wr) >= 1)

# ---------------- 2. build_multilang_outreach ----------------
print("[build_multilang_outreach]")


def _tp(title, site, maker):
    """DB 非登録の一時 Project（生成器は属性を読むだけ）。"""
    return Project(
        title=title, source_site=site, maker_name=maker,
        description="portable compact gadget for home cinema", category="design goods",
    )


res = build_multilang_outreach(_tp("Mini Projector", "wadiz", "Green Studio"))
check("4 言語すべて生成", set(res["variants"].keys()) == {"en", "ko", "zh", "ja"})
check("wadiz は韓国語推奨", res["recommended_language"] == "ko")
check("zeczec は中国語推奨",
      build_multilang_outreach(_tp("X", "zeczec", "M"))["recommended_language"] == "zh")
check("kickstarter は英語推奨",
      build_multilang_outreach(_tp("X", "kickstarter", "M"))["recommended_language"] == "en")
check("各言語に subject/body がある",
      all(v.get("subject") and v.get("body") for v in res["variants"].values()))
check("韓国語本文にメーカー名が入る", "Green Studio" in res["variants"]["ko"]["body"])

# ---------------- 3. today_priority（優先度順・CI 未完了除外） ----------------
print("[today_priority]")
db = SessionLocal()

# A: 評価済み・CI 完了・メールあり → 出る
pa = _mk_project(db, site="wadiz", maker="MakerA", title="Ready Gadget")
_mk_assessment(db, pa.id, jm=80, ex=70, mk=65)
_mk_cd(db, pa.id, status="completed", email="a@example.com")

# B: 評価済みだが CI 未完了（pending）→ 除外される
pb = _mk_project(db, site="zeczec", maker="MakerB", title="No CI Gadget")
_mk_assessment(db, pb.id, jm=90, ex=90, mk=90)
_mk_cd(db, pb.id, status="pending", email=None)

# C: CI 完了だがメール無し（フォームのみ）→ 出るが contact_ready
pc = _mk_project(db, site="zeczec", maker="MakerC", title="Form Only")
_mk_assessment(db, pc.id, jm=60, ex=50, mk=55)
_mk_cd(db, pc.id, status="completed", email=None, form="https://c.example.com/contact")

items = svc.today_priority(db, limit=20)
ids = [it["project_id"] for it in items]
check("CI 完了案件は含まれる", pa.id in ids and pc.id in ids)
check("Contact Intelligence 未完了案件は除外", pb.id not in ids)
a_item = next(it for it in items if it["project_id"] == pa.id)
c_item = next(it for it in items if it["project_id"] == pc.id)
check("メールありは contact_ready", a_item["contact_ready"] is True)
check("フォームのみでも contact_ready", c_item["contact_ready"] is True)
check("A(高適性+email) は C より上位", ids.index(pa.id) < ids.index(pc.id))
check("recommended_language が付く", a_item["recommended_language"] == "ko")

# ---------------- 4. run_generation（draft 作成・二重防止・CRM 反映） ----------------
print("[run_generation]")
row = svc.run_generation(db, pa)
check("draft が作成される", row.outreach_status == OutreachStatus.draft.value)
check("4 言語 variants が保存される",
      set((row.generated_variants or {}).keys()) == {"en", "ko", "zh", "ja"})
check("推奨言語が採用される(ko)", row.generated_language == "ko")
check("generated_at が入る", row.generated_at is not None)
check("priority_score が入る", isinstance(row.priority_score, int))

# 二重下書き防止：もう一度呼んでも行は 1 本のまま
svc.run_generation(db, pa)
cnt = db.query(SalesOutreach).filter(SalesOutreach.project_id == pa.id).count()
check("二重下書き防止（1 案件 1 行）", cnt == 1)

# CRM 反映：sales_opportunity が作られている
from app.services import sales_opportunity_service as sos  # noqa: E402

cd_a = db.query(ContactDiscovery).filter(ContactDiscovery.project_id == pa.id).first()
opp = sos.get_by_contact_discovery(db, cd_a.id)
check("CRM（sales_opportunity）へ反映", opp is not None and "営業メール" in (opp.next_action or ""))

# 終端状態（成約）は生成し直しても保持
row.outreach_status = OutreachStatus.contract.value
db.commit()
svc.run_generation(db, pa)
db.refresh(row)
check("成約(contract)は再生成で draft に戻さない",
      row.outreach_status == OutreachStatus.contract.value)

# ---------------- 5. request_generation（ジョブ重複防止） ----------------
print("[request_generation]")
# 生成ジョブが動作中（running）のとき、2 回目は duplicate=True で新ジョブを作らない
active = ContactIntelligenceJob(
    project_id=pc.id,
    job_type=CIJobType.outreach_generation.value,
    status=CIJobStatus.running.value,
    progress=10,
)
db.add(active)
db.commit()
out = svc.request_generation(db, pc, runner=lambda job_id: None)
check("動作中ジョブがあれば duplicate=True", out["duplicate"] is True and out["created"] is False)
job_cnt = db.query(ContactIntelligenceJob).filter(
    ContactIntelligenceJob.project_id == pc.id,
    ContactIntelligenceJob.job_type == CIJobType.outreach_generation.value,
).count()
check("重複ジョブを作らない（1 件のまま）", job_cnt == 1)

db.close()

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
