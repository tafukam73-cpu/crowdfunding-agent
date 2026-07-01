"""AI Document Reader の業務ロジック。

Web Research が到達したページ（クラファン案件・Creator/Maker プロフィール・公式
サイト・Contact/About/Team/Press/Wholesale/Distributor/FAQ・PDF）の本文/リンク/
抽出済みメール・SNS・検索スニペットを集め、AI（Claude / モック）に読解させて
会社名・公式サイト・メール・SNS・フォーム・担当者候補を整理する。

安全設計：
- AI が返したメール/人名は必ず既存フィルタ（email_exclusion_reason / platform 除外 /
  出典必須）で再検証する。推測メール・人名は採用しない。
- SNS は正規化し、運営（platform）自身の公式アカウントは除外する。
- 公式サイトはクラファン/プラットフォーム URL を採用しない。
- 手動実行（API コスト）。入力は 1 ページ最大 PAGE_TEXT_MAX 文字、全体上限あり。

結果は最新の ContactDiscovery 行の doc_reader_* に分離保存する（自動抽出 / AI 調査 /
Web 調査を無条件上書きしない）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.ai.document_reader import (
    DocReaderPage,
    DocumentReaderContext,
    PAGE_TEXT_MAX,
    get_document_reader,
)
from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.services import contact_discovery_service as cds
from app.services import web_research_service as wr

logger = logging.getLogger("document_reader")

MAX_PAGES = 8          # AI に渡す最大ページ数（要件 5〜10）
MAX_LINKS_PER_PAGE = 30

# ページ種別の優先度（小さいほど優先して AI に渡す）
_TYPE_PRIORITY = {
    "official_site": 1,
    "contact": 2,
    "about": 3,
    "press": 4,
    "wholesale": 5,
}


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _gather_pages(project: Project, row: ContactDiscovery, fetch_fn) -> list[DocReaderPage]:
    """AI に渡す重要ページを選び、本文・リンク・メール・SNS を付けて返す。"""
    official = cds.official_site_or_none(
        row.official_site_url if row else None
    ) or cds.official_site_or_none(project.maker_url)
    official_domain = cds._domain_of(official)
    site_domain = cds.source_site_email_domain(getattr(project, "source_site", None))

    seen: set[str] = set()
    candidates: list[tuple[int, str]] = []

    def add(url: str | None, prio: int) -> None:
        if url and url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            candidates.append((prio, url))

    add(official, 0)
    # Web Research が実際に取得できたページ（type 優先度順）
    for p in (row.web_candidate_pages or []) if row else []:
        if isinstance(p, dict) and p.get("ok") is not False:
            add(p.get("url"), _TYPE_PRIORITY.get(str(p.get("type", "")), 7))
    # クラファン案件ページ・Creator/Maker プロフィール
    add(project.source_url, 6)
    add(project.maker_url, 6)
    # 自動抽出の探索 URL / Web 調査の探索 URL
    for u in (row.searched_urls or []) if row else []:
        add(u, 8)
    for u in (row.web_searched_urls or []) if row else []:
        add(u, 9)

    candidates.sort(key=lambda t: t[0])
    urls = [u for _, u in candidates][:MAX_PAGES]

    own_fetcher = fetch_fn is None
    fetch = fetch_fn or wr._make_fetcher()
    pages: list[DocReaderPage] = []
    try:
        for url in urls:
            html = fetch(url)
            if not html:
                continue
            text = _html_to_text(html)[:PAGE_TEXT_MAX]
            emails = cds.extract_emails(html, site_domain)
            # 運営（platform）自身の SNS は除外して正規化（AI に混ぜない）
            socials = _validate_socials(cds.extract_socials(html, url))
            links = cds.extract_links(html, url)[:MAX_LINKS_PER_PAGE]
            title = ""
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
            if m:
                title = re.sub(r"\s+", " ", _html_to_text(m.group(1)))[:200]
            pages.append(
                DocReaderPage(
                    url=url,
                    title=title,
                    page_type=wr._page_type(url, official_domain or None),
                    text=text,
                    links=links,
                    emails=emails,
                    socials=socials,
                )
            )
    finally:
        if own_fetcher:
            client = getattr(fetch, "_client", None)
            if client is not None:
                client.close()
    return pages


def _build_context(
    project: Project, row: ContactDiscovery, pages: list[DocReaderPage]
) -> DocumentReaderContext:
    existing_emails: list[str] = []
    for e in (row.web_discovered_emails or []) + (row.discovered_emails or []):
        if isinstance(e, dict) and e.get("email") and e.get("email_owner") != "platform":
            if e["email"] not in existing_emails:
                existing_emails.append(e["email"])
    existing_socials = dict(row.web_discovered_socials or row.discovered_socials or {})
    snippets = [
        {
            "query": r.get("query", ""),
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": "",
        }
        for r in (row.web_search_results or [])
        if isinstance(r, dict) and r.get("adopted")
    ][:15]
    official = cds.official_site_or_none(
        row.official_site_url
    ) or cds.official_site_or_none(project.maker_url) or ""
    return DocumentReaderContext(
        title=project.title or "",
        maker_name=project.maker_name or "",
        source_site=project.source_site or "",
        source_url=project.source_url or "",
        maker_url=project.maker_url or "",
        description_clean=(project.description_clean or project.description or "")[:2000],
        official_site_url=official,
        pages=pages,
        existing_emails=existing_emails,
        existing_socials=existing_socials,
        pdf_texts=[],
        search_queries=(row.web_searched_queries or []) if row else [],
        search_snippets=snippets,
        platform_domain=cds.source_site_email_domain(
            getattr(project, "source_site", None)
        ) or "",
    )


def _validate_emails(emails, official_domain, site_domain) -> list[dict]:
    """AI 返却メールを既存フィルタで再検証（出典必須 / 運営・監視・no-reply 除外）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for e in emails:
        addr = (e.email or "").strip()
        if not addr or "@" not in addr:
            continue
        if not (e.source_url or "").strip():
            continue  # 出典の無い候補は捏造の疑い → 不採用
        if cds.email_exclusion_reason(addr, site_domain):
            continue
        key = addr.lower()
        if key in seen:
            continue
        owner = cds.classify_email_owner(addr, official_domain, site_domain)
        if owner == "platform":
            continue
        seen.add(key)
        out.append({
            "email": addr,
            "purpose": e.purpose or "",
            "confidence": max(0, min(100, int(e.confidence or 0))),
            "source_url": e.source_url,
            "reason": e.reason or "",
            "email_owner": owner,
        })
    out.sort(key=lambda e: e["confidence"], reverse=True)
    return out


