"""Wadiz 手動取り込み（人間ブラウザ取得 → 安全な抽出・保存）。

Akamai 回避は実装しない。ユーザーが通常の Chrome で Wadiz 詳細ページを開き
「もっと見る」を展開した後の **公開情報（本文/HTML）を貼り付け**、そこから
メール・SNS・公式サイト・メーカー情報を抽出して Contact Intelligence に反映する。

方針:
- 既存の抽出器（contact_discovery_service）を再利用（mailto / 難読化 [at][dot] /
  cfemail / JS 連結 / HTML entity / プラットフォームメール除外）。
- 除外は「メールのドメインが wadiz.kr の場合のみ」（外部の公開メーカーメールは保存可）。
- 推測メールは生成しない。プレビューは DB を変更しない。confirm 時のみ保存。
- 非破壊：既存メールを空値で消さない・重複保存しない・冪等（raw_content_hash）。
"""
from __future__ import annotations

import hashlib
import html as _html
import re
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.wadiz_import import WadizImport
from app.services import contact_discovery_service as cds

SOURCE_SITE_DOMAIN = "wadiz.kr"
SOURCE_TYPE = "wadiz_manual_import"

# 生メール候補（除外理由の説明用に、除外されるものも一旦拾う）
_RAW_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MAILTO_RE = re.compile(r"mailto:([^\"'>?\s]+)", re.IGNORECASE)

# 韓国系 SNS / 公開チャネル（既存 SOCIAL_PATTERNS を補完）
_KO_SOCIAL_PATTERNS = {
    "naver_blog": re.compile(r"https?://blog\.naver\.com/[^\s\"'<>]+", re.I),
    "naver_cafe": re.compile(r"https?://cafe\.naver\.com/[^\s\"'<>]+", re.I),
    "naver_smartstore": re.compile(r"https?://smartstore\.naver\.com/[^\s\"'<>]+", re.I),
    "kakao": re.compile(r"https?://(?:pf|open|story)\.kakao\.com/[^\s\"'<>]+", re.I),
    "band": re.compile(r"https?://band\.us/[^\s\"'<>]+", re.I),
}

# 公式サイト候補にしない集約/プラットフォーム/SNS 断片
_NON_OFFICIAL_HINTS = (
    "wadiz.kr", "facebook.", "instagram.", "twitter.", "x.com", "youtube.",
    "youtu.be", "linkedin.", "tiktok.", "pinterest.", "naver.com", "kakao.com",
    "band.us", "google.", "apple.com", "play.google",
)

_PHONE_RE = re.compile(r"(?<!\d)(0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4})(?!\d)")
# 「상호: X」「회사명: X」「대표자: X」「브랜드: X」等のラベル付き抽出（推測しない）。
# 値は非貪欲で取り、次のラベル語や区切りで打ち切る（過剰取得の防止）。
_LABEL_PATTERNS = {
    "company_name": re.compile(r"(?:상호(?:명)?|회사명|법인명)\s*[:：]\s*(.{2,40}?)(?=\s{2,}|[<|/·・]|$)"),
    "maker_name": re.compile(r"(?:메이커|판매자|제조사)\s*[:：]\s*(.{2,40}?)(?=\s{2,}|[<|/·・]|$)"),
    "brand_name": re.compile(r"(?:브랜드)\s*[:：]\s*(.{2,40}?)(?=\s{2,}|[<|/·・]|$)"),
    "representative": re.compile(r"(?:대표자?(?:명)?)\s*[:：]\s*(.{2,30}?)(?=\s{2,}|[<|/·・]|$)"),
    "address": re.compile(r"(?:주소|사업장\s*소재지)\s*[:：]\s*(.{5,80}?)(?=\s{2,}|[<|]|$)"),
}
# 値の中に別ラベル語が現れたらそこで打ち切る（1行に複数フィールドが並ぶ貼り付け対策）。
_STOP_TOKENS = ("대표", "이메일", "주소", "전화", "문의", "사업자", "상호",
                "브랜드", "메이커", "판매자", "제조사", "고객", "email", "@", "http")


