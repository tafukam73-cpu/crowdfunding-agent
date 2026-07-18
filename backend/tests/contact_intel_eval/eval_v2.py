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

from app.ai.contact_hunter import looks_like_person_name  # noqa: E402
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
# チャネル単位（登録ドメイン × intent）で採点する。exact URL slug ではなく「maker の
# どの窓口に到達できるか」を測るのが営業上意味がある粒度。
def form_channel(url):
    return (norm_domain(url), cds._form_intent(url))


def forms_before(pid):
    return [u for u in (PRED[pid].get("saved_forms") or [])]


def forms_after(pid):
    official = GT[pid]["expected_official_site"]
    dom = so.registrable_domain(official) if official else None
    return cds.select_maker_forms(PRED[pid].get("saved_forms") or [], dom)


def eval_forms(pred_of, label):
    tp = fp = fn = 0
    n = 0
    for pid in IDS:
        g = GT[pid]
        if g["verification_status"] not in STRICT:
            continue
        pred_forms = pred_of(pid)
        if not g["expected_forms"] and not pred_forms:
            continue
        n += 1
        exp = {form_channel(u) for u in g["expected_forms"]}
        pred = {form_channel(u) for u in pred_forms}
        tp += len(exp & pred)
        fp += len(pred - exp)
        fn += len(exp - pred)
    p, r, f = prf(tp, fp, fn)
    print(f"  [{label}] N(verified で form 期待 or 予測ありの案件)={n}  "
          f"TP={tp} FP={fp} FN={fn}  P={pct(p)} R={pct(r)} F1={pct(f)}")


def form_supplemental(pred_of, label):
    """channel 採点は expected_forms 未列挙の影響を受けるため、曖昧さのない補助指標を出す:
    総 URL 数（dedup 効果）と 非maker所有フォーム数（第三者/ユーティリティ混入）。"""
    total = nonmaker = 0
    for pid in IDS:
        if GT[pid]["verification_status"] not in STRICT:
            continue
        for u in pred_of(pid):
            total += 1
            cls = so.classify_domain(u).ownership_class
            path = u.lower()
            util = any(x in path for x in ("/login", "/signin", "/register", "/search",
                                           "newsletter", "/cart", "/account"))
            if cls in ("crowdfunding_platform", "crowdfunding_marketing_service",
                       "url_shortener", "messenger", "retailer", "agency") or util \
                    or cds.is_platform_url(u):
                nonmaker += 1
    print(f"  [{label}] 総フォームURL数={total}  非maker所有(第三者/ユーティリティ)={nonmaker}")


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


def _pnorm(name):
    return " ".join(str(name or "").split()).strip().lower()


# 役職互換グループ（compatible role match 用。CEO≒代表 等。Creator は Founder と同一視しない）。
_ROLE_GROUPS = [
    {"ceo", "chief executive", "代表", "대표", "president", "managing director", "社長"},
    {"founder", "co-founder", "cofounder", "創業者", "创始人", "창업자", "owner"},
    {"sales", "sales director", "head of sales", "営業", "business development"},
    {"marketing", "marketing director", "cmo", "brand", "brand designer"},
    {"pr", "press", "media", "communications", "publicity", "広報"},
]


def _role_group(role):
    low = _pnorm(role)
    hits = set()
    for i, grp in enumerate(_ROLE_GROUPS):
        if any(k in low for k in grp):
            hits.add(i)
    return hits


def _role_relation(pred_role, exp_role):
    """exact / compatible / mismatch / missing を返す。"""
    pr, er = _pnorm(pred_role), _pnorm(exp_role)
    if not pr:
        return "missing"
    if pr == er or (er and (er in pr or pr in er)):
        return "exact"
    if _role_group(pred_role) & _role_group(exp_role):
        return "compatible"
    return "mismatch"


def people_before(pid):
    return PRED[pid].get("saved_people") or []


def people_after(pid):
    """Step A フィルタ適用: 非maker/第三者 source と UI 片名を除去。"""
    off = GT[pid]["expected_official_site"]
    offdom = so.registrable_domain(off) if off else None
    out = []
    for p in PRED[pid].get("saved_people") or []:
        src = p.get("source_url") if isinstance(p, dict) else None
        nm = p.get("name") if isinstance(p, dict) else (p[0] if isinstance(p, (list, tuple)) else p)
        if src is not None and not so.is_maker_owned_person_source(src, offdom):
            continue
        if not looks_like_person_name(nm):
            continue
        out.append(p)
    return out


def _pred_pairs(people):
    """[(name_lower, role_str)] を返す（dict / [name,role] 両対応）。"""
    out = []
    for p in people or []:
        if isinstance(p, dict):
            out.append((_pnorm(p.get("name")), p.get("title") or ""))
        elif isinstance(p, (list, tuple)) and p:
            out.append((_pnorm(p[0]), p[1] if len(p) > 1 else ""))
    return out


def eval_people(pred_of, label):
    tp = fp = fn = 0
    n = 0
    role_stats = {"exact": 0, "compatible": 0, "mismatch": 0, "missing": 0}
    for pid in IDS:
        g = GT[pid]
        if g["verification_status"] not in STRICT:
            continue
        exp_pairs = {name: role for name, role in _pred_pairs(
            [[p[0], p[1] if len(p) > 1 else ""] for p in g["expected_people"]])}
        pred_pairs = dict(_pred_pairs(pred_of(pid)))
        if not exp_pairs and not pred_pairs:
            continue
        n += 1
        exp_n, pred_n = set(exp_pairs), set(pred_pairs)
        tp += len(exp_n & pred_n)
        fp += len(pred_n - exp_n)
        fn += len(exp_n - pred_n)
        for nm in (exp_n & pred_n):
            role_stats[_role_relation(pred_pairs[nm], exp_pairs[nm])] += 1
    p, r, f = prf(tp, fp, fn)
    print(f"  [{label}] N={n}  TP={tp} FP={fp} FN={fn}  P={pct(p)} R={pct(r)} F1={pct(f)}")
    print(f"    role(TP人物の役職一致): exact={role_stats['exact']} "
          f"compatible={role_stats['compatible']} mismatch={role_stats['mismatch']} "
          f"missing={role_stats['missing']}")


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

    print("\n■ フォーム（maker-owned real form / channel=登録ドメイン×intent）  Phase2 before→after")
    eval_forms(forms_before, "BEFORE saved")
    eval_forms(forms_after, "AFTER  select_maker_forms")
    form_supplemental(forms_before, "BEFORE saved")
    form_supplemental(forms_after, "AFTER  select_maker_forms")
    print("    注: channel-P は expected_forms 未列挙(email検証案件)で過小評価される。"
          "総URL数と非maker所有数が dedup/第三者除去の実効。")

    print("\n■ SNS（maker本人のみ TP / entity）  Phase1 before→after")
    eval_sns(sns_before, "BEFORE saved")
    eval_sns(sns_after, "AFTER  自己SNS除外")

    print("\n■ 人物（name 正規化 / entity）  Phase2 Step A before→after")
    eval_people(people_before, "BEFORE saved")
    eval_people(people_after, "AFTER  Step A(非maker/UI除去)")

    print("\n注: partially_verified(2) と blocked/unresolved(8) は上記 strict 分母から除外。")
    print("    plausible_unconfirmed_emails は TP/FP どちらにも数えていない。")


if __name__ == "__main__":
    main()
