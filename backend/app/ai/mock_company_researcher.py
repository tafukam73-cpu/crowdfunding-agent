"""モック企業リサーチャー。

外部 API を使わず、案件・メーカー情報から「それらしい」リサーチ結果を組み立てる。
Claude 未設定時でも画面・DB・メール連携を確認できるようにするためのもの。

結果は personalization のヘルパー（プラットフォーム名・達成率・カテゴリ強調など）を
再利用し、案件ごとに内容が変わるようにする。
"""
from __future__ import annotations

from app.ai.company_researcher import CompanyResearcher, ResearchResult
from app.ai.personalization import (
    _funding_rate,
    _functional_word,
    _impressive_points,
    _key_features,
    _money,
    platform_label,
)
from app.ai.prompts import emphasis_for
from app.models.project import Project


class MockCompanyResearcher(CompanyResearcher):
    name = "mock-research-v1"

    def research(self, project: Project) -> ResearchResult:
        maker = project.maker_name or "the maker"
        title = project.title
        platform = platform_label(project)
        emphasis = emphasis_for(project.category)
        functional = _functional_word(project.category)
        rate = _funding_rate(project)
        money = _money(project)
        features = _key_features(project)
        impressive = _impressive_points(project)

        brand_summary = (
            f"{maker} is the team behind {title}, an emerging brand that brought a "
            f"focused product to market through a {platform} campaign. Their work "
            f"centers on {functional}."
        )
        company_mission = (
            f"To turn a clear product insight into well-designed, dependable products "
            f"that resonate with everyday users — as reflected in how they positioned "
            f"{title}."
        )
        product_summary = (
            project.description
            or f"{title} is a {project.category or 'crowdfunding'} product that pairs "
            f"{functional} with a clear, easy-to-grasp value proposition."
        )

        brand_strengths = [
            f"Proven crowdfunding execution on {platform}",
            f"A product story built around {functional}",
        ]
        if money:
            brand_strengths.append(f"Demonstrated market demand ({money} raised)")

        differentiation = [
            f"Distinctive take on {emphasis.split(',')[0].strip()}",
            "Clear, backer-friendly presentation",
        ]
        if project.video_url:
            differentiation.append("Strong visual storytelling (campaign video)")

        japan_market_fit = (
            f"Japanese early adopters tend to value {emphasis}. {title} aligns with "
            f"that, and a Makuake or GreenFunding launch could introduce it to backers "
            "who actively seek out original overseas products."
        )

        compliment = (
            f"We were genuinely impressed by how {maker} shaped {title} around "
            f"{functional}, and by the traction it earned on {platform}"
            + (f" ({rate:,}% of goal)" if rate and rate >= 100 else "")
            + "."
        )

        outreach_angles = [
            f"Lead with specific admiration for {title} and its {platform} results",
            "Explain why the product fits Japanese early adopters",
            "Offer a Makuake / GreenFunding launch and exclusive Japan distribution",
            "Propose a short online meeting",
        ]

        risks = [
            "Avoid over-claiming or inventing facts not visible on the campaign page",
            "Don't assume Japanese certification (e.g., PSE) is already handled",
        ]
        if not project.maker_url:
            risks.append(
                "Official site was not available; rely on campaign-page information only"
            )

        sources = [
            url
            for url in (project.source_url, project.maker_url)
            if url
        ] or ["(no external page available; inferred from campaign data)"]

        notes_bits = [f"platform={platform}"]
        if money:
            notes_bits.append(f"raised={money}")
        if rate is not None:
            notes_bits.append(f"funding_rate={rate}%")
        raw_notes = "Mock research generated from campaign data. " + ", ".join(notes_bits)

        return ResearchResult(
            maker_name=project.maker_name or "",
            official_site_url=project.maker_url or "",
            project_url=project.source_url or "",
            brand_summary=brand_summary,
            company_mission=company_mission,
            product_summary=product_summary,
            key_product_features=features,
            brand_strengths=brand_strengths,
            differentiation_points=differentiation,
            japan_market_fit=japan_market_fit,
            personalized_compliment=compliment,
            outreach_angles=outreach_angles,
            risks_or_cautions=risks,
            sources=sources,
            raw_notes=raw_notes,
            model=self.name,
        )
