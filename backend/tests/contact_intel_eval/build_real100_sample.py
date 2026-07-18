"""実案件 100 件の official-site recall 評価テンプレート（cases CSV）を生成する（Phase C1）。

DB は **SELECT のみ**（読み取り専用）。projects と最新 contact_discoveries を結合し、
source_site 層化サンプリングで実案件を選ぶ。GT 列は空で出力し、人手レビュー（Phase C2）で
埋める。Step A/B 適用後の予測（official）は live 再実行（Phase C3）で別途取得するため、
ここには **旧保存 official（baseline）**のみを載せる。

原則:
  - DB へ書き込まない（commit/add/INSERT/UPDATE を一切行わない）。
  - PII を含めない（メール/電話/担当者/認証情報は出力しない。公開 URL・社名・ドメインのみ）。
  - 再現性のため固定 seed を使う。
  - Phase 3 開発に使った 24 gold 案件は偏り回避のため除外する。
  - 出力 CSV は .gitignore 済み（*.csv）。実案件の公開情報を含むため git に入れない。

出力: tests/contact_intel_eval/official_site_real100_cases.csv
実行: docker exec cfagent-backend python tests/contact_intel_eval/build_real100_sample.py
"""
from __future__ import annotations

import csv
import os
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_COMPONENT", "cfagent-eval")

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.services import source_ownership as so  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "official_site_real100_cases.csv"

TARGET = 100
SEED = 20260718  # 固定 seed（再現性）

# Phase 3 開発に使った 24 gold 案件（偏り回避のため評価母集団から除外）。
DEV_GOLD_IDS = {8, 9, 12, 19, 26, 91, 96, 97, 98, 104, 105, 107, 108, 109, 110,
                111, 115, 116, 117, 118, 122, 128, 135, 136}

COLUMNS = [
    "sample_id", "project_id", "campaign_url", "source_type", "maker_name",
    "maker_url", "predicted_official_url", "predicted_registered_domain",
    # --- 人手レビュー（Phase C2）で埋める GT 列。今回は空 ---
    "gt_official_url", "gt_registered_domain", "gt_status", "evidence_type",
    "evidence_url", "reviewer_note", "confidence",
    # --- 集計（Phase C3）で埋める判定列。今回は空 ---
    "result_class", "is_tp", "is_fp", "is_fn", "is_tn",
]


# PII 混入防止：DB のデータ品質問題で maker_name 等にメールが入っている場合があるため、
# 出力前にメールアドレスを redact する（評価ファイルに個人メールを載せない）。
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _redact(v: str) -> str:
    return _EMAIL_RE.sub("[redacted-email]", v or "")


def fetch_eligible(session) -> list[dict]:
    """eligible 実案件を SELECT で取得（最新 contact_discovery の official を baseline に）。"""
    sql = text(
        """
        SELECT p.id AS project_id, p.source_site, p.source_url, p.maker_name,
               p.maker_url, cd.official_site_url AS predicted_official_url
        FROM projects p
        LEFT JOIN LATERAL (
            SELECT official_site_url
            FROM contact_discoveries c
            WHERE c.project_id = p.id
            ORDER BY c.id DESC
            LIMIT 1
        ) cd ON true
        WHERE p.source_url IS NOT NULL
        ORDER BY p.id
        """
    )
    return [dict(r._mapping) for r in session.execute(sql)]


def dedup_by_maker(rows: list[dict]) -> list[dict]:
    """同一 maker_name（大小無視）は最初の 1 件のみ。maker_name が空はそのまま残す。"""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        mk = (r.get("maker_name") or "").strip().lower()
        if mk:
            if mk in seen:
                continue
            seen.add(mk)
        out.append(r)
    return out


def allocate(sizes: dict[str, int], target: int) -> dict[str, int]:
    """source_site サイズに比例して target 件を配分（largest-remainder・層サイズ上限）。"""
    total = sum(sizes.values())
    target = min(target, total)
    if total == 0:
        return {k: 0 for k in sizes}
    raw = {k: v / total * target for k, v in sizes.items()}
    alloc = {k: min(int(raw[k]), sizes[k]) for k in sizes}
    rem = target - sum(alloc.values())
    order = sorted(sizes, key=lambda k: (raw[k] - int(raw[k]), sizes[k]), reverse=True)
    i = 0
    guard = 0
    while rem > 0 and guard < 100000:
        k = order[i % len(order)]
        if alloc[k] < sizes[k]:
            alloc[k] += 1
            rem -= 1
        i += 1
        guard += 1
    return alloc


