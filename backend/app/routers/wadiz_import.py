"""Wadiz 手動取り込み API（人間ブラウザ取得 → 安全な抽出・保存）。

- POST /projects/{id}/wadiz-import/preview   貼り付け本文から抽出（DB 変更なし）
- POST /projects/{id}/wadiz-import/confirm    確認済み結果を保存＋Contact Intelligence反映
- GET  /projects/{id}/wadiz-imports           取り込み履歴

プレビューは保存しない。confirm 時のみ非破壊で保存し、冪等（raw_content_hash）。
GET は履歴の読み取りのみで重い処理を起動しない。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import project_service, wadiz_import_service

router = APIRouter(tags=["wadiz-import"])


class WadizPreviewIn(BaseModel):
    content: str
    content_type: str = "text"
    source_url: str | None = None
    captured_at: str | None = None
    imported_by: str | None = None


class WadizEmailIn(BaseModel):
    value: str
    evidence: str | None = None
    confidence: str | None = None
    extraction_method: str | None = None


class WadizConfirmIn(BaseModel):
    content_hash: str
    emails: list[WadizEmailIn] = []
    socials: dict[str, str] = {}
    official_url: str | None = None
    maker_name: str | None = None
    source_url: str | None = None
    content_type: str = "text"
    captured_at: str | None = None
    imported_by: str | None = None
    note: str | None = None


def _project_or_404(db: Session, project_id: int):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project


@router.post("/projects/{project_id}/wadiz-import/preview")
def wadiz_import_preview(
    project_id: int, payload: WadizPreviewIn, db: Session = Depends(get_db)
) -> dict:
    """貼り付け本文/HTML からメール・SNS・公式サイト・メーカー情報を抽出（DB 変更なし）。"""
    project = _project_or_404(db, project_id)
    if not (payload.content or "").strip():
        raise HTTPException(status_code=400, detail="本文が空です")
    return wadiz_import_service.preview(
        db, project,
        content=payload.content,
        content_type=payload.content_type,
        source_url=payload.source_url,
        captured_at=payload.captured_at,
        imported_by=payload.imported_by,
    )


@router.post("/projects/{project_id}/wadiz-import/confirm")
def wadiz_import_confirm(
    project_id: int, payload: WadizConfirmIn, db: Session = Depends(get_db)
) -> dict:
    """ユーザーが確認・選択した結果だけを保存し、Contact Intelligence へ非破壊反映する。"""
    project = _project_or_404(db, project_id)
    return wadiz_import_service.confirm(
        db, project,
        content_hash_value=payload.content_hash,
        emails=[e.model_dump() for e in payload.emails],
        socials=payload.socials,
        official_url=payload.official_url,
        maker_name=payload.maker_name,
        source_url=payload.source_url,
        content_type=payload.content_type,
        captured_at=payload.captured_at,
        imported_by=payload.imported_by,
        note=payload.note,
    )


@router.get("/projects/{project_id}/wadiz-imports")
def wadiz_import_history(project_id: int, db: Session = Depends(get_db)) -> dict:
    """取り込み履歴（読み取りのみ）。"""
    _project_or_404(db, project_id)
    rows = wadiz_import_service.get_imports(db, project_id)
    return {
        "items": [
            {
                "id": r.id,
                "source_url": r.source_url,
                "content_type": r.content_type,
                "email_count": r.email_count,
                "imported_by": r.imported_by,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "emails": [
                    e.get("value") for e in (r.extracted_json or {}).get("emails", [])
                ],
            }
            for r in rows
        ]
    }
