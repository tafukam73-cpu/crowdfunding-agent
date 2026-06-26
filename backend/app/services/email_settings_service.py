"""メール設定の業務ロジック。

利用者は 1 人前提のため、レコードは 1 件（id=1）固定で運用する。
get_settings は未登録時 None を返し、呼び出し側はフォールバックできる。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.email_settings import EmailSettings
from app.schemas.email_settings import EmailSettingsUpdate

# 単一レコード運用の固定 ID
SETTINGS_ID = 1


def get_settings(db: Session) -> EmailSettings | None:
    """保存済みのメール設定を返す（未登録なら None）。"""
    return db.get(EmailSettings, SETTINGS_ID)


def upsert_settings(db: Session, data: EmailSettingsUpdate) -> EmailSettings:
    """メール設定を作成または更新して返す（id=1 固定）。"""
    row = db.get(EmailSettings, SETTINGS_ID)
    values = data.model_dump(exclude_unset=True)
    if row is None:
        row = EmailSettings(id=SETTINGS_ID, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row
