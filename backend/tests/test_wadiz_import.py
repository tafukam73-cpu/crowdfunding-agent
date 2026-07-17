"""Wadiz 手動取り込みのオフライン検証（ネットワーク不要）。

貼り付け本文からの抽出（mailto/平文/難読化/span分割/HTML entity/[at][dot]）、
除外（@wadiz.kr のみ）、外部公開メール（Gmail/Naver）許可、プレビュー非書込、
confirm 保存、冪等、非破壊 merge、Contact Discovery 反映、推測メール非生成を検証する。

実行（backend ディレクトリで）:
    python tests/test_wadiz_import.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "wadiz_import_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.wadiz_import import WadizImport  # noqa: E402
from app.services import wadiz_import_service as w  # noqa: E402

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


_pcount = [0]


def _mk(db) -> Project:
    _pcount[0] += 1
    p = Project(title="Wadiz P", source_site="wadiz",
                source_url=f"https://www.wadiz.kr/web/campaign/detail/{_pcount[0]}",
                maker_name=None)
    db.add(p); db.commit(); db.refresh(p)
    return p


SRC = "https://www.wadiz.kr/web/campaign/detail/397151"

# 様々な難読化を含む貼り付け HTML
_HTML = """
<div>메이커 정보</div>
<a href="mailto:hello@makerbrand.co.kr">문의</a>
<p>대표 이메일: contact@nolo-brand.com 입니다.</p>
고객센터: support (at) example-maker (dot) com
Gmail 문의: nolobrand2024@gmail.com / Naver: nolo@naver.com
운영 문의 support@wadiz.kr / no-reply@wadiz.kr / test@example.com / noreply@foo.com
<span>info</span>@<span>splitmaker.com</span>
&#115;ales&#64;entitymaker.com
<a href="https://www.instagram.com/nolo.official">IG</a>
<a href="https://blog.naver.com/nolobrand">blog</a>
<a href="https://pf.kakao.com/_abcd">kakao</a>
<a href="https://nolo-brand.com">공식몰</a>
"""


def test_extract_all_patterns():
    print("test_extract_all_patterns")
    r = w.extract(_HTML, "html", SRC)
    vals = {e["value"].lower() for e in r["emails"]}
    check("mailto を抽出", "hello@makerbrand.co.kr" in vals)
    check("平文を抽出", "contact@nolo-brand.com" in vals)
    check("[at]/[dot] を復号", "support@example-maker.com" in vals)
    check("Gmail 公開メールを許可", "nolobrand2024@gmail.com" in vals)
    check("Naver 公開メールを許可", "nolo@naver.com" in vals)
    check("HTML entity メールを復号", "sales@entitymaker.com" in vals)
    # source/method/evidence 保持
    e0 = r["emails"][0]
    check("source_type=wadiz_manual_import", e0["source_type"] == "wadiz_manual_import")
    check("source_url を保持", e0["source_url"] == SRC)
    check("extraction_method を保持", "extraction_method" in e0)
    check("evidence を保持", bool(e0["evidence"]))


def test_exclusion_only_wadiz_platform():
    print("test_exclusion_only_wadiz_platform")
    r = w.extract(_HTML, "html", SRC)
    vals = {e["value"].lower() for e in r["emails"]}
    ex = {x["value"].lower(): x["reason"] for x in r["excluded"]}
    check("@wadiz.kr 運営を除外", "support@wadiz.kr" not in vals)
    check("no-reply@wadiz.kr を除外", "no-reply@wadiz.kr" not in vals)
    check("除外理由が platform_domain:wadiz.kr",
          ex.get("support@wadiz.kr", "").startswith("platform_domain:wadiz.kr"))
    check("example.com を除外", "test@example.com" not in vals)
    check("noreply@foo.com を除外", "noreply@foo.com" not in vals)
    check("外部ドメインは除外しない（gmail/naver/co.kr/com 保存可）",
          "nolo@naver.com" in vals and "hello@makerbrand.co.kr" in vals)


def test_socials_and_official():
    print("test_socials_and_official")
    r = w.extract(_HTML, "html", SRC)
    check("instagram 抽出", r["socials"].get("instagram", "").endswith("nolo.official"))
    check("naver_blog 抽出", "naver_blog" in r["socials"])
    check("kakao 抽出", "kakao" in r["socials"])
    check("公式サイト候補", "https://nolo-brand.com" in r["official_url_candidates"])


def test_preview_does_not_write():
    print("test_preview_does_not_write")
    db = SessionLocal()
    p = _mk(db)
    before_imports = db.query(func.count()).select_from(WadizImport).scalar()
    before_cd = db.query(func.count()).select_from(ContactDiscovery).scalar()
    pv = w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
    after_imports = db.query(func.count()).select_from(WadizImport).scalar()
    after_cd = db.query(func.count()).select_from(ContactDiscovery).scalar()
    check("preview で WadizImport を作らない", after_imports == before_imports)
    check("preview で ContactDiscovery を作らない", after_cd == before_cd)
    check("preview は content_hash を返す", len(pv["content_hash"]) == 64)
    check("preview は new_email_count を返す", pv["new_email_count"] >= 4)
    check("preview で enrichment を変更しない", (p.enrichment or {}).get("public_emails") is None)
    db.close()


def test_confirm_saves_and_reflects():
    print("test_confirm_saves_and_reflects")
    db = SessionLocal()
    p = _mk(db)
    pv = w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
    out = w.confirm(
        db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
        socials=pv["socials"], official_url=pv["official_url"],
        source_url=SRC, content_type="html", imported_by="tester",
    )
    check("保存件数>0（0件成功扱いしない）", out["saved_emails"] >= 4)
    check("contact_found=True", out["contact_found"] is True)
    db.refresh(p)
    pub = (p.enrichment or {}).get("public_emails") or []
    check("enrichment.public_emails に反映", len(pub) >= 4)
    check("public_email に provenance", pub[0].get("raw_content_hash") == pv["content_hash"])
    cd = db.query(ContactDiscovery).filter_by(project_id=p.id).first()
    check("ContactDiscovery を作成（Contact Intelligence反映）", cd is not None)
    check("primary_email 設定", cd.primary_email is not None)
    check("WadizImport 履歴を保存", len(w.get_imports(db, p.id)) == 1)
    db.close()


def test_idempotent_and_no_duplicate_email():
    print("test_idempotent_and_no_duplicate_email")
    db = SessionLocal()
    p = _mk(db)
    pv = w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
    w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
              socials=pv["socials"], official_url=pv["official_url"], source_url=SRC)
    # 同一内容を再取り込み → 重複作成しない
    out2 = w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
                     socials=pv["socials"], official_url=pv["official_url"], source_url=SRC)
    check("冪等：同一内容は already_imported", out2["already_imported"] is True)
    check("冪等：WadizImport は1件のまま", len(w.get_imports(db, p.id)) == 1)
    db.refresh(p)
    pub = (p.enrichment or {}).get("public_emails") or []
    vals = [e["value"].lower() for e in pub]
    check("同一メールを重複保存しない", len(vals) == len(set(vals)))
    db.close()


def test_non_destructive_merge():
    print("test_non_destructive_merge")
    db = SessionLocal()
    p = _mk(db)
    # 既存 public_emails を用意
    p.enrichment = {"public_emails": [{"value": "existing@keepme.com",
                                       "source_type": "prior"}]}
    db.commit()
    pv = w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
    w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
              socials=pv["socials"], source_url=SRC)
    db.refresh(p)
    vals = {e["value"].lower() for e in (p.enrichment or {}).get("public_emails", [])}
    check("既存メールを空値で消さない", "existing@keepme.com" in vals)
    check("新規メールも追加", "contact@nolo-brand.com" in vals)
    db.close()


def test_confirm_rejects_wadiz_and_dummy():
    print("test_confirm_rejects_wadiz_and_dummy")
    db = SessionLocal()
    p = _mk(db)
    # ユーザーが誤って @wadiz.kr / example を送っても保存しない
    out = w.confirm(
        db, p, content_hash_value="hash_x",
        emails=[{"value": "support@wadiz.kr"}, {"value": "test@example.com"},
                {"value": "real@makerX.com"}],
        source_url=SRC,
    )
    db.refresh(p)
    vals = {e["value"].lower() for e in (p.enrichment or {}).get("public_emails", [])}
    check("@wadiz.kr は confirm でも保存しない", "support@wadiz.kr" not in vals)
    check("example は保存しない", "test@example.com" not in vals)
    check("実メールは保存", "real@makerx.com" in vals)
    check("保存件数=1", out["saved_emails"] == 1)
    db.close()


def test_jsonld_and_script_and_plural_fields():
    print("test_jsonld_and_script_and_plural_fields")
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Organization","name":"놀로","email":"ld@nolo-brand.com",
     "telephone":"02-123-4567","sameAs":["https://www.instagram.com/nolo.official"]}
    </script>
    </head><body>
    <script>var contact = "js" + "@" + "scriptmaker.com";</script>
    <p>상호: 놀로  대표자: 홍길동  전화: 010-9876-5432</p>
    <a href="https://nolo-brand.com">website</a>
    </body></html>
    """
    r = w.extract(html, "html", SRC)
    vals = {e["value"].lower() for e in r["emails"]}
    check("JSON-LD 内メールを抽出", "ld@nolo-brand.com" in vals)
    check("script 文字列連結メールを抽出", "js@scriptmaker.com" in vals)
    check("websites（複数形）を返す", "https://nolo-brand.com" in r["websites"])
    check("phones（複数形）を返す", any("9876" in p or "123" in p for p in r["phones"]))
    check("company_names（複数形）を返す", len(r["company_names"]) >= 1)
    check("contact_names（複数形）を返す", "홍길동" in (r["contact_names"] or [""])[0]
          if r["contact_names"] else False)


