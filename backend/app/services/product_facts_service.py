"""確認可能な事実だけを組み立てる「商品ファクトシート」。

このシステムは実際の海外メーカー営業に使う実務ツールであり、根拠のない予測値
（返信率予測・独占契約成功率・日本で売れる可能性・営業成功確率・利益率予測など）を
ユーザー向け画面に出さない。この services は **画面に出してよい事実** だけを集約する。

方針:
- 取得できない項目は推測で埋めず ``value=None`` ＋ ``status="未取得"`` を返す。
- 各項目に取得元（URL・取得元の種類・最終確認日時）を付ける。
- AI が生成した文章（商品概要・特徴）は ``ai_generated=True`` を立て、画面が
  「AI要約」と明示できるようにする。事実と混同させない。
- 規制（PSE / 技適 / 食品衛生法 / 薬機法 等）は該当を断定せず「確認が必要な項目」と
  して返し、そう判断した商品ページ上の根拠語を必ず併記する。

内部スコア（japan_crowdfunding_score / priority_score / 各種 fit_score）はここでは
一切返さない。内部ゲート・並び順のためにバックエンドには残っているが、画面へは出さない。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.project import Project
from app.services import campaign_url as campaign_url_mod

logger = logging.getLogger("product_facts")

# 取得元の種類（画面に「どこで確認したか」を出すため）
SOURCE_CAMPAIGN = "クラファン商品ページ"
SOURCE_OFFICIAL = "メーカー公式サイト"
SOURCE_REGISTRY = "法人登記"
SOURCE_LINKEDIN = "LinkedIn"
SOURCE_EC = "ECサイト"
SOURCE_JP_CROWDFUNDING = "日本クラファンサイト"
SOURCE_CONTACT_SEARCH = "連絡先探索"
SOURCE_AI = "AI要約"

# 値が無いときの表示文言（推測で埋めない）
NOT_ACQUIRED = "未取得"
NOT_CONFIRMED = "未確認"


def _fact(
    label: str,
    value,
    *,
    source_kind: str | None = None,
    source_url: str | None = None,
    checked_at: datetime | date | None = None,
    ai_generated: bool = False,
    note: str | None = None,
) -> dict:
    """1 項目の事実。value が空なら status に「未取得」を入れる。"""
    empty = value is None or (isinstance(value, str) and not value.strip())
    return {
        "label": label,
        "value": None if empty else value,
        "status": NOT_ACQUIRED if empty else "取得済み",
        "source_kind": None if empty else source_kind,
        "source_url": None if empty else source_url,
        "checked_at": _iso(checked_at) if not empty else None,
        "ai_generated": ai_generated and not empty,
        "note": note,
    }


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  商品
# --------------------------------------------------------------------------- #
def product_facts(db: Session, project: Project) -> dict:
    from app.services import product_context_service as pcs
    from app.services.workflow_service import SITE_LABELS_JA

    urls = campaign_url_mod.url_state(project)
    context = pcs.build(db, project)
    site = str(project.source_site or "")

    return {
        "items": [
            _fact("商品名", project.title, source_kind=SOURCE_CAMPAIGN,
                  source_url=urls["campaign_url"], checked_at=project.updated_at),
            # 日本語概要・特徴は AI 生成（事実ではない）。画面で「AI要約」と明示する。
            _fact("日本語の商品概要", context.get("summary_ja"), source_kind=SOURCE_AI,
                  checked_at=project.updated_at, ai_generated=True),
            _fact("主な特徴", context.get("key_features") or None, source_kind=SOURCE_AI,
                  checked_at=project.updated_at, ai_generated=True),
            _fact("商品画像", project.image_url, source_kind=SOURCE_CAMPAIGN,
                  source_url=project.image_url, checked_at=project.updated_at),
            _fact("海外クラファン商品ページ", urls["campaign_url"],
                  source_kind=SOURCE_CAMPAIGN, source_url=urls["campaign_url"],
                  checked_at=project.updated_at,
                  note=(None if urls["campaign_url"]
                        else f"欠落理由: {urls['campaign_url_missing_reason']}")),
            _fact("メーカー公式サイト", urls["official_site_url"],
                  source_kind=SOURCE_OFFICIAL, source_url=urls["official_site_url"],
                  checked_at=project.updated_at),
            _fact("対象クラファンサイト", SITE_LABELS_JA.get(site, site or None),
                  source_kind=SOURCE_CAMPAIGN, source_url=urls["campaign_url"],
                  checked_at=project.updated_at),
            _fact("商品カテゴリー", project.category, source_kind=SOURCE_CAMPAIGN,
                  source_url=urls["campaign_url"], checked_at=project.updated_at),
        ],
        "image_url": project.image_url,
        "campaign_url": urls["campaign_url"],
        "official_site_url": urls["official_site_url"],
    }


# --------------------------------------------------------------------------- #
#  クラファン実績
# --------------------------------------------------------------------------- #
def _funding_rate(project: Project) -> int | None:
    try:
        goal = float(project.goal_amount or 0)
        raised = float(project.raised_amount or 0)
    except (TypeError, ValueError):
        return None
    if goal <= 0 or raised <= 0:
        return None
    return int(round(raised / goal * 100))


def _campaign_state(project: Project) -> tuple[str | None, int | None]:
    """(募集中/終了, 残り日数) を返す。終了日が無ければ (None, None)。"""
    end = project.end_date
    if end is None:
        return None, None
    today = _now().date()
    if end < today:
        return "終了", None
    return "募集中", (end - today).days


def funding_facts(project: Project) -> dict:
    urls = campaign_url_mod.url_state(project)
    src = dict(source_kind=SOURCE_CAMPAIGN, source_url=urls["campaign_url"],
               checked_at=project.updated_at)
    state, remaining = _campaign_state(project)
    rate = _funding_rate(project)
    currency = project.currency or ""

    def money(value) -> str | None:
        if value is None:
            return None
        try:
            return f"{currency} {float(value):,.0f}".strip()
        except (TypeError, ValueError):
            return None

    return {
        "items": [
            _fact("募集開始日", _iso(project.start_date), **src),
            _fact("募集終了日", _iso(project.end_date), **src),
            _fact("募集状況", state, **src),
            _fact("残り日数", None if remaining is None else f"{remaining}日", **src),
            _fact("目標金額", money(project.goal_amount), **src),
            _fact("調達金額", money(project.raised_amount), **src),
            _fact("支援率", None if rate is None else f"{rate:,}%", **src),
            _fact("支援者数",
                  None if project.backers_count is None
                  else f"{project.backers_count:,}人", **src),
            # コメント数・更新回数は現在どのスクレイパーも取得していない。
            # 推測しないため常に「未取得」を返す（取得できるようになったら差し替える）。
            _fact("コメント数", None, **src),
            _fact("更新回数", None, **src),
        ],
    }


def compact_facts(project: Project) -> dict:
    """一覧・ランキング・カードに出す最小限の事実（DB 追加クエリなし）。

    スコアや星の代わりにこれを見せる。取得できない値は None（画面は「未取得」表示）。
    """
    state, remaining = _campaign_state(project)
    return {
        "category": project.category,
        "image_url": project.image_url,
        "campaign_state": state,
        "days_remaining": remaining,
        "funding_rate": _funding_rate(project),
        "backers_count": project.backers_count,
        "raised_amount": (
            float(project.raised_amount) if project.raised_amount is not None else None
        ),
        "currency": project.currency,
    }


# --------------------------------------------------------------------------- #
#  メーカー情報
# --------------------------------------------------------------------------- #
def maker_facts(db: Session, project: Project) -> dict:
    from app.services import company_research_service, contact_discovery_service
    from app.services import contact_hunter_service

    cd = None
    try:
        cd = contact_discovery_service.get_latest(db, project.id)
    except Exception as exc:  # noqa: BLE001  未探索でも続行
        logger.info("contact discovery lookup skipped (project=%s): %s", project.id, exc)

    person = None
    try:
        person = contact_hunter_service.get_top_person(db, project.id)
    except Exception as exc:  # noqa: BLE001
        logger.info("contact person lookup skipped (project=%s): %s", project.id, exc)

    cr = None
    try:
        cr = company_research_service.get_latest_completed(db, project.id)
    except Exception as exc:  # noqa: BLE001
        logger.info("company research lookup skipped (project=%s): %s", project.id, exc)

    official = campaign_url_mod.official_site_url_of(project) or (
        cd.official_site_url if cd else None
    )
    contact_src = dict(source_kind=SOURCE_CONTACT_SEARCH,
                       checked_at=getattr(cd, "updated_at", None))

    return {
        "items": [
            _fact("会社名", project.maker_name, source_kind=SOURCE_CAMPAIGN,
                  source_url=campaign_url_mod.campaign_url_of(project),
                  checked_at=project.updated_at),
            _fact("ブランド名", getattr(cr, "brand_summary", None) and project.maker_name,
                  source_kind=SOURCE_OFFICIAL, source_url=official,
                  checked_at=getattr(cr, "updated_at", None)),
            # 所在国は projects に列が無く、推測もしない。
            _fact("所在国", None, source_kind=SOURCE_REGISTRY),
            _fact("公式サイト", official, source_kind=SOURCE_OFFICIAL,
                  source_url=official, checked_at=getattr(cd, "updated_at", None)),
            # 代表者名は法人登記等でしか確認できないため、未取得のまま返す。
            _fact("代表者名", None, source_kind=SOURCE_REGISTRY),
            _fact("担当者名", getattr(person, "name", None),
                  source_url=getattr(person, "source_url", None),
                  source_kind=SOURCE_CONTACT_SEARCH,
                  checked_at=getattr(person, "updated_at", None)),
            _fact("役職", getattr(person, "title", None),
                  source_url=getattr(person, "source_url", None),
                  source_kind=SOURCE_CONTACT_SEARCH,
                  checked_at=getattr(person, "updated_at", None)),
            _fact("メールアドレス", getattr(cd, "primary_email", None),
                  source_url=getattr(cd, "v2_primary_source_url", None), **contact_src),
            _fact("LinkedIn", getattr(cd, "linkedin_url", None),
                  source_url=getattr(cd, "linkedin_url", None),
                  source_kind=SOURCE_LINKEDIN,
                  checked_at=getattr(cd, "updated_at", None)),
            _fact("問い合わせフォーム", getattr(cd, "primary_contact_form_url", None),
                  source_url=getattr(cd, "primary_contact_form_url", None),
                  **contact_src),
            _fact("会社概要", getattr(cr, "brand_summary", None),
                  source_kind=SOURCE_AI, source_url=official,
                  checked_at=getattr(cr, "updated_at", None), ai_generated=True),
            # 設立年・法人情報の確認元は登記情報が必要。取得していないため未取得。
            _fact("設立年", None, source_kind=SOURCE_REGISTRY),
            _fact("法人情報の確認元", None, source_kind=SOURCE_REGISTRY),
        ],
    }


# --------------------------------------------------------------------------- #
#  日本市場確認
# --------------------------------------------------------------------------- #
# チャネル状況 → 画面表示。「見つからない」を「日本未発売」と断定しない。
JP_STATUS_LABELS = {
    "found": "販売・掲載あり",
    "limited": "一部のみ確認",
    "not_found": "確認した範囲では見つからず",
    "unknown": "未確認",
}


def japan_market_facts(db: Session, project: Project) -> dict:
    """日本市場確認の結果（チャネルごとの事実＋検索URL＋最終確認日時）。"""
    from app.services import japan_sales_service

    check = None
    try:
        check = japan_sales_service.get_latest(db, project.id)
    except Exception as exc:  # noqa: BLE001  未実行でも続行
        logger.info("japan sales lookup skipped (project=%s): %s", project.id, exc)

    checked_at = getattr(check, "updated_at", None)
    channels = list(getattr(check, "channels", None) or [])
    if not channels:
        # 未実行：チャネル一覧は出すが、すべて「未確認」にする（断定しない）。
        from app.ai.japan_sales_checker import CHANNELS, search_url

        channels = [
            {
                "channel": key,
                "label": label,
                "status": "unknown",
                "search_url": search_url(key, product=project.title,
                                         maker=project.maker_name),
                "note": "",
            }
            for key, label in CHANNELS
        ]

    items = []
    for c in channels:
        status = str(c.get("status") or "unknown")
        items.append(
            {
                "label": c.get("label") or c.get("channel"),
                "value": JP_STATUS_LABELS.get(status, JP_STATUS_LABELS["unknown"]),
                "status": status,
                "source_kind": (
                    SOURCE_JP_CROWDFUNDING
                    if c.get("channel") in ("makuake", "greenfunding", "campfire")
                    else SOURCE_EC
                ),
                "source_url": c.get("search_url"),
                "checked_at": _iso(checked_at),
                "ai_generated": False,
                "note": c.get("note") or None,
            }
        )

    return {
        "checked": check is not None,
        "checked_at": _iso(checked_at),
        "items": items,
    }


# --------------------------------------------------------------------------- #
#  確認が必要な規制項目（断定しない）
# --------------------------------------------------------------------------- #
# (確認項目, 根拠となる語, 表示文) — 「該当する」ではなく「確認が必要」と表現する。
REGULATORY_CHECKS: list[tuple[str, tuple[str, ...], str]] = [
    ("PSE",
     ("ac adapter", "power adapter", "plug", "charger", "電源", "コンセント",
      "battery", "lithium", "rechargeable", "バッテリー"),
     "電気製品のため PSE 確認が必要になる可能性あり"),
    ("技適",
     ("bluetooth", "wi-fi", "wifi", "wireless", "nfc", "lte", "無線", "電波"),
     "無線機能があるため技適確認が必要"),
    ("食品衛生法",
     ("food", "drink", "beverage", "coffee", "tea", "bottle", "mug", "cutlery",
      "tableware", "食品", "飲料", "食器", "カトラリー"),
     "食品接触製品のため輸入時の確認が必要"),
    ("薬機法",
     ("medical", "therapy", "treatment", "cure", "clinical", "diagnos",
      "supplement", "cosmetic", "skincare", "医療", "治療", "効能", "化粧品"),
     "医療効果表現があるため薬機法上の確認が必要"),
]


def regulatory_checks(project: Project) -> dict:
    """輸入・販売前に確認すべき規制項目を返す（該当を断定しない）。

    各項目には、そう判断した **商品ページ上の根拠語** を必ず併記する。
    根拠が見つからない項目は返さない（推測で増やさない）。
    """
    text = " ".join(
        str(x or "")
        for x in (project.title, project.description_clean or project.description,
                  project.category)
    ).lower()
    campaign = campaign_url_mod.campaign_url_of(project)

    out = []
    for name, hints, message in REGULATORY_CHECKS:
        evidence = sorted({h for h in hints if h in text})
        if not evidence:
            continue
        out.append(
            {
                "item": name,
                "message": message,
                # 商品ページ上の根拠（この語が説明文・タイトルに含まれていた）
                "evidence_terms": evidence[:5],
                "source_kind": SOURCE_CAMPAIGN,
                "source_url": campaign,
            }
        )
    return {
        "items": out,
        "note": (
            "該当の断定ではなく、輸入・販売前に確認すべき項目です。"
            "根拠は商品ページ上の記載語です。"
        ),
    }


# --------------------------------------------------------------------------- #
#  まとめ
# --------------------------------------------------------------------------- #
def build(db: Session, project: Project) -> dict:
    """商品ファクトシート（画面表示用）。内部スコアは一切含めない。"""
    from app.services import contact_search_gate

    gate = contact_search_gate.evaluate(db, project, persist=False)
    return {
        "project_id": project.id,
        "product": product_facts(db, project),
        "funding": funding_facts(project),
        "maker": maker_facts(db, project),
        "japan_market": japan_market_facts(db, project),
        "regulatory": regulatory_checks(project),
        # 探索しなかった具体的な理由（スコアではなく事実・ルールに基づく文言）
        "contact_search": {
            "eligible": gate["eligible_for_contact_search"],
            "reasons": gate.get("user_reasons") or [],
        },
        "generated_at": _iso(_now()),
    }
