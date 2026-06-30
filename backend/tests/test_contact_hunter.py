"""Contact Hunter AI のオフライン検証（ネットワーク/DB 不要）。

- 役職→営業優先度（title_to_priority）の決定的マッピング
- 人名らしさ判定（looks_like_person_name）で役職/ナビ語を弾く
- HTML からの担当者抽出（JSON-LD / LinkedIn アンカー / 氏名+役職テキスト）
- 捏造防止：人名らしくない文字列・出典の無い人物は採用しない
- end-to-end hunt（fetch/search 注入）で優先度順に並ぶこと
pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_contact_hunter.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ai.contact_hunter import (  # noqa: E402
    looks_like_person_name,
    title_to_priority,
)
from app.ai.mock_contact_hunter import (  # noqa: E402
    MockContactHunter,
    extract_people_from_html,
)

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_title_priority() -> None:
    print("test_title_priority")
    check("Business Development=95", title_to_priority("Head of Business Development") == ("Business Development", 95))
    check("Partnership=95", title_to_priority("Partnerships Manager") == ("Partnership", 95))
    check("International Sales=90", title_to_priority("International Sales Manager") == ("International Sales", 90))
    check("Export=88", title_to_priority("Export Manager") == ("Export", 88))
    check("Sales Director=85", title_to_priority("Sales Director")[1] == 85)
    check("Marketing=80", title_to_priority("Marketing Director") == ("Marketing", 80))
    check("Founder=75", title_to_priority("Co-Founder") == ("Founder", 75))
    check("CEO=70", title_to_priority("CEO & Chief Executive")[1] == 70)
    check("Support=30", title_to_priority("Customer Support") == ("Support", 30))
    check("不明役職=Other/50", title_to_priority("Office Cat") == ("Other", 50))
    check("役職なし=None/40", title_to_priority(None) == (None, 40))


def test_person_name_guard() -> None:
    print("test_person_name_guard")
    check("通常の人名は許可", looks_like_person_name("Sarah Johnson"))
    check("3 語の人名も許可", looks_like_person_name("Maria De Luca"))
    check("ナビ語は拒否", not looks_like_person_name("Our Team"))
    check("役職語は拒否", not looks_like_person_name("Sales Director"))
    check("単語のみは拒否", not looks_like_person_name("Contact"))
    check("数字入りは拒否", not looks_like_person_name("Room 101"))


_TEAM_HTML = """
<html><body>
<script type="application/ld+json">
{"@type":"Person","name":"David Kim","jobTitle":"Head of Partnerships",
 "sameAs":["https://www.linkedin.com/in/davidkim"],"email":"mailto:david@brandco.com"}
</script>
<div class="member">
  <h3>Sarah Johnson</h3>
  <p>Business Development Manager</p>
  <a href="https://www.linkedin.com/in/sarahjohnson">LinkedIn</a>
</div>
<p>Tomoko Sato, Export Manager</p>
<a href="https://www.linkedin.com/in/randomcompanypage">Our Company</a>
<p>Contact: support@brandco.com</p>
</body></html>
"""


def test_extract_people() -> None:
    print("test_extract_people")
    people = extract_people_from_html(_TEAM_HTML, "https://brandco.com/team")
    by_name = {p.name: p for p in people}

    check("David Kim を抽出（JSON-LD）", "David Kim" in by_name)
    check("Sarah Johnson を抽出（テキスト/LinkedIn）", "Sarah Johnson" in by_name)
    check("Tomoko Sato を抽出（インライン氏名,役職）", "Tomoko Sato" in by_name)
    check("会社ページのアンカーは人物にしない", "Our Company" not in by_name)

    if "David Kim" in by_name:
        d = by_name["David Kim"]
        check("David の部署=Partnership", d.department == "Partnership")
        check("David の優先度=95", d.priority == 95)
        check("David に LinkedIn", bool(d.linkedin_url))
        check("David にメール", d.email == "david@brandco.com")
        check("David の confidence が高い（LinkedIn+email+役職）", (d.confidence or 0) >= 80)

    if "Sarah Johnson" in by_name:
        s = by_name["Sarah Johnson"]
        check("Sarah の部署=Business Development", s.department == "Business Development")
        check("Sarah に LinkedIn が紐付く", bool(s.linkedin_url))

    if "Tomoko Sato" in by_name:
        t = by_name["Tomoko Sato"]
        check("Tomoko の部署=Export", t.department == "Export")
    # 全員に出典 URL が付く
    check("全員に source_url", all(p.source_url for p in people))


def test_hunt_end_to_end() -> None:
    print("test_hunt_end_to_end")

    class P:
        id = 1
        title = "Cool Lamp"
        maker_name = "BrandCo"
        maker_url = "https://brandco.com"
        source_url = "https://www.kickstarter.com/x"
        source_site = "kickstarter"

    pages = {"https://brandco.com/team": _TEAM_HTML}

    def fetch(url):
        return pages.get(url)

    def search(q):
        return []

    res = MockContactHunter().hunt(P(), fetch_fn=fetch, search_fn=search)
    names = [p.name for p in res.people]
    check("担当者を発見", len(res.people) >= 3)
    check("検索クエリを生成", any("founder" in q for q in res.searched_queries))
    check("優先度降順（先頭が最高優先度）", res.people[0].priority >= res.people[-1].priority)
    check("先頭は高優先度(BD/Partnership/Export=88-95)", res.people[0].priority >= 88)
    check("David/Sarah/Tomoko を含む", set(["David Kim", "Sarah Johnson", "Tomoko Sato"]).issubset(set(names)))


def main() -> int:
    test_title_priority()
    test_person_name_guard()
    test_extract_people()
    test_hunt_end_to_end()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