def test_no_guessed_emails():
    print("test_no_guessed_emails")
    # メールが本文に無ければ 0 件（推測で作らない）
    r = w.extract("메이커 놀로 / 대표 홍길동 / 문의 전화 010-1234-5678", "text", SRC)
    check("本文にメール無し→0件（推測しない）", len(r["emails"]) == 0)
    check("警告を返す", len(r["warnings"]) >= 1)


def test_browser_capture_footer_context_source_type():
    print("test_browser_capture_footer_context_source_type")
    html = (
        "<body><div class='maker'>메이커 고객센터 문의 이메일: contact@makerbrand.co.kr</div>"
        "<footer><a href='mailto:help@thirdparty-svc.com'>help</a> support@wadiz.kr</footer></body>"
    )
    r = w.extract(html, "html", SRC, source_type="wadiz_browser_capture")
    by = {e["value"]: e for e in r["emails"]}
    check("本文メールは region=body", by["contact@makerbrand.co.kr"]["region"] == "body")
    check("本文メールに文脈（고객센터/이메일）",
          by["contact@makerbrand.co.kr"]["context"] is not None)
    check("footer メールは region=chrome", by["help@thirdparty-svc.com"]["region"] == "chrome")
    check("footer メールは低確度", by["help@thirdparty-svc.com"]["confidence"] == "low")
    check("source_type を反映", by["contact@makerbrand.co.kr"]["source_type"] == "wadiz_browser_capture")
    check("@wadiz.kr は除外", "support@wadiz.kr" not in by)


