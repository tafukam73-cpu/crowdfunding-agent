"""アプリ全体の設定。環境変数（.env）から読み込む。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # PostgreSQL 接続情報
    database_url: str = (
        "postgresql+psycopg://cfagent:cfagent_password@db:5432/crowdfunding"
    )

    # アプリ表示名
    app_name: str = "海外クラファン案件発掘・営業支援システム"

    # CORS 許可オリジン（フロントエンド）
    frontend_origin: str = "http://localhost:3000"

    # スクレイピング取得方法： "httpx" | "playwright"
    scrape_fetcher: str = "playwright"
    # リクエスト間隔（秒）。レート制限への配慮
    scrape_rate_limit_seconds: float = 2.0
    # 取得タイムアウト（秒）
    scrape_timeout_seconds: float = 30.0
    # リトライ回数（403/タイムアウト/5xx 時。UA をローテーションして再試行）
    scrape_retries: int = 2

    # --- DB コネクションプール ---
    # 重いバックグラウンドジョブがセッションを保持しても、画面表示用の GET が
    # コネクション待ちで固まらないよう、プールを十分に確保する。
    db_pool_size: int = 10
    db_max_overflow: int = 40
    db_pool_timeout: int = 20  # コネクション取得の最大待ち（秒）

    # --- バックグラウンドジョブ（Contact Intelligence 等）の同時実行上限 ---
    # 重い外部クロール/検索ジョブを無制限に並列起動すると CPU/コネクションを
    # 食い潰して画面表示が固まる。同時実行数をこの値に制限する（超過分は待機）。
    ci_max_concurrent_jobs: int = 2
    # 商品発掘（Discovery Crawler）ジョブの同時実行上限。Contact Intelligence とは
    # 別枠のセマフォで制限し、収集ジョブと探索ジョブが互いに枠を奪わないようにする。
    discovery_max_concurrent_jobs: int = 1
    # 1 ジョブのウォールクロック上限（分）。専用ワーカーがこの時間を超えた実行
    # サブプロセスをプロセスツリーごと強制終了し、ジョブを timed_out にする。
    # タイムアウトの延長ではなく「ハングの封じ込め」が目的（0 で無効）。
    ci_job_hard_timeout_minutes: int = 20
    # --- Contact Intelligence 専用ワーカー（cfagent-ci-worker）の設定 ---
    # queued ジョブを探すポーリング間隔（秒）。
    ci_worker_poll_seconds: float = 2.0
    # 1 ワーカーの同時実行ジョブ数（既定 1＝重い探索を直列化）。
    ci_worker_concurrency: int = 1
    # 実行中サブプロセスの heartbeat 更新間隔（秒）。
    ci_worker_heartbeat_seconds: float = 5.0
    # running ジョブの heartbeat がこの秒数より古ければワーカー死亡とみなし stale 回収。
    ci_heartbeat_stale_seconds: int = 90

    # AI 評価：未設定ならモック評価器が使われる
    anthropic_api_key: str = ""
    # 既定は最も高性能な Opus 4.8。コスト重視なら claude-sonnet-4-6 等に変更可
    anthropic_model: str = "claude-opus-4-8"

    # 営業メールの差出人（署名に使用）
    sender_name: str = "Taro Yamada"
    sender_company: str = "Your Company"

    # --- メール下書きプロバイダー（Gmail 等。未設定なら mock） ---
    # Gmail API（OAuth2 リフレッシュトークン方式）。3 つ揃うと Gmail を使用。
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    # 下書きを作成する Gmail ユーザー（通常 "me"）と From 表示（省略可）
    gmail_user: str = "me"
    gmail_sender: str = ""

    # --- 日次自動収集スケジューラ ---
    # 有効化フラグ（false で起動時にスケジューラを開始しない）
    scrape_schedule_enabled: bool = True
    # 実行スケジュール（cron 式）。既定は毎日 03:00。
    scrape_schedule_cron: str = "0 3 * * *"
    # スケジュールのタイムゾーン
    scrape_timezone: str = "Asia/Tokyo"
    # 自動収集の 1 サイトあたり取得上限
    scrape_daily_limit: int = 20

    # --- Web Research 検索プロバイダー ---
    # 検索 API の切り替え：
    #   "none" | "brave" | "serpapi" | "tavily" | "google_cse" | "bing" | "multi"
    # "multi" は設定済みのプロバイダーを順に試し、最初に結果が返ったものを使う。
    # API キーが無い場合は自動的に DuckDuckGo HTML（手動検索クエリ方式）にフォールバック。
    search_provider: str = "none"
    # 各プロバイダーの API キー（設定したものだけ使用可能になる）
    brave_search_api_key: str = ""
    serpapi_api_key: str = ""
    tavily_api_key: str = ""
    google_cse_api_key: str = ""
    # Google Custom Search のエンジン ID（cx）。google_cse には key と cx の両方が必要。
    google_cse_cx: str = ""
    # Bing Web Search API（Azure）
    bing_search_api_key: str = ""
    # 1 クエリあたり取得する検索結果の上限
    search_max_results: int = 10

    # --- Contact Intelligence v3：公式サイト再帰クロールの上限（発見率と安全性） ---
    # 公式サイトが見つかった場合に、サイト全体を再帰巡回する際の上限。.env で変更可。
    # Phase 1：発見率を上げるため 50→100 に引き上げ（重複排除・同一ドメイン優先・
    # 1URL タイムアウト・レート制限で安全性は担保）。
    recursive_crawl_max_urls: int = 100
    recursive_crawl_max_depth: int = 2
    # 1 URL のハード上限（秒）：接続ハング対策（httpx の粒度別 timeout を貫通する
    # 遅延応答を別スレッドの期限で確実に打ち切る）。0 で無効。
    recursive_crawl_url_timeout: float = 10.0
    # 1 クロール全体のウォールクロック上限（秒）：低速サイトで超過時に発見済み結果を
    # 保持して打ち切る。0 で無効。
    recursive_crawl_max_seconds: float = 45.0

    # --- AI Search Agent の探索上限（発見率と安全性のバランス） ---
    search_agent_max_steps: int = 15
    search_agent_max_urls: int = 40
    search_agent_max_queries: int = 50
    # 同一ドメインを過剰巡回しない上限
    search_agent_max_per_domain: int = 8

    # --- 取得アラート通知（構造変化・成功率低下） ---
    # Slack Incoming Webhook URL。設定時のみ Slack 通知を行う（未設定なら何もしない）。
    slack_webhook_url: str = ""
    # 管理画面のベース URL（通知本文のリンク用）。未設定なら frontend_origin を使う。
    app_base_url: str = ""
    # 監視集計の対象にする直近 run 件数（通知判定にも使う）
    alert_window: int = 20

    # --- 営業対象除外判定の適用モード（送信前関門） ---
    # observe : 判定・履歴保存・警告だけ行い、Gmail 下書き作成や Compose URL は
    #           止めない（業務を止めずに実データを観測する段階）
    # enforce : clear または有効な override だけ許可し、review / blocked /
    #           判定不能は 409 で止める
    # 未設定・空文字・不正値は observe にフォールバックする（安全側＝業務を
    # 止めない側）。判定に失敗したときの fail closed は enforce 内の話であり、
    # ここでのフォールバックとは別物。
    outreach_gate_mode: str = "observe"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

# --- テスト DB 安全ガード（本番実行では no-op） ---
# 設定 singleton を読み込んだ時点で、テスト文脈（TESTING=true / pytest 実行中）にも
# かかわらず接続先が本番 DB（crowdfunding / host db=cfagent-db）を指していれば、
# ここでプロセスを即時終了する。「本番設定 singleton の読み込みでも安全側に停止」。
from app import db_safety as _db_safety  # noqa: E402

_db_safety.guard_or_abort(settings.database_url)
