"""`scripts/audit_contact_intelligence_population.py` のオフライン検証。

**読み取り専用ツールであること**を機械的に固定する。DB 書き込み・`create_job`・
LQE `run()`・外部 HTTP（Brave / Playwright / Gmail）は一切発生しない。

pytest は使わない（このリポジトリの他テストと同じ自前ハーネス）。
終了コード＝失敗件数。

実行（backend ディレクトリで）:
    python tests/test_audit_contact_intelligence_population.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "audit_pop_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.models.sales_outreach import SalesOutreach  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
import audit_contact_intelligence_population as ap  # noqa: E402
import requeue_contact_intelligence as rq  # noqa: E402

Base.metadata.create_all(engine)

SCRIPT_PATH = BACKEND / "scripts" / "audit_contact_intelligence_population.py"
SCRIPT_SRC = SCRIPT_PATH.read_text(encoding="utf-8")

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS- {name}")
    else:
        _failed += 1
        print(f"FAIL- {name}")


def _code_only() -> str:
    """docstring・コメント・文字列リテラルを除いたコード本文。

    禁止語は **help 文言や禁止事項の説明文にも出てくる**ため、素の grep では
    誤検出する。ここでは「実際に呼んでいるか」だけを見る。
    """
    src = re.sub(r'"""(?:.|\n)*?"""', "", SCRIPT_SRC)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    src = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', src)
    src = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", src)
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )


# --------------------------------------------------------------------------- #
#  fixture
# --------------------------------------------------------------------------- #
def reset_db() -> None:
    db = SessionLocal()
    for model in (ContactIntelligenceJob, SalesOutreach, ContactDiscovery, Project):
        db.query(model).delete()
    db.commit()
    db.close()


def make_project(
    db, pid, *, title="P", site="kickstarter", archived=False,
    source_url=..., maker_name=None, maker_url=None, end_date=None, category=None,
):
    if source_url is ...:
        source_url = f"https://www.kickstarter.com/projects/acme-lab/widget-{pid}"
    p = Project(
        id=pid, title=title, source_site=site, currency="USD",
        source_url=source_url, maker_name=maker_name, maker_url=maker_url,
        archived_at=NOW if archived else None,
    )
    if end_date is not None:
        p.end_date = end_date
    if category is not None:
        p.category = category
    db.add(p)
    db.commit()
    return p


def make_discovery(db, pid, *, v2_status=None, email=None, official=None,
                   v2_official=None, v2_source=None, v2_researched=None,
                   v2_emails=None):
    d = ContactDiscovery(project_id=pid, v2_status=v2_status)
    if email:
        d.primary_email = email
    if official:
        d.official_site_url = official
    if v2_official:
        d.v2_official_site_url = v2_official
    if v2_source:
        d.v2_official_site_source = v2_source
    if v2_researched:
        d.v2_researched_at = v2_researched
    if v2_emails is not None:
        d.v2_emails = v2_emails
    db.add(d)
    db.commit()


def make_job(db, pid, *, job_type=None, status="completed"):
    db.add(ContactIntelligenceJob(
        project_id=pid,
        job_type=job_type or CIJobType.contact_discovery_v2.value,
        status=status, progress=0,
    ))
    db.commit()


def no_active(db, pid):
    return None


# --------------------------------------------------------------------------- #
#  1. 純粋関数: ratio（分子/分母・N/A）
# --------------------------------------------------------------------------- #
def test_ratio() -> None:
    r = ap.ratio(3, 10)
    check("ratio: 分子/分母を先に出す", r["display"].startswith("3/10"))
    check("ratio: パーセントも併記", "30.0%" in r["display"])
    check("ratio: numerator/denominator を保持",
          r["numerator"] == 3 and r["denominator"] == 10)
    z = ap.ratio(0, 0)
    check("ratio: 分母 0 は N/A", "N/A" in z["display"] and "分母0" in z["display"])
    check("ratio: 分母 0 で 0% と書かない", "0.0%" not in z["display"])
    check("ratio: 分母 0 の percent は None", z["percent"] is None)
    check("ratio: 分母 0 でも分子/分母を出す", z["display"].startswith("0/0"))
    check("ratio: 負の分母も N/A 扱い", ap.ratio(1, -1)["percent"] is None)
    check("ratio: 100% を正しく出す", ap.ratio(5, 5)["display"] == "5/5（100.0%）")


# --------------------------------------------------------------------------- #
#  2. 純粋関数: charset（3 値。二値へ丸めない）
# --------------------------------------------------------------------------- #
def test_charset() -> None:
    check("charset: 英字は latin", ap.charset_of("AVIX Lab") == ap.CHARSET_LATIN)
    check("charset: 韓国語は non_latin", ap.charset_of("주식회사 올음") == ap.CHARSET_NON_LATIN)
    check("charset: 中国語は non_latin", ap.charset_of("真影王科技") == ap.CHARSET_NON_LATIN)
    check("charset: 日本語は non_latin", ap.charset_of("株式会社テスト") == ap.CHARSET_NON_LATIN)
    check("charset: None は no_maker_name", ap.charset_of(None) == ap.CHARSET_UNKNOWN)
    check("charset: 空文字は no_maker_name", ap.charset_of("   ") == ap.CHARSET_UNKNOWN)
    check("charset: 記号だけは no_maker_name", ap.charset_of("--- ###") == ap.CHARSET_UNKNOWN)
    check("charset: 数字だけは no_maker_name", ap.charset_of("12345") == ap.CHARSET_UNKNOWN)
    check("charset: no_maker_name を non_latin へ丸めない",
          ap.charset_of(None) != ap.CHARSET_NON_LATIN)
    check("charset: ラテン優勢の混在は latin", ap.charset_of("GPD HK 香港") == ap.CHARSET_LATIN)
    check("charset: 3 値がすべて別物",
          len({ap.CHARSET_LATIN, ap.CHARSET_NON_LATIN, ap.CHARSET_UNKNOWN}) == 3)


# --------------------------------------------------------------------------- #
#  3. 純粋関数: age_bucket（date / datetime 両対応）
# --------------------------------------------------------------------------- #
def test_age_bucket() -> None:
    check("age: None は unknown", ap.age_bucket(None, NOW) == ap.AGE_UNKNOWN)
    check("age: date 型の過去は ended（tzinfo で落ちない）",
          ap.age_bucket(date(2025, 1, 1), NOW) == ap.AGE_ENDED)
    check("age: date 型の未来は live",
          ap.age_bucket(date(2027, 1, 1), NOW) == ap.AGE_LIVE)
    check("age: naive datetime の過去は ended",
          ap.age_bucket(datetime(2025, 1, 1), NOW) == ap.AGE_ENDED)
    check("age: aware datetime の未来は live",
          ap.age_bucket(datetime(2027, 1, 1, tzinfo=timezone.utc), NOW) == ap.AGE_LIVE)
    check("age: 想定外の型は unknown", ap.age_bucket("2025-01-01", NOW) == ap.AGE_UNKNOWN)


# --------------------------------------------------------------------------- #
#  4. 純粋関数: population_reason（Gate を条件に使わない）
# --------------------------------------------------------------------------- #
def test_population_reason() -> None:
    base = dict(archived=False, campaign_url="https://x.example-brand.com/p/1",
                v2_status=None, dummy_signals=[], seed_kind="campaign_url",
                has_active_job=False)

    def r(**kw):
        return ap.population_reason(**{**base, **kw})

    check("pop: 条件を満たせば in_population", r() == ap.IN_POPULATION)
    check("pop: archived を除外", r(archived=True) == ap.OUT_ARCHIVED)
    check("pop: campaign_url なしを除外（起点は別途ある場合）",
          r(campaign_url=None, seed_kind="maker_url") == ap.OUT_NO_CAMPAIGN_URL)
    check("pop: v2 completed を除外",
          r(v2_status="completed") == ap.OUT_V2_COMPLETED)
    check("pop: dummy を除外", r(dummy_signals=["x"]) == ap.OUT_DUMMY)
    check("pop: 探索起点なしを除外", r(seed_kind=None) == ap.OUT_NO_SEED)
    check("pop: active job を除外", r(has_active_job=True) == ap.OUT_ACTIVE_JOB)
    check("pop: archived が最優先",
          r(archived=True, campaign_url=None) == ap.OUT_ARCHIVED)
    # 具体的な理由を先に返す（逆順だと dummy / no_seed へ到達できない）
    check("pop: dummy は no_campaign_url より優先",
          r(campaign_url=None, dummy_signals=["x"]) == ap.OUT_DUMMY)
    check("pop: no_seed は no_campaign_url より優先",
          r(campaign_url=None, seed_kind=None) == ap.OUT_NO_SEED)
    check("pop: 判定順は EXCLUSION_ORDER と一致",
          ap.EXCLUSION_ORDER.index(ap.OUT_DUMMY)
          < ap.EXCLUSION_ORDER.index(ap.OUT_NO_CAMPAIGN_URL))
    # Gate / LQE は引数に存在しない ＝ 母集団条件に使えない
    import inspect
    params = set(inspect.signature(ap.population_reason).parameters)
    check("pop: Gate を母集団条件に使わない（引数に無い）",
          not any("gate" in p for p in params))
    check("pop: LQE を母集団条件に使わない（引数に無い）",
          not any(p.startswith("pre_") or "lqe" in p for p in params))


# --------------------------------------------------------------------------- #
#  5. PR #39 の判定を再利用している（再実装していない）
# --------------------------------------------------------------------------- #
def test_reuses_pr39_rules() -> None:
    code = _code_only()
    check("再利用: requeue_contact_intelligence を import",
          "import requeue_contact_intelligence" in SCRIPT_SRC)
    check("再利用: dummy 判定を呼ぶ", "rq.dummy_or_test_signals" in code)
    check("再利用: 探索起点判定を呼ぶ", "rq.research_seed" in code)
    check("再実装しない: dummy 判定を自前定義しない",
          "def dummy_or_test_signals" not in SCRIPT_SRC)
    check("再実装しない: 探索起点判定を自前定義しない",
          "def research_seed" not in SCRIPT_SRC)
    check("再実装しない: 予約ドメイン一覧を持たない",
          "example.com" not in code and "example.org" not in code
          and "localhost" not in code)
    check("再利用: V2_COMPLETED も共有", ap.V2_COMPLETED == rq.V2_COMPLETED)


# --------------------------------------------------------------------------- #
#  6. project ID / タイトル非依存
# --------------------------------------------------------------------------- #
def test_no_id_or_title_dependency() -> None:
    code = _code_only()
    check("非依存: project ID をハードコードしない",
          not re.search(r"\b(13[7-9]|14[0-3]|11[4-6])\b", code))
    check("非依存: タイトル文字列で分岐しない",
          "Gadget" not in SCRIPT_SRC and "Editable" not in SCRIPT_SRC)
    check("非依存: メーカー名で分岐しない",
          "EditCo" not in SCRIPT_SRC and "SendCo" not in SCRIPT_SRC)
    check("非依存: source_site 名で母集団を分岐しない",
          not re.search(r"source_site\s*==", code))


# --------------------------------------------------------------------------- #
#  7. 読み取り専用（書き込み・ジョブ・run()・外部 HTTP なし）
# --------------------------------------------------------------------------- #
def test_read_only() -> None:
    code = _code_only()
    low = code.lower()
    check("read-only 走査が効いている（説明文を除去）", "読み取り専用" not in code)
    check("create_job を呼ばない", "create_job" not in code)
    check("LQE run() を呼ばない", not re.search(r"lqs\.run\(|\.run\(", code))
    check("qualify のみ利用（run は使わない）", "lqs.qualify" in code)
    check("gather_signals のみ利用", "lqs.gather_signals" in code)
    check("DB へ add しない", "db.add(" not in code)
    check("DB へ commit しない", "commit(" not in code)
    check("DB へ delete しない", "delete(" not in code)
    check("gate は persist=False",
          "persist=False" in SCRIPT_SRC and "persist=True" not in SCRIPT_SRC)
    check("外部 HTTP なし",
          not re.search(r"\b(requests|httpx|urllib|aiohttp|socket)\b", code))
    check("Brave なし", "brave" not in low)
    check("Playwright なし", "playwright" not in low and "chromium" not in low)
    check("Gmail API なし", "gmail" not in low)
    check("メール送信なし", not re.search(r"send_email|smtplib|send_message", code))
    check("下書き作成なし", not re.search(r"compose|draft", low))
    check("subprocess / thread を起動しない",
          not re.search(r"\b(subprocess|threading|Thread|multiprocessing)\b", code))
    check("自動 archive しない", not re.search(r"archived_at\s*=(?!=)", code))
    check("Ground Truth に触れない",
          "ground_truth" not in low and "lqe_eval" not in low)
    check("OUTREACH_GATE_MODE を変更しない", "outreach_gate_mode" not in low)
    check("予測値を出さない",
          not re.search(r"reply_rate|success_rate|probability|likelihood|score", low))


def test_read_only_at_runtime() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 1, maker_name="Acme")
    make_discovery(db, 1)
    make_job(db, 1)
    before = (
        db.query(Project).count(),
        db.query(ContactDiscovery).count(),
        db.query(ContactIntelligenceJob).count(),
    )
    db.close()

    calls = []
    orig = ci.create_job
    ci.create_job = lambda *a, **k: calls.append(a)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ap.main([])
    finally:
        ci.create_job = orig

    db = SessionLocal()
    after = (
        db.query(Project).count(),
        db.query(ContactDiscovery).count(),
        db.query(ContactIntelligenceJob).count(),
    )
    db.close()
    check("実行時: DB 件数が完全に不変", before == after)
    check("実行時: create_job を呼ばない", calls == [])
    check("実行時: exit 0", code == ap.EXIT_OK)


# --------------------------------------------------------------------------- #
#  8. 母集団の構築（実データ相当の構成）
# --------------------------------------------------------------------------- #
def _build_population_fixture(db) -> None:
    # 母集団に入る（latin / kickstarter / live）
    make_project(db, 10, maker_name="AcmeLab", site="kickstarter",
                 end_date=date(2027, 1, 1), category="Product Design",
                 maker_url="https://acme-lab.co.jp")
    make_discovery(db, 10)
    # 母集団に入る（non_latin / wadiz / ended）
    make_project(db, 11, maker_name="주식회사 올음", site="wadiz",
                 source_url="https://www.wadiz.kr/web/campaign/detail/1",
                 end_date=date(2025, 1, 1), category="생활가전")
    make_discovery(db, 11)
    # 母集団に入る（maker 名なし / zeczec / unknown）
    make_project(db, 12, site="zeczec",
                 source_url="https://www.zeczec.com/projects/roly-one")
    make_discovery(db, 12, official="https://suntrail.com.tw")
    # archived
    make_project(db, 13, maker_name="X", archived=True)
    # campaign_url なし（かつ実ドメイン公式サイト → dummy ではなく起点あり）
    make_project(db, 14, maker_name="Y", source_url=None)
    make_discovery(db, 14, official="https://acme-lab.co.jp")
    # v2 completed
    make_project(db, 15, maker_name="Z")
    make_discovery(db, 15, v2_status="completed")
    # dummy（campaign_url なし + 予約ドメイン）
    make_project(db, 16, title="Editable Gadget", maker_name="EditCo", source_url=None)
    make_discovery(db, 16, official="https://maker.example.com",
                   email="a@example.com")
    # 起点なし（campaign_url なし・maker_url なし・公式サイトなし）
    make_project(db, 17, maker_name="W", source_url=None)
    make_discovery(db, 17, email="a@acme-lab.co.jp")
    # active heavy job
    make_project(db, 18, maker_name="V")
    make_discovery(db, 18)
    make_job(db, 18, status=CIJobStatus.running.value)


def test_population_membership() -> None:
    reset_db()
    db = SessionLocal()
    _build_population_fixture(db)
    records = ap.collect(db, now=NOW)
    db.close()

    by = {r.project_id: r for r in records}
    check("母集団: 通常案件を含む", by[10].membership == ap.IN_POPULATION)
    check("母集団: 非ラテン案件も含む", by[11].membership == ap.IN_POPULATION)
    check("母集団: maker 名なしも含む", by[12].membership == ap.IN_POPULATION)
    check("母集団: archived を外す", by[13].membership == ap.OUT_ARCHIVED)
    check("母集団: campaign_url なしを外す",
          by[14].membership == ap.OUT_NO_CAMPAIGN_URL)
    check("母集団: v2 completed を外す", by[15].membership == ap.OUT_V2_COMPLETED)
    check("母集団: dummy を外す", by[16].membership == ap.OUT_DUMMY)
    check("母集団: dummy の根拠を持つ", by[16].dummy_signals != [])
    check("母集団: 起点なしを外す", by[17].membership == ap.OUT_NO_SEED)
    check("母集団: active job を外す", by[18].membership == ap.OUT_ACTIVE_JOB)
    check("母集団: サイズは 3", sum(1 for r in records
                                    if r.membership == ap.IN_POPULATION) == 3)
    check("母集団: 探索起点は campaign_url",
          all(by[i].seed_kind == "campaign_url" for i in (10, 11, 12)))
    check("母集団外は Gate を算出しない（集計軸は母集団のみ）",
          by[13].gate_decision is None and by[16].gate_decision is None)
    check("母集団は Gate を算出する", by[10].gate_decision is not None)


# --------------------------------------------------------------------------- #
#  9. 集計（分子/分母・軸）
# --------------------------------------------------------------------------- #
def test_report_aggregation() -> None:
    reset_db()
    db = SessionLocal()
    _build_population_fixture(db)
    records = ap.collect(db, now=NOW)
    db.close()
    rep = ap.build_report(records)

    check("集計: 母集団サイズ", rep["population_size"] == 3)
    check("集計: ファネルに総件数", rep["funnel"]["total_projects"] == 9)
    check("集計: in_population が分子/分母",
          rep["funnel"]["in_population"]["display"].startswith("3/9"))

    ev = rep["evidence"]
    check("集計: 証跡が分子/分母",
          all("display" in v for v in ev.values()))
    check("集計: official site verified は 0/3",
          ev["official_site_verified"]["display"].startswith("0/3"))
    check("集計: email が無ければ分母 0 で N/A",
          "N/A" in ev["emails_with_source_url"]["display"])
    check("集計: maker_name あり 2/3",
          ev["maker_name_present"]["display"].startswith("2/3"))

    br = rep["breakdown"]
    check("集計: source_site 別",
          br["source_site"] == {"kickstarter": 1, "wadiz": 1, "zeczec": 1})
    check("集計: 文字種別（3 値が保たれる）",
          br["charset"] == {ap.CHARSET_LATIN: 1, ap.CHARSET_NON_LATIN: 1,
                            ap.CHARSET_UNKNOWN: 1})
    check("集計: campaign age 別",
          br["campaign_age"] == {"live": 1, "ended": 1, "unknown": 1})
    check("集計: 探索起点別", br["seed_kind"] == {"campaign_url": 3})
    check("集計: 母集団外の理由別を全件基準で出す",
          br["membership"][ap.IN_POPULATION] == 3
          and br["membership"][ap.OUT_DUMMY] == 1)

    ju = rep["judgement"]
    check("集計: gate 分布がある", sum(ju["gate"].values()) == 3)
    check("集計: pre_research 分布がある", sum(ju["pre_research"].values()) == 3)
    check("集計: pre_outreach 分布がある", sum(ju["pre_outreach"].values()) == 3)
    check("集計: blocker 内訳が分子/分母",
          all("display" in v for v in ju["pre_outreach_blockers"].values()))

    cr = rep["cross"]
    check("集計: site × 文字種", cr["site_x_charset"]["wadiz"]["_n"] == 1)
    check("集計: site × gate がある", "kickstarter" in cr["site_x_gate"])
    check("集計: site × pre_research がある", "zeczec" in cr["site_x_pre_research"])
    check("集計: site × age がある", "wadiz" in cr["site_x_age"])
    check("集計: 文字種 × gate がある", ap.CHARSET_NON_LATIN in cr["charset_x_gate"])

    rs = rep["research"]
    check("集計: v2 未実行率", rs["v2_never_run"]["display"].startswith("3/3"))
    check("集計: 予測値を持たない",
          not any(k in json.dumps(rep, default=str)
                  for k in ("reply_rate", "success_rate", "probability")))


def test_distribution_deterministic() -> None:
    d1 = ap.distribution(["b", "a", "a", "c"])
    d2 = ap.distribution(["c", "a", "b", "a"])
    check("決定的: 同じ入力なら同じ順序", list(d1) == list(d2))
    check("決定的: 件数降順→名前順", list(d1) == ["a", "b", "c"])
    ordered = ap.distribution(["x", "y"], order=("y", "x"))
    check("決定的: order 指定を尊重", list(ordered) == ["y", "x"])
    check("決定的: order 外は末尾へ",
          list(ap.distribution(["z", "y"], order=("y",))) == ["y", "z"])
    check("決定的: None も文字列化して数える", ap.distribution([None]) == {"None": 1})


def test_rerun_identical() -> None:
    reset_db()
    db = SessionLocal()
    _build_population_fixture(db)
    r1 = ap.build_report(ap.collect(db, now=NOW))
    r2 = ap.build_report(ap.collect(db, now=NOW))
    db.close()
    check("再実行で同一結果",
          json.dumps(r1, sort_keys=True, default=str)
          == json.dumps(r2, sort_keys=True, default=str))
    check("再実行で母集団サイズが同じ",
          r1["population_size"] == r2["population_size"])


# --------------------------------------------------------------------------- #
#  10. 出力（JSON / 表形式 / 秘密情報なし）
# --------------------------------------------------------------------------- #
def test_output() -> None:
    reset_db()
    db = SessionLocal()
    _build_population_fixture(db)
    # 証跡つきメールを持つ案件（分母が 0 でなくなる）
    make_project(db, 20, maker_name="Evidence Co")
    make_discovery(
        db, 20,
        v2_official="https://evidence.co.jp",
        v2_source="search", v2_researched=NOW,
        v2_emails=[{"email": "secret-owner@evidence.co.jp",
                    "source_url": "https://evidence.co.jp/contact",
                    "email_owner": "maker"}],
    )
    db.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ap.main(["--json", "--top", "50"])
    out = buf.getvalue()
    payload = json.loads(out)

    check("JSON: exit 0", code == ap.EXIT_OK)
    check("JSON: 母集団サイズがある", payload["population_size"] == 4)
    check("JSON: 定義が B", payload["population_definition"] == "B")
    check("JSON: records を含む", len(payload["records"]) == 4)
    check("JSON: 分子/分母の display がある",
          "display" in payload["evidence"]["official_site_verified"])
    check("JSON: N/A 表記がある（分母 0 の軸）",
          "N/A" in json.dumps(payload, ensure_ascii=False)
          or payload["evidence"]["emails_with_source_url"]["denominator"] > 0)
    check("JSON: メールアドレスを出さない",
          "secret-owner@evidence.co.jp" not in out
          and not re.search(r"[\w.]+@[\w.]+", out))
    check("JSON: API キー / Cookie を出さない",
          "api_key" not in out.lower() and "cookie" not in out.lower())
    check("JSON: 証跡つき email をカウントする",
          payload["evidence"]["emails_with_source_url"]["numerator"] == 1)
    check("JSON: verified official site を数える",
          payload["evidence"]["official_site_verified"]["numerator"] == 1)
    check("JSON: 予測値を出さない",
          not re.search(r"reply_rate|success_rate|probability", out.lower()))
    check("JSON: note に禁止事項を明記", "予測" in payload["note"])

    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        ap.main([])
    out2 = buf2.getvalue()
    check("表形式: 母集団の定義を表示", "母集団の定義" in out2)
    check("表形式: Gate が集計軸である旨を表示", "集計軸" in out2)
    check("表形式: 分子/分母を表示", re.search(r"\d+/\d+（", out2) is not None)
    check("表形式: クロス集計を表示", "site × 文字種" in out2)
    check("表形式: 一覧を表示", "母集団の一覧" in out2)
    check("表形式: メールアドレスを出さない",
          "secret-owner@evidence.co.jp" not in out2)
    check("表形式: 注意書きを表示", "予測は算出しません" in out2)

    buf3 = io.StringIO()
    with redirect_stdout(buf3):
        ap.main(["--top", "1"])
    out3 = buf3.getvalue()
    check("--top: 一覧の表示件数を絞る", "母集団の一覧（先頭 1 / 4 件）" in out3)
    check("--top: 母集団サイズ自体は変えない", "先頭 1 / 4" in out3)
    buf3b = io.StringIO()
    with redirect_stdout(buf3b):
        ap.main(["--json", "--top", "2"])
    check("--top: JSON の records も絞る",
          len(json.loads(buf3b.getvalue())["records"]) == 2)
    buf4 = io.StringIO()
    with redirect_stdout(buf4):
        rc = ap.main(["--top", "-1"])
    check("--top: 負値は非 0 終了", rc == ap.EXIT_ERROR)


# --------------------------------------------------------------------------- #
#  11. ドキュメント
# --------------------------------------------------------------------------- #
def test_documentation() -> None:
    doc = ap.__doc__ or ""
    for phrase, label in [
        ("読み取り専用", "読み取り専用"),
        ("ジョブは作りません", "ジョブを作らない"),
        ("外部 HTTP", "外部 HTTP なし"),
        ("集計軸", "Gate は集計軸"),
        ("再実装しません", "判定を再実装しない"),
        ("分母が 0", "分母 0 は N/A"),
        ("予測", "予測を出さない"),
    ]:
        check(f"doc: docstring に「{label}」", phrase in doc)
    helptext = ap.build_parser().format_help()
    for phrase in ["読み取り専用", "集計軸", "N/A", "再利用"]:
        check(f"help: --help に「{phrase}」", phrase in helptext)


def main() -> int:
    print("=== audit_contact_intelligence_population ===")
    test_ratio()
    test_charset()
    test_age_bucket()
    test_population_reason()
    test_reuses_pr39_rules()
    test_no_id_or_title_dependency()
    test_read_only()
    test_read_only_at_runtime()
    test_population_membership()
    test_report_aggregation()
    test_distribution_deterministic()
    test_rerun_identical()
    test_output()
    test_documentation()
    print(f"\n{_passed} passed / {_failed} failed")
    return _failed


if __name__ == "__main__":
    sys.exit(main())
