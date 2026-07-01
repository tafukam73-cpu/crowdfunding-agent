"""営業先連絡先探索 API。

- POST /projects/{id}/contact-discovery           探索を実行（同期）して保存
- GET  /projects/{id}/contact-discovery            最新の探索結果を取得
- POST /projects/{id}/contact-discovery/apply-to-crm  発見メールを CRM に反映

取得失敗してもアプリは落とさない（status=failed として 200 で返す）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.ai.outreach import OUTREACH_CHANNELS
from app.db.session import get_db
from app.models.contact_person import ContactPerson
from app.schemas.contact_discovery import (
    ApplyToCrmRequest,
    ApplyToCrmResult,
    ContactDiscoveryOut,
    OutreachMessageOut,
)
from app.schemas.contact_person import (
    ApplyPersonToCrmRequest,
    ApplyPersonToCrmResult,
    ContactPersonOut,
)
from app.services import (
    contact_discovery_service,
    contact_hunter_service,
    document_reader_service,
    email_service,
    project_service,
    search_agent_service,
    web_research_service,
)

logger = logging.getLogger("router.contact_discovery")

router = APIRouter(tags=["contact-discovery"])


@router.post(
    "/projects/{project_id}/contact-discovery", response_model=ContactDiscoveryOut
)
def run_contact_discovery(
    project_id: int, db: Session = Depends(get_db)
) -> ContactDiscoveryOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return contact_discovery_service.run_discovery(db, project)


@router.get(
    "/projects/{project_id}/contact-discovery", response_model=ContactDiscoveryOut
)
def get_contact_discovery(project_id: int, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    row = contact_discovery_service.get_latest(db, project_id)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return row


@router.post(
    "/projects/{project_id}/contact-discovery/ai-research",
    response_model=ContactDiscoveryOut,
)
def run_ai_contact_research(
    project_id: int, db: Session = Depends(get_db)
) -> ContactDiscoveryOut:
    """AI 連絡先リサーチを実行して最新の探索結果に保存する（同期）。

    既存の探索結果が無ければ先に自動探索を実行する。ANTHROPIC_API_KEY 未設定時は
    モックで動作する。失敗時も ai_notes にエラーを記録し 200 で返す。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return contact_discovery_service.run_ai_research(db, project)


@router.post(
    "/projects/{project_id}/contact-discovery/web-research",
    response_model=ContactDiscoveryOut,
)
def run_web_research(
    project_id: int, db: Session = Depends(get_db)
) -> ContactDiscoveryOut:
    """AI Web Research を実行して最新の探索結果の web_* に保存する（同期）。

    検索エンジン（DuckDuckGo HTML）の結果と公式サイトの代表パスを横断クロールし、
    実際に取得したページからメール・フォーム・SNS・PDF を抽出する（既存の除外
    フィルタを必ず通すため、推測メールや platform / sentry メールは候補に残らない）。
    既存の探索結果が無ければ先に自動探索を実行する。失敗時も web_research_error に
    記録し 200 で返す。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return web_research_service.run_web_research(db, project)


@router.get(
    "/projects/{project_id}/contact-discovery/web-research",
    response_model=ContactDiscoveryOut,
)
def get_web_research(project_id: int, db: Session = Depends(get_db)):
    """最新の探索結果（web_* を含む）を返す。未実行なら 204。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    row = contact_discovery_service.get_latest(db, project_id)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return row


@router.post(
    "/projects/{project_id}/contact-discovery/document-reader",
    response_model=ContactDiscoveryOut,
)
def run_document_reader(
    project_id: int, db: Session = Depends(get_db)
) -> ContactDiscoveryOut:
    """AI Document Reader を実行して最新の探索結果の doc_reader_* に保存する（同期）。

    Web Research が到達したページの本文・リンク・抽出済みメール/SNS・検索スニペットを
    集め、AI（Claude / モック）にページ全体を読解させて会社名・公式サイト・メール・
    SNS・フォーム・担当者候補を整理する。ANTHROPIC_API_KEY 未設定時はモックで動作。
    AI が返したメール・人名は既存フィルタで再検証し、推測メールは採用しない。失敗時も
    doc_reader_evidence_summary にエラーを記録し 200 で返す。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return document_reader_service.run_document_reader(db, project)


@router.get(
    "/projects/{project_id}/contact-discovery/document-reader",
    response_model=ContactDiscoveryOut,
)
def get_document_reader(project_id: int, db: Session = Depends(get_db)):
    """最新の探索結果（doc_reader_* を含む）を返す。未実行なら 204。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    row = contact_discovery_service.get_latest(db, project_id)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return row


