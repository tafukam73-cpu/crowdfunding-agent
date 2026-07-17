# Gold set 監査ログ（Contact Intelligence 評価基盤）

作成: 2026-07-18 / 対象: `tests/contact_intel_eval/`

## 1. 判明した問題

### 1.1 `gold_frozen_24.json` — expected_* が全件空
- 24 案件すべてで `expected_emails / expected_socials / expected_official_sites /
  expected_forms / expected_people` が空、`manually_verified=0`。
- このファイル単体では TP/FP/FN・precision/recall/F1 を測定できない（正解ラベル不在）。
- 用途を **prediction snapshot（saved_* のみ）** に限定する。ground truth には使わない。

### 1.2 `gold_partial.json` — auto-label 由来の汚染
- `harness.py build-partial` は「公式サイト上の live `mailto:` を無条件で
  `expected_emails` かつ `manually_verified=True` に昇格」する素朴ヒューリスティック。
- これにより **maker でない第三者メールが正解へ誤混入**した。全件監査結果:

| project | email | 実クラス | 監査判定 |
|---|---|---|---|
| p109 | `admin@reurl.cc` | url_shortener | **汚染**: reurl.cc は URL 短縮サービス。その運営者アドレスで maker でない |
| p108 | `info@mediafol.io` | (unknown) | **汚染**: 人手検証(GOLD_B)で「第三者」と確認済み。maker でない |
| p105 | `moreshop07@gmail.com` | personal | **未確認**: GOLD_B は `b_emails=[]`（gmail 未確定）。verified に昇格不可 |
| p108 | `info@singlestep.com` | (unknown) | 正: maker 公式メール |
| p136 | `momtobabydj@naver.com` | personal | 正: 人手検証済み maker CEO 連絡先 |
| p4 | `info@greenlab.com` | (unknown) | 出所不明（24 gold 対象外） |

- `admin@reurl.cc` 誤ラベルの根本原因: **prediction（サイト巡回で拾った mailto）を
  そのまま expected に採用**したこと。reurl.cc はユーザー指定の必須除外ドメインでもある。

## 2. 対応（この監査での変更）

### 2.1 ground truth を新設（prediction から生成しない）
- 追加: `build_ground_truth.py`（authoring スクリプト・**saved_* を一切読まない**）→
  `gold_ground_truth.json` を生成。
- 出典は `gold_set_v1.py` の `GOLD_B`（2026-07-18 に WebFetch で公式ページを
  literally 確認した人手検証記録）。証拠 URL・証拠文・検証日時・検証者を各案件に付与。
- スキーマ B: `expected_direct_emails / expected_fallback_emails / expected_forms /
  expected_socials / expected_people / expected_official_site / plausible_unconfirmed_emails /
  expected_no_public_contact / verification_status / blocked_reason / evidence_urls /
  evidence_snippets / verified_at / verified_by`。
- `verification_status`: verified(14) / partially_verified(2) / blocked(3) / unresolved(5)。
  blocked・unresolved は precision/recall の分母から除外し「取得失敗」で別集計。

### 2.2 `gold_partial.json` の扱い
- **正解セットとして使用しない**（汚染のため）。ファイル自体は履歴として残すが、
  評価 v2（`eval_v2.py`）は参照しない。
- 汚染エントリ（`admin@reurl.cc` / `info@mediafol.io` / `moreshop07@gmail.com`）は
  ground truth へ昇格させない。`gold_ground_truth.json` では:
  - p109 `admin@reurl.cc` → 記載せず（`blocked_reason` に「短縮URL運営者=非maker」と明記）。
  - p108 `info@mediafol.io` → `expected_direct_emails` から除外（証拠文に第三者と明記）。
  - p105 `moreshop07@gmail.com` → `plausible_unconfirmed_emails`（採点対象外）へ。

### 2.3 数値の作り方
- **expected を数値改善目的で改変していない**。gold ラベルはシステム出力から自動生成しない。
- ドメイン一致だが公式ページ未掲載のメールは `plausible_unconfirmed_emails` として
  TP/FP どちらにも数えない（不当な FP/TP を避ける）。

## 3. 変更種別
- `gold_ground_truth.json` / `build_ground_truth.py` / `eval_v2.py`: **新規追加（手動検証転記）**。
- `gold_frozen_24.json` / `gold_partial.json` / `gold_candidates.json`: **無変更**
  （prediction snapshot として温存。汚染ファイルも履歴保持）。

## 4. 変更前後
- 変更前: 信頼できる正解ラベル 0 件（frozen は空・partial は汚染）。
- 変更後: 人手検証 ground truth 24 案件（verified 14 / partial 2 / blocked 3 / unresolved 5、
  direct email 6・fallback email 5）。precision/recall を分母付きで測定可能。
