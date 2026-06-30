"""モック AI 連絡先リサーチャー。

外部 API を使わず、既存の探索結果（メール・SNS・フォーム）と案件情報から、
営業に使える連絡先候補を「整理」する。Claude 未設定時でも UI・DB・営業/Gmail
連携を確認できるようにするためのもの。

重要：メールアドレスを推測で捏造しない。候補メールは「既存探索で実際に見つかった
（出典 URL を持つ）メール」だけを昇格させる。出典の無いメールは作らない。
メールが見つからない場合は、問い合わせフォーム・SNS・検索クエリを推奨として返す。
"""
from __future__ import annotations

from app.ai.contact_researcher import (
    AiCandidateEmail,
    ContactResearchContext,
    ContactResearcher,
    ContactResearchResult,
)

# 既存探索メールの tier → confidence の対応
_TIER_CONFIDENCE = {"high": "high", "mid": "medium", "other": "medium", "low": "low"}

_SNS_LABELS = {
    "instagram": "Instagram",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "twitter": "X / Twitter",
    "youtube": "YouTube",
}


class MockContactResearcher(ContactResearcher):
    name = "mock-contact-research-v1"

    def research(self, ctx: ContactResearchContext) -> ContactResearchResult:
        brand = (ctx.maker_name or ctx.title or "").strip()

        # 1. 既存探索で見つかった（出典付き）メールのみを候補に昇格（捏造しない）
        candidates: list[AiCandidateEmail] = []
        for e in ctx.existing_candidate_emails:
            email = str(e.get("email", "")).strip()
            if not email:
                continue
            sources = e.get("sources") or []
            source_url = sources[0] if sources else ""
            if not source_url:
                # 出典が無いものは採用しない
                continue
            tier = str(e.get("tier", "other"))
            candidates.append(
                AiCandidateEmail(
                    email=email,
                    score=int(e.get("score", 50) or 50),
                    confidence=_TIER_CONFIDENCE.get(tier, "medium"),
                    reason=(
                        "公式サイト/探索ページに実在が確認できたメール"
                        f"（{tier}）。出典あり。"
                    ),
                    source_url=source_url,
                )
            )
        candidates.sort(key=lambda c: c.score, reverse=True)
        primary_email = candidates[0].email if candidates else None

        # 2. 出典の整理（実在が確認できている URL のみ）
        sources: list[dict] = []
        seen_src: set[str] = set()

        def add_source(url: str | None, type_: str, note: str) -> None:
            if url and url not in seen_src:
                seen_src.add(url)
                sources.append({"url": url, "type": type_, "note": note})

        add_source(ctx.official_site_url, "official_site", "公式サイト")
        add_source(ctx.primary_contact_form_url, "contact_form", "問い合わせフォーム")
        for platform, url in (ctx.discovered_socials or {}).items():
            add_source(url, f"social_{platform}", _SNS_LABELS.get(platform, platform))
        add_source(ctx.source_url, "crowdfunding", "クラファン案件ページ")
        for s in ctx.company_sources:
            add_source(s, "company_research", "企業リサーチ参照元")

        # 3. 検索クエリ候補（既存の探索クエリを引き継ぎ、ブランド名で補強）
        queries: list[str] = list(ctx.search_queries or [])
        seen_q = set(queries)

        def add_query(q: str) -> None:
            if q and q not in seen_q:
                seen_q.add(q)
                queries.append(q)

        if brand:
            add_query(f'"{brand}" partnership email')
            add_query(f'"{brand}" distributor contact')
            add_query(f'"{brand}" wholesale inquiry')
            add_query(f'"{brand}" press contact')

        socials = ctx.discovered_socials or {}

        # 4. 推奨チャネル（メール→フォーム→SNS の順で実在するものを推奨）
        if primary_email:
            recommended = "email"
        elif ctx.primary_contact_form_url:
            recommended = "contact_form"
        elif socials.get("linkedin"):
            recommended = "linkedin"
        elif socials.get("instagram"):
            recommended = "instagram"
        elif socials.get("facebook"):
            recommended = "facebook"
        else:
            recommended = "manual_research"

        # 5. 確度（メール有無・チャネルの充実度で素朴に算出）
        if primary_email:
            confidence = max((c.score for c in candidates), default=60)
        elif ctx.primary_contact_form_url:
            confidence = 55
        elif socials:
            confidence = 40
        else:
            confidence = 20

        # 6. メモ（メール未発見でも次の一手が分かる説明）
        if primary_email:
            notes = (
                f"出典付きのメール {primary_email} を主要連絡先候補として利用できます。"
                "推測メールは作成していません。"
            )
        else:
            found = [
                _SNS_LABELS[k] for k in _SNS_LABELS if socials.get(k)
            ]
            if ctx.primary_contact_form_url:
                found.insert(0, "問い合わせフォーム")
            if found:
                notes = (
                    "メールアドレスは確認できませんでした（推測では作成しません）。"
                    + "・".join(found)
                    + "が利用可能なので、これらのチャネルでの営業を推奨します。"
                    "あわせて検索クエリで担当者メールを手動リサーチしてください。"
                )
            else:
                notes = (
                    "メール・フォーム・SNS のいずれも確認できませんでした。"
                    "提示した検索クエリで公式サイト・LinkedIn・代理店情報を手動リサーチ"
                    "してください（推測メールは作成しません）。"
                )

        return ContactResearchResult(
            primary_email=primary_email,
            candidate_emails=candidates,
            contact_form_url=ctx.primary_contact_form_url or None,
            instagram_url=socials.get("instagram"),
            facebook_url=socials.get("facebook"),
            linkedin_url=socials.get("linkedin"),
            recommended_channel=recommended,
            confidence_score=confidence,
            search_queries=queries,
            sources=sources,
            notes=notes,
            model=self.name,
        )
