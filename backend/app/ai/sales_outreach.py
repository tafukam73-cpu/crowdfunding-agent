"""営業実行パイプライン向け・多言語アウトリーチ生成（決定的・API キー不要）。

営業メール 1 通を 4 言語（英語 / 韓国語 / 中国語(繁体) / 日本語）で生成する。
Discovery→Promote→Contact Intelligence を経た案件に対し、「日本市場（Makuake /
GreenFunding）での展開」を打診する初回アウトリーチを組み立てる。

方針:
- 決定的・ネットワーク不要（テスト可能）。差出人情報（SenderContext）と案件の
  個別化材料（personalization）を反映する。
- 推奨言語は案件のプラットフォームから導く（wadiz=韓国語 / zeczec=中国語 /
  makuake・greenfunding=日本語 / それ以外=英語）。
- 実際の Claude 生成は背景ジョブ側から本モジュールの結果に重ねられる（任意）。
  外部 Claude 呼び出しは同期 POST/GET では行わない。
"""
from __future__ import annotations

from app.ai.personalization import build_personalization
from app.ai.prompts import SenderContext
from app.models.project import Project

# 生成する言語（順序＝表示順）。
OUTREACH_LANGUAGES: tuple[str, ...] = ("en", "ko", "zh", "ja")

LANGUAGE_LABELS: dict[str, str] = {
    "en": "英語",
    "ko": "韓国語",
    "zh": "中国語",
    "ja": "日本語",
}

# プラットフォーム → 推奨言語（相手メーカーの母国語で送るのが最も返信率が高い）。
_SITE_LANGUAGE: dict[str, str] = {
    "wadiz": "ko",       # 韓国
    "zeczec": "zh",      # 台湾（繁体中文）
    "makuake": "ja",     # 日本
    "greenfunding": "ja",
    "kickstarter": "en",
    "indiegogo": "en",
    "ulule": "en",       # フランス発だが英語で到達しやすい
}

OUTREACH_MODEL_NAME = "rule-sales-outreach-v1"


def recommended_language(project: Project) -> str:
    """案件のプラットフォームから推奨アウトリーチ言語を導く。"""
    return _SITE_LANGUAGE.get((project.source_site or "").lower(), "en")


def _greeting(lang: str, maker_name: str | None) -> str:
    maker = (maker_name or "").strip()
    if lang == "ko":
        return f"{maker} 담당자님께," if maker else "안녕하세요,"
    if lang == "zh":
        return f"{maker} 團隊 您好：" if maker else "您好："
    if lang == "ja":
        return f"{maker} ご担当者様" if maker else "ご担当者様"
    return f"Hello {maker} Team," if maker else "Hello there,"


def _signature(lang: str, ctx: SenderContext) -> str:
    sender = (ctx.sender_name or "").strip() or "the team"
    title = (ctx.sender_title or "").strip()
    company = (ctx.company_name or "").strip()
    email = (ctx.sender_email or "").strip()
    website = (ctx.website_url or "").strip()
    contact_bits = " | ".join([b for b in (email, website) if b])

    if lang == "ko":
        lines = ["감사합니다.", sender]
    elif lang == "zh":
        lines = ["順頌商祺，", sender]
    elif lang == "ja":
        lines = ["何卒よろしくお願いいたします。", sender]
    else:
        lines = ["Best regards,", sender]

    if title and company:
        lines.append(f"{title}, {company}")
    elif company:
        lines.append(company)
    if contact_bits:
        lines.append(contact_bits)
    return "\n".join(lines)


def _subjects(lang: str, product: str) -> list[str]:
    if lang == "ko":
        return [
            f"{product}의 일본 시장(Makuake) 진출 제안",
            f"{product} · 일본 런칭 파트너십 문의",
            f"일본 크라우드펀딩으로 {product}를 소개하고 싶습니다",
        ]
    if lang == "zh":
        return [
            f"關於 {product} 進軍日本市場（Makuake）的合作提案",
            f"{product} · 日本群眾募資上市洽談",
            f"想在日本 Makuake 為 {product} 開拓市場",
        ]
    if lang == "ja":
        return [
            f"{product} の日本展開（Makuake）に関するご提案",
            f"{product} 日本ローンチのパートナーシップについて",
            f"日本のクラウドファンディングで {product} を紹介したく存じます",
        ]
    return [
        f"Bringing {product} to Japan via Makuake",
        f"{product} × Japan launch partnership",
        f"Introducing {product} to Japanese backers",
    ]


