"""AI Search Agent の業務ロジック（反復探索オーケストレータ）。

AI（Claude / モック）は各ステップで「次に取得する URL・実行する検索クエリ・理由・
続行/終了」を判断するだけ。実際の取得・検索・抽出・フィルタは本 service が安全に
実行する。SNS プロフィール → Linktree 等のリンク集 → 公式サイト → Contact のように
リンクを辿って連絡先を掘り下げる。

安全制限：最大 5 ステップ / 20 URL / 20 クエリ / 1 URL 12 秒 / ログイン必須ページは
スキップ / platform 公式メールは除外 / 推測メール禁止 / 出典 URL なしメール禁止。

結果は最新 ContactDiscovery 行の search_agent_* に分離保存する（既存レイヤーを
無条件上書きしない）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.ai.search_agent import (
    FETCH_TIMEOUT,
    MAX_QUERIES,
    MAX_STEPS,
    MAX_URLS,
    STEP_QUERY_BUDGET,
    STEP_URL_BUDGET,
    SearchAgentState,
    get_search_agent,
)
from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.services import contact_discovery_service as cds
from app.services import document_reader_service as drs
from app.services import web_research_service as wr

logger = logging.getLogger("search_agent")

# リンク集（Linktree 等）のホスト。ここに外部公式サイト/SNS が集まる。
LINK_HUB_HOSTS = (
    "linktr.ee", "linktree", "beacons.ai", "beacons.page", "bio.site",
    "carrd.co", "lit.link", "link.bio", "allmylinks.com", "withkoji.com",
    "tap.bio", "campsite.bio", "solo.to", "many.link", "msha.ke",
)

# SNS/動画ホスト。ログイン必須・JS ノイズ（バンドル JS 由来のライブラリ作者メール等）
# が多いため巡回しない。SNS URL は「発見」として記録するが本文はクロールしない。
_SOCIAL_VIDEO_HOSTS = (
    "instagram.com", "facebook.com", "fb.com", "twitter.com", "x.com",
    "tiktok.com", "youtube.com", "youtu.be", "linkedin.com", "pinterest.com",
    "reddit.com",
)
# 公式サイト/メール抽出の対象にしないインフラ/解析/CDN ホスト。
_INFRA_HOSTS = (
    "cloudfront", "akamai", "gstatic", "googletagmanager", "google-analytics",
    "doubleclick", "fbcdn", "ksr-static", "ksr.io", "kck.st", "stripe.",
    "segment.", "siftscience", "sk-diagnostics", "transcend-cdn", "onetrust",
)
# プラットフォーム別の正しいホスト（youtube の説明欄リダイレクト等の誤分類を防ぐ）
_PLATFORM_HOSTS = {
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com"),
    "twitter": ("twitter.com", "x.com"),
    "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com",),
}

_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _is_social_video(url: str) -> bool:
    return any(h in _host(url) for h in _SOCIAL_VIDEO_HOSTS)


def _host_matches_platform(url: str, platform: str) -> bool:
    hosts = _PLATFORM_HOSTS.get(platform)
    return bool(hosts and any(h in _host(url) for h in hosts))


def _is_email_trusted_page(url: str) -> bool:
    """メール/公式サイトを抽出してよいページか（SNS/動画/運営/CDN は信頼しない）。"""
    if _is_social_video(url) or cds.is_platform_url(url):
        return False
    if any(c in _host(url) for c in _INFRA_HOSTS):
        return False
    return True


def _make_agent_fetcher():
    """1 URL 12 秒のフェッチャ（Cloudflare/JS 対策のため設定済み fetcher を使う）。"""
    from app.config import settings

    method = getattr(settings, "scrape_fetcher", "httpx") or "httpx"
    try:
        from app.scrapers.fetcher import get_fetcher

        client = get_fetcher(method, rate_limit_seconds=1.0, timeout=FETCH_TIMEOUT, retries=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("search agent fetcher init failed (%s); httpx fallback", exc)
        from app.scrapers.http import HttpClient

        client = HttpClient(rate_limit_seconds=1.0, timeout=FETCH_TIMEOUT, retries=1)

    def fetch(url: str) -> str | None:
        try:
            return client.get_text(url)
        except Exception as exc:  # noqa: BLE001
            logger.info("search agent fetch failed (%s): %s", url, exc)
            return None

    fetch._client = client  # type: ignore[attr-defined]
    return fetch


def _is_link_hub(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in LINK_HUB_HOSTS)


def _initial_state(project: Project, row: ContactDiscovery) -> SearchAgentState:
    official = (
        cds.official_site_or_none(row.official_site_url if row else None)
        or cds.official_site_or_none(project.maker_url)
        or ""
    )
    socials: dict[str, str] = {}
    emails: list[dict] = []
    forms: list[str] = []
    candidates: list[str] = []

    def add_cand(u: str | None) -> None:
        if u and u.startswith(("http://", "https://")) and u not in candidates:
            candidates.append(u)

    if row is not None:
        for src in (row.web_discovered_socials, row.doc_reader_socials):
            # 既存 SNS も正規化＋運営SNS除外を通して取り込む
            for k, v in drs._validate_socials(src or {}).items():
                if v and not socials.get(k):
                    socials[k] = v
        for e in (row.web_discovered_emails or []) + (row.discovered_emails or []):
            if isinstance(e, dict) and e.get("email") and e.get("email_owner") != "platform":
                s = (e.get("sources") or [""])[0]
                if not any(x["email"].lower() == e["email"].lower() for x in emails):
                    emails.append({"email": e["email"], "source_url": s or ""})
        forms = [f for f in (row.web_discovered_forms or []) if isinstance(f, str)]
        for v in socials.values():
            add_cand(v)
        for u in (row.web_searched_urls or [])[:6]:
            add_cand(u)
    add_cand(project.source_url)
    add_cand(project.maker_url)

    return SearchAgentState(
        title=project.title or "",
        maker_name=project.maker_name or "",
        source_site=project.source_site or "",
        source_url=project.source_url or "",
        maker_url=project.maker_url or "",
        description_clean=(project.description_clean or project.description or "")[:1500],
        official_site_url=official,
        emails=emails,
        socials=socials,
        forms=forms,
        candidate_urls=candidates,
    )


def _add_candidate(state: SearchAgentState, url: str) -> None:
    if not url or not url.startswith(("http://", "https://")):
        return
    if wr._is_skip_url(url):
        return
    if url in state.visited_urls or url in state.candidate_urls:
        return
    state.candidate_urls.append(url)


def _extract_into_state(
    state: SearchAgentState, url: str, html: str, terms: set[str], site_domain
) -> dict:
    """1 ページから連絡先・SNS・候補リンクを state に取り込む。

    メール・公式サイトは「信頼できるページ」（SNS/動画/運営/CDN でない）からのみ抽出し、
    メールはバンドル JS 由来のライブラリ作者メール等を避けるため <script>/<style> を
    除去した HTML から抽出する（ノイズ・誤採用の防止）。
    """
    found = {"emails": 0, "socials": 0, "forms": 0, "links": 0}
    trusted = _is_email_trusted_page(url)
    hub = _is_link_hub(url)

    if trusted:
        clean_html = _SCRIPT_STYLE_RE.sub(" ", html or "")
        for addr in cds.extract_emails(clean_html, site_domain):
            if not any(e["email"].lower() == addr.lower() for e in state.emails):
                state.emails.append({"email": addr, "source_url": url})
                found["emails"] += 1
        if cds._is_contact_url(url) and url not in state.forms:
            state.forms.append(url)
            found["forms"] += 1

    # 公式サイトは信頼ページ or リンク集ページからのみ推定（SNS/動画から拾わない）
    if not state.official_site_url and (trusted or hub):
        cand = cds.extract_official_link(html, url, terms)
        if cand:
            state.official_site_url = cand

    # SNS リンクは記録するが本文はクロールしない（候補には入れない）
    for lk in cds.extract_links(html, url):
        plat = wr._social_platform(lk)
        if plat:
            key = "twitter" if plat == "x" else plat
            # youtube 説明欄のリダイレクト等、ホストが一致しない誤分類は捨てる
            if not _host_matches_platform(lk, key):
                continue
            norm = wr._normalize_social(key, lk)
            if not norm or wr._is_platform_social_handle(key, norm):
                continue
            if any(h in urlparse(norm).path.lower() for h in wr._PLATFORM_SOCIAL_HANDLES):
                continue
            store_key = plat  # instagram/facebook/linkedin/youtube/tiktok/twitter
            if not state.socials.get(store_key):
                state.socials[store_key] = norm
                found["socials"] += 1
            continue
        if cds.is_platform_url(lk) or any(c in _host(lk) for c in _INFRA_HOSTS):
            continue
        # リンク集ページの外部リンク／リンク集そのもの → 有力候補として辿る
        if hub or any(h in _host(lk) for h in LINK_HUB_HOSTS):
            _add_candidate(state, lk)
            found["links"] += 1
        else:
            # 公式ドメインの下位ページ（Contact 等）だけ候補にする
            off_domain = cds._domain_of(state.official_site_url)
            if off_domain and cds._same_domain(lk, off_domain) and cds._is_contact_url(lk):
                _add_candidate(state, lk)
    return found


def _finalize(project: Project, row: ContactDiscovery, state: SearchAgentState) -> dict:
    official = cds.official_site_or_none(state.official_site_url) or None
    official_domain = cds._domain_of(official)
    site_domain = cds.source_site_email_domain(getattr(project, "source_site", None))

    emails: list[dict] = []
    seen: set[str] = set()
    for e in state.emails:
        addr = str(e.get("email", "")).strip()
        src = str(e.get("source_url", "")).strip()
        if not addr or "@" not in addr or not src:
            continue  # 出典なしメールは採用しない（捏造防止）
        if cds.email_exclusion_reason(addr, site_domain):
            continue
        owner = cds.classify_email_owner(addr, official_domain or None, site_domain)
        if owner == "platform":
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        rk = cds.rank_sales_email(addr, email_owner=owner)
        emails.append({
            "email": addr,
            "purpose": rk["category"],
            "confidence": rk["stars"] * 20,
            "source_url": src,
            "reason": rk["reason"],
            "email_owner": owner,
        })
    emails.sort(key=lambda e: e["confidence"], reverse=True)

    socials = state.socials
    forms = [{"url": f, "confidence": 80, "source_url": f} for f in state.forms]
    people = state.people
    recommended_contact = emails[0]["email"] if emails else None

    if recommended_contact:
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

    score = drs._score(emails, forms, socials, official, people)

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
        "探索で" + "・".join(found_bits) + "を発見しました。"
        if found_bits
        else "探索では有効な連絡先を確認できませんでした。"
    )
    return {
        "official_site_url": official,
        "emails": emails,
        "contact_forms": forms,
        "socials": socials,
        "people": people,
        "recommended_channel": channel,
        "recommended_contact": recommended_contact,
        "confidence_score": score,
        "evidence_summary": evidence,
    }


def run_search_agent(
    db: Session, project: Project, *, agent=None, fetch_fn=None, search_fn=None
) -> ContactDiscovery:
    """AI Search Agent を反復実行し、結果を search_agent_* に保存する。"""
    agent = agent or get_search_agent()
    row = cds.get_latest(db, project.id)
    if row is None:
        row = cds.run_discovery(db, project)

    site_domain = cds.source_site_email_domain(getattr(project, "source_site", None))
    terms = cds.significant_terms(project.title, project.maker_name)
    platform_seed_ok = {project.source_url or "", project.maker_url or ""}

    own_fetch = fetch_fn is None
    own_search = search_fn is None
    fetch = fetch_fn or _make_agent_fetcher()
    if own_search:
        from app.services import search_providers

        search = search_providers.get_search_fn()
    else:
        search = search_fn

    now = datetime.now(timezone.utc)
    steps: list[dict] = []
    visited: list[str] = []
    ran: list[str] = []
    stop_reason = "最大ステップ数に到達したため終了しました。"
    pid = getattr(project, "id", "?")
    try:
        state = _initial_state(project, row)
        for step_i in range(MAX_STEPS):
            state.step = step_i + 1
            plan = agent.plan(state)
            logger.info(
                "search_agent[%s] step %d: stop=%s urls=%d queries=%d reason=%s",
                pid, state.step, plan.stop, len(plan.next_urls),
                len(plan.next_queries), plan.reason,
            )
            if plan.stop:
                stop_reason = plan.reason or "AI が探索終了を判断しました。"
                steps.append({
                    "step": state.step, "action": "stop",
                    "reason": stop_reason, "missing": plan.missing,
                })
                break
            if not plan.next_urls and not plan.next_queries:
                stop_reason = "調査すべき URL・検索クエリが無くなったため終了しました。"
                steps.append({"step": state.step, "action": "stop", "reason": stop_reason})
                break

            # 検索クエリを実行して候補 URL を増やす
            for q in plan.next_queries[:STEP_QUERY_BUDGET]:
                if len(ran) >= MAX_QUERIES or q in ran:
                    continue
                ran.append(q)
                state.ran_queries.append(q)
                try:
                    results = search(q) or []
                except Exception as exc:  # noqa: BLE001
                    logger.info("search error (%s): %s", q, exc)
                    results = []
                for item in results[:6]:
                    u = item.get("url") if isinstance(item, dict) else item
                    _add_candidate(state, str(u or ""))
                # 検索の診断（provider/status/reason/fallback）をステップに記録
                diag = (getattr(search, "diagnostics", None) or [{}])[-1]
                steps.append({
                    "step": state.step, "action": "search", "query": q,
                    "results": len(results), "reason": plan.reason,
                    "search_provider": diag.get("provider"),
                    "search_status": diag.get("status"),
                    "search_detail": diag.get("reason"),
                    "search_fallback": diag.get("fallback"),
                })

            # URL を取得して連絡先・候補リンクを抽出
            for url in plan.next_urls[:STEP_URL_BUDGET]:
                if len(visited) >= MAX_URLS or url in visited:
                    continue
                if wr._is_skip_url(url):
                    steps.append({"step": state.step, "action": "skip", "url": url,
                                  "reason": "ログイン/カート等はスキップ"})
                    continue
                if cds.is_platform_url(url) and url not in platform_seed_ok:
                    steps.append({"step": state.step, "action": "skip", "url": url,
                                  "reason": "プラットフォームURLはスキップ"})
                    continue
                if _is_social_video(url):
                    # SNS/動画ページはログイン必須・JS ノイズが多く本文を巡回しない
                    # （SNS は発見済みとして記録済み）。
                    steps.append({"step": state.step, "action": "skip", "url": url,
                                  "reason": "SNS/動画ページは巡回しない（ログイン/ノイズ回避）"})
                    continue
                visited.append(url)
                state.visited_urls.append(url)
                if url in state.candidate_urls:
                    state.candidate_urls.remove(url)
                html = fetch(url)
                if not html:
                    steps.append({"step": state.step, "action": "visit", "url": url,
                                  "ok": False})
                    continue
                found = _extract_into_state(state, url, html, terms, site_domain)
                steps.append({"step": state.step, "action": "visit", "url": url,
                              "ok": True, "found": found, "reason": plan.reason})

        result = _finalize(project, row, state)
        row.search_agent_researched = True
        row.search_agent_researched_at = now
        row.search_agent_model = getattr(agent, "name", "search-agent")
        row.search_agent_status = "completed"
        row.search_agent_steps = steps or None
        row.search_agent_searched_queries = ran or None
        row.search_agent_searched_urls = visited or None
        row.search_agent_official_site_url = result["official_site_url"]
        row.search_agent_emails = result["emails"] or None
        row.search_agent_contact_forms = result["contact_forms"] or None
        row.search_agent_socials = result["socials"] or None
        row.search_agent_people = result["people"] or None
        row.search_agent_recommended_channel = result["recommended_channel"]
        row.search_agent_recommended_contact = result["recommended_contact"]
        row.search_agent_confidence_score = result["confidence_score"]
        row.search_agent_evidence_summary = result["evidence_summary"]
        row.search_agent_stop_reason = stop_reason
        row.search_agent_error = None
    except Exception as exc:  # noqa: BLE001  失敗してもアプリは落とさない
        logger.warning("search agent failed (project=%s): %s", project.id, exc)
        row.search_agent_researched = True
        row.search_agent_researched_at = now
        row.search_agent_status = "failed"
        row.search_agent_error = str(exc)[:4000]
    finally:
        if own_fetch:
            client = getattr(fetch, "_client", None)
            if client is not None:
                client.close()
        if own_search:
            if hasattr(search, "close"):
                search.close()
            else:
                client = getattr(search, "_client", None)
                if client is not None:
                    client.close()

    db.commit()
    db.refresh(row)
    return row
