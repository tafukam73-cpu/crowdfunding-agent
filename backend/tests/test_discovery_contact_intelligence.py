"""発掘商品 → Contact Intelligence 連携（Discovery Engine v1-5）のオフライン検証。

discovered_product から既存の Contact Discovery を起動する連携を、実ネットワーク
なしで検証する。重い探索（contact_discovery_service.run_discovery）は discovery_fn
注入 / モック差し替えで置き換える。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_discovery_contact_intelligence.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "discovery_ci_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.contact_discovery import ContactDiscovery, DiscoveryStatus  # noqa: E402
from app.models.discovered_product import DiscoveredProduct  # noqa: E402
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


# 実ネットワークを使わない run_discovery の代替（ContactDiscovery を作って返す）。
_calls: list[int] = []


def fake_run_discovery(db, project, fetch_fn=None):
    _calls.append(project.id)
    row = ContactDiscovery(
        project_id=project.id,
        status=DiscoveryStatus.completed.value,
        official_site_url=project.maker_url,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _mk(db, **kwargs):
    product, _ = svc.create(db, kwargs)
    return product


def test_official_url_preferred():
    print("test_official_url_preferred")
    db = SessionLocal()
    p = _mk(db, source_platform="kickstarter",
            source_url="https://kck.st/ci-off",
            official_website_url="https://maker-off.example",
            product_name="Gadget Off", creator_name="Off Maker")
    res = svc.start_contact_intelligence_from_product(
        db, p.id, discovery_fn=fake_run_discovery)
    check("official_website_url が used_url に優先される",
          res["used_url"] == "https://maker-off.example")
    check("status=started", res["status"] == "started")
    check("contact_discovery_id が返る", res["contact_discovery_id"] is not None)
    db.close()


def test_source_url_fallback():
    print("test_source_url_fallback")
    db = SessionLocal()
    p = _mk(db, source_platform="indiegogo",
            source_url="https://igg.me/ci-src",
            product_name="Gadget Src", creator_name="Src Maker")
    res = svc.start_contact_intelligence_from_product(
        db, p.id, discovery_fn=fake_run_discovery)
    check("official が無ければ source_url を used_url に使う",
          res["used_url"] == "https://igg.me/ci-src")
    check("status=started", res["status"] == "started")
    db.close()


def test_no_url_is_safe_error():
    print("test_no_url_is_safe_error")
    db = SessionLocal()
    p = _mk(db, source_platform="manual", product_name="No URL Gadget")
    res = svc.start_contact_intelligence_from_product(
        db, p.id, discovery_fn=fake_run_discovery)
    check("URL 未設定は status=error", res["status"] == "error")
    check("URL 未設定は contact_discovery_id なし",
          res["contact_discovery_id"] is None)
    check("URL 未設定はメッセージあり", bool(res["message"]))
    # 連携は作られない
    reloaded = svc.get(db, p.id)
    check("URL 未設定では contact_discovery_id を更新しない",
          reloaded.contact_discovery_id is None)
    db.close()


def test_missing_product_returns_none():
    print("test_missing_product_returns_none")
    db = SessionLocal()
    res = svc.start_contact_intelligence_from_product(
        db, 999999, discovery_fn=fake_run_discovery)
    check("存在しない商品は None（router で 404）", res is None)
    db.close()


def test_updates_contact_discovery_id():
    print("test_updates_contact_discovery_id")
    db = SessionLocal()
    p = _mk(db, source_platform="kickstarter",
            source_url="https://kck.st/ci-update",
            official_website_url="https://maker-update.example",
            product_name="Update Gadget", creator_name="U Maker")
    res = svc.start_contact_intelligence_from_product(
        db, p.id, discovery_fn=fake_run_discovery)
    reloaded = svc.get(db, p.id)
    check("discovered_products.contact_discovery_id が更新される",
          reloaded.contact_discovery_id == res["contact_discovery_id"])
    # 作成された ContactDiscovery が存在し、Project に紐づく
    row = db.get(ContactDiscovery, res["contact_discovery_id"])
    check("ContactDiscovery 行が作成される", row is not None)
    check("maker_name(company) は creator_name を優先",
          _project_maker_name(db, row.project_id) == "U Maker")
    db.close()


def _project_maker_name(db, project_id):
    from app.models.project import Project
    proj = db.get(Project, project_id)
    return proj.maker_name if proj else None


def test_existing_link_not_recreated():
    print("test_existing_link_not_recreated")
    db = SessionLocal()
    p = _mk(db, source_platform="kickstarter",
            source_url="https://kck.st/ci-existing",
            official_website_url="https://maker-existing.example",
            product_name="Existing Gadget")
    first = svc.start_contact_intelligence_from_product(
        db, p.id, discovery_fn=fake_run_discovery)
    calls_before = len(_calls)
    second = svc.start_contact_intelligence_from_product(
        db, p.id, discovery_fn=fake_run_discovery)
    check("2 回目は status=existing", second["status"] == "existing")
    check("2 回目は同じ contact_discovery_id",
          second["contact_discovery_id"] == first["contact_discovery_id"])
    check("2 回目は run_discovery を呼ばない（再作成しない）",
          len(_calls) == calls_before)
    db.close()


def test_api_endpoint():
    print("test_api_endpoint")
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        import app.services.contact_discovery_service as cds
    except ModuleNotFoundError as exc:
        print(f"  skip- API テストをスキップ（{exc}）")
        return

    # 実ネットワークの run_discovery をモックに差し替え（テスト中のみ）
    original = cds.run_discovery
    cds.run_discovery = fake_run_discovery
    try:
        client = TestClient(app)
        # 商品を作成
        r = client.post("/discovery/products", json={
            "source_platform": "kickstarter",
            "source_url": "https://kck.st/api-ci",
            "official_website_url": "https://maker-api.example",
            "product_name": "API CI Gadget",
            "creator_name": "API Maker",
        })
        check("商品作成 200", r.status_code == 200)
        pid = r.json()["id"]

        r2 = client.post(f"/discovery/products/{pid}/contact-intelligence")
        check("POST contact-intelligence 200", r2.status_code == 200)
        body = r2.json()
        check("used_url は official を優先",
              body["used_url"] == "https://maker-api.example")
        check("contact_discovery_id が返る",
              body["contact_discovery_id"] is not None)

        # 商品詳細に contact_discovery_id が反映される
        r3 = client.get(f"/discovery/products/{pid}")
        check("商品に contact_discovery_id が反映",
              r3.json()["contact_discovery_id"] == body["contact_discovery_id"])

        # 存在しない商品は 404
        r4 = client.post("/discovery/products/999999/contact-intelligence")
        check("存在しない商品は 404", r4.status_code == 404)

        # URL の無い商品は 400
        r5 = client.post("/discovery/products", json={
            "source_platform": "manual",
            "product_name": "API No URL",
        })
        pid2 = r5.json()["id"]
        r6 = client.post(f"/discovery/products/{pid2}/contact-intelligence")
        check("URL 未設定は 400", r6.status_code == 400)
    finally:
        cds.run_discovery = original


def main():
    test_official_url_preferred()
    test_source_url_fallback()
    test_no_url_is_safe_error()
    test_missing_product_returns_none()
    test_updates_contact_discovery_id()
    test_existing_link_not_recreated()
    test_api_endpoint()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