def _cut_at_stop(value: str) -> str:
    v = value.strip()
    cut = len(v)
    for tok in _STOP_TOKENS:
        i = v.find(tok, 1)
        if i > 0:
            cut = min(cut, i)
    return v[:cut].strip().strip(".,;:·・")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_CAMPAIGN_ID_RE = re.compile(r"/campaign/(?:detail|example)/([A-Za-z0-9_\-]+)")


def extract_campaign_id(url: str | None) -> str | None:
    """Wadiz URL から campaign ID（/campaign/detail/{id}）を取り出す。"""
    if not url:
        return None
    m = _CAMPAIGN_ID_RE.search(url)
    return m.group(1) if m else None


def _canon(url: str | None) -> str:
    u = (url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    return u.lower()


def resolve_projects(db: Session, url: str | None) -> list[Project]:
    """Wadiz URL / campaign ID から対象 project 候補を返す（自動確定はしない）。

    優先: 1) source_url 完全一致 → 2) campaign ID 一致 → 3) 正規化 URL 一致。
    複数一致・不一致は呼び出し側（拡張機能）でユーザーに選ばせる。
    """
    from app.models.project import SourceSite

    if not url:
        return []
    # 1) 完全一致
    exact = db.scalar(select(Project).where(Project.source_url == url))
    if exact is not None:
        return [exact]
    cid = extract_campaign_id(url)
    canon = _canon(url)
    rows = list(
        db.scalars(select(Project).where(Project.source_site == SourceSite.wadiz.value))
    )
    by_cid = [p for p in rows if cid and extract_campaign_id(p.source_url) == cid]
    if by_cid:
        return by_cid
    by_canon = [p for p in rows if _canon(p.source_url) == canon]
    return by_canon


def build_capture_content(payload: dict) -> tuple[str, str]:
    """拡張機能の取得ペイロード → 抽出用テキストと content_type を作る。

    outerHTML があれば HTML として使い、JSON-LD / mailto / meta / links / text を
    連結して補う（本文/フッター区別のため HTML を優先）。
    """
    html = payload.get("html") or ""
    text = payload.get("text") or ""
    parts: list[str] = []
    if html:
        parts.append(html)
        content_type = "html"
    else:
        parts.append(text)
        content_type = "text"
    for key in ("json_ld", "jsonld"):
        for blk in (payload.get(key) or []):
            parts.append(blk if isinstance(blk, str) else _json_dump(blk))
    for m in (payload.get("mailtos") or payload.get("mailto") or []):
        parts.append(str(m))
    for m in (payload.get("tels") or []):
        parts.append(str(m))
    for lk in (payload.get("links") or []):
        parts.append(str(lk))
    meta = payload.get("meta") or {}
    if isinstance(meta, dict):
        parts.extend(str(v) for v in meta.values() if v)
    if html and text and text not in html:
        parts.append(text)  # innerText も補助的に含める
    return "\n".join(p for p in parts if p), content_type


def _json_dump(obj) -> str:
    import json as _json

    try:
        return _json.dumps(obj, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return ""


def content_hash(content: str) -> str:
    """本文のハッシュ（冪等判定）。空白を正規化して安定化する。"""
    norm = " ".join((content or "").split())
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()


def _evidence_for(content: str, email: str) -> str:
    """メール周辺のスニペット（証拠）。難読化で本文に現れない場合はその旨。"""
    text = content or ""
    idx = text.lower().find(email.lower())
    if idx >= 0:
        start = max(0, idx - 40)
        end = min(len(text), idx + len(email) + 40)
        return " ".join(text[start:end].split())[:120]
    return "難読化/mailto から復元（本文に平文では現れない）"


def _method_for(content: str, email: str) -> str:
    low = (content or "").lower()
    e = email.lower()
    if f"mailto:{e}" in low:
        return "mailto"
    if e in low:
        return "plain_text"
    return "deobfuscated"


def _confidence_for(email: str) -> str:
    """手動確認済み公開メールの確度。役割宛先=high、その他=medium、汎用=low。"""
    _score, tier = cds.score_email(email)
    return {"high": "high", "mid": "medium", "low": "low", "other": "medium"}.get(
        tier, "medium"
    )


def _extract_socials(content: str, base: str) -> dict[str, str]:
    socials = dict(cds.extract_socials(content, base or "https://www.wadiz.kr"))
    for plat, pat in _KO_SOCIAL_PATTERNS.items():
        if plat in socials:
            continue
        m = pat.search(content or "")
        if m:
            socials[plat] = m.group(0).split("?")[0]
    return socials


_BARE_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


def _official_candidates(content: str, base: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    # href 由来（HTML）＋ 本文中の裸 URL（テキスト貼り付け）の双方を候補にする。
    links = list(cds.extract_links(content or "", base or "https://www.wadiz.kr"))
    links += _BARE_URL_RE.findall(content or "")
    for link in links:
        host = link.lower()
        if any(h in host for h in _NON_OFFICIAL_HINTS):
            continue
        if cds.is_platform_url(link):
            continue
        root = link.split("?")[0].rstrip("/")
        if root in seen:
            continue
        seen.add(root)
        if cds.official_site_or_none(root):
            out.append(root)
    return out[:8]


_CONTACT_URL_HINTS = ("contact", "inquiry", "cs", "support", "help", "문의", "고객")


def _contact_url_candidates(content: str, base: str) -> list[str]:
    """問い合わせ導線らしい URL（contact/문의 等を含む）を返す。"""
    out: list[str] = []
    seen: set[str] = set()
    links = list(cds.extract_links(content or "", base or "https://www.wadiz.kr"))
    links += _BARE_URL_RE.findall(content or "")
    for link in links:
        low = link.lower()
        if "wadiz.kr" in low:
            continue
        if any(h in low for h in _CONTACT_URL_HINTS):
            root = link.split("#")[0]
            if root not in seen:
                seen.add(root)
                out.append(root)
    return out[:8]


def _labeled_fields(content: str) -> dict:
    out: dict = {}
    for key, pat in _LABEL_PATTERNS.items():
        m = pat.search(content or "")
        if m:
            val = _cut_at_stop(m.group(1))
            if len(val) >= 2:
                out[key] = val[:80]
    phones = sorted(set(_PHONE_RE.findall(content or "")))
    if phones:
        out["phone"] = phones[0]
        out["phones"] = phones[:5]
    return out


# サイト共通枠（footer/nav/header）内のメールは Wadiz 共通ナビ/運営寄りなので、
# プロジェクト本文のメーカー情報と区別する（除外はしないが確度を下げて注記する）。
_CHROME_REGION_RE = re.compile(
    r"<(footer|nav|header)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
# メーカー問い合わせ文脈のキーワード（周辺に出れば evidence/確度に反映）。
_CONTACT_CONTEXT = (
    "고객센터", "문의", "이메일", "메일", "연락처", "메이커", "판매자", "사업자",
    "회사", "대표", "제휴", "해외", "수출", "contact", "inquiry", "maker", "email",
)


def _chrome_email_set(html: str) -> set[str]:
    out: set[str] = set()
    for m in _CHROME_REGION_RE.finditer(html or ""):
        seg = _html.unescape(m.group(0))
        for e in _RAW_EMAIL_RE.findall(seg):
            out.add(e.strip().strip(".").lower())
    return out


def _context_for(content: str, email: str) -> str | None:
    """メール周辺（前後 60 文字）に現れたメーカー問い合わせ文脈の語を返す。"""
    text = content or ""
    idx = text.lower().find(email.lower())
    if idx < 0:
        return None
    window = text[max(0, idx - 60): idx + len(email) + 60]
    for kw in _CONTACT_CONTEXT:
        if kw in window:
            return kw
    return None


def extract(
    content: str, content_type: str, source_url: str | None, *,
    source_type: str = SOURCE_TYPE,
) -> dict:
    """貼り付け/取得本文・HTML から公開連絡先情報を抽出する（DB 非依存・純粋）。"""
    content = content or ""
    base = source_url or "https://www.wadiz.kr"
    # HTML entity（数値/名前つき）を復号したテキストからも抽出する（&#64;=@ / &#115;=s 等）。
    unescaped = _html.unescape(content)
    chrome_emails = _chrome_email_set(content)

    # 採用メール（既存抽出器：mailto/平文/難読化 + wadiz.kr 等の除外込み）。
    # 生本文と entity 復号後の双方から抽出して統合する。
    accepted = list(cds.extract_emails(content, source_site_domain=SOURCE_SITE_DOMAIN))
    _acc_lc = {e.lower() for e in accepted}
    for e in cds.extract_emails(unescaped, source_site_domain=SOURCE_SITE_DOMAIN):
        if e.lower() not in _acc_lc:
            accepted.append(e)
            _acc_lc.add(e.lower())

    def _email_record(e: str) -> dict:
        in_chrome = e.lower() in chrome_emails
        # 本文にも出るなら本文優先（メーカー情報）。footer/nav のみなら確度を下げる。
        body_hit = _method_for(content, e) != "deobfuscated" and not in_chrome
        ctx = _context_for(content, e)
        conf = _confidence_for(e)
        if in_chrome and not body_hit:
            conf = "low"
        elif ctx:
            conf = "high" if conf != "low" else "medium"
        ev = _evidence_for(content, e)
        if in_chrome and not body_hit:
            ev = "[サイト共通枠(footer/nav)] " + ev
        elif ctx:
            ev = f"[文脈: {ctx}] " + ev
        return {
            "value": e,
            "source_url": source_url,
            "source_type": source_type,
            "evidence": ev,
            "context": ctx,
            "region": "chrome" if (in_chrome and not body_hit) else "body",
            "confidence": conf,
            "extraction_method": _method_for(content, e),
        }

    emails = [_email_record(e) for e in accepted]

    # 除外メール（理由つき。@wadiz.kr / noreply / example / test 等）
    accepted_lc = {e.lower() for e in accepted}
    candidates: set[str] = set()
    for m in _MAILTO_RE.findall(content):
        candidates.add(m.split("?")[0].strip())
    for txt in (content, unescaped):
        for m in _RAW_EMAIL_RE.findall(txt):
            candidates.add(m.strip().strip("."))
    for dec in cds.deobfuscate_emails(content):
        mm = _RAW_EMAIL_RE.search(dec)
        if mm:
            candidates.add(mm.group(0).strip().strip("."))
    excluded = []
    seen_ex: set[str] = set()
    for c in candidates:
        cl = c.lower()
        if cl in accepted_lc or cl in seen_ex or "@" not in c:
            continue
        seen_ex.add(cl)
        reason = cds.email_exclusion_reason(c, SOURCE_SITE_DOMAIN)
        if reason:
            excluded.append({"value": c, "reason": reason})

    socials = _extract_socials(content, base)
    official = _official_candidates(content, base)
    contact_urls = _contact_url_candidates(content, base)
    fields = _labeled_fields(content)

    warnings = []
    if not emails:
        warnings.append(
            "公開メールを抽出できませんでした（本文に明示メールが無い / 画像内・"
            "外部リンク先の可能性）。展開後の本文全体を貼り付けたか確認してください。"
        )

    maker = fields.get("maker_name") or fields.get("company_name")
    company_names = list(dict.fromkeys(
        [x for x in (fields.get("company_name"), fields.get("maker_name")) if x]
    ))
    contact_names = [fields["representative"]] if fields.get("representative") else []
    return {
        "emails": emails,
        "excluded": excluded,
        "socials": socials,
        # 複数形（要件）と従来の単数フィールドの両方を返す
        "websites": official,
        "official_urls": official,
        "official_url_candidates": official,
        "official_url": official[0] if official else None,
        "contact_urls": contact_urls,
        "maker_names": [maker] if maker else [],
        "phones": fields.get("phones") or ([] if not fields.get("phone") else [fields["phone"]]),
        "company_names": company_names,
        "contact_names": contact_names,
        "maker_name": maker,
        "brand_name": fields.get("brand_name"),
        "company_name": fields.get("company_name"),
        "representative": fields.get("representative"),
        "phone": fields.get("phone"),
        "address": fields.get("address"),
        "evidence": {
            "source_url": source_url,
            "email_count": len(emails),
            "excluded_count": len(excluded),
        },
        "warnings": warnings,
        "content_hash": content_hash(content),
        "content_type": content_type or "text",
        "source_url": source_url,
    }


def _existing_public_emails(project: Project) -> set[str]:
    enr = project.enrichment or {}
    return {
        str(e.get("value", "")).lower()
        for e in (enr.get("public_emails") or [])
        if e.get("value")
    }


def preview(
    db: Session,
    project: Project,
    *,
    content: str,
    content_type: str = "text",
    source_url: str | None = None,
    captured_at: str | None = None,
    imported_by: str | None = None,
    source_type: str = SOURCE_TYPE,
) -> dict:
    """抽出結果 + 既存との差分を返す（DB は一切変更しない）。"""
    result = extract(
        content, content_type, source_url or project.source_url,
        source_type=source_type,
    )
    existing = _existing_public_emails(project)
    for e in result["emails"]:
        e["is_new"] = e["value"].lower() not in existing
    result["new_email_count"] = sum(1 for e in result["emails"] if e["is_new"])
    result["already_have_count"] = sum(
        1 for e in result["emails"] if not e["is_new"]
    )
    # 同一内容を既に取り込み済みか（冪等の事前通知）
    dup = db.scalar(
        select(WadizImport).where(
            WadizImport.project_id == project.id,
            WadizImport.raw_content_hash == result["content_hash"],
        )
    )
    result["already_imported"] = dup is not None
    return result


def get_imports(db: Session, project_id: int) -> list[WadizImport]:
    return list(
        db.scalars(
            select(WadizImport)
            .where(WadizImport.project_id == project_id)
            .order_by(desc(WadizImport.created_at), desc(WadizImport.id))
        )
    )


def confirm(
    db: Session,
    project: Project,
    *,
    content_hash_value: str,
    emails: list[dict],
    socials: dict | None = None,
    official_url: str | None = None,
    maker_name: str | None = None,
    source_url: str | None = None,
    content_type: str = "text",
    captured_at: str | None = None,
    imported_by: str | None = None,
    note: str | None = None,
    source_type: str = SOURCE_TYPE,
) -> dict:
    """確認済みの抽出結果を保存し、Contact Intelligence へ非破壊反映する。

    冪等：同一 (project, raw_content_hash) は再取り込みしても既存を返す。
    """
    src = source_url or project.source_url
    now = _now()

    # 冪等：同一内容の再取り込みは新規作成しない
    dup = db.scalar(
        select(WadizImport).where(
            WadizImport.project_id == project.id,
            WadizImport.raw_content_hash == content_hash_value,
        )
    )
    if dup is not None:
        return {
            "project_id": project.id,
            "already_imported": True,
            "wadiz_import_id": dup.id,
            "saved_emails": 0,
            "message": "同一内容は取り込み済みです（重複保存しません）。",
        }

    # --- 採用メールを正規化・重複排除（@wadiz.kr 等はここでも念のため除外） ---
    existing = _existing_public_emails(project)
    accepted: list[dict] = []
    seen: set[str] = set()
    for e in emails or []:
        v = str(e.get("value", "")).strip()
        if not v or "@" not in v:
            continue
        vl = v.lower()
        if vl in seen:
            continue
        if cds.email_exclusion_reason(v, SOURCE_SITE_DOMAIN):
            continue  # 運営/ダミー等は保存しない
        seen.add(vl)
        accepted.append({
            "value": v,
            "source_url": src,
            "source_type": source_type,
            "evidence": (e.get("evidence") or "")[:200],
            "context": e.get("context"),
            "confidence": e.get("confidence") or "medium",
            "extraction_method": e.get("extraction_method") or "manual",
            "imported_at": now,
            "confirmed_at": now,
            "imported_by": imported_by,
            "raw_content_hash": content_hash_value,
            "project_id": project.id,
        })

    # --- 取り込み履歴を保存（生本文は保持しない） ---
    row = WadizImport(
        project_id=project.id,
        source_url=src,
        content_type=content_type or "text",
        raw_content_hash=content_hash_value,
        imported_by=imported_by,
        note=note,
        extracted_json={
            "emails": accepted,
            "socials": socials or {},
            "official_url": official_url,
            "maker_name": maker_name,
        },
        email_count=len(accepted),
    )
    db.add(row)

    # --- enrichment.public_emails へ非破壊 merge（重複保存しない・既存を消さない） ---
    enr = dict(project.enrichment or {})
    pub = list(enr.get("public_emails") or [])
    saved = 0
    for rec in accepted:
        if rec["value"].lower() in existing:
            continue  # 重複保存しない
        pub.append(rec)
        existing.add(rec["value"].lower())
        saved += 1
    if pub:
        enr["public_emails"] = pub
    # SNS も非破壊 merge（既存を上書きしない）
    socs = dict(enr.get("socials") or {})
    for k, v in (socials or {}).items():
        if v:
            socs.setdefault(k, v)
    if socs:
        enr["socials"] = socs
    # 公式サイト候補（非破壊）
    off = cds.official_site_or_none(official_url) if official_url else None
    if off:
        cands = list(enr.get("official_site_candidates") or [])
        if not any(c.get("url") == off for c in cands):
            cands.append({"url": off, "confidence": "medium",
                          "source": SOURCE_TYPE, "verdict": "candidate"})
        enr["official_site_candidates"] = cands
    prov = dict(enr.get("provenance") or {})
    prov[source_type] = now
    enr["provenance"] = prov
    project.enrichment = enr

    # 既存が空のときだけメーカー名/公式サイトを補完（非破壊）
    if maker_name and not (project.maker_name or "").strip():
        project.maker_name = maker_name[:255]
    if off and not (project.maker_url or "").strip():
        project.maker_url = off

    # --- Contact Intelligence（Contact Discovery）へ反映：最新行として追加 ---
    contact_found = _write_contact_discovery(
        db, project, accepted, socs, off, src, source_type=source_type
    )

    db.commit()
    db.refresh(project)

    # --- Sales 適性を再評価（ルールベース・軽量・外部HTTPなし） ---
    #   連絡先が増えたので独占スコアの reachability 等が更新される。
    reassessed = False
    try:
        from app.services import sales_assessment_service

        sales_assessment_service.run_assessment(db, project)
        reassessed = True
    except Exception:  # noqa: BLE001  再評価失敗でも取り込みは成功
        db.rollback()

    return {
        "project_id": project.id,
        "already_imported": False,
        "wadiz_import_id": row.id,
        "saved_emails": saved,
        "total_accepted": len(accepted),
        "contact_found": contact_found,
        "reassessed": reassessed,
        "public_emails_total": len(pub),
    }


def _write_contact_discovery(
    db: Session, project: Project, accepted: list[dict], socials: dict,
    official_url: str | None, source_url: str | None, *,
    source_type: str = SOURCE_TYPE,
) -> bool:
    """手動取り込みメールを ContactDiscovery の最新結果として反映する。

    Sales Copilot / Contact Intelligence が連絡先ありと認識できるようにする
    （既存の探索履歴は消さず、新しい行を追加する＝非破壊）。
    """
    from app.models.contact_discovery import ContactDiscovery, DiscoveryStatus

    if not accepted and not official_url and not socials:
        return False

    ranked = []
    for rec in accepted:
        score, tier = cds.score_email(rec["value"])
        ranked.append({
            "email": rec["value"], "score": score, "tier": tier,
            "sources": [source_url] if source_url else [],
            "source_type": source_type,
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    primary = ranked[0]["email"] if ranked else None

    row = ContactDiscovery(
        project_id=project.id,
        maker_id=project.maker_id,
        status=DiscoveryStatus.completed.value,
        primary_email=primary,
        official_site_url=official_url or cds.official_site_or_none(project.maker_url),
        discovered_emails=ranked or None,
        discovered_socials=socials or None,
        instagram_url=socials.get("instagram"),
        facebook_url=socials.get("facebook"),
        youtube_url=socials.get("youtube"),
        twitter_url=socials.get("twitter"),
        linkedin_url=socials.get("linkedin"),
        confidence_score=90 if primary else 40,
        notes=f"[{source_type}] 取り込み（{source_url or ''}）",
    )
    db.add(row)
    return bool(primary)
