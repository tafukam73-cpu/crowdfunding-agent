"""real100 official-site Ground Truth レビュー基盤（Phase C2）の単体テスト。

review_real100_ground_truth のコア関数（正規化/検証/atomic write/backup/進捗/PII）を
機能別に検証する。外部通信・DB 非接続。pytest 非依存で単体実行できる。

実行: docker exec cfagent-backend python tests/test_real100_ground_truth_review.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests" / "contact_intel_eval"))

import review_real100_ground_truth as gt  # noqa: E402

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  - {name}")
    else:
        _f += 1
        print(f"  FAIL- {name}")


def _row(**kw):
    base = {c: "" for c in gt.ALL_COLUMNS}
    base.update(kw)
    return base


# ---- URL 正規化 ----
def test_registered_domain_and_https():
    print("test_registered_domain_and_https")
    check("www 正規化", gt.registered_domain("https://www.example.com/about") == "example.com")
    check("二段TLD", gt.registered_domain("https://shop.unionchen.com.tw/x") == "unionchen.com.tw")
    check("https 補完", gt.normalize_official_url("example.com") == "https://example.com")
    check("scheme 保持", gt.normalize_official_url("http://x.com") == "http://x.com")


# ---- URL 拒否 ----
def test_url_reject():
    print("test_url_reject")
    check("email 拒否", gt.url_reject_reason("info@example.com") == "email_not_url")
    check("private IP 拒否", gt.url_reject_reason("http://192.168.0.1") == "private_ip")
    check("loopback 拒否", gt.url_reject_reason("http://127.0.0.1") == "private_ip")
    check("localhost 拒否", gt.url_reject_reason("http://localhost:8000") == "localhost")
    check("file scheme 拒否", gt.url_reject_reason("file:///etc/passwd") == "invalid_scheme:file")
    check("正常 URL は None", gt.url_reject_reason("https://sharge.com") is None)


# ---- URL 警告 ----
def test_url_warnings():
    print("test_url_warnings")
    check("platform 警告", "platform_url" in gt.url_warnings("https://www.kickstarter.com/x"))
    check("sns 警告", "sns_url" in gt.url_warnings("https://instagram.com/brand"))
    check("marketplace 警告", "marketplace_url" in gt.url_warnings("https://www.amazon.com/dp/x"))
    check("linktree 警告", "linktree_url" in gt.url_warnings("https://linktr.ee/brand"))
    check("agency 警告", "marketing_or_agency_url" in gt.url_warnings("https://ideafound.com"))
    check("正規 maker は警告なし", gt.url_warnings("https://sharge.com") == [])


# ---- PII ----
def test_pii_detection():
    print("test_pii_detection")
    check("email PII", "email" in gt.contains_pii("reach a@b.com"))
    check("phone PII", "phone" in gt.contains_pii("call +1 555-123-4567"))
    check("secret PII", "secret" in gt.contains_pii("api_key: sk_live_abc"))
    check("URL は phone 誤検出しない", "phone" not in gt.contains_pii("https://x.com/12345678"))
    check("通常 note は PII なし", gt.contains_pii("brand official site confirmed") == [])


# ---- 行検証 ----
def test_validate_confirmed():
    print("test_validate_confirmed")
    ok = _row(gt_status="confirmed", gt_official_url="https://sharge.com",
              gt_registered_domain="sharge.com", evidence_type="product_brand_page",
              evidence_url="https://sharge.com/about", reviewer_note="brand", confidence="high")
    check("confirmed 完全は OK", gt.validate_row(ok) == [])
    miss = _row(gt_status="confirmed")
    check("confirmed 欠落は複数エラー", len(gt.validate_row(miss)) >= 5)
    mism = _row(gt_status="confirmed", gt_official_url="https://sharge.com",
                gt_registered_domain="wrong.com", evidence_type="product_brand_page",
                evidence_url="https://sharge.com", reviewer_note="x", confidence="high")
    check("registered domain 不一致を検出",
          any("mismatch" in e for e in gt.validate_row(mism)))


def test_validate_none_and_allowed():
    print("test_validate_none_and_allowed")
    none_ok = _row(gt_status="none", reviewer_note="platform only")
    check("none URL 空は OK", gt.validate_row(none_ok) == [])
    none_url = _row(gt_status="none", gt_official_url="https://x.com",
                    gt_registered_domain="x.com", reviewer_note="n")
    check("none で URL ありはエラー",
          any("empty gt_official_url" in e for e in gt.validate_row(none_url)))
    bad = _row(gt_status="banana", reviewer_note="n")
    check("不正 status を検出", any("invalid gt_status" in e for e in gt.validate_row(bad)))
    bad_ev = _row(gt_status="ambiguous", evidence_type="weird", reviewer_note="n")
    check("不正 evidence_type を検出", any("invalid evidence_type" in e for e in gt.validate_row(bad_ev)))
    bad_cf = _row(gt_status="ambiguous", confidence="maybe", reviewer_note="n")
    check("不正 confidence を検出", any("invalid confidence" in e for e in gt.validate_row(bad_cf)))
    unrev = _row()  # status 空 = 未レビュー
    check("未レビューは検証対象外", gt.validate_row(unrev) == [])


def test_validate_pii_in_row():
    print("test_validate_pii_in_row")
    r = _row(gt_status="ambiguous", reviewer_note="contact john@maker.com")
    check("note の email をエラー化", any("PII in reviewer_note" in e for e in gt.validate_row(r)))


# ---- apply_input（自動処理）----
def test_apply_input_auto():
    print("test_apply_input_auto")
    r, warns = gt._apply_input(_row(sample_id="R1"), {
        "gt_status": "confirmed", "gt_official_url": "sharge.com",
        "evidence_type": "product_brand_page", "evidence_url": "https://sharge.com",
        "reviewer_note": "brand", "confidence": "low"})
    check("https 補完+domain 自動", r["gt_official_url"] == "https://sharge.com"
          and r["gt_registered_domain"] == "sharge.com")
    check("low は needs_second_review=1", r["needs_second_review"] == "1")
    check("reviewer_version 記録", r["reviewer_version"] == gt.REVIEWER_VERSION)
    r2, warns2 = gt._apply_input(_row(sample_id="R2"), {
        "gt_status": "none", "gt_official_url": "https://x.com", "reviewer_note": "platform"})
    check("none は URL クリア", r2["gt_official_url"] == "" and r2["gt_registered_domain"] == "")
    r3, warns3 = gt._apply_input(_row(sample_id="R3"), {
        "gt_status": "confirmed", "gt_official_url": "https://www.kickstarter.com/x",
        "evidence_type": "campaign_outbound_link", "evidence_url": "https://x", "reviewer_note": "n",
        "confidence": "high"})
    check("platform URL は警告", "platform_url" in warns3)


# ---- CSV 互換 / atomic write / backup ----
def _sample_csv(tmp: Path):
    rows = []
    for i in range(1, 4):
        r = {c: "" for c in gt.BASE_COLUMNS}
        r["sample_id"] = f"R{i:03d}"
        r["project_id"] = str(i)
        r["source_type"] = "kickstarter"
        r["maker_name"] = f"Maker{i}"
        rows.append(r)
    import csv
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=gt.BASE_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_load_upgrades_columns():
    print("test_load_upgrades_columns")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.csv"
        _sample_csv(p)  # 20 カラム（Phase C1 互換）
        rows, fn = gt.load_rows(p)
        check("EXTRA 列が後方互換で追加", all(c in fn for c in gt.EXTRA_COLUMNS))
        check("既存 20 カラム順序維持", fn[:20] == gt.BASE_COLUMNS)
        check("3 行読み込み", len(rows) == 3)


def test_atomic_write_and_backup():
    print("test_atomic_write_and_backup")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.csv"
        _sample_csv(p)
        rows, fn = gt.load_rows(p)
        rows[0]["gt_status"] = "confirmed"
        rows[0]["gt_official_url"] = "https://sharge.com"
        gt.atomic_write(p, rows, fn)
        check(".bak 作成", (p.with_suffix(".csv.bak")).exists())
        rows2, fn2 = gt.load_rows(p)
        check("保存内容が読み戻せる", rows2[0]["gt_status"] == "confirmed")
        check("行数維持", len(rows2) == 3)
        check("tmp file 残らない", not any(x.name.startswith(".tmp_") for x in Path(d).iterdir()))


# ---- 進捗 / 未レビュー ----
def test_progress_and_unreviewed():
    print("test_progress_and_unreviewed")
    rows = [_row(sample_id="A", source_type="kickstarter", gt_status="confirmed", confidence="high"),
            _row(sample_id="B", source_type="wadiz", gt_status="none"),
            _row(sample_id="C", source_type="wadiz")]
    p = gt.progress(rows)
    check("total/reviewed/unreviewed", (p["total"], p["reviewed"], p["unreviewed"]) == (3, 2, 1))
    check("status 集計", p["by_status"].get("confirmed") == 1 and p["by_status"].get("none") == 1)
    check("source 進捗", p["by_source_done"].get("wadiz") == 1 and p["by_source_total"].get("wadiz") == 2)
    check("未レビュー抽出", [r["sample_id"] for r in gt.list_unreviewed(rows)] == ["C"])


def test_validate_dataset():
    print("test_validate_dataset")
    rows = [_row(sample_id=f"R{i:03d}", project_id=str(i), maker_name=f"M{i}") for i in range(1, 101)]
    rep = gt.validate_dataset(rows)
    check("100 件 OK", not any("expected 100" in e for e in rep["errors"]))
    check("未レビュー100", rep["unreviewed"] == 100)
    rows_dup = rows[:99] + [_row(sample_id="R001", project_id="1", maker_name="M1")]
    rep2 = gt.validate_dataset(rows_dup)
    check("重複 sample_id 検出", any("duplicate sample_id" in e for e in rep2["errors"]))


def main():
    test_registered_domain_and_https()
    test_url_reject()
    test_url_warnings()
    test_pii_detection()
    test_validate_confirmed()
    test_validate_none_and_allowed()
    test_validate_pii_in_row()
    test_apply_input_auto()
    test_load_upgrades_columns()
    test_atomic_write_and_backup()
    test_progress_and_unreviewed()
    test_validate_dataset()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
