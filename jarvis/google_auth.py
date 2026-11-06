"""Google との連携の共通部分（OAuth の資格情報・API の呼び出し・初回設定）。

ToDo リスト（gtasks）とカレンダー（gcal）は、同じ OAuth クライアントと同じ更新用トークンを使う。
資格情報は Windows の資格情報マネージャーに保存する（.env や OneDrive には置かない）。
"""

import os
import sys
from pathlib import Path

import keyring
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",  # 予定の読み書き
    "https://www.googleapis.com/auth/calendar.readonly",  # カレンダーの一覧の読み取り
]
SCOPES = [TASKS_SCOPE, *CALENDAR_SCOPES]
TOKEN_URI = "https://oauth2.googleapis.com/token"
KEYRING_SERVICE = "Jarvis-Google"
TIMEOUT = 20
SETUP_HINT = "jarvis-google-setup.bat を実行して Google アカウントを連携し直してください"

_SECRET_NAMES = ("client_id", "client_secret", "refresh_token")
_session: AuthorizedSession | None = None


class GoogleApiError(Exception):
    """Google API がエラーを返した。status は HTTP の状態コード、reason は Google のエラー理由。"""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(f"{status} {reason}: {message}")
        self.status = status
        self.reason = reason


def granted_scopes() -> set[str]:
    """許可済みのスコープ。カレンダー対応より前の連携では記録がないため、ToDo だけとみなす。"""
    if not keyring.get_password(KEYRING_SERVICE, "refresh_token"):
        return set()
    recorded = keyring.get_password(KEYRING_SERVICE, "scopes")
    return set(recorded.split()) if recorded else {TASKS_SCOPE}


def has_tasks() -> bool:
    return TASKS_SCOPE in granted_scopes()


def has_calendar() -> bool:
    return set(CALENDAR_SCOPES) <= granted_scopes()


def _get_session() -> AuthorizedSession:
    global _session
    if _session is None:
        secrets = {name: keyring.get_password(KEYRING_SERVICE, name) for name in _SECRET_NAMES}
        if not all(secrets.values()):
            raise GoogleAuthError("資格情報がありません")
        _session = AuthorizedSession(Credentials(None, token_uri=TOKEN_URI, scopes=sorted(granted_scopes()), **secrets))
    return _session


def forget_connection() -> None:
    """通信エラーのあとは、次の呼び出しで接続を作り直す。"""
    global _session
    _session = None


def request(method: str, url: str, **kwargs) -> dict:
    """Google API を呼んで JSON を返す。エラーは GoogleApiError にする。"""
    response = _get_session().request(method, url, timeout=TIMEOUT, **kwargs)
    if response.status_code >= 400:
        reason, message = "", response.text[:200]
        try:
            error = response.json().get("error", {})
            message = error.get("message", message)
            details = error.get("errors") or error.get("details") or []
            reason = next((d.get("reason") for d in details if d.get("reason")), error.get("status", ""))
        except ValueError:
            pass
        raise GoogleApiError(response.status_code, reason or "", message)
    return response.json() if response.content else {}


def describe_error(e: Exception, service: str) -> str:
    """Google 連携の失敗をユーザー向けの文にする。service は「Google カレンダー」など。"""
    if isinstance(e, (RefreshError, GoogleAuthError)):
        return f"Google にログインできませんでした。連携が切れたのかもしれません。{SETUP_HINT}"
    if isinstance(e, GoogleApiError):
        if e.reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
            return f"{service}を使う許可がありません。{SETUP_HINT}"
        if e.reason in ("accessNotConfigured", "SERVICE_DISABLED"):
            return f"{service}の API が有効になっていません。Google Cloud Console の「API とサービス」で有効にしてください"
        if e.status == 404:
            return f"{service}で対象が見つかりませんでした（すでに削除された可能性があります）"
    return f"{service}の操作に失敗しました（{type(e).__name__}）。実行されたかどうかは一覧で確かめてください"


# ── 初回設定 ──────────────────────────────


def _client_config() -> dict | None:
    """保存済みの OAuth クライアントがあれば使い、JSON ファイルを探し直さずに済ませる。"""
    client_id = keyring.get_password(KEYRING_SERVICE, "client_id")
    client_secret = keyring.get_password(KEYRING_SERVICE, "client_secret")
    if not (client_id and client_secret):
        return None
    return {"installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": TOKEN_URI,
        "redirect_uris": ["http://localhost"],
    }}


def setup() -> int:
    """ブラウザで Google の許可を受け、資格情報マネージャーに保存して接続を確かめる。"""
    from google_auth_oauthlib.flow import InstalledAppFlow

    from jarvis import gcal, gtasks

    # 一部のチェックを外して許可された場合も、エラーにせず受け取って後で確かめる
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    print("Google（ToDo リスト・カレンダー）の連携\n手順は README の「Google の連携の設定」を見てください。\n")
    config, path = _client_config(), None
    if config:
        print("保存済みの OAuth クライアントを使います。")
    else:
        answer = input("ダウンロードした OAuth クライアントの JSON ファイルのパス: ").strip().strip('"')
        path = Path(answer) if answer else None
        if path is None or not path.is_file():
            print("ファイルが見つからないため中止しました")
            return 1
    print("\nブラウザが開きます。Google アカウントを選び、表示された項目をすべて許可してください…")
    try:
        if config:
            flow = InstalledAppFlow.from_client_config(config, SCOPES)
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
        credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent", open_browser=True)
    except Exception as e:
        print(f"許可を受けられませんでした（{type(e).__name__}: {e}）。何も変更していません")
        return 1
    if not credentials.refresh_token:
        print("更新用のトークンを受け取れませんでした。何も変更していません")
        return 1
    granted = set(credentials.granted_scopes or SCOPES)
    keyring.set_password(KEYRING_SERVICE, "client_id", credentials.client_id)
    keyring.set_password(KEYRING_SERVICE, "client_secret", credentials.client_secret)
    keyring.set_password(KEYRING_SERVICE, "refresh_token", credentials.refresh_token)
    keyring.set_password(KEYRING_SERVICE, "scopes", " ".join(sorted(granted)))
    forget_connection()

    ok = True
    if TASKS_SCOPE in granted:
        try:
            print(f"ToDo リスト: 接続できました（リスト「{gtasks.LIST_NAME}」を{gtasks.ensure_list_for_setup()}）")
        except Exception as e:
            ok = False
            print(f"ToDo リスト: 接続の確認に失敗しました（{describe_error(e, 'Google ToDo リスト')}）")
    else:
        ok = False
        print("ToDo リスト: 許可されなかったため使えません")
    if set(CALENDAR_SCOPES) <= granted:
        try:
            names = gcal.calendar_names_for_setup()
            print("カレンダー: 接続できました。読み取るカレンダー:")
            for name in names:
                print(f"- {name}")
        except Exception as e:
            ok = False
            print(f"カレンダー: 接続の確認に失敗しました（{describe_error(e, 'Google カレンダー')}）")
    else:
        ok = False
        print("カレンダー: 許可されなかったため使えません。もう一度実行して、カレンダーの項目にもチェックを入れてください")
    if path:
        print(f"\nダウンロードした JSON ファイルはもう不要なので、削除してかまいません: {path}")
    print("\n資格情報マネージャーに保存しました。Jarvis を起動し直すと使えるようになります。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(setup())
