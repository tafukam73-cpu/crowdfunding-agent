"""Contact Intelligence 再調査の**母集団**を可視化・監査する（読み取り専用）。

「どの案件に外部一次証拠が取れていないのか」を、分子/分母付きで把握するための
ツールです。**ジョブは作りません。DB も書き換えません。外部 HTTP もしません。**

母集団の定義（B）
------------------
次を **すべて** 満たす案件を母集団とします。

1. `archived_at IS NULL`
2. **campaign_url がある**（`campaign_url.campaign_url_of` の正本判定）
3. `contact_discoveries.v2_status` が `completed` ではない
4. **ダミー/テストデータではない**（`requeue_contact_intelligence` の判定を再利用）
5. **探索の起点がある**（campaign_url / maker_url / 検証済み公式サイト）
6. 同一 project に進行中の重い CI ジョブがない

**Contact Search Gate の判定は母集団条件に使いません。** 集計軸として持つだけです。
Gate は判定時点が古いことがあり、`not_eligible` の理由が「証拠不足」である場合は
まさに再調査すべき案件だからです（Gate で絞ると 8 件しか変わらないことを実測済み）。

判定の再利用
------------
ダミー判定と探索起点判定は `scripts/requeue_contact_intelligence` から import します。
**同じロジックを再実装しません。** さらにその先は既存の正本
（`url_validation` / `contact_discovery_service.is_dummy_domain`）へ委ねています。

**project ID・タイトル・メーカー名・source_site では判定しません。**

出さないもの
------------
- メールアドレス（**有無とドメイン種別のみ**）
- API キー / Cookie / シークレット
- **返信率・成功率・可能性予測**（このリポジトリでは禁止）

率はすべて **分子/分母を先に**表示し、**分母が 0 のときは `N/A（分母0）`** と書きます
（`0%` とは書きません）。

実行（backend ディレクトリで）:
    python scripts/audit_contact_intelligence_population.py           # 表形式
    python scripts/audit_contact_intelligence_population.py --json    # 機械可読
    python scripts/audit_contact_intelligence_population.py --top 50  # 一覧の表示件数
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.models.sales_outreach import SalesOutreach  # noqa: E402
from app.services import campaign_url as campaign_url_mod  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
from app.services import contact_search_gate  # noqa: E402
from app.services import lead_qualification_service as lqs  # noqa: E402

# PR #39 でマージ済みの判定を再利用する（再実装しない）。
import requeue_contact_intelligence as rq  # noqa: E402

V2_COMPLETED = rq.V2_COMPLETED

# 母集団に入らない理由（表示順＝判定順）
OUT_ARCHIVED = "archived"
OUT_NO_CAMPAIGN_URL = "no_campaign_url"
OUT_V2_COMPLETED = "v2_completed"
OUT_DUMMY = "dummy_or_test_data"
OUT_NO_SEED = "no_research_seed"
OUT_ACTIVE_JOB = "active_heavy_job"
IN_POPULATION = "in_population"

#: 判定順＝表示順。**具体的な理由から先に返す。**
#: `dummy` と `no_seed` は「campaign_url が無い」案件の中の、より具体的な診断なので、
#: `no_campaign_url` より先に見る（順序を逆にすると両者へ到達できない）。
EXCLUSION_ORDER = (
    IN_POPULATION,
    OUT_ARCHIVED,
    OUT_V2_COMPLETED,
    OUT_DUMMY,
    OUT_NO_SEED,
    OUT_NO_CAMPAIGN_URL,
    OUT_ACTIVE_JOB,
)

CHARSET_LATIN = "latin"
CHARSET_NON_LATIN = "non_latin"
#: maker 名そのものが無い。**latin/non_latin へ丸めない**（Zeczec がこの層に集中する）。
CHARSET_UNKNOWN = "no_maker_name"

AGE_ENDED = "ended"
AGE_LIVE = "live"
AGE_UNKNOWN = "unknown"

#: CI ジョブ履歴の有無を見るときの「重い探索」ジョブ種別。
_RESEARCH_JOB_TYPES = (
    CIJobType.full_contact_intelligence.value,
    CIJobType.contact_discovery_v2.value,
)

EXIT_OK = 0
EXIT_ERROR = 1


# --------------------------------------------------------------------------- #
#  純粋関数（DB に触れない。テストはここを直接叩く）
# --------------------------------------------------------------------------- #
def ratio(numerator: int, denominator: int) -> dict:
    """率を分子/分母付きで返す。**分母 0 は `N/A（分母0）`**（`0%` とは書かない）。"""
    if denominator <= 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "percent": None,
            "display": f"{numerator}/{denominator}（N/A・分母0）",
        }
    pct = 100.0 * numerator / denominator
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percent": round(pct, 1),
        "display": f"{numerator}/{denominator}（{pct:.1f}%）",
    }


def charset_of(maker_name: str | None) -> str:
    """maker 名の文字種を 3 値で返す。**二値へ丸めない。**

    `no_maker_name` は「名前が無い」であって「ラテン文字でない」ではありません。
    実データでは Zeczec がこの層に集中するため、潰すと層そのものが消えます。
    """
    if not maker_name or not maker_name.strip():
        return CHARSET_UNKNOWN
    letters = [c for c in maker_name if c.isalpha()]
    if not letters:
        return CHARSET_UNKNOWN
    latin = sum(1 for c in letters if "LATIN" in unicodedata.name(c, ""))
    return CHARSET_LATIN if latin / len(letters) >= 0.6 else CHARSET_NON_LATIN


def age_bucket(end_date, now: datetime) -> str:
    """募集終了日から `ended` / `live` / `unknown` を返す。

    `projects.end_date` は **`date` 型**（`datetime` ではない）。`tzinfo` を触ると
    落ちるため、両方の型を受ける（実装中に実際に踏んだ）。
    """
    if end_date is None:
        return AGE_UNKNOWN
    if isinstance(end_date, datetime):
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        return AGE_ENDED if end_date < now else AGE_LIVE
    if isinstance(end_date, date):
        return AGE_ENDED if end_date < now.date() else AGE_LIVE
    return AGE_UNKNOWN


def population_reason(
    *,
    archived: bool,
    campaign_url: str | None,
    v2_status: str | None,
    dummy_signals: list[str],
    seed_kind: str | None,
    has_active_job: bool,
) -> str:
    """母集団に入るか、入らないならその理由を返す（純粋関数）。

    **Gate / LQE の判定は条件に使わない**（集計軸として別に持つ）。

    判定順は `EXCLUSION_ORDER` と同じで、**具体的な理由から先に返す**。
    `dummy` / `no_seed` は「campaign_url が無い」案件のより具体的な診断なので、
    `no_campaign_url` より先に見る。逆にすると両者へ到達できず、
    「ダミーが何件あるか」を数えられなくなる（実装中に実際に踏んだ）。
    """
    if archived:
        return OUT_ARCHIVED
    if v2_status == V2_COMPLETED:
        return OUT_V2_COMPLETED
    if dummy_signals:
        return OUT_DUMMY
    if not seed_kind:
        return OUT_NO_SEED
    if not campaign_url:
        return OUT_NO_CAMPAIGN_URL
    if has_active_job:
        return OUT_ACTIVE_JOB
    return IN_POPULATION


def email_domain(addr: str | None) -> str | None:
    """メールアドレスからドメインだけを取り出す（**ローカル部は捨てる**）。"""
    if not addr or "@" not in addr:
        return None
    return addr.rsplit("@", 1)[1].strip().lower() or None


def distribution(values, order=None) -> dict:
    """値の分布を `{値: 件数}` で返す。順序は決定的（指定順 → 件数降順 → 名前順）。"""
    counts = Counter(str(v) for v in values)
    if order:
        keys = [k for k in order if k in counts]
        keys += sorted(k for k in counts if k not in order)
    else:
        keys = sorted(counts, key=lambda k: (-counts[k], k))
    return {k: counts[k] for k in keys}


# --------------------------------------------------------------------------- #
#  収集（読み取りのみ）
# --------------------------------------------------------------------------- #
@dataclass
class Record:
    """1 案件ぶんの監査データ。**メールアドレスそのものは持たない。**"""

    project_id: int
    title: str | None = None
    source_site: str | None = None
    category: str | None = None
    maker_name_present: bool = False
    charset: str = CHARSET_UNKNOWN
    has_campaign_url: bool = False
    has_maker_url: bool = False
    v2_status: str | None = None
    seed_kind: str | None = None
    dummy_signals: list[str] = field(default_factory=list)
    membership: str = IN_POPULATION
    age: str = AGE_UNKNOWN
    active_job_id: int | None = None
    has_research_job_history: bool = False
    outreach_status: str | None = None
    # --- 以下は母集団のみ算出（Gate / LQE は集計軸） ---
    gate_decision: str | None = None
    pre_research: str | None = None
    pre_outreach: str | None = None
    blocker_codes: list[str] = field(default_factory=list)
    review_codes: list[str] = field(default_factory=list)
    official_site_present: bool = False
    official_site_verified: bool = False
    maker_identity_verified: bool = False
    business_email_count: int = 0
    emails_with_source_url: int = 0
    emails_with_checked_at: int = 0
    emails_with_role: int = 0
    has_primary_email: bool = False
    legacy_email_path: bool = False

    def to_public(self) -> dict:
        d = dict(self.__dict__)
        d["title"] = (self.title or "")[:60]
        return d


def _has_research_job(db, project_id: int) -> bool:
    row = db.scalar(
        select(ContactIntelligenceJob.id)
        .where(
            ContactIntelligenceJob.project_id == project_id,
            ContactIntelligenceJob.job_type.in_(list(_RESEARCH_JOB_TYPES)),
        )
        .limit(1)
    )
    return row is not None


def collect(db, *, now: datetime | None = None, active_heavy=None) -> list[Record]:
    """全案件の監査データを作る。**読み取りのみ・外部 HTTP なし。**"""
    now = now or datetime.now(timezone.utc)
    active_heavy = active_heavy or ci.find_active_heavy

    records: list[Record] = []
    for project in db.scalars(select(Project).order_by(Project.id)).all():
        disc = db.scalar(
            select(ContactDiscovery)
            .where(ContactDiscovery.project_id == project.id)
            .order_by(ContactDiscovery.id.desc())
            .limit(1)
        )
        primary = None
        official = None
        v2_status = None
        if disc is not None:
            v2_status = disc.v2_status
            primary = getattr(disc, "v2_primary_email", None) or getattr(
                disc, "primary_email", None
            )
            official = getattr(disc, "v2_official_site_url", None) or getattr(
                disc, "official_site_url", None
            )

        campaign = campaign_url_mod.campaign_url_of(project)
        maker_url = getattr(project, "maker_url", None)

        # PR #39 の判定をそのまま使う
        dummy = rq.dummy_or_test_signals(
            campaign_url=campaign,
            official_site_url=official,
            email_domain=email_domain(primary),
        )
        seed = rq.research_seed(
            campaign_url=campaign, maker_url=maker_url, official_site_url=official
        )
        active = active_heavy(db, project.id)

        rec = Record(
            project_id=project.id,
            title=project.title,
            source_site=project.source_site,
            category=getattr(project, "category", None),
            maker_name_present=bool(getattr(project, "maker_name", None)),
            charset=charset_of(getattr(project, "maker_name", None)),
            has_campaign_url=bool(campaign),
            has_maker_url=bool(maker_url),
            v2_status=v2_status,
            seed_kind=seed,
            dummy_signals=dummy,
            age=age_bucket(getattr(project, "end_date", None), now),
            active_job_id=active.id if active is not None else None,
            has_research_job_history=_has_research_job(db, project.id),
            has_primary_email=bool(primary),
        )
        rec.membership = population_reason(
            archived=project.archived_at is not None,
            campaign_url=campaign,
            v2_status=v2_status,
            dummy_signals=dummy,
            seed_kind=seed,
            has_active_job=active is not None,
        )
        outreach = db.scalar(
            select(SalesOutreach).where(SalesOutreach.project_id == project.id)
        )
        rec.outreach_status = outreach.outreach_status if outreach else None

        if rec.membership == IN_POPULATION:
            _fill_gate_and_lqe(db, project, rec)
        records.append(rec)
    return records


def _fill_gate_and_lqe(db, project, rec: Record) -> None:
    """Gate と LQE を **集計軸として** 埋める。

    Gate は `persist=False`（判定を保存しない）。LQE は `gather_signals` +
    `qualify` のみで、**`run()` は呼ばない**（履歴を書かない）。
    """
    gate = contact_search_gate.evaluate(db, project, persist=False)
    rec.gate_decision = gate.get("contact_search_gate_decision")

    signals = lqs.gather_signals(db, project)
    pre_r = lqs.qualify(signals, lqs.STAGE_PRE_RESEARCH)
    pre_o = lqs.qualify(signals, lqs.STAGE_PRE_OUTREACH)
    rec.pre_research = pre_r.decision
    rec.pre_outreach = pre_o.decision
    rec.blocker_codes = sorted(
        {f.code for f in pre_o.findings if f.severity == lqs.SEVERITY_BLOCKER}
    )
    rec.review_codes = sorted(
        {f.code for f in pre_o.findings if f.severity == lqs.SEVERITY_REVIEW}
    )

    official = signals.get("official_site") or {}
    identity = signals.get("maker_identity") or {}
    emails = signals.get("business_emails") or []
    rec.official_site_present = bool(official.get("url"))
    rec.official_site_verified = bool(official.get("verified"))
    rec.maker_identity_verified = bool(identity.get("verified"))
    rec.business_email_count = len(emails)
    rec.emails_with_source_url = sum(1 for e in emails if e.get("source_url"))
    rec.emails_with_checked_at = sum(1 for e in emails if e.get("checked_at"))
    rec.emails_with_role = sum(1 for e in emails if e.get("role"))
    # 旧経路 = メールはあるが取得元 URL が 1 件も無い
    rec.legacy_email_path = bool(emails) and rec.emails_with_source_url == 0


# --------------------------------------------------------------------------- #
#  集計
# --------------------------------------------------------------------------- #
def build_report(records: list[Record]) -> dict:
    """監査レポートを組み立てる（純粋関数。DB に触れない）。"""
    total = len(records)
    pop = [r for r in records if r.membership == IN_POPULATION]
    n = len(pop)

    emails_total = sum(r.business_email_count for r in pop)

    funnel = {
        "total_projects": total,
        "not_archived": ratio(
            sum(1 for r in records if r.membership != OUT_ARCHIVED), total
        ),
        "in_population": ratio(n, total),
    }

    evidence = {
        "official_site_present": ratio(
            sum(1 for r in pop if r.official_site_present), n
        ),
        "official_site_verified": ratio(
            sum(1 for r in pop if r.official_site_verified), n
        ),
        "maker_identity_verified": ratio(
            sum(1 for r in pop if r.maker_identity_verified), n
        ),
        "maker_name_present": ratio(sum(1 for r in pop if r.maker_name_present), n),
        "maker_url_present": ratio(sum(1 for r in pop if r.has_maker_url), n),
        "primary_email_present": ratio(sum(1 for r in pop if r.has_primary_email), n),
        "business_email_present": ratio(
            sum(1 for r in pop if r.business_email_count), n
        ),
        "emails_with_source_url": ratio(
            sum(r.emails_with_source_url for r in pop), emails_total
        ),
        "emails_with_checked_at": ratio(
            sum(r.emails_with_checked_at for r in pop), emails_total
        ),
        "emails_with_role": ratio(sum(r.emails_with_role for r in pop), emails_total),
        "legacy_email_path": ratio(sum(1 for r in pop if r.legacy_email_path), n),
    }

    blocker_counter: Counter = Counter()
    review_counter: Counter = Counter()
    for r in pop:
        blocker_counter.update(r.blocker_codes)
        review_counter.update(r.review_codes)

    judgement = {
        "gate": distribution(
            (r.gate_decision for r in pop),
            order=("eligible", "needs_review", "not_eligible"),
        ),
        "pre_research": distribution(
            (r.pre_research for r in pop), order=("clear", "review", "blocked")
        ),
        "pre_outreach": distribution(
            (r.pre_outreach for r in pop), order=("clear", "review", "blocked")
        ),
        "pre_outreach_blockers": {
            code: ratio(cnt, n) for code, cnt in sorted(blocker_counter.items())
        },
        "pre_outreach_reviews": {
            code: ratio(cnt, n) for code, cnt in sorted(review_counter.items())
        },
    }

    research = {
        "v2_never_run": ratio(sum(1 for r in pop if r.v2_status is None), n),
        "v2_incomplete": ratio(
            sum(1 for r in pop if r.v2_status not in (None, V2_COMPLETED)), n
        ),
        "no_research_job_history": ratio(
            sum(1 for r in pop if not r.has_research_job_history), n
        ),
        "outreach_row_present": ratio(sum(1 for r in pop if r.outreach_status), n),
    }

    breakdown = {
        "source_site": distribution(r.source_site for r in pop),
        "charset": distribution(
            (r.charset for r in pop),
            order=(CHARSET_LATIN, CHARSET_NON_LATIN, CHARSET_UNKNOWN),
        ),
        "campaign_age": distribution(
            (r.age for r in pop), order=(AGE_LIVE, AGE_ENDED, AGE_UNKNOWN)
        ),
        "seed_kind": distribution(r.seed_kind for r in pop),
        "category": distribution(r.category for r in pop),
        "membership": distribution(
            (r.membership for r in records), order=EXCLUSION_ORDER
        ),
    }

    cross = {
        "site_x_charset": _cross(pop, "source_site", "charset"),
        "site_x_gate": _cross(pop, "source_site", "gate_decision"),
        "site_x_pre_research": _cross(pop, "source_site", "pre_research"),
        "site_x_age": _cross(pop, "source_site", "age"),
        "charset_x_gate": _cross(pop, "charset", "gate_decision"),
    }

    return {
        "population_definition": "B",
        "population_size": n,
        "funnel": funnel,
        "evidence": evidence,
        "judgement": judgement,
        "research": research,
        "breakdown": breakdown,
        "cross": cross,
        "note": (
            "率は分子/分母を先に示す。分母 0 は N/A。"
            "返信率・成功率・可能性予測は算出しない。"
        ),
    }


def _cross(pop: list[Record], row_attr: str, col_attr: str) -> dict:
    """2 軸のクロス集計。行も列も決定的な順序で返す。"""
    out: dict[str, dict[str, int]] = {}
    for key in sorted({str(getattr(r, row_attr)) for r in pop}):
        subset = [r for r in pop if str(getattr(r, row_attr)) == key]
        out[key] = {"_n": len(subset), **distribution(
            getattr(r, col_attr) for r in subset
        )}
    return out


# --------------------------------------------------------------------------- #
#  出力
# --------------------------------------------------------------------------- #
def _print_ratios(title: str, block: dict) -> None:
    print(f"## {title}")
    for key, value in block.items():
        if isinstance(value, dict) and "display" in value:
            print(f"  {key:<26} {value['display']}")
        elif isinstance(value, dict):
            print(f"  {key}:")
            for k2, v2 in value.items():
                shown = v2["display"] if isinstance(v2, dict) else v2
                print(f"    {k2:<24} {shown}")
        else:
            print(f"  {key:<26} {value}")
    print("")


def _print_cross(title: str, table: dict) -> None:
    print(f"## {title}")
    for row, cols in table.items():
        n = cols.get("_n", 0)
        rest = {k: v for k, v in cols.items() if k != "_n"}
        print(f"  {row:<14} N={n:<5} {rest}")
    print("")


def print_report(report: dict, records: list[Record], top: int) -> None:
    print("=== Contact Intelligence 母集団監査（読み取り専用・ジョブは作りません） ===")
    print("")
    print(f"母集団の定義: {report['population_definition']}"
          "（archived でない / campaign_url あり / v2 未完了 / "
          "ダミーでない / 探索起点あり / active job なし）")
    print("Gate は母集団条件に使わず、集計軸として持つ")
    print("")
    _print_ratios("ファネル", report["funnel"])
    _print_ratios("証跡", report["evidence"])
    _print_ratios("判定（集計軸）", report["judgement"])
    _print_ratios("探索の実施状況", report["research"])
    _print_ratios("内訳", report["breakdown"])
    _print_cross("site × 文字種", report["cross"]["site_x_charset"])
    _print_cross("site × gate", report["cross"]["site_x_gate"])
    _print_cross("site × pre_research", report["cross"]["site_x_pre_research"])
    _print_cross("site × campaign age", report["cross"]["site_x_age"])
    _print_cross("文字種 × gate", report["cross"]["charset_x_gate"])

    pop = [r for r in records if r.membership == IN_POPULATION]
    shown = pop[:top]
    print(f"## 母集団の一覧（先頭 {len(shown)} / {len(pop)} 件）")
    print(f"{'id':>5} {'site':<12} {'gate':<13} {'pre_r':<8} {'pre_o':<8} "
          f"{'文字種':<12} {'age':<8} {'seed':<13} {'mk':<3}{'ml':<3}{'of':<3}{'em':<3} title")
    for r in shown:
        print(
            f"{r.project_id:>5} {str(r.source_site):<12} {str(r.gate_decision):<13} "
            f"{str(r.pre_research):<8} {str(r.pre_outreach):<8} {r.charset:<12} "
            f"{r.age:<8} {str(r.seed_kind):<13} "
            f"{'有' if r.maker_name_present else '-':<3}"
            f"{'有' if r.has_maker_url else '-':<3}"
            f"{'有' if r.official_site_present else '-':<3}"
            f"{'有' if r.has_primary_email else '-':<3} {str(r.title)[:32]}"
        )
    print("")
    print("※ メールアドレスは表示しません（有無のみ）")
    print("※ 返信率・成功率・可能性予測は算出しません")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audit_contact_intelligence_population.py",
        description=(
            "Contact Intelligence 再調査の母集団を可視化・監査する（読み取り専用）。"
            " ジョブは作らず、DB も書き換えず、外部 HTTP もしない。"
            " ダミー判定と探索起点判定は requeue_contact_intelligence から再利用する。"
            " Gate は母集団条件ではなく集計軸として扱う。"
            " 率は分子/分母を先に示し、分母 0 は N/A（0% とは書かない）。"
            " メールアドレス・返信率・成功率・可能性予測は出さない。"
        ),
    )
    p.add_argument("--json", action="store_true", help="機械可読な結果を stdout へ出す")
    p.add_argument(
        "--top", type=int, default=30, help="一覧に表示する件数（既定 30）"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.top < 0:
        print("ERROR: --top は 0 以上で指定してください")
        return EXIT_ERROR

    try:
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: DB へ接続できません: {type(exc).__name__}")
        return EXIT_ERROR

    try:
        records = collect(db)
        report = build_report(records)
    finally:
        db.close()

    if args.json:
        payload = dict(report)
        payload["records"] = [
            r.to_public() for r in records if r.membership == IN_POPULATION
        ][: args.top]
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report, records, args.top)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