def pick(rows: list[dict], n: int, rng: random.Random) -> list[dict]:
    """層から n 件抽出。maker_url あり/なしの両方を（可能なら各 ≥1）含める。"""
    n = min(n, len(rows))
    have = [r for r in rows if (r.get("maker_url") or "").strip()]
    lack = [r for r in rows if not (r.get("maker_url") or "").strip()]
    rng.shuffle(have)
    rng.shuffle(lack)
    picked: list[dict] = []
    # 両群が存在し n>=2 なら各群 1 件を先に確保
    if n >= 2 and have and lack:
        picked.append(have.pop())
        picked.append(lack.pop())
    # 残りは層内比率に比例して埋める（不足側は他群で補う）
    pool_have, pool_lack = have[:], lack[:]
    while len(picked) < n and (pool_have or pool_lack):
        total = len(pool_have) + len(pool_lack)
        take_have = rng.random() < (len(pool_have) / total if total else 0)
        if take_have and pool_have:
            picked.append(pool_have.pop())
        elif pool_lack:
            picked.append(pool_lack.pop())
        elif pool_have:
            picked.append(pool_have.pop())
    return picked


def main() -> int:
    rng = random.Random(SEED)
    session = SessionLocal()
    try:
        rows = fetch_eligible(session)
    finally:
        session.close()  # 読み取り専用・commit しない

    pool = [r for r in rows if r["project_id"] not in DEV_GOLD_IDS]
    pool = dedup_by_maker(pool)

    strata: dict[str, list[dict]] = defaultdict(list)
    for r in pool:
        strata[r["source_site"]].append(r)
    sizes = {k: len(v) for k, v in strata.items()}
    alloc = allocate(sizes, TARGET)

    selected: list[dict] = []
    for site in sorted(strata):
        selected.extend(pick(strata[site], alloc.get(site, 0), rng))
    # sample_id は決定的順序（source_site, project_id）で採番
    selected.sort(key=lambda r: (r["source_site"], r["project_id"]))

    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for i, r in enumerate(selected, 1):
            pred = (r.get("predicted_official_url") or "").strip()
            w.writerow({
                "sample_id": f"R{i:03d}",
                "project_id": r["project_id"],
                "campaign_url": _redact(r.get("source_url") or ""),
                "source_type": r["source_site"],
                "maker_name": _redact(r.get("maker_name") or ""),
                "maker_url": _redact(r.get("maker_url") or ""),
                "predicted_official_url": _redact(pred),
                "predicted_registered_domain": so.registrable_domain(pred) if pred else "",
                # GT / 判定列は空（Phase C2 / C3 で記入）
                "gt_official_url": "", "gt_registered_domain": "", "gt_status": "",
                "evidence_type": "", "evidence_url": "", "reviewer_note": "",
                "confidence": "", "result_class": "", "is_tp": "", "is_fp": "",
                "is_fn": "", "is_tn": "",
            })

    # サマリ（PII なし）
    by_site: dict[str, int] = defaultdict(int)
    with_maker = 0
    with_pred = 0
    for r in selected:
        by_site[r["source_site"]] += 1
        if (r.get("maker_url") or "").strip():
            with_maker += 1
        if (r.get("predicted_official_url") or "").strip():
            with_pred += 1
    print(f"eligible pool（source_url あり・dev24除外・maker dedup 後）= {len(pool)}")
    print(f"source_site 別 pool サイズ = {sizes}")
    print(f"配分 alloc = {alloc}")
    print(f"抽出 = {len(selected)} 件 -> {OUT.name}")
    print(f"  source_type 別 = {dict(by_site)}")
    print(f"  maker_url あり = {with_maker} / なし = {len(selected) - with_maker}")
    print(f"  baseline predicted official あり = {with_pred}")
    print("  GT 列は空（Phase C2 で人手入力）。CSV は .gitignore 済み（PII 非混入）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
