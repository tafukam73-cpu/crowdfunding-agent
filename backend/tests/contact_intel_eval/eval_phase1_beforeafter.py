"""Phase 1 precision 変更の gold set(24) before/after 決定的オフライン評価。

BEFORE = saved_*（変更前パイプラインが保存した予測）をそのまま採点。
AFTER  = saved_* に「今回追加した除外」だけを適用して採点。
  - email : email_exclusion_reason（agency/marketing/shortener/platform を除外）
  - sns   : is_platform_self_social（運営自己アカウントを除外）
  - official/form/people : 今回未変更（before==after で参照値として出す）

今回の変更は「除外を足すだけ」で新規候補を足さないため、この差分が precision/recall への
影響そのもの。gold(expected_*) は一切変更しない。ネットワークにアクセスしない。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402
from app.services import source_ownership as so  # noqa: E402

HERE = Path(__file__).resolve().parent
GOLD = json.loads((HERE / "gold_frozen_24.json").read_text(encoding="utf-8"))


def norm(xs):
    return {str(x).strip().lower() for x in (xs or []) if str(x).strip()}


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def score(pred_of, expected_key):
    """各案件で pred_of(g)->set を採点し、集計＋案件単位の正否を返す。"""
    tp = fp = fn = 0
    projects_with_any_correct = 0
    projects_with_fp = 0
    fp_items, fn_items = [], []
    for g in GOLD:
        exp = norm(g.get(expected_key))
        pred = norm(pred_of(g))
        t = exp & pred
        fps = pred - exp
        fns = exp - pred
        tp += len(t)
        fp += len(fps)
        fn += len(fns)
        if t:
            projects_with_any_correct += 1
        if fps:
            projects_with_fp += 1
            fp_items += [(g["project_id"], x) for x in sorted(fps)]
        if fns:
            fn_items += [(g["project_id"], x) for x in sorted(fns)]
    return dict(tp=tp, fp=fp, fn=fn, proj_ok=projects_with_any_correct,
                proj_fp=projects_with_fp, fp_items=fp_items, fn_items=fn_items)


def site_domain(g):
    return cds.source_site_email_domain(g.get("source_site"))


# --- predictors ---
def emails_before(g):
    return g.get("saved_emails")


def emails_after(g):
    sd = site_domain(g)
    out = {"direct": [], "fallback": []}
    for e in norm(g.get("saved_emails")):
        info = so.classify_email(e)
        if cds.email_exclusion_reason(e, sd):
            # 除外だが agency/distributor は fallback として別集計に温存
            if info["accepted_as_fallback_contact"]:
                out["fallback"].append(e)
            continue
        out["direct"].append(e)
    return out["direct"], out["fallback"]


def emails_after_direct(g):
    return emails_after(g)[0]


def emails_after_fallback(g):
    return emails_after(g)[1]


def socials_before(g):
    return g.get("saved_socials")


def socials_after(g):
    return [s for s in norm(g.get("saved_socials")) if not so.is_platform_self_social(s)]


def official_pred(g):
    return g.get("saved_official_sites")


def forms_pred(g):
    return g.get("saved_forms")


def people_pred(g):
    # people は [name, role] のリスト。名前のみで採点する。
    def names(xs):
        out = []
        for p in (xs or []):
            if isinstance(p, (list, tuple)) and p:
                out.append(str(p[0]))
            elif p:
                out.append(str(p))
        return out
    return names(g.get("saved_people"))


def people_expected_key(g):
    pass


def report(title, res):
    p, r, f = prf(res["tp"], res["fp"], res["fn"])
    print(f"  {title}")
    print(f"    TP={res['tp']} FP={res['fp']} FN={res['fn']}  "
          f"precision={p:.1%} recall={r:.1%} F1={f:.1%}")
    print(f"    正解を1件以上得た案件={res['proj_ok']}  FPを含む案件={res['proj_fp']}")


def main():
    print("=" * 68)
    print("Phase1 gold(24) before/after  ※gold は不変・ネットワーク非接続")
    print("=" * 68)

    print("\n[メール] direct maker email")
    b = score(emails_before, "expected_emails")
    a = score(emails_after_direct, "expected_emails")
    report("BEFORE (saved そのまま)", b)
    report("AFTER  (direct のみ)", a)

    # agency誤採用/platform誤採用 の変化
    def count_class(pred_of, cls_check):
        n = 0
        for g in GOLD:
            for e in norm(pred_of(g)):
                if cls_check(e):
                    n += 1
        return n
    is_platform = lambda e: so.classify_domain(e).ownership_class == "crowdfunding_platform"
    is_agency = lambda e: so.classify_domain(e).ownership_class == "agency"
    is_mkt = lambda e: so.classify_domain(e).ownership_class == "crowdfunding_marketing_service"
    print(f"    platform運営メール誤採用 BEFORE={count_class(emails_before, is_platform)} "
          f"AFTER={count_class(emails_after_direct, is_platform)}")
    print(f"    agencyメール誤採用       BEFORE={count_class(emails_before, is_agency)} "
          f"AFTER={count_class(emails_after_direct, is_agency)}")
    print(f"    marketingメール誤採用    BEFORE={count_class(emails_before, is_mkt)} "
          f"AFTER={count_class(emails_after_direct, is_mkt)}")

    # fallback集計（別指標）
    fb_total = sum(len(norm(emails_after_fallback(g))) for g in GOLD)
    fb_proj = sum(1 for g in GOLD if norm(emails_after_fallback(g)))
    print(f"    [別指標] agency/distributor fallback email 数={fb_total}（{fb_proj}案件）")

    # 失った正解（新規FN）と 除去したFP
    lost = sorted(set(map(tuple, a["fn_items"])) - set(map(tuple, b["fn_items"])))
    removed_fp = sorted(set(map(tuple, b["fp_items"])) - set(map(tuple, a["fp_items"])))
    print(f"    新規FN（失った正解maker直通）: {lost if lost else 'なし'}")
    print(f"    除去したFP件数: {len(removed_fp)}")
    for pid, e in removed_fp:
        cls = so.classify_domain(e).ownership_class
        print(f"      - project {pid}: {e}  ({cls})")

    print("\n[SNS]")
    report("BEFORE", score(socials_before, "expected_socials"))
    report("AFTER (platform自己SNS除外)", score(socials_after, "expected_socials"))
    def platform_self_count(pred_of):
        return sum(1 for g in GOLD for s in norm(pred_of(g)) if so.is_platform_self_social(s))
    print(f"    platform自己SNS誤採用 BEFORE={platform_self_count(socials_before)} "
          f"AFTER={platform_self_count(socials_after)}")

    print("\n[公式サイト]（今回未変更・参照）")
    report("official", score(official_pred, "expected_official_sites"))
    print("\n[フォーム]（今回未変更・参照）")
    report("form", score(forms_pred, "expected_forms"))
    print("\n[担当者]（今回未変更・参照・名前一致）")
    # expected_people も [name, role] 形式なので名前化して比較
    def people_score():
        tp = fp = fn = 0
        for g in GOLD:
            def names(xs):
                out = []
                for p in (xs or []):
                    if isinstance(p, (list, tuple)) and p:
                        out.append(str(p[0]).strip().lower())
                    elif p:
                        out.append(str(p).strip().lower())
                return set(out)
            exp = names(g.get("expected_people"))
            pred = names(g.get("saved_people"))
            tp += len(exp & pred); fp += len(pred - exp); fn += len(exp - pred)
        return dict(tp=tp, fp=fp, fn=fn, proj_ok=0, proj_fp=0)
    report("people", people_score())


if __name__ == "__main__":
    main()
