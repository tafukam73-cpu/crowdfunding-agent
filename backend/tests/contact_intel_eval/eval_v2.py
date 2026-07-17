"""Contact Intelligence 評価 v2（信頼できる ground truth に基づく entity+案件単位測定）。

入力:
  - ground truth (schema B) : gold_ground_truth.json（人手検証・build_ground_truth.py が生成）
  - prediction snapshot     : gold_frozen_24.json の saved_*（現行パイプライン出力）
prediction と ground truth を project_id で結合して測定する。ネットワーク非接続。

原則:
  - blocked / unresolved は precision/recall の分母から除外し、別途「取得失敗」で集計する。
  - partially_verified は別バケットで報告（strict の分母に入れない）。
  - plausible_unconfirmed_emails（ドメイン一致だが未掲載）は TP/FP どちらにも数えない。
  - 分母 N を必ず表示する。N 不足の指標は率を出さず「N 不足」と記す。

Phase 1 は主に FP 除去なので、email/SNS は BEFORE(saved) と AFTER(新フィルタ適用) を比較する。

実行: docker exec cfagent-backend python tests/contact_intel_eval/eval_v2.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402
from app.services import source_ownership as so  # noqa: E402

HERE = Path(__file__).resolve().parent
GT = {c["project_id"]: c
      for c in json.loads((HERE / "gold_ground_truth.json").read_text(encoding="utf-8"))}
PRED = {c["project_id"]: c
        for c in json.loads((HERE / "gold_frozen_24.json").read_text(encoding="utf-8"))}
IDS = sorted(GT)

STRICT = {"verified"}          # precision/recall の分母
PARTIAL = {"partially_verified"}
EXCLUDED = {"blocked", "unresolved"}  # 分母から除外（取得失敗として別集計）


def nset(xs):
    return {str(x).strip().lower() for x in (xs or []) if str(x).strip()}


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else None
    r = tp / (tp + fn) if (tp + fn) else None
    f = (2 * p * r / (p + r)) if (p and r) else None
    return p, r, f


def pct(x):
    return "N/A" if x is None else f"{x:.1%}"


def norm_domain(url):
    return so.registrable_domain(url)


def norm_url(url):
    u = (url or "").strip().lower()
    if "://" in u:
        pr = urlparse(u)
        path = pr.path.rstrip("/")
        return pr.netloc.replace("www.", "") + path
    return u.rstrip("/")


def site_email_domain(pid):
    return cds.source_site_email_domain(PRED[pid].get("source_site"))


# ---------- email predictors (Phase1 before/after) ----------
def pred_emails_before(pid):
    return nset(PRED[pid].get("saved_emails"))


def pred_emails_after_direct(pid):
    sd = site_email_domain(pid)
    return {e for e in nset(PRED[pid].get("saved_emails"))
            if not cds.email_exclusion_reason(e, sd)}


def pred_emails_after_fallback(pid):
    sd = site_email_domain(pid)
    out = set()
    for e in nset(PRED[pid].get("saved_emails")):
        if cds.email_exclusion_reason(e, sd) and so.classify_email(e)["accepted_as_fallback_contact"]:
            out.add(e)
    return out


# =================== EMAIL ===================
def eval_email(pred_of, label):
    tp = fp = fn = 0
    fp_items, fn_items = [], []
    n_strict = 0
    for pid in IDS:
        if GT[pid]["verification_status"] not in STRICT:
            continue
        n_strict += 1
        exp = nset(GT[pid]["expected_direct_emails"])
        plausible = nset(GT[pid]["plausible_unconfirmed_emails"])
        pred = pred_of(pid) - plausible  # plausible は採点しない
        tps = exp & pred
        fps = pred - exp
        fns = exp - pred_of(pid)  # 予測に無い verified 期待メール
        tp += len(tps); fp += len(fps); fn += len(fns)
        fp_items += [(pid, e) for e in sorted(fps)]
        fn_items += [(pid, e) for e in sorted(fns)]
    p, r, f = prf(tp, fp, fn)
    print(f"  [{label}] N(verified email cases)={n_strict}  "
          f"TP={tp} FP={fp} FN={fn}  P={pct(p)} R={pct(r)} F1={pct(f)}")
    return dict(tp=tp, fp=fp, fn=fn, fp_items=fp_items, fn_items=fn_items, n=n_strict)


def email_class_counts(pred_of, cls):
    return sum(1 for pid in IDS for e in pred_of(pid)
              if so.classify_domain(e).ownership_class == cls)


def eval_email_case_level(pred_direct, pred_fallback):
    print("  案件単位:")
    verified = [pid for pid in IDS if GT[pid]["verification_status"] in STRICT]
    got_direct = sum(1 for pid in verified
                     if nset(GT[pid]["expected_direct_emails"]) & pred_direct(pid))
    with_fp = 0
    for pid in verified:
        exp = nset(GT[pid]["expected_direct_emails"])
        plausible = nset(GT[pid]["plausible_unconfirmed_emails"])
        if (pred_direct(pid) - plausible) - exp:
            with_fp += 1
    # fallback は unresolved 含む全案件で「期待 fallback を取得」を見る（別指標）
    fb_cases = [pid for pid in IDS if GT[pid]["expected_fallback_emails"]]
    got_fb = sum(1 for pid in fb_cases
                 if nset(GT[pid]["expected_fallback_emails"]) & pred_fallback(pid))
    plat = sum(1 for pid in IDS
               if any(so.classify_domain(e).ownership_class == "crowdfunding_platform"
                      for e in pred_direct(pid)))
    agency = sum(1 for pid in IDS
                 if any(so.classify_domain(e).ownership_class == "agency"
                        for e in pred_direct(pid)))
    short = sum(1 for pid in IDS
                if any(so.classify_domain(e).ownership_class == "url_shortener"
                       for e in pred_direct(pid)))
    print(f"    正しい direct maker email を1件以上取得: {got_direct}/{len(verified)} 案件")
    print(f"    誤メール(FP)を含む案件            : {with_fp}/{len(verified)} 案件")
    print(f"    正しい fallback email を1件以上取得: {got_fb}/{len(fb_cases)} 案件(fallback 定義案件)")
    print(f"    platform メールを direct 誤採用   : {plat} 案件")
    print(f"    agency を direct maker 誤採用     : {agency} 案件")
    print(f"    shortener メール誤採用            : {short} 案件")


# =================== OFFICIAL SITE ===================
def eval_official():
    tp = fp = fn = 0
    n = 0
    for pid in IDS:
        g = GT[pid]
        if g["verification_status"] not in STRICT or not g["expected_official_site"]:
            continue
        n += 1
        exp = norm_domain(g["expected_official_site"])
        pred = {norm_domain(u) for u in (PRED[pid].get("saved_official_sites") or [])}
        if exp in pred:
            tp += 1
            fp += len(pred - {exp})
        else:
            fn += 1
            fp += len(pred)
    p, r, f = prf(tp, fp, fn)
    print(f"  N(expected_official ありの verified 案件)={n}  "
          f"TP={tp} FP={fp} FN={fn}  P={pct(p)} R={pct(r)} F1={pct(f)}")


# =================== FORMS ===================
_NON_MAKER_FORM = ("zeczec.com", "kickstarter.com", "indiegogo.com", "kickbooster",
                   "m.me/", "wa.me/", "/login", "/signin", "newsletter", "/search")


def is_maker_form(url, official):
    u = (url or "").lower()
    if any(x in u for x in _NON_MAKER_FORM):
        return False
    if official and norm_domain(url) == norm_domain(official):
        return True
    return norm_domain(url) not in so.CROWDFUNDING_PLATFORMS


def eval_forms():
    tp = fp = fn = 0
    n = 0
    for pid in IDS:
        g = GT[pid]
        if g["verification_status"] not in STRICT:
            continue
        exp = {norm_url(u) for u in g["expected_forms"]}
        if not exp and not (PRED[pid].get("saved_forms")):
            continue
        n += 1
        official = g["expected_official_site"]
        pred = {norm_url(u) for u in (PRED[pid].get("saved_forms") or [])}
        pred_maker = {u for u in pred if is_maker_form(u, official)}
        pred_nonmaker = pred - pred_maker
        tps = exp & pred_maker
        tp += len(tps)
        fp += len(pred_maker - exp) + len(pred_nonmaker)  # 非maker form も FP
        fn += len(exp - pred_maker)
    p, r, f = prf(tp, fp, fn)
    print(f"  N(verified で form 期待 or 予測ありの案件)={n}  "
          f"TP={tp} FP={fp} FN={fn}  P={pct(p)} R={pct(r)} F1={pct(f)}")
    print("  注: platform/marketing/messenger/login/newsletter/search フォームは FP 扱い")


# =================== SNS ===================
def sns_before(pid):
    return {norm_url(s) for s in (PRED[pid].get("saved_socials") or [])}


def sns_after(pid):
    return {norm_url(s) for s in (PRED[pid].get("saved_socials") or [])
            if not so.is_platform_self_social(s)}


def eval_sns(pred_of, label):
    tp = fp = fn = 0
    n = 0
    plat_self = 0
    for pid in IDS:
        g = GT[pid]
        if g["verification_status"] not in STRICT:
            continue
        exp = {norm_url(s) for s in g["expected_socials"]}
        if not exp and not (PRED[pid].get("saved_socials")):
            continue
        n += 1
        pred = pred_of(pid)
        tp += len(exp & pred)
        fp += len(pred - exp)
        fn += len(exp - pred)
        plat_self += sum(1 for s in (PRED[pid].get("saved_socials") or [])
                         if so.is_platform_self_social(s)
                         and norm_url(s) in pred)
    p, r, f = prf(tp, fp, fn)
    print(f"  [{label}] N(verified で SNS 期待 or 予測ありの案件)={n}  "
          f"TP={tp} FP={fp} FN={fn}  P={pct(p)} R={pct(r)} F1={pct(f)}  "
          f"platform自己SNS採用={plat_self}")


# =================== PEOPLE ===================
def _names(xs):
    out = set()
    for p in (xs or []):
        if isinstance(p, (list, tuple)) and p:
            out.add(str(p[0]).strip().lower())
        elif p:
            out.add(str(p).strip().lower())
    return out


def eval_people():
    tp = fp = fn = 0
    n = 0
    for pid in IDS:
        g = GT[pid]
        if g["verification_status"] not in STRICT:
            continue
        exp = _names(g["expected_people"])
        pred = _names(PRED[pid].get("saved_people"))
        if not exp and not pred:
            continue
        n += 1
        tp += len(exp & pred)
        fp += len(pred - exp)
        fn += len(exp - pred)
    p, r, f = prf(tp, fp, fn)
    print(f"  N(verified で people 期待 or 予測ありの案件)={n}  "
          f"TP={tp} FP={fp} FN={fn}  P={pct(p)} R={pct(r)} F1={pct(f)}")


def main():
    by_status = {}
    for pid in IDS:
        s = GT[pid]["verification_status"]
        by_status[s] = by_status.get(s, 0) + 1
    print("=" * 72)
    print("Contact Intelligence 評価 v2  ※ground truth=人手検証 / prediction=saved_*")
    print("=" * 72)
    print(f"総案件 N={len(IDS)}  status 内訳: {by_status}")
    print(f"  strict(分母)={sorted(p for p in IDS if GT[p]['verification_status'] in STRICT)}")
    print(f"  partially_verified={sorted(p for p in IDS if GT[p]['verification_status'] in PARTIAL)}")
    print(f"  excluded(blocked/unresolved・分母外)="
          f"{sorted(p for p in IDS if GT[p]['verification_status'] in EXCLUDED)}")

    print("\n■ メール（direct maker email / entity）  Phase1 before→after")
    b = eval_email(pred_emails_before, "BEFORE saved")
    a = eval_email(pred_emails_after_direct, "AFTER  direct(新フィルタ)")
    lost = sorted(set(map(tuple, a["fn_items"])) - set(map(tuple, b["fn_items"])))
    removed = sorted(set(map(tuple, b["fp_items"])) - set(map(tuple, a["fp_items"])))
    print(f"    Phase1 FP除去: {b['fp']}→{a['fp']}（-{b['fp']-a['fp']}）  "
          f"新規FN: {lost if lost else 'なし'}")
    for pid, e in removed:
        print(f"      removed FP  p{pid}: {e}  ({so.classify_domain(e).ownership_class})")
    print("    誤採用クラス数 BEFORE→AFTER: "
          f"platform {email_class_counts(pred_emails_before,'crowdfunding_platform')}→"
          f"{email_class_counts(pred_emails_after_direct,'crowdfunding_platform')}, "
          f"agency {email_class_counts(pred_emails_before,'agency')}→"
          f"{email_class_counts(pred_emails_after_direct,'agency')}, "
          f"shortener {email_class_counts(pred_emails_before,'url_shortener')}→"
          f"{email_class_counts(pred_emails_after_direct,'url_shortener')}")
    eval_email_case_level(pred_emails_after_direct, pred_emails_after_fallback)

    print("\n■ 公式サイト（normalized domain / entity）")
    eval_official()

    print("\n■ フォーム（maker-owned real form / entity）")
    eval_forms()

    print("\n■ SNS（maker本人のみ TP / entity）  Phase1 before→after")
    eval_sns(sns_before, "BEFORE saved")
    eval_sns(sns_after, "AFTER  自己SNS除外")

    print("\n■ 人物（name 正規化 / entity）")
    eval_people()

    print("\n注: partially_verified(2) と blocked/unresolved(8) は上記 strict 分母から除外。")
    print("    plausible_unconfirmed_emails は TP/FP どちらにも数えていない。")


if __name__ == "__main__":
    main()
