"""実案件100件 official-site Ground Truth 入力・レビュー CLI（Phase C2）。

`official_site_real100_cases.csv`（Phase C1・gitignore 済み）の GT 列を 1 件ずつ安全に
入力・検証する。判定基準は README_real100.md 参照。

原則:
  - **PII を記録しない**（メール/電話/担当者/認証情報を GT に書かない・入力時に拒否）。
  - 入力のたびに **atomic write（tmp→os.replace）** + **`.bak` バックアップ**。
  - Ctrl+C 等の中断でも本体 CSV を破損させない（部分書き込みは tmp のみ）。
  - 既存 20 カラム互換を維持し、`reviewed_at`/`reviewer_version`/`needs_second_review` の
    3 列のみ後方互換で追加する。
  - DB 書き込み・大量ライブアクセスをしない（このスクリプト自体は外部通信しない）。

コア関数（normalize/validate/progress/atomic_write 等）は単体テスト可能な純粋関数。
対話入力は main() のみ。

実行例:
  python tests/contact_intel_eval/review_real100_ground_truth.py --status
  python tests/contact_intel_eval/review_real100_ground_truth.py --list-unreviewed
  python tests/contact_intel_eval/review_real100_ground_truth.py --sample-id R001
  python tests/contact_intel_eval/review_real100_ground_truth.py --validate-only
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.services import source_ownership as so  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE / "official_site_real100_cases.csv"
REVIEWER_VERSION = "c2-v1"

# Phase C1 の既存 20 カラム（順序維持）。
BASE_COLUMNS = [
    "sample_id", "project_id", "campaign_url", "source_type", "maker_name",
    "maker_url", "predicted_official_url", "predicted_registered_domain",
    "gt_official_url", "gt_registered_domain", "gt_status", "evidence_type",
    "evidence_url", "reviewer_note", "confidence", "result_class",
    "is_tp", "is_fp", "is_fn", "is_tn",
]
# Phase C2 で後方互換に追加する 3 列。
EXTRA_COLUMNS = ["reviewed_at", "reviewer_version", "needs_second_review"]
ALL_COLUMNS = BASE_COLUMNS + EXTRA_COLUMNS

ALLOWED_STATUS = {"confirmed", "none", "ambiguous", "unreachable", "excluded"}
ALLOWED_EVIDENCE = {
    "campaign_outbound_link", "legal_company_page", "product_brand_page",
    "official_social_link", "trademark_brand_record", "marketplace_profile",
    "manual_other", "",
}
ALLOWED_CONFIDENCE = {"high", "medium", "low", ""}

# URL 警告対象ホスト（採用時に警告。拒否ではない）。
_PLATFORM_HOSTS = so.CROWDFUNDING_PLATFORMS
_MARKETPLACE_HOSTS = so.RETAILERS
_SNS_HOSTS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "youtu.be", "tiktok.com", "pinterest.com", "linkedin.com", "reddit.com",
    "threads.net", "weibo.com",
}
_LINKTREE_HOSTS = {
    "linktr.ee", "lnk.bio", "linkin.bio", "campsite.bio", "beacons.ai",
    "taplink.cc", "solo.to", "linkpop.com", "lit.link", "bio.link", "potofu.me",
}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\-\s().]{7,}\d)(?!\d)")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|bearer|password|cookie|authorization)\s*[:=]"
    r"|sk_live_|ghp_|xox[baprs]-|AKIA[0-9A-Z]{16}")


# ---------------- URL 正規化・判定（純粋関数） ----------------
def normalize_official_url(url: str) -> str:
    """scheme 補完（https://）とトリムのみ。空はそのまま空。"""
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", u):
        u = "https://" + u
    return u


def registered_domain(url: str) -> str:
    """URL から registered domain（eTLD+1・二段 TLD 考慮）を返す。"""
    return so.registrable_domain(normalize_official_url(url)) if url else ""


def url_reject_reason(url: str) -> str | None:
    """公式 URL として受理できない場合の拒否理由を返す（受理可なら None）。"""
    raw = (url or "").strip()
    if not raw:
        return None
    if _EMAIL_RE.fullmatch(raw) or (("@" in raw) and "://" not in raw):
        return "email_not_url"
    u = normalize_official_url(raw)
    pr = urlparse(u)
    if pr.scheme not in ("http", "https"):
        return f"invalid_scheme:{pr.scheme}"
    host = (pr.netloc or "").split(":")[0].lower()
    if not host:
        return "invalid_host"
    if host == "localhost" or host.endswith(".local"):
        return "localhost"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_reserved:
            return "private_ip"
        return None  # 公開 IP は稀だが URL としては許容
    except ValueError:
        pass
    if "." not in host:
        return "invalid_host"
    return None


