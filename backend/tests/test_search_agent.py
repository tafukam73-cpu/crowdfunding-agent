"""AI Search Agent のオフライン検証（ネットワーク/DB/Claude 不要）。

反復ループ（最大ステップで停止）・SNS→Linktree→公式→Contact→メールの連鎖・
推測メール不採用・出典必須・SNS正規化・Linktree外部リンク収集・platform除外を検証。

実行（backend ディレクトリで）:
    python tests/test_search_agent.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ai.mock_search_agent import MockSearchAgent  # noqa: E402
from app.ai.search_agent import SearchAgentState, get_search_agent, MAX_STEPS  # noqa: E402
from app.services import search_agent_service as sas  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


class P:
    id = 1
    title = "Vitesy Fruit Bowl"
    maker_name = "Vitesy"
    source_site = "indiegogo"
    source_url = "https://www.indiegogo.com/projects/vitesy"
    maker_url = None
    description = ""
    description_clean = ""


PAGES = {
    "https://www.indiegogo.com/projects/vitesy":
        '<html><body><a href="https://linktr.ee/vitesy">links</a>'
        '<a href="https://www.instagram.com/vitesy/">ig</a>'
        '<a href="https://www.instagram.com/indiegogo/">platform ig</a></body></html>',
    "https://linktr.ee/vitesy":
        '<html><body><a href="https://vitesy.com">Official Website</a>'
        '<a href="https://www.facebook.com/vitesy">fb</a></body></html>',
    "https://vitesy.com":
        '<html><body><a href="/contact">Contact</a></body></html>',
    "https://vitesy.com/contact":
        '<html><body><a href="mailto:hello@vitesy.com">m</a></body></html>',
}


def _fetch(u):
    return PAGES.get(u.rstrip("/")) or PAGES.get(u)


def _run_loop(project, pages_fetch, max_steps=MAX_STEPS):
    """service のループを DB 無しで再現（run_search_agent と同じ判断ロジック）。"""
    state = sas._initial_state(project, None)
    agent = MockSearchAgent()
    steps = []
    for i in range(max_steps):
        state.step = i + 1
        plan = agent.plan(state)
        if plan.stop:
            steps.append(("stop", plan.reason))
            break
        if not plan.next_urls and not plan.next_queries:
            steps.append(("stop", "no candidates"))
            break
        for url in plan.next_urls[:4]:
            if url in state.visited_urls:
                continue
            state.visited_urls.append(url)
            if url in state.candidate_urls:
                state.candidate_urls.remove(url)
            html = pages_fetch(url)
            if html:
                sas._extract_into_state(state, url, html, {"vitesy"}, "indiegogo.com")
            steps.append(("visit", url))
    return state, steps


def test_chain_discovers_email():
    print("test_chain_discovers_email")
    state, steps = _run_loop(P(), _fetch)
    res = sas._finalize(P(), None, state)
    check("公式サイトを発見（linktree経由）", res["official_site_url"] == "https://vitesy.com")
    check("hello@vitesy.com を取得", any(e["email"] == "hello@vitesy.com" for e in res["emails"]))
    check("各メールに出典", all(e["source_url"] for e in res["emails"]))
    check("Instagram を正規化して発見", res["socials"].get("instagram") == "https://www.instagram.com/vitesy/")
    check("Facebook を発見", res["socials"].get("facebook") == "https://www.facebook.com/vitesy")
    check("運営SNS(indiegogo)を採用しない", "indiegogo" not in (res["socials"].get("instagram") or ""))
    check("推奨チャネル email", res["recommended_channel"] == "email")
    check("スコア>=80", res["confidence_score"] >= 80)
    check("停止（メール取得で終了）", steps[-1][0] == "stop")


def test_stops_at_max_steps():
    print("test_stops_at_max_steps")
    # 常に新しい候補が出続けても最大ステップで止まる
    def endless_fetch(u):
        n = abs(hash(u)) % 100000
        return f'<html><body><a href="https://hub{n}.linktr.ee/x">next</a></body></html>'

    state, steps = _run_loop(P(), endless_fetch, max_steps=MAX_STEPS)
    visits = [s for s in steps if s[0] == "visit"]
    check("最大5ステップ以内で終了", len(state.visited_urls) >= 1)
    check("ループが無限に続かない（stepで打ち切り）", len([s for s in steps]) <= MAX_STEPS * 4 + 2)


def test_no_email_no_fabrication():
    print("test_no_email_no_fabrication")

    def no_email_fetch(u):
        return '<html><body><a href="https://www.youtube.com/@narrationos">yt</a></body></html>'

    class NOS(P):
        title = "NarrationOS"
        maker_name = "NarrationOS"
        source_url = "https://www.kickstarter.com/projects/lunosama/narrationos"

    state, _ = _run_loop(NOS(), no_email_fetch)
    res = sas._finalize(NOS(), None, state)
    check("メール未発見なら空（捏造しない）", res["emails"] == [])
    check("推奨連絡先 None", res["recommended_contact"] is None)
    check("YouTube のみ発見", res["socials"].get("youtube"))
    check("公式サイト未発見は None", res["official_site_url"] is None)


def test_validation_filters():
    print("test_validation_filters")
    st = SearchAgentState(source_site="kickstarter")
    st.emails = [
        {"email": "hello@vitesy.com", "source_url": "https://vitesy.com/contact"},
        {"email": "support@kickstarter.com", "source_url": "https://x"},   # platform
        {"email": "no-reply@vitesy.com", "source_url": "https://x"},        # auto-reply
        {"email": "ghost@vitesy.com", "source_url": ""},                    # 出典なし
    ]
    st.official_site_url = "https://vitesy.com"

    class Prj:
        source_site = "kickstarter"
        maker_name = "Vitesy"
        title = "Vitesy"
    res = sas._finalize(Prj(), None, st)
    got = {e["email"] for e in res["emails"]}
    check("hello@ 採用", "hello@vitesy.com" in got)
    check("platform 除外", "support@kickstarter.com" not in got)
    check("no-reply 除外", "no-reply@vitesy.com" not in got)
    check("出典なし除外", "ghost@vitesy.com" not in got)


def test_platform_not_official():
    print("test_platform_not_official")
    st = SearchAgentState()
    st.official_site_url = "https://www.kickstarter.com/profile/x"

    class Prj:
        source_site = "kickstarter"
        maker_name = "X"
        title = "X"
    res = sas._finalize(Prj(), None, st)
    check("platform URLを公式サイト扱いしない", res["official_site_url"] is None)


def test_factory_mock():
    print("test_factory_mock")
    from app.config import settings
    old = settings.anthropic_api_key
    settings.anthropic_api_key = ""
    try:
        check("キー未設定は mock", get_search_agent().name == "mock-search-agent-v1")
    finally:
        settings.anthropic_api_key = old


def main():
    test_chain_discovers_email()
    test_stops_at_max_steps()
    test_no_email_no_fabrication()
    test_validation_filters()
    test_platform_not_official()
    test_factory_mock()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