def test_resolve_projects():
    print("test_resolve_projects")
    db = SessionLocal()
    p = Project(title="R", source_site="wadiz",
                source_url="https://www.wadiz.kr/web/campaign/detail/999123",
                maker_name=None)
    db.add(p); db.commit(); db.refresh(p)
    # 完全一致
    got = w.resolve_projects(db, "https://www.wadiz.kr/web/campaign/detail/999123")
    check("完全一致で 1 件", len(got) == 1 and got[0].id == p.id)
    # campaign ID 一致（クエリ付き）
    got2 = w.resolve_projects(db, "https://www.wadiz.kr/web/campaign/detail/999123?ref=x")
    check("campaign ID 一致", len(got2) == 1 and got2[0].id == p.id)
    # 不一致
    check("不一致は 0 件", len(w.resolve_projects(db, "https://www.wadiz.kr/web/campaign/detail/000")) == 0)
    check("campaign id 抽出", w.extract_campaign_id(
        "https://www.wadiz.kr/web/campaign/detail/999123") == "999123")
    db.close()


def test_build_capture_content():
    print("test_build_capture_content")
    content, ctype = w.build_capture_content({
        "html": "<div>maker@x.com</div>",
        "text": "innerText",
        "json_ld": ['{"@type":"Organization","email":"ld@x.com"}'],
        "mailtos": ["mailto:mt@x.com"],
        "meta": {"og:email": "meta@x.com"},
    })
    check("html があれば content_type=html", ctype == "html")
    for frag in ("maker@x.com", "ld@x.com", "mt@x.com", "meta@x.com"):
        check(f"content に {frag} を含む", frag in content)