def url_warnings(url: str) -> list[str]:
    """公式 URL として不自然なホスト（platform/marketplace/SNS/Linktree）への警告。"""
    if not url:
        return []
    reg = so.registrable_domain(normalize_official_url(url))
    host = so.host_of(normalize_official_url(url))
    out: list[str] = []
    if reg in _PLATFORM_HOSTS:
        out.append("platform_url")
    if reg in _MARKETPLACE_HOSTS:
        out.append("marketplace_url")
    if reg in _SNS_HOSTS or any(host == h or host.endswith("." + h) for h in _SNS_HOSTS):
        out.append("sns_url")
    if reg in _LINKTREE_HOSTS:
        out.append("linktree_url")
    if reg in so.CROWDFUNDING_MARKETING or reg in so.KNOWN_AGENCIES:
        out.append("marketing_or_agency_url")
    return out


def contains_pii(text: str) -> list[str]:
    """PII/機密の混入種別を返す（reviewer_note/evidence_url 等の検査用）。"""
    t = text or ""
    found: list[str] = []
    if _EMAIL_RE.search(t):
        found.append("email")
    # 電話（URL 断片を除く）
    if _PHONE_RE.search(re.sub(r"https?://\S+", " ", t)):
        found.append("phone")
    if _SECRET_RE.search(t):
        found.append("secret")
    return found


# ---------------- 行バリデーション ----------------
def validate_row(row: dict) -> list[str]:
    """1 件の GT 入力の妥当性エラー一覧（空なら OK）。未レビュー(status 空)は検査しない。"""
    errs: list[str] = []
    status = (row.get("gt_status") or "").strip()
    if not status:
        return errs  # 未レビューは対象外
    if status not in ALLOWED_STATUS:
        errs.append(f"invalid gt_status:{status}")
    ev = (row.get("evidence_type") or "").strip()
    if ev not in ALLOWED_EVIDENCE:
        errs.append(f"invalid evidence_type:{ev}")
    conf = (row.get("confidence") or "").strip()
    if conf not in ALLOWED_CONFIDENCE:
        errs.append(f"invalid confidence:{conf}")
    url = (row.get("gt_official_url") or "").strip()
    reg = (row.get("gt_registered_domain") or "").strip()
    note = (row.get("reviewer_note") or "").strip()

    if status == "confirmed":
        if not url:
            errs.append("confirmed requires gt_official_url")
        if not reg:
            errs.append("confirmed requires gt_registered_domain")
        if not ev:
            errs.append("confirmed requires evidence_type")
        if not (row.get("evidence_url") or "").strip():
            errs.append("confirmed requires evidence_url")
        if not note:
            errs.append("confirmed requires reviewer_note")
        if not conf:
            errs.append("confirmed requires confidence")
        # registered domain 整合性
        if url and reg and registered_domain(url) != reg:
            errs.append(f"gt_registered_domain mismatch (expected {registered_domain(url)})")
    if status in ("none", "excluded"):
        if url:
            errs.append(f"{status} must have empty gt_official_url")
        if reg:
            errs.append(f"{status} must have empty gt_registered_domain")
        if not note:
            errs.append(f"{status} requires reviewer_note")
    if status in ("ambiguous", "unreachable"):
        if not note:
            errs.append(f"{status} requires reviewer_note")
    # URL 拒否・PII
    if url:
        rej = url_reject_reason(url)
        if rej:
            errs.append(f"gt_official_url rejected:{rej}")
    for col in ("gt_official_url", "evidence_url", "reviewer_note"):
        pii = contains_pii(row.get(col) or "")
        if pii:
            errs.append(f"PII in {col}:{','.join(pii)}")
    return errs