def _validate_socials(socials) -> dict[str, str]:
    """SNS URL を正規化し、運営（platform）公式アカウントは除外する。

    ハンドル一致（instagram.com/kickstarter 等）に加え、URL パスにプラットフォーム名を
    含む運営アカウント（youtube.com/user/kickstarter 等）も除外する。
    """
    out: dict[str, str] = {}
    for plat, url in (socials or {}).items():
        if not url:
            continue
        key = "twitter" if plat == "x" else plat
        norm = wr._normalize_social(key, url)
        if not norm:
            continue
        if wr._is_platform_social_handle(key, norm):
            continue
        path = urlparse(norm).path.lower()
        if any(h in path for h in wr._PLATFORM_SOCIAL_HANDLES):
            continue
        out[plat] = norm
    return out


def _validate_people(people, site_domain) -> list[dict]:
    """AI 返却の担当者を再検証（氏名 + 出典必須。メールは検証を通したものだけ）。"""
    out: list[dict] = []
    for p in people:
        name = (p.name or "").strip()
        if not name or not (p.source_url or "").strip():
            continue
        email = (p.email or "").strip() or None
        if email and cds.email_exclusion_reason(email, site_domain):
            email = None
        out.append({
            "name": name,
            "title": p.title or "",
            "linkedin_url": p.linkedin_url or None,
            "email": email,
            "confidence": max(0, min(100, int(p.confidence or 0))),
            "source_url": p.source_url,
            "reason": p.reason or "",
        })
    return out