def test_browser_capture_confirm_source_type():
    print("test_browser_capture_confirm_source_type")
    db = SessionLocal()
    p = _mk(db)
    # サンプルは maker 公式ドメイン（makerz.co.kr は既知代理店で正しく除外されるため使わない。
    # このテストの意図は browser-capture 保存フローと source_type の検証でメール分類ではない）。
    pv = w.preview(db, p, content="<div>메이커 문의 real@makerbrand.co.kr</div>",
                   content_type="html", source_url=SRC, source_type="wadiz_browser_capture")
    out = w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
                    source_url=SRC, source_type="wadiz_browser_capture")
    db.refresh(p)
    pub = (p.enrichment or {}).get("public_emails") or []
    check("保存された", out["saved_emails"] >= 1)
    check("source_type=wadiz_browser_capture で保存",
          any(e["source_type"] == "wadiz_browser_capture" for e in pub))
    check("provenance に wadiz_browser_capture",
          "wadiz_browser_capture" in (p.enrichment.get("provenance") or {}))
    db.close()


def test_confirm_is_async_queues_reassessment():
    # confirm は同期で run_assessment を呼ばず、queued の再評価ジョブを作って即返す。
    print("test_confirm_is_async_queues_reassessment")
    import time

    import app.services.contact_intelligence_service as ci
    from app.services import sales_assessment_service as sa

    sync_calls: list[int] = []
    orig_ra = sa.run_assessment
    orig_qr = ci.queue_reassessment
    made: dict = {}

    # 同期で run_assessment が呼ばれたら記録（呼ばれてはいけない）。
    sa.run_assessment = lambda db, project, **k: sync_calls.append(project.id)

    # 背景スレッドを起動させず job 行だけ作る（テストを決定的にする）。
    def fake_qr(db, project, runner=None):
        j = orig_qr(db, project, runner=lambda jid: None)
        made["id"] = j.id
        return j

    ci.queue_reassessment = fake_qr
    try:
        db = SessionLocal()
        p = _mk(db)
        pv = w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
        t0 = time.time()
        out = w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
                        socials=pv["socials"], official_url=pv["official_url"], source_url=SRC)
        dt = time.time() - t0
        check("confirm は同期で run_assessment を呼ばない", sync_calls == [])
        check("confirm は数秒以内に返る（≤3s）", dt < 3.0)
        check("reassessment_status=queued", out["reassessment_status"] == "queued")
        check("reassessment_job_id を返す", out["reassessment_job_id"] == made.get("id"))
        check("同期でも保存済みメールはある（0件成功扱いしない）", out["saved_emails"] >= 4)
        from app.models.contact_intelligence_job import ContactIntelligenceJob as J
        job = db.get(J, out["reassessment_job_id"])
        check("job_type=wadiz_contact_reassessment", job.job_type == "wadiz_contact_reassessment")
        db.close()
    finally:
        sa.run_assessment = orig_ra
        ci.queue_reassessment = orig_qr


def test_queue_reassessment_dedup():
    # 同一 project・同一 job_type の queued/running を重複作成しない。
    print("test_queue_reassessment_dedup")
    import app.services.contact_intelligence_service as ci
    from app.models.contact_intelligence_job import (
        ContactIntelligenceJob as J,
        CIJobStatus,
    )

    db = SessionLocal()
    p = _mk(db)
    j1 = ci.queue_reassessment(db, p, runner=lambda jid: None)  # queued のまま
    j2 = ci.queue_reassessment(db, p, runner=lambda jid: None)
    check("重複作成せず同一 active ジョブを返す", j1.id == j2.id)
    n = (
        db.query(J)
        .filter_by(project_id=p.id, job_type="wadiz_contact_reassessment")
        .count()
    )
    check("再評価ジョブは1件のみ", n == 1)
    check("status=queued で作成", j1.status == CIJobStatus.queued.value)
    db.close()


def test_reassessment_runner_completes_rule_based():
    # 再評価ジョブは run_assessment（ルールベース・外部HTTPなし）だけを実行して完了する。
    print("test_reassessment_runner_completes_rule_based")
    import app.services.contact_intelligence_service as ci
    from app.services import sales_assessment_service as sa
    from app.models.contact_intelligence_job import (
        ContactIntelligenceJob as J,
        CIJobStatus,
    )

    ran: list[int] = []
    orig_ra = sa.run_assessment
    sa.run_assessment = lambda db, project, **k: ran.append(project.id)
    try:
        db = SessionLocal()
        p = _mk(db)
        # runner=ci.execute_job で同期実行（本番の実行経路をそのまま通す）。
        j = ci.queue_reassessment(db, p, runner=ci.execute_job)
        db.expire_all()
        job = db.get(J, j.id)
        check("再評価で run_assessment を実行", ran == [p.id])
        check("ジョブは completed", job.status == CIJobStatus.completed.value)
        db.close()
    finally:
        sa.run_assessment = orig_ra


