"""Discovery Crawler Framework（Discovery Engine v1-3）。

海外クラウドファンディングの商品候補を発掘する共通フレームワーク。発掘元ごとの
取得・抽出は platform adapter（``discovery_adapters/``）に委譲し、本サービスは
オーケストレーションに徹する：

  1. source_platform に対応する adapter を選ぶ
  2. adapter に query / limit / fetch_fn（注入可能）/ records を渡して
     ``DiscoveryCandidate`` を得る
  3. source_url を正規化し、実行内・DB それぞれで重複を排除する
  4. discovered_products に保存する（既存の discovery_service を再利用）
  5. auto_score=True なら既存の AI Discovery Scoring Engine で評価する
  6. 実行結果サマリ（found / saved / duplicate / errors / product_ids）を返し、
     discovery_runs に 1 行記録する

安全設計（v1-3）：
- 実ネットワークは既定で行わない。ネットワーク系 adapter は ``fetch_fn`` を注入
  したときのみ取得する（未注入なら空）。よって本 API を叩いても外部送信・課金・
  スクレイピングサービスは一切発生しない。
- successful / ended / failed / canceled も**保存する**（発掘対象から除外しない）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.discovery_run import DiscoveryRun, DiscoveryRunStatus
from app.services import discovery_service
from app.services.discovery_adapters import get_adapter
from app.services.discovery_adapters.base import FetchFn, normalize_url

logger = logging.getLogger("discovery_crawler")


def run(
    db: Session,
    *,
    source_platform: str,
    query: str | None = None,
    limit: int = 20,
    auto_score: bool = False,
    fetch_fn: FetchFn | None = None,
    records: list[dict] | None = None,
    ai_fn: Callable[[dict], Any] | None = None,
    record_run: bool = True,
) -> dict:
    """発掘を 1 回実行し、結果サマリ（dict）を返す。

    引数:
      source_platform: 発掘元（kickstarter / indiegogo / backerkit / manual …）
      query:           検索クエリ（adapter が解釈。manual では未使用）
      limit:           保存対象の上限件数
      auto_score:      True なら保存時に AI Discovery Scoring で評価する
      fetch_fn:        本文取得関数（注入）。未指定ならネットワーク系は空を返す
      records:         manual 用の候補レコード（dict のリスト）
      ai_fn:           スコアリングの AI 注入（テスト・将来用。未指定はルール評価）
      record_run:      True なら discovery_runs に実行ログを残す

    返り値のキー:
      run_id, source_platform, query, status, found_count, saved_count,
      duplicate_count, error_message, product_ids, started_at, finished_at
    """
    started_at = datetime.now(timezone.utc)
    limit = max(0, int(limit or 0))
    errors: list[str] = []
    candidates = []

    adapter = get_adapter(source_platform)
    platform_value = adapter.platform

    try:
        candidates = adapter.discover(
            query, limit, fetch_fn=fetch_fn, records=records
        )
    except Exception as exc:  # noqa: BLE001  収集失敗はサマリに載せて継続
        logger.exception("discovery adapter failed: platform=%s", source_platform)
        errors.append(f"収集エラー: {exc}")

    found_count = len(candidates)
    product_ids: list[int] = []
    duplicate_count = 0
    seen_urls: set[str] = set()

    for candidate in candidates:
        data = candidate.to_product_dict(default_platform=platform_value)
        norm_url = data.get("source_url")

        # 実行内の重複（同一 source_url）を先に弾く
        if norm_url is not None:
            if norm_url in seen_urls:
                duplicate_count += 1
                continue
            seen_urls.add(norm_url)

        try:
            product, created = discovery_service.create(
                db, data, auto_score=auto_score, ai_fn=ai_fn
            )
        except Exception as exc:  # noqa: BLE001  1件保存失敗は継続
            logger.warning("discovery save failed (%s): %s", norm_url, exc)
            errors.append(f"保存エラー({norm_url}): {exc}")
            continue

        if created:
            product_ids.append(product.id)
        else:
            # DB に既存（source_url 重複）→ 二重保存しない
            duplicate_count += 1

    finished_at = datetime.now(timezone.utc)
    saved_count = len(product_ids)

    if errors and saved_count > 0:
        status = DiscoveryRunStatus.partial.value
    elif errors:
        status = DiscoveryRunStatus.error.value
    else:
        status = DiscoveryRunStatus.success.value

    error_message = "\n".join(errors) if errors else None

    run_id = None
    if record_run:
        run_id = _record_run(
            db,
            source_platform=platform_value,
            query=query,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            found_count=found_count,
            saved_count=saved_count,
            duplicate_count=duplicate_count,
            error_message=error_message,
        )

    # 実ネットワーク取得を試みたか（fetch_fn 注入の有無）。取得0件のとき
    # 「実取得したが0件」と「fetch未接続で0件」を画面で区別するために返す。
    network_fetched = fetch_fn is not None

    logger.info(
        "discovery run done: platform=%s found=%s saved=%s dup=%s status=%s "
        "network_fetched=%s",
        platform_value, found_count, saved_count, duplicate_count, status,
        network_fetched,
    )

    return {
        "run_id": run_id,
        "source_platform": platform_value,
        "query": query,
        "status": status,
        "found_count": found_count,
        "saved_count": saved_count,
        "duplicate_count": duplicate_count,
        "error_message": error_message,
        "product_ids": product_ids,
        "started_at": started_at,
        "finished_at": finished_at,
        "network_fetched": network_fetched,
    }


def _record_run(
    db: Session,
    *,
    source_platform: str,
    query: str | None,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    found_count: int,
    saved_count: int,
    duplicate_count: int,
    error_message: str | None,
) -> int | None:
    """discovery_runs に実行ログを 1 行残す（失敗しても収集結果は壊さない）。"""
    try:
        run_row = DiscoveryRun(
            source_platform=source_platform,
            query=query,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            found_count=found_count,
            saved_count=saved_count,
            duplicate_count=duplicate_count,
            error_message=error_message,
        )
        db.add(run_row)
        db.commit()
        db.refresh(run_row)
        return run_row.id
    except Exception as exc:  # noqa: BLE001  ログ記録失敗は握りつぶす
        logger.warning("discovery_run の記録に失敗: %s", exc)
        db.rollback()
        return None


# 正規化 URL を外部から使えるよう再エクスポート（テスト・呼び出し側の利便）
__all__ = ["run", "normalize_url"]
