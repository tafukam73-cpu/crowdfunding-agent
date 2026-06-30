"""モック Contact Hunter（決定的な HTML 担当者抽出）。

外部 LLM を使わず、実際に取得したページ HTML から「ページ上に実在が確認できる」
担当者だけを抽出する。これにより人名の捏造を構造的に防ぐ（出典 = 取得したページ）。

抽出戦略（いずれもページ上の実テキスト由来。推測しない）：
  A) JSON-LD（schema.org Person / Organization の founder・employee）
  B) LinkedIn 個人プロフィール（/in/…）リンクのアンカーテキスト
  C) チームページの「氏名 + 役職」テキスト行（"Name, Title" / 2 行ペア）

役職 → 部署・営業優先度は title_to_priority で決定的に付与する。
クロールは AI Web Research Mode の取得・検索基盤を再利用し、Team / About /
Leadership / People 等を優先巡回する。fetch_fn / search_fn を注入できる（テスト用）。
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from urllib.parse import urlparse

from app.ai.contact_hunter import (
    ContactHunter,
    ContactHuntResult,
    PersonResult,
    compute_confidence,
    looks_like_person_name,
    title_to_priority,
)
from app.services import contact_discovery_service as cds
from app.services import web_research_service as wrs

logger = logging.getLogger("ai.mock_contact_hunter")

# 担当者が載りがちなページの代表パス（優先巡回）
TEAM_PATHS = [
    "/team",
    "/about",
    "/about-us",
    "/leadership",
    "/our-team",
    "/people",
    "/company",
    "/our-story",
    "/meet-the-team",
    "/management",
    "/press",
    "/contact",
    "/contact-us",
]

MAX_QUERIES = 10
MAX_RESULTS_PER_QUERY = 5
MAX_URLS = 20

# 役職らしさを示す語（氏名+役職テキスト抽出で「役職行」を見分ける）
_TITLE_HINTS = (
    "ceo", "cto", "cfo", "coo", "cmo", "cro", "founder", "co-founder", "cofounder",
    "president", "director", "manager", "head of", "lead", "officer", "vp",
    "vice president", "chief", "marketing", "sales", "partnership", "partnerships",
    "business development", "export", "press", "communications", "owner",
    "international", "wholesale", "distribution",
)

_LINKEDIN_IN_RE = re.compile(
    r'<a\b[^>]*href=["\']([^"\']*linkedin\.com/in/[^"\']*)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(
    r"(?i)</?(?:p|div|li|tr|td|th|h[1-6]|br|section|article|header|footer)\b[^>]*>"
)


def build_people_search_queries(project) -> list[str]:
    """担当者探索用の検索クエリ（要件の例に準拠。重複排除・順序維持）。"""
    name = (getattr(project, "maker_name", None) or "").strip()
    domain = cds._domain_of(getattr(project, "maker_url", None))
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    if name:
        add(f'"{name}"')
        for kw in (
            "founder", "team", "leadership", "linkedin", "business development",
            "partnership", "export manager", "international sales", "marketing director",
        ):
            add(f'"{name}" {kw}')
    if domain:
        for kw in ("team", "people", "leadership", "about", "contact", "pdf"):
            add(f"site:{domain} {kw}")
    return queries


# ---------------- HTML → テキスト ----------------
def _strip_tags(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _html_to_lines(html: str) -> list[str]:
    """ブロック境界を改行に変換してから可視テキスト行に分解する。"""
    if not html:
        return []
    no_sd = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    with_breaks = _BLOCK_RE.sub("\n", no_sd)
    text = html_lib.unescape(_TAG_RE.sub(" ", with_breaks))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def _has_title_hint(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _TITLE_HINTS)


# ---------------- 抽出戦略 ----------------
def _walk_jsonld(node, people: list[dict]) -> None:
    """JSON-LD を再帰的に走査し Person を集める。"""
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, people)
        return
    if not isinstance(node, dict):
        return
    types = node.get("@type")
    type_set = {types} if isinstance(types, str) else set(types or [])
    if "Person" in type_set:
        name = node.get("name")
        if isinstance(name, str) and looks_like_person_name(name):
            job = node.get("jobTitle")
            if isinstance(job, list):
                job = job[0] if job else None
            same = node.get("sameAs")
            linkedin = None
            candidates = [same] if isinstance(same, str) else (same or [])
            for c in candidates:
                if isinstance(c, str) and "linkedin.com/in/" in c.lower():
                    linkedin = c
                    break
            email = node.get("email")
            if isinstance(email, str) and email.lower().startswith("mailto:"):
                email = email.split(":", 1)[1]
            people.append(
                {
                    "name": name.strip(),
                    "title": job.strip() if isinstance(job, str) else None,
                    "linkedin_url": linkedin,
                    "email": email if isinstance(email, str) else None,
                    "email_source": "jsonld" if isinstance(email, str) else None,
                }
            )
    # Organization.founder / employee などのネストも辿る
    for key in ("founder", "founders", "employee", "employees", "member", "@graph"):
        if key in node:
            _walk_jsonld(node[key], people)


def _extract_jsonld(html: str) -> list[dict]:
    out: list[dict] = []
    for block in _JSONLD_RE.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        _walk_jsonld(data, out)
    return out


_LINKEDIN_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#\"']+)", re.IGNORECASE)


def _extract_linkedin_people(
    html: str,
) -> tuple[list[dict], dict[str, str], list[tuple[str, str]]]:
    """LinkedIn /in/ アンカーから人物を抽出する。

    返り値: (people, name->linkedin マップ, slug_links[(slug_alnum, url)])。
    アンカーテキストが人名のときは人物として採用。slug_links は氏名との突き合わせ用
    （アンカーテキストが "LinkedIn" 等でも、URL の slug に氏名が含まれていれば紐付ける）。
    """
    people: list[dict] = []
    name_to_linkedin: dict[str, str] = {}
    for href, inner in _LINKEDIN_IN_RE.findall(html or ""):
        text = _strip_tags(inner)
        url = href.split("?", 1)[0]
        if looks_like_person_name(text):
            name = " ".join(text.split()).strip(" .,-")
            people.append({"name": name, "linkedin_url": url})
            name_to_linkedin.setdefault(name.lower(), url)

    slug_links: list[tuple[str, str]] = []
    seen_slug: set[str] = set()
    for m in _LINKEDIN_SLUG_RE.findall(html or ""):
        url = "https://www.linkedin.com/in/" + m.split("?", 1)[0]
        slug_alnum = re.sub(r"[^a-z]", "", m.lower())
        if slug_alnum and slug_alnum not in seen_slug:
            seen_slug.add(slug_alnum)
            slug_links.append((slug_alnum, url))
    return people, name_to_linkedin, slug_links


def _match_linkedin_by_name(name: str, slug_links: list[tuple[str, str]]) -> str | None:
    """氏名と一致しそうな LinkedIn slug を返す（slug に氏名が含まれる場合のみ）。"""
    parts = [re.sub(r"[^a-z]", "", t.lower()) for t in name.split() if t]
    parts = [p for p in parts if p]
    if not parts:
        return None
    first, last = parts[0], parts[-1]
    for slug_alnum, url in slug_links:
        if last and slug_alnum in (first + last, last + first):
            return url
        if last and first in slug_alnum and last in slug_alnum:
            return url
    return None


def _extract_text_people(lines: list[str]) -> list[dict]:
    """「氏名, 役職」/ 2 行ペア（氏名→役職）から人物を抽出する。"""
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: str, title: str | None) -> None:
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        out.append({"name": name, "title": title})

    for i, line in enumerate(lines):
        # インライン "Name, Title" / "Name - Title" / "Name | Title"
        m = re.match(r"^(.+?)\s*[,\-–—|]\s*(.+)$", line)
        if m:
            cand_name, cand_title = m.group(1).strip(), m.group(2).strip()
            if (
                looks_like_person_name(cand_name)
                and _has_title_hint(cand_title)
                and len(cand_title) <= 80
            ):
                add(cand_name, cand_title)
                continue
        # 2 行ペア（氏名→次行が役職）
        if looks_like_person_name(line) and i + 1 < len(lines):
            nxt = lines[i + 1]
            if _has_title_hint(nxt) and len(nxt) <= 80 and not looks_like_person_name(nxt):
                add(line, nxt)
    return out


def _find_email_for(name: str, mailtos: list[str]) -> str | None:
    """ページ上の mailto から、氏名に対応しそうなメールを保守的に選ぶ。

    ローカル部が名（first name）または first.last で始まるものだけ採用（推測で
    別人のメールを割り当てない）。
    """
    parts = [p for p in re.split(r"\s+", name.lower()) if p]
    if not parts:
        return None
    first = re.sub(r"[^a-z]", "", parts[0])
    last = re.sub(r"[^a-z]", "", parts[-1]) if len(parts) > 1 else ""
    for addr in mailtos:
        local = addr.split("@", 1)[0].lower()
        local_alnum = re.sub(r"[^a-z]", "", local)
        if first and (local_alnum == first or local_alnum == first + last):
            return addr
        if first and last and (first in local and last in local):
            return addr
    return None


def extract_people_from_html(html: str, page_url: str) -> list[PersonResult]:
    """1 ページの HTML から担当者候補を抽出する（出典 = page_url）。"""
    if not html:
        return []

    raw: list[dict] = []
    raw.extend(_extract_jsonld(html))
    li_people, name_to_linkedin, slug_links = _extract_linkedin_people(html)
    raw.extend(li_people)
    raw.extend(_extract_text_people(_html_to_lines(html)))

    # ページ上の mailto（既存フィルタは service 側で実施。ここでは紐付け候補）
    mailtos: list[str] = []
    seen_mail: set[str] = set()
    for m in cds.MAILTO_RE.findall(html):
        addr = m.split("?", 1)[0].strip()
        if "@" in addr and addr.lower() not in seen_mail:
            seen_mail.add(addr.lower())
            mailtos.append(addr)

    # 氏名でマージ（LinkedIn / メール / 役職を補完）
    merged: dict[str, dict] = {}
    for r in raw:
        name = (r.get("name") or "").strip()
        if not name or not looks_like_person_name(name):
            continue
        key = name.lower()
        cur = merged.setdefault(
            key,
            {
                "name": name,
                "title": None,
                "linkedin_url": None,
                "email": None,
                "email_source": None,
            },
        )
        if r.get("title") and not cur["title"]:
            cur["title"] = r["title"]
        if r.get("linkedin_url") and not cur["linkedin_url"]:
            cur["linkedin_url"] = r["linkedin_url"]
        if r.get("email") and not cur["email"]:
            cur["email"] = r["email"]
            cur["email_source"] = r.get("email_source") or "page"

    out: list[PersonResult] = []
    for key, rec in merged.items():
        name = rec["name"]
        # アンカー由来の LinkedIn を補完。無ければ URL slug に氏名が含まれるものを紐付け
        if not rec["linkedin_url"] and key in name_to_linkedin:
            rec["linkedin_url"] = name_to_linkedin[key]
        if not rec["linkedin_url"]:
            rec["linkedin_url"] = _match_linkedin_by_name(name, slug_links)
        # メール未確定ならページ上 mailto から保守的に紐付け
        if not rec["email"]:
            mail = _find_email_for(name, mailtos)
            if mail:
                rec["email"] = mail
                rec["email_source"] = "page"
        department, priority = title_to_priority(rec["title"])
        confidence = compute_confidence(
            has_name=True,
            has_linkedin=bool(rec["linkedin_url"]),
            has_email=bool(rec["email"]),
            has_known_title=department not in (None, "Other"),
        )
        out.append(
            PersonResult(
                name=name,
                title=rec["title"],
                department=department,
                linkedin_url=rec["linkedin_url"],
                email=rec["email"],
                email_source=rec["email_source"],
                source_url=page_url,
                confidence=confidence,
                priority=priority,
                notes="",
            )
        )
    return out


# ---------------- クロール ----------------
def _candidate_urls(project) -> list[str]:
    """巡回する起点 URL（公式サイト + 担当者ページ代表パス + 案件ページ）。"""
    official = getattr(project, "maker_url", None) or ""
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        if u and u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            urls.append(u)

    add(official)
    add(getattr(project, "source_url", None))
    if official:
        p = urlparse(official)
        root = f"{p.scheme}://{p.netloc}"
        for path in TEAM_PATHS:
            add(root + path)
    return urls


class MockContactHunter(ContactHunter):
    name = "mock-contact-hunter-v1"

    def hunt(
        self, project, *, fetch_fn=None, search_fn=None, research=None
    ) -> ContactHuntResult:
        official_domain = cds._domain_of(getattr(project, "maker_url", None))
        own_fetcher = fetch_fn is None
        own_search = search_fn is None
        fetch = fetch_fn or wrs._make_fetcher()
        search = search_fn or wrs._default_search_fn()

        queries = build_people_search_queries(project)
        searched_queries: list[str] = []

        # 1. 検索結果を集める（公式ドメイン内の team/people/about を優先採用）
        search_urls: list[str] = []
        seen_su: set[str] = set()
        try:
            for q in queries[:MAX_QUERIES]:
                searched_queries.append(q)
                try:
                    results = search(q) or []
                except Exception:  # noqa: BLE001
                    results = []
                for u in results[:MAX_RESULTS_PER_QUERY]:
                    if u in seen_su:
                        continue
                    seen_su.add(u)
                    # LinkedIn 会社/個人ページや公式ドメイン内ページを優先採用
                    host = cds._domain_of(u)
                    if (
                        (official_domain and cds._same_domain(u, official_domain))
                        or "linkedin.com" in host
                    ):
                        search_urls.append(u)
        finally:
            if own_search:
                client = getattr(search, "_client", None)
                if client is not None:
                    client.close()

        # 2. クロール対象（起点 + 検索で見つかった公式/LinkedIn ページ）
        crawl: list[str] = []
        crawl_seen: set[str] = set()
        for u in _candidate_urls(project) + search_urls:
            # LinkedIn 個人ページはログイン必須で本文が取れないため取得しない
            # （URL は人物の linkedin_url として後段で活用）
            if "linkedin.com/in/" in u.lower():
                continue
            if u not in crawl_seen and len(crawl) < MAX_URLS:
                crawl_seen.add(u)
                crawl.append(u)

        # 3. 取得して人物抽出
        searched_urls: list[str] = []
        people_map: dict[str, PersonResult] = {}
        try:
            for url in crawl:
                if len(searched_urls) >= MAX_URLS:
                    break
                html = fetch(url)
                searched_urls.append(url)
                if not html:
                    continue
                for person in extract_people_from_html(html, url):
                    key = (person.name or "").lower()
                    if not key:
                        continue
                    existing = people_map.get(key)
                    if existing is None:
                        people_map[key] = person
                    else:
                        # より情報量の多い方を優先（LinkedIn/メール/役職）
                        if not existing.linkedin_url and person.linkedin_url:
                            existing.linkedin_url = person.linkedin_url
                        if not existing.email and person.email:
                            existing.email = person.email
                            existing.email_source = person.email_source
                        if not existing.title and person.title:
                            existing.title = person.title
                            existing.department, existing.priority = title_to_priority(
                                person.title
                            )
                        existing.confidence = compute_confidence(
                            has_name=True,
                            has_linkedin=bool(existing.linkedin_url),
                            has_email=bool(existing.email),
                            has_known_title=existing.department not in (None, "Other"),
                        )
        finally:
            if own_fetcher:
                client = getattr(fetch, "_client", None)
                if client is not None:
                    client.close()

        people = sorted(
            people_map.values(),
            key=lambda p: (p.priority, p.confidence),
            reverse=True,
        )
        notes = (
            f"{len(searched_queries)} query(ies), {len(searched_urls)} url(s), "
            f"{len(people)} person(s) found"
        )
        if not people:
            notes += (
                " — 担当者は特定できませんでした（公式サイトのチーム/会社情報や "
                "LinkedIn が非公開の可能性）。推測の人名は作成していません。"
            )
        return ContactHuntResult(
            people=people,
            searched_queries=searched_queries,
            searched_urls=searched_urls,
            notes=notes,
            model=self.name,
        )
