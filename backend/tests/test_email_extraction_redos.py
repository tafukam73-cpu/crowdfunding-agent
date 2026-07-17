"""巨大トークンに対するメール抽出の走査コストを固定する回帰テスト（ハング防止版）。

背景（実障害）：Indiegogo の実ページには base64 の webpack sourcemap がインラインで
埋まっており、local-part 文字（[A-Za-z0-9._%+-]）だけが **215,391 文字** 連続する。
当時（および本修正前）の EMAIL_RE / _OBF_EMAIL_RE / _SPLIT_EMAIL_RE は local-part の
量指定子が開いていたため、findall が開始位置ごとにトークン全体を舐め直して O(n^2) に
なり、extract_emails が数百秒 CPU を占有して返らなかった（本調査で PID の utime 422s を
実測）。「じっくり調査」は progress=1% のままハードタイムアウトで kill され、その案件は
永久に同じ場所で停止した。

**このテスト自体がハングしない設計**にする：
- 各抽出は **独立プロセスグループの子プロセス**で実行し、親がハードタイムアウトで
  プロセスツリーごと kill する。C 実装の正規表現は SIGALRM では中断できないため、
  「別プロセス＋killpg」でしか確実に打ち切れない。
- タイムアウトは **失敗**として記録する（成功扱いにしない）。
- 抽出関数そのものの実行時間（子が測定した __DT__）が危険入力でも 2 秒未満であること、
  スイート全体が 60 秒未満であることを固定する。
- 実行後に子プロセスが残らないことを確認する。

ネットワーク・API キー・実 DB 不要（子は sqlite 固定）。

実行（backend ディレクトリで）:
    python tests/test_email_extraction_redos.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
CHILD = Path(__file__).resolve().parent / "_redos_child.py"

# 親のウォールクロック上限（Python 起動＋import 込み）。超過＝ハング＝FAIL。
_PER_CASE_HARD_TIMEOUT_S = 10.0
# 抽出関数そのもの（子が測定）の上限。危険入力でも 2 秒以内であること。
_EXTRACT_BUDGET_S = 2.0
# スイート全体の上限。
_SUITE_BUDGET_S = 60.0

# 実ページで観測した最長ランと同等（base64 sourcemap 相当）。
_BLOB = "eyJ2ZXJzaW9uIjozLCJzb3VyY2VzIjpb" * 6800  # ≒ 217,600 文字

_results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    _results.append((name, ok, detail))
    print(("  ok  - " if ok else "  FAIL- ") + f"{name} — {detail}")


def _run_extraction(func: str, html: str) -> tuple[float | None, str, str]:
    """子プロセスで抽出を実行。戻り値 (dt or None, res_repr, note)。

    dt が None なら異常（タイムアウト kill / 子プロセスエラー）。
    """
    proc = subprocess.Popen(
        [sys.executable, str(CHILD), func],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # 独立プロセスグループ（killpg のため）
    )
    try:
        out, err = proc.communicate(input=html, timeout=_PER_CASE_HARD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # プロセスグループごと確実に終了（暴走 regex ＋子孫を残さない）。
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        return None, "", (
            f"ハードタイムアウト {_PER_CASE_HARD_TIMEOUT_S}s 超過 → killpg（ハング検出）"
        )
    if proc.returncode != 0:
        return None, "", f"子プロセス異常終了 rc={proc.returncode} err={err[:200]!r}"
    dt: float | None = None
    res_repr = ""
    for line in out.splitlines():
        if line.startswith("__DT__="):
            dt = float(line.split("=", 1)[1])
        elif line.startswith("__RES__="):
            res_repr = line.split("=", 1)[1]
    if dt is None:
        return None, res_repr, f"__DT__ を取得できず out={out[:200]!r}"
    return dt, res_repr, ""


def case_huge_token_extract_emails() -> None:
    html = f"<html><body><script>var m='{_BLOB}';</script></body></html>"
    dt, _res, note = _run_extraction("extract_emails", html)
    if dt is None:
        record("extract_emails: 217k base64 でハングしない", False, note)
        return
    record(
        "extract_emails: 217k base64 でハングしない",
        dt < _EXTRACT_BUDGET_S,
        f"抽出 {dt:.3f}s < {_EXTRACT_BUDGET_S}s",
    )


def case_huge_token_deobfuscate() -> None:
    html = f"<p>data at {_BLOB} end</p>"
    dt, _res, note = _run_extraction("deobfuscate_emails", html)
    if dt is None:
        record("deobfuscate_emails: 217k base64 でハングしない", False, note)
        return
    record(
        "deobfuscate_emails: 217k base64 でハングしない",
        dt < _EXTRACT_BUDGET_S,
        f"抽出 {dt:.3f}s < {_EXTRACT_BUDGET_S}s",
    )


def case_not_quadratic() -> None:
    """入力 4 倍で時間が概ね線形（<< 16 倍）に収まる（二次爆発を落とす）。"""
    small = "x at " + ("a" * 20_000)
    large = "x at " + ("a" * 80_000)
    dt_s, _r1, n1 = _run_extraction("deobfuscate_emails", small)
    dt_l, _r2, n2 = _run_extraction("deobfuscate_emails", large)
    if dt_s is None or dt_l is None:
        record("scan cost が二次でない", False, f"small={n1} large={n2}")
        return
    ratio = (dt_l / dt_s) if dt_s > 1e-6 else 1.0
    record(
        "scan cost が二次でない",
        ratio < 9.0 and dt_l < _EXTRACT_BUDGET_S,
        f"4 倍長で時間比 {ratio:.1f}x (<9x), large {dt_l:.3f}s",
    )


def case_real_emails_still_extracted() -> None:
    html = (
        f"<script>var m='{_BLOB}';</script>"
        '<a href="mailto:hello@vitesy-brand.io">contact</a>'
        "<p>support [at] vitesy-brand [dot] io</p>"
    )
    dt, res, note = _run_extraction("extract_emails", html)
    if dt is None:
        record("巨大トークン同居でも実メールを抽出", False, note)
        return
    low = res.lower()
    ok_mailto = "hello@vitesy-brand.io" in low
    ok_obf = "support@vitesy-brand.io" in low
    record(
        "巨大トークン同居でも mailto を抽出",
        ok_mailto,
        f"hello@vitesy-brand.io in result={ok_mailto}",
    )
    record(
        "巨大トークン同居でも難読化メールを抽出",
        ok_obf,
        f"support@vitesy-brand.io in result={ok_obf}",
    )
    record("抽出は 2s 以内", dt < _EXTRACT_BUDGET_S, f"{dt:.3f}s < {_EXTRACT_BUDGET_S}s")


def case_oversized_local_part_not_truncated() -> None:
    """RFC 上限超の local-part から末尾を切り出した偽アドレスを作らない。"""
    html = "<p>" + ("z" * 100) + "@vitesy-brand.io</p>"
    dt, res, note = _run_extraction("extract_emails", html)
    if dt is None:
        record("64 文字超の local-part を切り出さない", False, note)
        return
    truncated = "@vitesy-brand.io" in res.lower()
    record(
        "64 文字超の local-part を末尾切り出しで採用しない",
        not truncated,
        f"@vitesy-brand.io in result={truncated} (期待 False), {dt:.3f}s",
    )


def _count_leftover_children() -> int:
    """_redos_child が残存していないかを /proc で数える（Linux コンテナ前提）。"""
    n = 0
    proc_root = Path("/proc")
    if not proc_root.exists():
        return 0
    for p in proc_root.iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                errors="replace"
            )
        except Exception:  # noqa: BLE001
            continue
        if "_redos_child" in cmd:
            n += 1
    return n


def main() -> int:
    print("test_email_extraction_redos")
    t0 = time.perf_counter()
    case_huge_token_extract_emails()
    case_huge_token_deobfuscate()
    case_not_quadratic()
    case_real_emails_still_extracted()
    case_oversized_local_part_not_truncated()
    suite_dt = time.perf_counter() - t0

    record(
        "スイート全体が 60s 以内",
        suite_dt < _SUITE_BUDGET_S,
        f"{suite_dt:.2f}s < {_SUITE_BUDGET_S}s",
    )
    leftover = _count_leftover_children()
    record("実行後に子プロセス _redos_child が残らない", leftover == 0, f"残存 {leftover}")

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"\npassed={passed} failed={failed} suite={suite_dt:.2f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
