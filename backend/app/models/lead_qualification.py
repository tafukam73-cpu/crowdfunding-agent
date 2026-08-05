"""営業対象除外判定の履歴（Lead Qualification Engine / PR-2）。

1 案件・1 ステージの判定結果を **追記専用（append-only）** で保存する。

## append-only の前提

- 判定のたびに 1 行 INSERT する。**既存行を UPDATE / DELETE しない。**
- 同じ入力・同じ結果でも、再判定すれば新しい行が増える。
  ルールを変えたときに「旧判定と何が変わったか」を後から比較できることが、
  この設計の唯一の目的である（`sales_assessments` / `project_status_events`
  と同じ方針）。
- 最新の判定は `created_at` / `id` の降順 1 件で取得する。
  一覧のフィルタ用キャッシュは `projects.lead_qualification_decision` /
  `.lead_qualification_at` に持つ（あちらは上書き更新してよい）。

## スナップショット列は pre_research 専用

`projects.lead_qualification_decision` / `.lead_qualification_at` が保持するのは
**最新の `pre_research` 判定だけ**。

- `stage="pre_research"` の判定（run / override）だけがこの 2 列を更新する
- `stage="pre_outreach"` の判定は **履歴にのみ残し、2 列を変更しない**
- 一覧の `?qualification=` フィルタは pre_research スナップショットとして扱う

送信可否（pre_outreach）は案件一覧の絞り込み軸ではなく、送信直前に判定するもの
だから（PR-5 の責務）。

## override の表現

人が判定を覆した場合も **履歴 1 行の追記**で表す（既存行を書き換えない）。

- `decision` 列には実効判定（人の指定値）が入る
- `findings_json` の末尾に予約メタデータを 1 要素だけ足す:
  `{"_qualification_meta": {"machine_decision", "effective_decision", "overridden"}}`
- メタは通常の Finding と混同しない（Finding は必ず `code` を持つ）
- メタは `evidence_count` に加算しない
- `blocker_codes` / `review_codes` は機械判定のまま残す

## findings_json / positive_facts_json 内の Evidence の契約

`lead_qualification_service.Evidence` を dict 化したものが入る。
証跡 URL の扱いには固定された契約がある。

- 外部ページの証跡は、実際に取得した URL のみ。**URL を組み立てない。**
- DB の状態そのものが根拠になる場合に限り、内部ロケータを使う:
  - `method == "db_state"` のときだけ `source_url` が `db://` で始まってよい
  - その `source_kind` は必ず `"internal_db"`（外部 URL と明確に区別する）
  - 形式は `db://projects/<project_id>#<anchor>` のみ
  - **外部 Web リンクとして扱わない。UI でクリック可能なリンクにしない**
    （表示側の実装は PR-6。ここではデータ側の契約として明記する）
  - **外部証跡の代用にしない**（外部で確認すべき事実を `db://` で置き換えない）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LeadQualification(Base):
    __tablename__ = "lead_qualifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 判定ステージ（pre_research / pre_outreach）。
    stage: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 3 段階の判定（blocked / review / clear）。
    decision: Mapped[str] = mapped_column(String(12), nullable=False, index=True)

    # 判定を止めた／人の判断が要るカテゴリ記号（["F", "N"] など）。
    blocker_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    review_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 全カテゴリ（A〜T）の Finding。no_hit も含めて保存する
    # （「何を確認したか」が後から検証できるようにするため）。
    findings_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 営業する根拠（確認できた事実のみ）。推測は入れない。
    positive_facts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 4 点セットが揃った証跡の総数（findings ＋ positive_facts）。
    # QualificationResult の値をそのまま保存する（**再計算しない**）。
    evidence_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 判定エンジンのバージョン（lqe-v1 など）。
    engine: Mapped[str] = mapped_column(String(60), nullable=False)

    # 人が判定を覆した場合の記録。理由だけでは覆せず、根拠 URL が要る。
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_evidence_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
