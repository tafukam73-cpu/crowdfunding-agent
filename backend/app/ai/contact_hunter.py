"""Contact Hunter AI の共通インターフェースと純粋ロジック。

「会社」ではなく「誰に送るか（営業担当者）」を特定するためのレイヤー。
Business Development / Partnership / Export / Sales / Marketing / Founder などの
人物を、出典 URL 付きで抽出する。

設計方針（最重要）：
- AI に人名を推測（捏造）させない。出典 URL を持ち、ページ上に実在が確認できた
  人物だけを採用する。
- 役職 → 営業優先度（0〜100）の変換は決定的な純粋関数（title_to_priority）で行い、
  単体テストできるようにする。
- メールは contact_discovery_service の既存除外フィルタを必ず通す（service 側で実施）。

get_contact_hunter() が設定に応じてエンジンを選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockContactHunter（決定的 HTML 抽出）
  - 設定済み            → ClaudeContactHunter（出典必須・捏造禁止のプロンプト）
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.config import settings

# 役職キーワード → (部署ラベル, 営業優先度)。上から評価し最初に一致したものを採用。
# 優先度は要件の対応表に準拠（BD/Partnership=95, Intl Sales=90, Export=88,
# Sales=85, Marketing=80, Founder=75, CEO=70, Press=45, Support=30）。
_TITLE_RULES: list[tuple[tuple[str, ...], str, int]] = [
    (("business development", "biz dev", "bizdev", "bd manager", "head of bd"),
     "Business Development", 95),
    (("partnership", "partnerships", "partner relations", "alliances",
      "strategic partner"), "Partnership", 95),
    (("international sales", "global sales", "overseas sales", "international business"),
     "International Sales", 90),
    (("export", "import/export"), "Export", 88),
    (("sales director", "head of sales", "vp sales", "vp of sales", "sales manager",
      "sales lead", "chief revenue", "cro", "director of sales", "account executive",
      "sales"), "Sales", 85),
    (("marketing director", "head of marketing", "cmo", "marketing manager",
      "brand manager", "growth", "marketing"), "Marketing", 80),
    (("co-founder", "cofounder", "co founder", "founder", "owner"), "Founder", 75),
    (("ceo", "chief executive", "managing director", "general manager", "president"),
     "CEO", 70),
    (("press", "public relations", "pr manager", "communications", "media relations",
      "publicity"), "Press", 45),
    (("support", "customer service", "customer success", "customer care", "help desk",
      "helpdesk"), "Support", 30),
]

# 役職が無い/不明な人物のデフォルト
_UNKNOWN_DEPARTMENT = None
_UNKNOWN_PRIORITY = 40
# 役職はあるが既知ルールに一致しない場合
_OTHER_DEPARTMENT = "Other"
_OTHER_PRIORITY = 50

# 人名らしさの判定で弾く一般語（ナビ/UIテキストが名前に紛れるのを防ぐ）
_NON_NAME_WORDS = frozenset(
    {
        "team", "contact", "about", "home", "shop", "cart", "login", "signin",
        "sign", "menu", "search", "press", "media", "news", "blog", "careers",
        "support", "help", "faq", "company", "story", "people", "leadership",
        "our", "meet", "the", "us", "view", "profile", "linkedin", "facebook",
        "instagram", "twitter", "youtube", "follow", "more", "read", "learn",
        "privacy", "terms", "policy", "all", "rights", "reserved", "copyright",
        "newsletter", "subscribe", "email", "click", "here",
    }
)

# 役職に含まれがちな語（人名判定で「役職を名前と誤認」しないため）
_TITLE_WORDS = frozenset(
    {
        "ceo", "cto", "cfo", "coo", "cmo", "cro", "founder", "co-founder",
        "cofounder", "president", "director", "manager", "head", "lead", "officer",
        "vp", "chief", "executive", "marketing", "sales", "partnership", "business",
        "development", "export", "press", "support", "owner", "engineer",
    }
)

_PERSON_NAME_RE = re.compile(r"^[A-Z][A-Za-zÀ-ÿ.'\-]+(?:\s+[A-Z][A-Za-zÀ-ÿ.'\-]+){1,3}$")


class PersonResult(BaseModel):
    """発見した担当者候補（DB / モデル非依存）。

    source_url は必須相当（service 側で出典の無い人物は捨てる）。
    """

    name: str | None = None
    title: str | None = None
    department: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    email_source: str | None = None
    source_url: str = ""
    confidence: int = 0
    priority: int = 0
    notes: str = ""


class ContactHuntResult(BaseModel):
    """Contact Hunter の出力（DB 非依存）。"""

    people: list[PersonResult] = Field(default_factory=list)
    searched_queries: list[str] = Field(default_factory=list)
    searched_urls: list[str] = Field(default_factory=list)
    notes: str = ""
    model: str = ""


class ContactHunter(ABC):
    """全 Contact Hunter の基底クラス。"""

    name: str = "base"
    last_usage: dict | None = None

    @abstractmethod
    def hunt(
        self,
        project,
        *,
        fetch_fn=None,
        search_fn=None,
        research=None,
    ) -> ContactHuntResult:
        """案件のメーカーの営業担当者候補を探して返す。

        人名は推測しない（出典ページに実在するもののみ）。fetch_fn / search_fn を
        注入できる（テスト用）。
        """
        raise NotImplementedError


# ---------------- 純粋関数 ----------------
def title_to_priority(title: str | None) -> tuple[str | None, int]:
    """役職テキストから (部署ラベル, 営業優先度 0〜100) を決定的に返す。

    役職が無ければ (None, 40)、既知ルールに無ければ (\"Other\", 50)。
    """
    if not title or not title.strip():
        return _UNKNOWN_DEPARTMENT, _UNKNOWN_PRIORITY
    low = title.lower()
    for keywords, dept, priority in _TITLE_RULES:
        if any(kw in low for kw in keywords):
            return dept, priority
    return _OTHER_DEPARTMENT, _OTHER_PRIORITY


def looks_like_person_name(text: str | None) -> bool:
    """テキストが人名らしいか（2〜4 語、各語が大文字始まり、一般語/役職語を含まない）。

    人名を捏造しないための門番。ナビゲーション文言や役職を名前と誤認しない。
    """
    if not text:
        return False
    name = " ".join(text.split()).strip(" .,-")
    if not name or len(name) > 60:
        return False
    if any(ch.isdigit() for ch in name):
        return False
    if not _PERSON_NAME_RE.match(name):
        return False
    tokens = [t.lower().strip(".,'-") for t in name.split()]
    if any(t in _NON_NAME_WORDS for t in tokens):
        return False
    if any(t in _TITLE_WORDS for t in tokens):
        return False
    return True


def compute_confidence(
    *, has_name: bool, has_linkedin: bool, has_email: bool, has_known_title: bool
) -> int:
    """担当者候補の信頼度（0〜100）。LinkedIn/メール/既知役職で上昇。"""
    score = 35 if has_name else 10
    if has_linkedin:
        score += 35  # 要件：LinkedIn URL があれば confidence 上昇
    if has_email:
        score += 20
    if has_known_title:
        score += 15
    return max(0, min(100, score))


def get_contact_hunter() -> ContactHunter:
    if settings.anthropic_api_key:
        from app.ai.claude_contact_hunter import ClaudeContactHunter

        return ClaudeContactHunter(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from app.ai.mock_contact_hunter import MockContactHunter

    return MockContactHunter()