def validate_dataset(rows: list[dict]) -> dict:
    """データセット全体の検証結果（件数・重複・許可値・PII・未レビュー数等）。"""
    report: dict = {"errors": [], "row_errors": {}}
    if len(rows) != 100:
        report["errors"].append(f"expected 100 rows, got {len(rows)}")
    for key in ("sample_id", "project_id"):
        vals = [r.get(key) for r in rows]
        dup = len(vals) - len(set(vals))
        if dup:
            report["errors"].append(f"duplicate {key}: {dup}")
    makers = [(r.get("maker_name") or "").strip().lower() for r in rows
              if (r.get("maker_name") or "").strip() and r.get("maker_name") != "[redacted-email]"]
    if len(makers) - len(set(makers)):
        report["errors"].append(f"duplicate maker_name: {len(makers)-len(set(makers))}")
    for r in rows:
        e = validate_row(r)
        if e:
            report["row_errors"][r.get("sample_id")] = e
    report["reviewed"] = sum(1 for r in rows if (r.get("gt_status") or "").strip())
    report["unreviewed"] = len(rows) - report["reviewed"]
    report["needs_second_review"] = sum(
        1 for r in rows if str(r.get("needs_second_review") or "").strip() in ("1", "true", "True"))
    report["ok"] = not report["errors"] and not report["row_errors"]
    return report


# ---------------- 進捗集計 ----------------
def progress(rows: list[dict]) -> dict:
    total = len(rows)
    reviewed = [r for r in rows if (r.get("gt_status") or "").strip()]
    by_status: dict[str, int] = {}
    by_conf: dict[str, int] = {}
    by_source_total: dict[str, int] = {}
    by_source_done: dict[str, int] = {}
    for r in rows:
        st = r.get("source_type") or "?"
        by_source_total[st] = by_source_total.get(st, 0) + 1
    for r in reviewed:
        s = (r.get("gt_status") or "").strip()
        by_status[s] = by_status.get(s, 0) + 1
        c = (r.get("confidence") or "").strip()
        if c:
            by_conf[c] = by_conf.get(c, 0) + 1
        st = r.get("source_type") or "?"
        by_source_done[st] = by_source_done.get(st, 0) + 1
    return {
        "total": total, "reviewed": len(reviewed), "unreviewed": total - len(reviewed),
        "by_status": by_status, "by_confidence": by_conf,
        "by_source_total": by_source_total, "by_source_done": by_source_done,
    }


def list_unreviewed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not (r.get("gt_status") or "").strip()]


# ---------------- CSV I/O（互換アップグレード + atomic write + backup） ----------------
def load_rows(path: Path) -> tuple[list[dict], list[str]]:
    """CSV を読み、必要なら EXTRA 列を後方互換で補完して返す。"""
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    # EXTRA 列を後方互換で追加（値は空）
    for col in EXTRA_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)
            for r in rows:
                r.setdefault(col, "")
    # 未知列があってもそのまま保持（末尾）。既存順序は壊さない。
    for r in rows:
        for col in fieldnames:
            r.setdefault(col, "")
    return rows, fieldnames