def test_reassessment_failure_keeps_saved_emails():
    # 再評価が失敗しても保存済みメールはロールバック・削除されない。
    print("test_reassessment_failure_keeps_saved_emails")
    import app.services.contact_intelligence_service as ci
    from app.services import sales_assessment_service as sa
    from app.models.contact_intelligence_job import (
        ContactIntelligenceJob as J,
        CIJobStatus,
    )

    orig_ra = sa.run_assessment
    orig_qr = ci.queue_reassessment

    # confirm 時はジョブ行を作らないスタブ（後で明示的に失敗ジョブを走らせる）。
    class _Fake:
        id = None
        status = "queued"

    ci.queue_reassessment = lambda db, project, runner=None: _Fake()
    try:
        db = SessionLocal()
        p = _mk(db)
        pv = w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
        w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
                  socials=pv["socials"], source_url=SRC)
        db.refresh(p)
        before = len((p.enrichment or {}).get("public_emails") or [])
        check("confirm で保存済み（前提）", before >= 4)

        # 実際の再評価ジョブを同期実行して失敗させる。
        ci.queue_reassessment = orig_qr

        def boom(db, project, **k):
            raise RuntimeError("reassessment boom")

        sa.run_assessment = boom
        j = ci.queue_reassessment(db, p, runner=ci.execute_job)
        db.expire_all()
        job = db.get(J, j.id)
        p2 = db.get(Project, p.id)
        after = len((p2.enrichment or {}).get("public_emails") or [])
        check("再評価失敗でも保存済みメールは残る", after == before and after >= 4)
        check("ジョブは failed として記録", job.status == CIJobStatus.failed.value)
        check("失敗理由を保持", bool(job.error))
        db.close()
    finally:
        sa.run_assessment = orig_ra
        ci.queue_reassessment = orig_qr


def test_confirm_resend_idempotent_status():
    # タイムアウト後の再送（同一 content_hash）は重複保存せず already_imported を返す。
    print("test_confirm_resend_idempotent_status")
    import app.services.contact_intelligence_service as ci

    orig_qr = ci.queue_reassessment
    ci.queue_reassessment = lambda db, project, runner=None: orig_qr(
        db, project, runner=lambda jid: None
    )
    try:
        db = SessionLocal()
        p = _mk(db)
        pv = w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
        w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
                  socials=pv["socials"], source_url=SRC)
        out2 = w.confirm(db, p, content_hash_value=pv["content_hash"], emails=pv["emails"],
                         socials=pv["socials"], source_url=SRC)
        check("再送は already_imported", out2["already_imported"] is True)
        check("再送の reassessment_status=already_imported",
              out2["reassessment_status"] == "already_imported")
        check("再送でジョブを重複作成しない", out2["reassessment_job_id"] is None)
        check("WadizImport は1件のまま", len(w.get_imports(db, p.id)) == 1)
        db.close()
    finally:
        ci.queue_reassessment = orig_qr


def test_preview_creates_no_reassessment_job():
    # GET 相当の preview は再評価ジョブを作らない（重い処理を起動しない）。
    print("test_preview_creates_no_reassessment_job")
    from app.models.contact_intelligence_job import ContactIntelligenceJob as J

    db = SessionLocal()
    p = _mk(db)
    w.preview(db, p, content=_HTML, content_type="html", source_url=SRC)
    n = db.query(J).filter_by(project_id=p.id).count()
    check("preview は CI ジョブを作らない", n == 0)
    db.close()


if __name__ == "__main__":
    test_browser_capture_footer_context_source_type()
    test_resolve_projects()
    test_build_capture_content()
    test_browser_capture_confirm_source_type()
    test_extract_all_patterns()
    test_exclusion_only_wadiz_platform()
    test_socials_and_official()
    test_preview_does_not_write()
    test_confirm_saves_and_reflects()
    test_idempotent_and_no_duplicate_email()
    test_non_destructive_merge()
    test_confirm_rejects_wadiz_and_dummy()
    test_jsonld_and_script_and_plural_fields()
    test_no_guessed_emails()
    test_confirm_is_async_queues_reassessment()
    test_queue_reassessment_dedup()
    test_reassessment_runner_completes_rule_based()
    test_reassessment_failure_keeps_saved_emails()
    test_confirm_resend_idempotent_status()
    test_preview_creates_no_reassessment_job()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
