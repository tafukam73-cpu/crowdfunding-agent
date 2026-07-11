"""Wadiz ブラウザ支援型取り込み API（Chrome 拡張の content script から受ける）。

ユーザーが通常の Chrome で Wadiz 詳細ページを開き「もっと見る」を展開した状態の
公開 DOM を拡張機能が取得し、ここへ送る（Akamai 回避ではない）。

- GET  /wadiz-browser-capture/resolve?url=...          URL/campaign ID から project 特定
- POST /projects/{id}/wadiz-browser-capture/preview     抽出（DB 変更なし）
- POST /projects/{id}/wadiz-browser-capture/confirm     確認済みを非破壊保存＋CI 反映

抽出・除外・保存は既存の wadiz_import_service を再利用する（source_type だけ差し替え）。
GET は読み取りのみで重い処理を起動しない。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import project_service, wadiz_import_service

router = APIRouter(tags=["wadiz-browser-capture"])

SOURCE_TYPE = "wadiz_browser_capture"


class CaptureIn(BaseModel):
    source_url: str | None = None
    title: str | None = None
    text: str | None = None
    html: str | None = None
    links: list[str] = []
    mailtos: list[str] = []
    tels: list[str] = []
    meta: dict[str, str] = {}
    json_ld: list = []
    captured_at: str | None = None
    capture_version: str = "1"
    imported_by: str | None = None


class CaptureEmailIn(BaseModel):
    value: str
    evidence: str | None = None
    confidence: str | None = None
    extraction_method: str | None = None
    context: str | None = None


class CaptureConfirmIn(BaseModel):
    content_hash: str
    emails: list[CaptureEmailIn] = []
    socials: dict[str, str] = {}
    official_url: str | None = None
    maker_name: str | None = None
    source_url: str | None = None
    captured_at: str | None = None
    imported_by: str | None = None
    note: str | None = None


def _project_or_404(db: Session, project_id: int):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project


@router.get("/wadiz-browser-capture/resolve")
def resolve(url: str = Query(..., description="Wadiz 商品ページ URL"),
            db: Session = Depends(get_db)) -> dict:
    """Wadiz URL / campaign ID から対象 project 候補を返す（自動確定しない）。"""
    candidates = wadiz_import_service.resolve_projects(db, url)
    return {
        "url": url,
        "campaign_id": wadiz_import_service.extract_campaign_id(url),
        "match": "unique" if len(candidates) == 1 else (
            "multiple" if len(candidates) > 1 else "none"
        ),
        "candidates": [
            {"project_id": p.id, "title": p.title, "source_url": p.source_url,
             "maker_name": p.maker_name}
            for p in candidates[:10]
        ],
    }


@router.post("/projects/{project_id}/wadiz-browser-capture/preview")
def preview(project_id: int, payload: CaptureIn, db: Session = Depends(get_db)) -> dict:
    """取得 DOM から公開連絡先を抽出（DB 変更なし）。"""
    project = _project_or_404(db, project_id)
    content, content_type = wadiz_import_service.build_capture_content(payload.model_dump())
    if not content.strip():
        raise HTTPException(status_code=400, detail="取得内容が空です")
    result = wadiz_import_service.preview(
        db, project,
        content=content,
        content_type=content_type,
        source_url=payload.source_url,
        captured_at=payload.captured_at,
        imported_by=payload.imported_by,
        source_type=SOURCE_TYPE,
    )
    result["capture_version"] = payload.capture_version
    return result


@router.post("/projects/{project_id}/wadiz-browser-capture/confirm")
def confirm(project_id: int, payload: CaptureConfirmIn,
            db: Session = Depends(get_db)) -> dict:
    """ユーザーが確認・選択した結果だけを非破壊保存し、Contact Intelligence へ反映。"""
    project = _project_or_404(db, project_id)
    return wadiz_import_service.confirm(
        db, project,
        content_hash_value=payload.content_hash,
        emails=[e.model_dump() for e in payload.emails],
        socials=payload.socials,
        official_url=payload.official_url,
        maker_name=payload.maker_name,
        source_url=payload.source_url,
        content_type="html",
        captured_at=payload.captured_at,
        imported_by=payload.imported_by,
        note=payload.note,
        source_type=SOURCE_TYPE,
    )