def atomic_write(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """tmp に全書き込み→fsync→os.replace で置換。書込前に .bak を作る（破損防止）。"""
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fieldnames})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------- 対話入力 ----------------
def _apply_input(row: dict, values: dict) -> tuple[dict, list[str]]:
    """入力値を row に適用（自動正規化つき）し、(更新row, 警告) を返す。検証は呼出側。"""
    warnings: list[str] = []
    status = (values.get("gt_status") or "").strip().lower()
    row["gt_status"] = status
    url = (values.get("gt_official_url") or "").strip()
    if status == "confirmed":
        url = normalize_official_url(url)
        if url:
            warnings.extend(url_warnings(url))
        row["gt_official_url"] = url
        row["gt_registered_domain"] = registered_domain(url)
    else:
        # none/ambiguous/unreachable/excluded は URL 空にできる
        if url and status in ("none", "excluded"):
            warnings.append(f"{status} with URL provided (will be cleared)")
            url = ""
        url = normalize_official_url(url) if url else ""
        row["gt_official_url"] = url
        row["gt_registered_domain"] = registered_domain(url) if url else ""
    row["evidence_type"] = (values.get("evidence_type") or "").strip()
    row["evidence_url"] = (values.get("evidence_url") or "").strip()
    row["reviewer_note"] = (values.get("reviewer_note") or "").strip()
    row["confidence"] = (values.get("confidence") or "").strip().lower()
    row["reviewed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row["reviewer_version"] = REVIEWER_VERSION
    row["needs_second_review"] = "1" if row["confidence"] == "low" else "0"
    return row, warnings


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        v = input(f"  {label}{suffix}: ").strip()
    except EOFError:
        return default
    return v or default


def review_one(rows: list[dict], fieldnames: list[str], path: Path, sample_id: str) -> int:
    idx = next((i for i, r in enumerate(rows) if r.get("sample_id") == sample_id), None)
    if idx is None:
        print(f"sample_id not found: {sample_id}")
        return 1
    r = rows[idx]
    print(f"\n=== {r['sample_id']} (project {r['project_id']}) ===")
    print(f"  maker_name  : {r.get('maker_name')}")
    print(f"  source_type : {r.get('source_type')}")
    print(f"  campaign_url: {r.get('campaign_url')}")
    print(f"  maker_url   : {r.get('maker_url')}")
    print(f"  baseline predicted official: {r.get('predicted_official_url')}")
    if (r.get("gt_status") or "").strip():
        print(f"  [既存GT] status={r.get('gt_status')} url={r.get('gt_official_url')} "
              f"conf={r.get('confidence')}")
    print(f"  許可 status: {sorted(ALLOWED_STATUS)}")
    values = {
        "gt_status": _prompt("gt_status", r.get("gt_status", "")),
        "gt_official_url": _prompt("gt_official_url", r.get("gt_official_url", "")),
        "evidence_type": _prompt(f"evidence_type {sorted(ALLOWED_EVIDENCE-{''})}",
                                 r.get("evidence_type", "")),
        "evidence_url": _prompt("evidence_url", r.get("evidence_url", "")),
        "reviewer_note": _prompt("reviewer_note", r.get("reviewer_note", "")),
        "confidence": _prompt("confidence high/medium/low", r.get("confidence", "")),
    }
    if not values["gt_status"]:
        print("  スキップ（status 未入力・保存しない）")
        return 0
    updated, warns = _apply_input(dict(r), values)
    for w in warns:
        print(f"  ⚠ warning: {w}")
    errs = validate_row(updated)
    if errs:
        print("  ✗ 検証エラー（保存しない）:")
        for e in errs:
            print(f"     - {e}")
        return 1
    rows[idx] = updated
    atomic_write(path, rows, fieldnames)
    print(f"  ✓ 保存（atomic + .bak）: status={updated['gt_status']} "
          f"domain={updated.get('gt_registered_domain')}")
    return 0


def print_progress(rows: list[dict]) -> None:
    p = progress(rows)
    print(f"Total: {p['total']}")
    print(f"Reviewed: {p['reviewed']}")
    print(f"Unreviewed: {p['unreviewed']}\n")
    for s in ("confirmed", "none", "ambiguous", "unreachable", "excluded"):
        print(f"{s}: {p['by_status'].get(s, 0)}")
    print()
    for c in ("high", "medium", "low"):
        print(f"{c}: {p['by_confidence'].get(c, 0)}")
    print("\nsource:")
    for st in sorted(p["by_source_total"]):
        print(f"{st} {p['by_source_done'].get(st, 0)}/{p['by_source_total'][st]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="real100 official-site Ground Truth review")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--sample-id")
    ap.add_argument("--status", action="store_true", help="進捗表示")
    ap.add_argument("--list-unreviewed", action="store_true")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.csv)
    if not path.exists():
        print(f"CSV not found: {path}（Phase C1 の build_real100_sample.py で生成してください）")
        return 2
    rows, fieldnames = load_rows(path)

    if args.status:
        print_progress(rows)
        return 0
    if args.list_unreviewed:
        un = list_unreviewed(rows)
        print(f"未レビュー {len(un)} 件:")
        for r in un:
            print(f"  {r['sample_id']} p{r['project_id']} {r.get('source_type')} "
                  f"{r.get('maker_name')}")
        return 0
    if args.validate_only:
        rep = validate_dataset(rows)
        print(f"reviewed={rep['reviewed']} unreviewed={rep['unreviewed']} "
              f"needs_second_review={rep['needs_second_review']}")
        if rep["errors"]:
            print("dataset errors:")
            for e in rep["errors"]:
                print(f"  - {e}")
        if rep["row_errors"]:
            print("row errors:")
            for sid, e in rep["row_errors"].items():
                print(f"  {sid}: {e}")
        print("OK" if rep["ok"] else "NG")
        return 0 if rep["ok"] else 1
    if args.sample_id:
        try:
            return review_one(rows, fieldnames, path, args.sample_id)
        except KeyboardInterrupt:
            print("\n中断（本体 CSV は未変更・atomic write 前）")
            return 130

    # 引数なし: 未レビューを順に対話レビュー
    try:
        for r in list_unreviewed(rows):
            rc = review_one(rows, fieldnames, path, r["sample_id"])
            if rc not in (0, 1):
                return rc
    except KeyboardInterrupt:
        print("\n中断（保存済み分は破損なし）")
        return 130
    print_progress(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
