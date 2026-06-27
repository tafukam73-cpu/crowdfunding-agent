"""Gmail 下書き連携用の GMAIL_REFRESH_TOKEN を取得するローカル用スクリプト。

Google OAuth2（インストール済みアプリ／デスクトップ クライアント）で
スコープ gmail.compose のリフレッシュトークンを 1 回だけ取得するための補助ツール。
取得したトークンを .env の GMAIL_REFRESH_TOKEN に設定すると、Gmail で「下書き」を
作成できるようになる（送信はしない）。

このスクリプトは取得補助のみで、アプリ本体（既存機能）には一切影響しない。

前提:
  - Google Cloud Console で OAuth クライアント ID（種類: デスクトップ アプリ）を作成済み
  - その CLIENT_ID / CLIENT_SECRET を .env の GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET に設定済み

実行例（ローカル・backend ディレクトリで）:
    python -m scripts.get_gmail_refresh_token
    python scripts/get_gmail_refresh_token.py

依存: 標準ライブラリのみ（python-dotenv があれば .env 読み込みに利用）。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.compose"
# OOB（urn:ietf:wg:oauth:2.0:oob）は Google で廃止済みのため localhost を使う。
# ローカルサーバは立てず、リダイレクト後のアドレスバーから code を手動コピーする方式。
REDIRECT_URI = "http://localhost"


def _find_env_file() -> Path | None:
    """.env を探す。スクリプト位置と CWD から上方向に探索する。"""
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    for base in (Path.cwd(), here.parent):
        candidates.append(base)
        candidates.extend(base.parents)
    seen: set[Path] = set()
    for d in candidates:
        if d in seen:
            continue
        seen.add(d)
        env = d / ".env"
        if env.is_file():
            return env
    return None


def _load_env(env_path: Path | None) -> dict[str, str]:
    """.env を読み込んで dict を返す。python-dotenv があれば使い、無ければ簡易パース。"""
    if env_path is None:
        return {}
    try:
        from dotenv import dotenv_values

        return {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    except ImportError:
        values: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
        return values


def _exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    """認可コードをトークンに交換する（標準ライブラリで POST）。"""
    payload = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URI,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (固定の Google URL)
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        detail = exc.read().decode(errors="replace")
        raise SystemExit(
            f"トークン交換に失敗しました（HTTP {exc.code}）:\n{detail}\n"
            "→ CLIENT_ID/SECRET の誤り、code の貼り間違い、または code の期限切れの可能性があります。"
        ) from exc


def _parse_code_input(raw: str) -> str:
    """ユーザー入力から認可コードを取り出す。コード単体でも URL 全体でも可。"""
    raw = raw.strip()
    if not raw:
        return ""
    # リダイレクト後の URL を丸ごと貼られた場合に code= を抽出する
    if "code=" in raw:
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs and qs["code"]:
            return qs["code"][0]
    return raw


def main() -> int:
    env_path = _find_env_file()
    env = _load_env(env_path)

    # .env を優先しつつ、既存の環境変数もフォールバックとして利用
    client_id = env.get("GMAIL_CLIENT_ID") or os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = env.get("GMAIL_CLIENT_SECRET") or os.environ.get(
        "GMAIL_CLIENT_SECRET", ""
    )

    print("=== Gmail refresh token 取得スクリプト ===")
    if env_path:
        print(f".env: {env_path}")
    else:
        print(".env: 見つかりませんでした（環境変数から読み込みを試みます）")

    if not client_id or not client_secret:
        print(
            "\nエラー: GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET が設定されていません。\n"
            "Google Cloud Console で OAuth クライアント ID（種類: デスクトップ アプリ）を作成し、\n"
            ".env に以下を設定してから再実行してください:\n"
            "  GMAIL_CLIENT_ID=...\n"
            "  GMAIL_CLIENT_SECRET=..."
        )
        return 1

    # 1) 認可 URL を組み立てる
    auth_params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",  # refresh_token を得るために必須
        "prompt": "consent",  # 既存同意があっても refresh_token を確実に再発行
    }
    auth_url = f"{AUTH_URI}?{urllib.parse.urlencode(auth_params)}"

    print("\n[1] ブラウザで Google 認証ページを開きます。")
    print("    開かない場合は、以下の URL を手動でブラウザに貼り付けてください:\n")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:  # noqa: BLE001 ヘッドレス環境などで失敗しても続行
        pass

    print(
        "\n[2] 同意後、ブラウザは http://localhost?... へリダイレクトします。\n"
        "    （ローカルサーバは無いためページは開けませんが、それで問題ありません）\n"
        "    ブラウザのアドレスバーに表示された URL 全体、または URL 内の code= の値を\n"
        "    そのままコピーして、下に貼り付けてください。"
    )
    raw = input("\nコード（または リダイレクト URL）を入力: ")
    code = _parse_code_input(raw)
    if not code:
        print("コードが入力されませんでした。中止します。")
        return 1

    # 3) コードをトークンへ交換
    print("\n[3] トークンを取得中...")
    token = _exchange_code(client_id, client_secret, code)
    refresh_token = token.get("refresh_token")

    if not refresh_token:
        print(
            "\nrefresh_token が返りませんでした。レスポンス内容:\n"
            f"{json.dumps(token, ensure_ascii=False, indent=2)}\n\n"
            "→ 既に同意済みで refresh_token が省略された可能性があります。\n"
            "  https://myaccount.google.com/permissions で当該アプリのアクセスを削除し、\n"
            "  再度このスクリプトを実行してください（prompt=consent 指定済み）。"
        )
        return 1

    print("\n=== 取得成功 ===")
    print("GMAIL_REFRESH_TOKEN:\n")
    print(refresh_token)
    print(
        "\n--- .env への設定手順 ---\n"
        "1) プロジェクトの .env を開く\n"
        "2) 次の行を上記の値で更新する:\n"
        f"   GMAIL_REFRESH_TOKEN={refresh_token}\n"
        "3) GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET も設定済みであることを確認する\n"
        "   （3 つが揃うと Gmail 下書き作成が有効化されます。未設定なら mock 動作）\n"
        "4) バックエンドを再起動して反映する\n"
        "   例: docker compose up -d --build backend"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
