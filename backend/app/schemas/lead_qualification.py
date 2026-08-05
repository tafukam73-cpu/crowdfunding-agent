"""営業対象除外判定（Lead Qualification Engine）の API スキーマ。

## 返さないもの（CLAUDE.md §1）

根拠のない予測値をユーザー向けに出さない。以下は **一切返さない**。

- 数値の confidence（ラベル `high` / `medium` / `low` / `unverified` だけを返す）
- score / probability / forecast / reply rate / success rate
- makuake_fit / japan_crowdfunding_score などの内部スコア

## internal_db の扱い

DB の状態そのものを根拠とする証跡は `source_kind="internal_db"` ＋
`source_url="db://projects/<id>#<anchor>"` で表される。これは外部 Web リンクでは
ないため、``is_external_link=False`` を返し、**UI でクリック可能なリンクにしない**。

``is_external_link`` が True になるのは次の両方を満たすときだけ。

1. ``source_kind`` が ``"internal_db"`` でない
2. ``source_url`` が Pydantic の ``AnyHttpUrl`` として妥当（= http / https）

``db://`` / 空文字 / 不正 URL / ``file://`` / ローカルパスはすべて False。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

# 判定ステージ / 判定値（サービス側の定数と一致させる）。
StageLiteral = Literal["pre_research", "pre_outreach"]
DecisionLiteral = Literal["blocked", "review", "clear"]
# confidence は **ラベルのみ**。数値は返さない。
ConfidenceLiteral = Literal["high", "medium", "low", "unverified"]

# 内部ロケータ専用の source_kind。これが付いた証跡は外部リンクとして扱わない。
INTERNAL_DB_SOURCE_KIND = "internal_db"

_HTTP_URL = TypeAdapter(AnyHttpUrl)


def is_external_link(source_kind: str | None, source_url: str | None) -> bool:
    """証跡 URL を外部 Web リンクとして扱ってよいか。

    内部ロケータ（internal_db / db://）は False。URL の妥当性判定は自前の
    文字列処理ではなく Pydantic の ``AnyHttpUrl`` に委ねる（標準検証を優先）。
    """
    if source_kind == INTERNAL_DB_SOURCE_KIND:
        return False
    if not source_url:
        return False
    try:
        _HTTP_URL.validate_python(source_url)
    except Exception:  # noqa: BLE001  検証に通らないものは外部リンクにしない
        return False
    return True


class EvidenceOut(BaseModel):
    """1 件の証跡（evidence-ledger の 4 点セット＋抜粋）。"""

    claim: str | None = None
    source_url: str | None = None
    source_kind: str | None = None
    method: str | None = None
    checked_at: str | None = None
    excerpt: str | None = None
    # UI でリンク化してよいか。internal_db / db:// は必ず False。
    is_external_link: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "EvidenceOut":
        return cls(
            claim=data.get("claim"),
            source_url=data.get("source_url"),
            source_kind=data.get("source_kind"),
            method=data.get("method"),
            checked_at=data.get("checked_at"),
            excerpt=data.get("excerpt"),
            is_external_link=is_external_link(
                data.get("source_kind"), data.get("source_url")
            ),
        )


class FindingOut(BaseModel):
    """1 カテゴリ（A〜T）の判定結果。"""

    code: str
    key: str
    label: str
    stage: StageLiteral
    verdict: str
    severity: str
    # **ラベルのみ。** 数値 confidence は返さない。
    confidence: ConfidenceLiteral
    reason: str
    evidence: list[EvidenceOut] = Field(default_factory=list)
    rule_version: str
    entity_role: str = "unknown"
    facts: dict[str, Any] = Field(default_factory=dict)
    downgraded_from: str | None = None
    downgrade_reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "FindingOut":
        return cls(
            code=data["code"],
            key=data["key"],
            label=data["label"],
            stage=data["stage"],
            verdict=data["verdict"],
            severity=data["severity"],
            confidence=data["confidence"],
            reason=data["reason"],
            evidence=[EvidenceOut.from_dict(e) for e in (data.get("evidence") or [])],
            rule_version=data["rule_version"],
            entity_role=data.get("entity_role") or "unknown",
            facts=data.get("facts") or {},
            downgraded_from=data.get("downgraded_from"),
            downgrade_reason=data.get("downgrade_reason"),
        )


class PositiveFactOut(BaseModel):
    """営業する根拠（確認できた事実のみ。推測は含まない）。"""

    key: str
    label: str
    evidence: list[EvidenceOut] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "PositiveFactOut":
        return cls(
            key=data["key"],
            label=data["label"],
            evidence=[EvidenceOut.from_dict(e) for e in (data.get("evidence") or [])],
        )


class QualificationOut(BaseModel):
    """1 案件・1 ステージの判定。

    ``machine_decision`` は機械判定、``effective_decision`` は人の上書きを含む
    実効判定。上書きが無ければ両者は一致し ``overridden=False``。
    """

    project_id: int
    stage: StageLiteral
    # 実効判定（後方互換のため decision も同値で返す）
    decision: DecisionLiteral
    machine_decision: DecisionLiteral
    effective_decision: DecisionLiteral
    overridden: bool = False
    # 履歴として保存済みか。False は「その場で算出した未保存の判定」。
    persisted: bool
    blocker_codes: list[str] = Field(default_factory=list)
    review_codes: list[str] = Field(default_factory=list)
    findings: list[FindingOut] = Field(default_factory=list)
    positive_facts: list[PositiveFactOut] = Field(default_factory=list)
    evidence_count: int = 0
    rule_version: str
    evaluated_at: str | None = None
    override_reason: str | None = None
    override_evidence_url: str | None = None


class OverrideRequest(BaseModel):
    """人が判定を覆すときの入力。**理由と根拠 URL の両方が必須**。"""

    model_config = ConfigDict(extra="forbid")

    stage: StageLiteral = "pre_research"
    decision: DecisionLiteral
    # 空白のみは不可（strip 後に 1 文字以上）。
    reason: str = Field(..., min_length=1, max_length=1000)
    # http(s) のみ。db:// / file:// / ローカルパスは 422 で弾く。
    evidence_url: AnyHttpUrl

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("reason は空白のみにできません")
        return stripped


class OverrideResult(BaseModel):
    """上書きの結果。機械判定と同じ値を指定した場合は ``changed=False``。"""

    changed: bool
    qualification: QualificationOut


class RecheckResult(BaseModel):
    """再判定の結果。履歴を 1 行追加したことを ``persisted`` で表す。"""

    qualification: QualificationOut
    # pre_research のときだけ projects のスナップショットを更新する。
    snapshot_updated: bool