@router.post(
    "/projects/{project_id}/contact-discovery/search-agent",
    response_model=ContactDiscoveryOut,
)
def run_search_agent(
    project_id: int, db: Session = Depends(get_db)
) -> ContactDiscoveryOut:
    """AI Search Agent を反復実行して最新の探索結果の search_agent_* に保存する（同期）。

    AI が各ステップで「次に見る URL・検索クエリ・続行/終了」を判断し、SNS プロフィール
    → Linktree 等のリンク集 → 公式サイト → Contact のようにリンクを辿って連絡先を探す。
    最大 5 ステップ / 20 URL / 20 クエリ。ANTHROPIC_API_KEY 未設定時はモックで動作。
    AI が返したメール・人名は既存フィルタで再検証し、推測メールは採用しない。失敗時も
    search_agent_error に記録し 200 で返す。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return search_agent_service.run_search_agent(db, project)


@router.get(
    "/projects/{project_id}/contact-discovery/search-agent",
    response_model=ContactDiscoveryOut,
)
def get_search_agent(project_id: int, db: Session = Depends(get_db)):
    """最新の探索結果（search_agent_* を含む）を返す。未実行なら 204。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    row = contact_discovery_service.get_latest(db, project_id)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return row


@router.post(
    "/projects/{project_id}/contact-discovery/contact-people",
    response_model=list[ContactPersonOut],
)
def run_contact_hunter(
    project_id: int, db: Session = Depends(get_db)
) -> list[ContactPersonOut]:
    """Contact Hunter AI を実行し、営業担当者候補を発見・保存して返す（同期）。

    会社ではなく「誰に送るか（Business Development / Partnership / Founder 等）」を
    出典 URL 付きで特定する。人名は推測せず、出典のある人物のみ保存。Claude 未設定
    時は決定的な HTML 抽出（モック）で動作する。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return contact_hunter_service.run_hunt(db, project)


@router.get(
    "/projects/{project_id}/contact-discovery/contact-people",
    response_model=list[ContactPersonOut],
)
def list_contact_people(
    project_id: int, db: Session = Depends(get_db)
) -> list[ContactPersonOut]:
    """案件の担当者候補を営業優先度順で返す（未実行なら空配列）。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return contact_hunter_service.get_people(db, project_id)


@router.post(
    "/projects/{project_id}/contact-discovery/contact-people/apply-to-crm",
    response_model=ApplyPersonToCrmResult,
)
def apply_contact_person_to_crm(
    project_id: int,
    payload: ApplyPersonToCrmRequest,
    db: Session = Depends(get_db),
) -> ApplyPersonToCrmResult:
    """担当者を CRM の Contact として追加する（氏名・役職・部署・LinkedIn・メール）。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    person = db.get(ContactPerson, payload.contact_person_id)
    if person is None or person.project_id != project_id:
        raise HTTPException(status_code=404, detail="担当者が見つかりません")
    maker_id, contact_id = contact_hunter_service.apply_to_crm(db, project, person)
    return ApplyPersonToCrmResult(
        maker_id=maker_id, contact_id=contact_id, name=person.name, recorded=True
    )


@router.get(
    "/projects/{project_id}/contact-discovery/outreach-message",
    response_model=OutreachMessageOut,
)
def get_outreach_message(
    project_id: int,
    channel: str | None = None,
    db: Session = Depends(get_db),
) -> OutreachMessageOut:
    """メール以外のチャネル向けの短文アウトリーチ文を生成して返す。

    channel 未指定なら最新の探索結果の推奨チャネルを使う。問い合わせフォーム /
    SNS（Instagram / LinkedIn / Facebook）以外のチャネルでは 400 を返す。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")

    if channel is None:
        latest = contact_discovery_service.get_latest(db, project_id)
        channel = latest.recommended_channel if latest else None
    if channel not in OUTREACH_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=(
                "短文アウトリーチ文は問い合わせフォーム / SNS（Instagram / "
                "LinkedIn / Facebook）チャネル向けです。"
            ),
        )
    return email_service.generate_outreach_message(db, project, channel)


@router.post(
    "/projects/{project_id}/contact-discovery/apply-to-crm",
    response_model=ApplyToCrmResult,
)
def apply_to_crm(
    project_id: int,
    payload: ApplyToCrmRequest | None = None,
    db: Session = Depends(get_db),
) -> ApplyToCrmResult:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")

    latest = contact_discovery_service.get_latest(db, project_id)
    email = (payload.email if payload else None) or None
    if not email and latest is not None:
        email = latest.primary_email
    if latest is None and not email:
        raise HTTPException(
            status_code=400,
            detail="反映する情報がありません。先に連絡先探索を実行してください。",
        )

    # メールが無くても推奨チャネル・アクション等を CRM に記録する
    maker_id, contact_id = contact_discovery_service.apply_to_crm(
        db, project, email=email, row=latest
    )
    return ApplyToCrmResult(
        maker_id=maker_id, contact_id=contact_id, email=email, recorded=True
    )
