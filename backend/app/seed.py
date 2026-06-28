"""開発用モックデータ投入。

projects テーブルが空のときだけサンプル案件を投入する（冪等）。
Step 3 で実スクレイピングが入るまでの画面確認用。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.ulule import clean_description
from app.models.project import Project, ProjectStatus, SourceSite

MOCK_PROJECTS = [
    dict(
        title="折りたたみ式ソーラー充電バックパック",
        source_site=SourceSite.kickstarter.value,
        source_url="https://www.kickstarter.com/projects/example/solar-backpack",
        category="ガジェット",
        description="ソーラーパネル内蔵で外出先でもスマホを充電できる軽量バックパック。",
        image_url="https://picsum.photos/seed/solar/640/360",
        video_url="https://www.youtube.com/watch?v=example1",
        currency="USD",
        goal_amount=Decimal("20000.00"),
        raised_amount=Decimal("184320.00"),
        backers_count=2143,
        start_date=date(2026, 5, 1),
        end_date=date(2026, 6, 30),
        maker_name="SunPack Inc.",
        maker_url="https://sunpack.example.com",
        contact_info="hello@sunpack.example.com",
        status=ProjectStatus.reviewing.value,
    ),
    dict(
        title="AIノイズキャンセリング・スマートイヤホン",
        source_site=SourceSite.indiegogo.value,
        source_url="https://www.indiegogo.com/projects/example/ai-earbuds",
        category="オーディオ",
        description="環境音をAIが解析し、自動で最適なノイズキャンセリングを行うイヤホン。",
        image_url="https://picsum.photos/seed/earbuds/640/360",
        video_url="https://www.youtube.com/watch?v=example2",
        currency="USD",
        goal_amount=Decimal("50000.00"),
        raised_amount=Decimal("612400.00"),
        backers_count=8021,
        start_date=date(2026, 4, 15),
        end_date=date(2026, 6, 15),
        maker_name="AudioMind",
        maker_url="https://audiomind.example.com",
        contact_info="partnership@audiomind.example.com",
        status=ProjectStatus.new.value,
    ),
    dict(
        title="超軽量チタン製マルチツールカード",
        source_site=SourceSite.kickstarter.value,
        source_url="https://www.kickstarter.com/projects/example/titanium-tool",
        category="アウトドア",
        description="財布に入るサイズに18の機能を詰め込んだチタン製マルチツール。",
        image_url="https://picsum.photos/seed/titanium/640/360",
        video_url=None,
        currency="USD",
        goal_amount=Decimal("10000.00"),
        raised_amount=Decimal("95780.00"),
        backers_count=3120,
        start_date=date(2026, 5, 20),
        end_date=date(2026, 7, 10),
        maker_name="EDC Works",
        maker_url="https://edcworks.example.com",
        contact_info=None,
        status=ProjectStatus.contacted.value,
    ),
    dict(
        title="スマート水耕栽培キット（アプリ連動）",
        source_site=SourceSite.wadiz.value,
        source_url="https://www.wadiz.kr/web/campaign/example/hydroponics",
        category="ホーム",
        description="アプリで水やりと照明を自動管理する卓上型の水耕栽培キット。",
        image_url="https://picsum.photos/seed/garden/640/360",
        video_url="https://www.youtube.com/watch?v=example4",
        currency="KRW",
        goal_amount=Decimal("30000000.00"),
        raised_amount=Decimal("128400000.00"),
        backers_count=1540,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 5, 31),
        maker_name="GreenLab",
        maker_url="https://greenlab.example.com",
        contact_info="contact@greenlab.example.com",
        status=ProjectStatus.negotiating.value,
    ),
    dict(
        title="Recycled Ocean Plastic Tote Bag — Made in France",
        source_site=SourceSite.ulule.value,
        source_url="https://www.ulule.com/example/ocean-tote",
        category="Lifestyle & Design",
        description=(
            "Sustainable, eco-friendly tote bag made from recycled ocean plastic and "
            "organic textile. Ethical, made in France, with a refined European design "
            "for everyday lifestyle and travel."
        ),
        image_url="https://picsum.photos/seed/ululetote/640/360",
        video_url="https://www.youtube.com/watch?v=ulule1",
        currency="EUR",
        goal_amount=Decimal("15000.00"),
        raised_amount=Decimal("82500.00"),
        backers_count=1640,
        start_date=date(2026, 5, 10),
        end_date=date(2026, 6, 25),
        maker_name="Atelier Vert",
        maker_url="https://ateliervert.example.com",
        contact_info="hello@ateliervert.example.com",
        status=ProjectStatus.new.value,
    ),
    dict(
        title="磁気浮上式ワイヤレス卓上時計",
        source_site=SourceSite.indiegogo.value,
        source_url="https://www.indiegogo.com/projects/example/levitating-clock",
        category="インテリア",
        description="磁力で宙に浮きながら回転する近未来的なデザインの卓上時計。",
        image_url="https://picsum.photos/seed/clock/640/360",
        video_url=None,
        currency="USD",
        goal_amount=Decimal("15000.00"),
        raised_amount=Decimal("9800.00"),
        backers_count=210,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 7, 20),
        maker_name="LeviDesign",
        maker_url="https://levidesign.example.com",
        contact_info=None,
        status=ProjectStatus.rejected.value,
    ),
]


def seed_if_empty(db: Session) -> int:
    """空のときだけ投入。投入件数を返す。"""
    count = db.scalar(select(func.count()).select_from(Project)) or 0
    if count > 0:
        return 0
    projects = [Project(**row) for row in MOCK_PROJECTS]
    for p in projects:
        p.description_clean = clean_description(p.description)
    db.add_all(projects)
    db.commit()
    return len(MOCK_PROJECTS)
