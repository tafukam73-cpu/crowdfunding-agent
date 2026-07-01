"""モック AI Document Reader。

外部 API を使わず、service が渡した「実ページの本文・リンク・抽出済みメール/SNS・
検索スニペット」だけから、会社名・公式サイト・メール・SNS・問い合わせフォーム・
担当者候補を整理する。Claude 未設定時でも UI・DB・営業/Gmail/CRM 連携を確認できる。

重要：メール・人名を推測で捏造しない。
- メールは渡されたページ本文/抽出済みメールに実在するものだけ（source_url 付き）。
- 人名は確実な抽出が難しいため、モックでは返さない（空配列）。捏造しない。
"""
from __future__ import annotations

import re

from app.ai.document_reader import (
    DocReaderEmail,
    DocumentReader,
    DocumentReaderContext,
    DocumentReaderResult,
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# ローカル部 → 用途と確度（rank と整合。捏造ではなく分類）。
_PURPOSE = [
    (("hello", "contact", "info", "inquiry", "enquir"), "general_contact", 85),
    (("sales", "partnership", "partner", "business", "bd", "b2b",
      "distribution", "distributor", "wholesale", "export", "international"),
     "sales", 80),
    (("support", "help", "service", "customer", "care"), "support", 55),
    (("press", "media", "pr", "marketing"), "press", 45),
]


def _purpose_of(email: str) -> tuple[str, int]:
    local = email.split("@", 1)[0].lower()
    for prefixes, purpose, conf in _PURPOSE:
        if any(local == p or local.startswith(p) for p in prefixes):
            return purpose, conf
    return "other", 50


class MockDocumentReader(DocumentReader):
    name = "mock-document-reader-v1"

    def read(self, ctx: DocumentReaderContext) -> DocumentReaderResult:
        # 1. 渡されたページ本文/リンク/抽出済みメールから実在メールを集める（出典付き）
        email_src: dict[str, str] = {}  # email_lower -> source_url

        def note_email(email: str, src: str) -> None:
            e = email.strip().strip(".")
            key = e.lower()
            if "@" in e and key not in email_src:
                email_src[key] = src

        for pg in ctx.pages:
            for e in pg.emails:
                note_email(e, pg.url)
            for m in _EMAIL_RE.findall(pg.text or ""):
                note_email(m, pg.url)
        # 既存抽出メール（出典はページ群から拾えなければ空）
        for e in ctx.existing_emails:
            note_email(e, ctx.official_site_url or ctx.source_url or "")

        emails: list[DocReaderEmail] = []
        for key, src in email_src.items():
            purpose, conf = _purpose_of(key)
            emails.append(
                DocReaderEmail(
                    email=key,
                    purpose=purpose,
                    confidence=conf,
                    source_url=src,
                    reason="渡したページ本文/リンクに実在（出典あり）",
                )
            )
        emails.sort(key=lambda e: e.confidence, reverse=True)

        # 2. SNS（既存 + ページ抽出。captured URL のみ、捏造しない）
        socials: dict[str, str | None] = {}
        for src in (ctx.existing_socials, *[p.socials for p in ctx.pages]):
            for k, v in (src or {}).items():
                if v and not socials.get(k):
                    socials[k] = v
        for k in ("instagram", "facebook", "linkedin", "youtube", "tiktok", "x"):
            socials.setdefault(k, None)

        # 3. 問い合わせフォーム（contact 系ページ。クラファン/プラットフォーム URL は
        #    企業の問い合わせフォームではないため除外する）。
        from app.services.contact_discovery_service import is_platform_url

        forms: list[dict] = []
        seen_forms: set[str] = set()
        for pg in ctx.pages:
            low = pg.url.lower()
            if is_platform_url(pg.url):
                continue
            if ("contact" in low or "inquiry" in low) and pg.url not in seen_forms:
                seen_forms.add(pg.url)
                forms.append({"url": pg.url, "confidence": 80, "source_url": pg.url})

        # 4. 公式サイト・会社名・ブランド名（渡された情報のみ。推測しない）
        official = ctx.official_site_url or None
        company = (ctx.maker_name or "").strip() or None
        brands = [ctx.maker_name] if ctx.maker_name else []

        # 5. 推奨チャネル/連絡先
        recommended_contact = emails[0].email if emails else None
        if emails:
            channel = "email"
        elif forms:
            channel = "contact_form"
        elif socials.get("linkedin"):
            channel = "linkedin"
        elif socials.get("instagram"):
            channel = "instagram"
        elif socials.get("facebook"):
            channel = "facebook"
        else:
            channel = "manual_search"

        # 6. スコアリング（要件のルール）
        score = _score(bool(emails), bool(forms), socials, bool(official), people=False)

        # 7. 証拠サマリ・不足情報
        found_bits = []
        if emails:
            found_bits.append(f"メール{len(emails)}件")
        if forms:
            found_bits.append("問い合わせフォーム")
        for k in ("instagram", "facebook", "linkedin"):
            if socials.get(k):
                found_bits.append(k.capitalize())
        if official:
            found_bits.append("公式サイト")
        evidence = (
            "渡されたページから" + "・".join(found_bits) + "を整理しました。"
            if found_bits
            else "渡されたページからは有効な連絡先を確認できませんでした。"
        )
        missing = []
        if not emails:
            missing.append("公開メールアドレスは未発見（推測では作成しません）")
        if not any(socials.get(k) for k in ("instagram", "facebook", "linkedin")):
            missing.append("主要 SNS（Instagram/Facebook/LinkedIn）が未発見")
        missing.append("担当者名はモックでは抽出しません（Claude 設定時に対応）")

        # 8. 参照ページ
        sources = [
            {"url": p.url, "type": p.page_type or "page", "note": p.title or ""}
            for p in ctx.pages
        ]

        return DocumentReaderResult(
            official_company_name=company,
            brand_names=brands,
            official_site_url=official,
            emails=emails,
            contact_forms=forms,
            socials=socials,
            people=[],  # 捏造しない
            recommended_channel=channel,
            recommended_contact=recommended_contact,
            confidence_score=score,
            evidence_summary=evidence,
            missing_info=missing,
            sources=sources,
            model=self.name,
        )


def _score(
    has_email: bool, has_form: bool, socials: dict, has_official: bool, people: bool
) -> int:
    """要件のスコアリング（有効メール+40 / フォーム+25 / 主要SNS+15 / 公式+10 / 担当者+15）。"""
    s = 0
    if has_email:
        s += 40
    if has_form:
        s += 25
    if any(socials.get(k) for k in ("instagram", "facebook", "linkedin")):
        s += 15
    if has_official:
        s += 10
    if people:
        s += 15
    return min(100, s)