def _score(emails, forms, socials, official, people) -> int:
    """要件のスコアリング（メール+40 / フォーム+25 / 主要SNS+15 / 公式+10 / 担当者+15）。"""
    s = 0
    if emails:
        s += 40
    if forms:
        s += 25
    if any(socials.get(k) for k in ("instagram", "facebook", "linkedin")):
        s += 15
    if official:
        s += 10
    if people:
        s += 15
    return min(100, s)


def run_document_reader(
    db: Session, project: Project, *, reader=None, fetch_fn=None
) -> ContactDiscovery:
    """AI Document Reader を実行し、最新の探索結果の doc_reader_* に保存する。

    既存の探索結果が無ければ先に自動探索を実行して土台を作る。失敗時も
    doc_reader_evidence_summary にエラーを記録し、アプリは落とさない。
    """
    reader = reader or get_document_reader()
    row = cds.get_latest(db, project.id)
    if row is None:
        row = cds.run_discovery(db, project)

    official_domain = cds._domain_of(
        cds.official_site_or_none(row.official_site_url)
        or cds.official_site_or_none(project.maker_url)
    )
    site_domain = cds.source_site_email_domain(getattr(project, "source_site", None))
    now = datetime.now(timezone.utc)
    try:
        pages = _gather_pages(project, row, fetch_fn)
        ctx = _build_context(project, row, pages)
        result = reader.read(ctx)

        emails = _validate_emails(result.emails, official_domain or None, site_domain)
        socials = _validate_socials(result.socials)
        people = _validate_people(result.people, site_domain)
        official = cds.official_site_or_none(result.official_site_url) or (
            cds.official_site_or_none(row.official_site_url)
        )
        forms = [
            {"url": f.get("url"), "confidence": int(f.get("confidence", 0) or 0),
             "source_url": f.get("source_url", "")}
            for f in (result.contact_forms or [])
            if isinstance(f, dict) and f.get("url") and not cds.is_platform_url(f["url"])
        ]

        # 推奨連絡先/チャネル（メール→フォーム→SNS）。推奨メールは検証済みに限る。
        primary = result.recommended_contact
        valid_email_set = {e["email"].lower() for e in emails}
        if primary and primary.lower() not in valid_email_set:
            primary = None
        if not primary and emails:
            primary = emails[0]["email"]
        # チャネル正規化：検証済みデータから必ず導出する（AI の stale な値を採らない）。
        if primary:
            channel = "email"
        elif forms:
            channel = "contact_form"
        elif socials.get("linkedin"):
            channel = "linkedin"
        elif socials.get("instagram"):
            channel = "instagram"
        elif socials.get("facebook"):
            channel = "facebook"
        elif socials.get("youtube") or socials.get("tiktok"):
            channel = "manual_search"
        else:
            channel = "manual_search"

        score = _score(emails, forms, socials, official, people)

        row.doc_reader_researched = True
        row.doc_reader_researched_at = now
        row.doc_reader_model = result.model or reader.name
        row.doc_reader_official_company_name = result.official_company_name
        row.doc_reader_brand_names = result.brand_names or None
        row.doc_reader_official_site_url = official
        row.doc_reader_emails = emails or None
        row.doc_reader_contact_forms = forms or None
        row.doc_reader_socials = socials or None
        row.doc_reader_people = people or None
        row.doc_reader_recommended_channel = channel
        row.doc_reader_recommended_contact = primary
        row.doc_reader_confidence_score = score
        row.doc_reader_evidence_summary = result.evidence_summary or None
        row.doc_reader_missing_info = result.missing_info or None
        row.doc_reader_sources = result.sources or None
    except Exception as exc:  # noqa: BLE001  失敗してもアプリは落とさない
        logger.warning("document reader failed (project=%s): %s", project.id, exc)
        row.doc_reader_researched = True
        row.doc_reader_researched_at = now
        row.doc_reader_evidence_summary = f"AI Document Reader に失敗しました: {exc}"[:4000]

    db.commit()
    db.refresh(row)
    return row