def _body(lang: str, product: str, maker_name: str | None, ctx: SenderContext,
          angle: str, compliment: str) -> str:
    greeting = _greeting(lang, maker_name)
    sign = _signature(lang, ctx)
    company = (ctx.company_name or "").strip()

    if lang == "ko":
        comp = f"{compliment}\n\n" if compliment else ""
        extra = f"{angle}\n\n" if angle else ""
        para = (
            f"{comp}"
            f"저희는 해외 제품을 일본 크라우드펀딩(Makuake / GreenFunding)에 소개하는 "
            f"일을 하고 있습니다. {product} 는 일본 시장에서 좋은 반응을 얻을 수 있다고 "
            f"생각합니다.\n\n"
            f"{extra}"
            f"현지화, 마케팅, 물류를 저희가 담당하므로 부담 없이 일본 런칭을 진행하실 수 "
            f"있습니다. 일본 독점 판매 파트너십에 관심이 있으신지 여쭙고 싶습니다. "
            f"짧게 온라인 미팅도 가능합니다."
        )
    elif lang == "zh":
        comp = f"{compliment}\n\n" if compliment else ""
        extra = f"{angle}\n\n" if angle else ""
        para = (
            f"{comp}"
            f"我們協助海外優秀產品透過日本群眾募資平台（Makuake / GreenFunding）進入日本"
            f"市場。我們認為 {product} 很適合日本消費者。\n\n"
            f"{extra}"
            f"在地化、行銷與物流皆由我方負責，讓您能輕鬆完成日本上市。想請問貴司是否有意"
            f"洽談日本獨家銷售的合作？也歡迎安排一次簡短的線上會議。"
        )
    elif lang == "ja":
        comp = f"{compliment}\n\n" if compliment else ""
        extra = f"{angle}\n\n" if angle else ""
        para = (
            f"{comp}"
            f"私たちは海外の優れた製品を日本のクラウドファンディング（Makuake / "
            f"GreenFunding）で紹介する事業を行っております。{product} は日本市場でも"
            f"高い反響が期待できると考えております。\n\n"
            f"{extra}"
            f"ローカライズ・マーケティング・物流は当社が担当いたしますので、御社の"
            f"ご負担なく日本ローンチを進められます。日本での独占販売パートナーシップに"
            f"ご興味はございますでしょうか。短いオンライン面談も可能です。"
        )
    else:
        comp = f"{compliment}\n\n" if compliment else ""
        extra = f"{angle}\n\n" if angle else ""
        para = (
            f"{comp}"
            f"We help outstanding overseas products launch in Japan through "
            f"crowdfunding (Makuake / GreenFunding), and we believe {product} would "
            f"resonate strongly with Japanese backers.\n\n"
            f"{extra}"
            f"We handle localization, marketing, and logistics, so a Japan launch is "
            f"smooth on your side. Would you be open to exploring an exclusive Japan "
            f"distribution partnership? A short call also works whenever convenient."
        )

    return f"{greeting}\n\n{para}\n\n{sign}"


def _angle(lang: str, p: dict, japan_sales: dict | None) -> str:
    """日本市場の切り口（既存代理店が無ければ『日本初上陸』を訴求）。"""
    no_distributor = bool(japan_sales and japan_sales.get("no_japan_presence"))
    base = str(p.get("japan_market_angle") or "").strip()
    if lang == "en":
        if no_distributor:
            return "As far as we can tell, it is not yet available in Japan — a strong " \
                   "'first in Japan' story for backers."
        return base
    if no_distributor:
        return {
            "ko": "아직 일본에 정식 유통되지 않은 것으로 보이며, '일본 최초' 스토리로 "
                  "강하게 어필할 수 있습니다.",
            "zh": "據我們了解貴產品尚未在日本正式販售，可作為『日本首發』的強力賣點。",
            "ja": "現時点で日本での正規流通は確認できず、『日本初上陸』として強く訴求"
                  "できます。",
        }[lang]
    return ""


def _compliment(lang: str, p: dict) -> str:
    """商品ごとの短い称賛の一文（英語材料を言語別の枕詞に載せる）。"""
    en = str(p.get("personalized_compliment") or "").strip()
    if not en:
        return ""
    if lang == "en":
        return en
    prefix = {
        "ko": "귀사의 제품을 인상 깊게 보았습니다. ",
        "zh": "我們對貴司的產品印象深刻。",
        "ja": "御社の製品を拝見し、大変魅力的だと感じております。",
    }
    return prefix.get(lang, "")


def build_multilang_outreach(
    project: Project,
    ctx: SenderContext | None = None,
    *,
    research: dict | None = None,
    japan_sales: dict | None = None,
) -> dict:
    """4 言語の営業アウトリーチを生成する（決定的・DB 非依存）。

    Returns:
      {
        "recommended_language": "ko",
        "model": "rule-sales-outreach-v1",
        "variants": {lang: {"subject", "subject_options", "body"}},
        "japanese_summary": "...",
      }
    """
    ctx = ctx or SenderContext.fallback()
    p = build_personalization(project)
    # 企業リサーチがあれば日本市場切り口を差し替える（既存 followup と同様）。
    if research and research.get("japan_market_fit"):
        p["japan_market_angle"] = str(research["japan_market_fit"]).strip()
    product = project.title

    variants: dict[str, dict] = {}
    for lang in OUTREACH_LANGUAGES:
        subjects = _subjects(lang, product)
        body = _body(
            lang, product, project.maker_name, ctx,
            angle=_angle(lang, p, japan_sales),
            compliment=_compliment(lang, p),
        )
        variants[lang] = {
            "subject": subjects[0],
            "subject_options": subjects,
            "body": body,
        }

    rec = recommended_language(project)
    jp_summary = (
        f"{LANGUAGE_LABELS.get(rec, rec)}を推奨言語として、{product} の日本展開"
        f"（Makuake / GreenFunding）と独占販売を打診する初回営業メール。4 言語"
        f"（英語/韓国語/中国語/日本語）を生成。"
    )
    return {
        "recommended_language": rec,
        "model": OUTREACH_MODEL_NAME,
        "variants": variants,
        "japanese_summary": jp_summary,
    }


__all__ = [
    "OUTREACH_LANGUAGES",
    "LANGUAGE_LABELS",
    "recommended_language",
    "build_multilang_outreach",
    "OUTREACH_MODEL_NAME",
]
