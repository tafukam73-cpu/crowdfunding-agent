"""営業対象除外判定（Lead Qualification Engine）の API。

判定の正本は ``lead_qualification_service``。このルーターは薄い入出力層に徹し、
判定ロジックを持たない。

## 履歴の書き込み方針

- ``GET``  … **履歴を書かない。** 保存済みが無ければその場で算出し
  ``persisted=false`` を返す（画面を開くたびに履歴が増えるのを防ぐ）
- ``POST /recheck``  … ``run()`` で履歴を 1 行追加
- ``POST /override`` … 機械判定を取り直し、実効判定を上書きした履歴を **1 行だけ** 追加

いずれも外部 HTTP を行わないため同期 POST でよい（CLAUDE.md §5 の
「重い処理の同期 POST 禁止」に抵触しない）。

## 返さないもの（CLAUDE.md §1）

数値 confidence / score / probability / forecast / 返信率 / 成功率 /
makuake_fit / japan_crowdfunding_score は返さない。confidence はラベルのみ。

## 自動アーカイブはしない

``blocked`` を返すだけで ``archived_at`` / ``archive_reason`` には触れない。
アーカイブは既存の ``PATCH /projects/{id}/archive`` を人が実行する。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.lead_qualification import (
    FindingOut,
    OverrideRequest,
    OverrideResult,
    PositiveFactOut,
    QualificationOut,
    RecheckResult,
    StageLiteral,
)
from app.services import lead_qualification_service as lqs
from app.services import project_service

router = APIRouter(tags=["lead-qualification"])


def _get_project_or_404(db: Session, project_id: int):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project


def _out_from_row(row) -> QualificationOut:
    """保存済み履歴 1 行を API 形へ変換する。"""
    meta = lqs.qualification_meta(row) or {}
    machine = meta.get("machine_decision") or row.decision
    effective = meta.get("effective_decision") or row.decision
    return QualificationOut(
        project_id=row.project_id,
        stage=row.stage,
        decision=effective,
        machine_decision=machine,
        effective_decision=effective,
        overridden=bool(meta.get("overridden")),
        persisted=True,
        blocker_codes=list(row.blocker_codes or []),
        review_codes=list(row.review_codes or []),
        findings=[FindingOut.from_dict(f) for f in lqs.findings_of(row)],
        positive_facts=[
            PositiveFactOut.from_dict(p) for p in (row.positive_facts_json or [])
        ],
        evidence_count=row.evidence_count or 0,
        rule_version=row.engine,
        evaluated_at=row.created_at.isoformat() if row.created_at else None,
        override_reason=row.override_reason,
        override_evidence_url=row.override_evidence_url,
    )


def _out_from_result(result) -> QualificationOut:
    """未保存の判定（その場算出）を API 形へ変換する。"""
    data = result.to_dict()
    return QualificationOut(
        project_id=data["project_id"],
        stage=data["stage"],
        decision=data["decision"],
        machine_decision=data["decision"],
        effective_decision=data["decision"],
        overridden=False,
        persisted=False,
        blocker_codes=list(data["blocker_codes"]),
        review_codes=list(data["review_codes"]),
        findings=[FindingOut.from_dict(f) for f in data["findings"]],
        positive_facts=[PositiveFactOut.from_dict(p) for p in data["positive_facts"]],
        evidence_count=data["evidence_count"],
        rule_version=data["rule_version"],
        evaluated_at=data["evaluated_at"],
    )


@router.get(
    "/projects/{project_id}/lead-qualification", response_model=QualificationOut
)
def get_lead_qualification(
    project_id: int,
    stage: StageLiteral = Query(
        "pre_research", description="pre_research（調査前）/ pre_outreach（送信前）"
    ),
    db: Session = Depends(get_db),
) -> QualificationOut:
    """ステージ別の最新判定を返す。**履歴は書かない。**

    保存済み履歴が無ければ、その場で判定して ``persisted=false`` で返す。
    最新履歴が人の上書きなら ``overridden=true`` になり、``machine_decision`` と
    ``effective_decision`` で機械判定と実効判定を区別できる。
    """
    project = _get_project_or_404(db, project_id)
    latest = lqs.get_latest(db, project_id, stage=stage)
    if latest is not None:
        return _out_from_row(latest)
    # 未判定 → その場で算出（保存しない）。
    signals = lqs.gather_signals(db, project)
    return _out_from_result(lqs.qualify(signals, stage))


@router.post(
    "/projects/{project_id}/lead-qualification/recheck", response_model=RecheckResult
)
def recheck_lead_qualification(
    project_id: int,
    stage: StageLiteral = Query("pre_research", description="判定ステージ"),
    db: Session = Depends(get_db),
) -> RecheckResult:
    """再判定して履歴を 1 行追加する。

    外部 HTTP を行わないため同期で実行してよい。``pre_research`` のときだけ
    ``projects`` のスナップショット 2 列を更新する。
    """
    project = _get_project_or_404(db, project_id)
    row = lqs.run(db, project, stage)
    return RecheckResult(
        qualification=_out_from_row(row),
        snapshot_updated=stage == lqs.STAGE_PRE_RESEARCH,
    )


@router.post(
    "/projects/{project_id}/lead-qualification/override", response_model=OverrideResult
)
def override_lead_qualification(
    project_id: int,
    payload: OverrideRequest,
    db: Session = Depends(get_db),
) -> OverrideResult:
    """人が判定を覆した記録を履歴 1 行として残す。

    ``reason`` と ``evidence_url`` は両方必須（``evidence_url`` は http(s) のみ。
    ``db://`` は受け付けない）。履歴が 1 件も無くても、その場で機械判定してから
    上書きできる。機械判定と同じ値を指定してもエラーにせず、監査記録として保存し
    ``changed=false`` を返す。
    """
    project = _get_project_or_404(db, project_id)
    row, changed = lqs.record_override(
        db,
        project,
        payload.stage,
        payload.decision,
        reason=payload.reason,
        evidence_url=str(payload.evidence_url),
    )
    return OverrideResult(changed=changed, qualification=_out_from_row(row))
