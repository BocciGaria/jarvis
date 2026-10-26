"""Jarvis GUI 版：ローカルサーバーを起動し、Edge/Chrome のアプリウィンドウで画面を開く。"""

import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import anthropic

from jarvis.assistant import EFFORT, MODEL, Assistant, describe_error
from jarvis.tools import load_memories
from jarvis.wakeword import WakeWordListener

HOST = "127.0.0.1"
PORT = int(os.getenv("JARVIS_PORT", "8765"))
WEB_DIR = Path(__file__).resolve().parent / "web"
WAKEWORD_ON = os.getenv("JARVIS_WAKEWORD", "on").lower() not in ("off", "0", "false", "no")

# 読み上げに使う VOICEVOX（既定は 47 = ナースロボ＿タイプＴ ノーマル）
VOICEVOX_URL = os.getenv("JARVIS_VOICEVOX_URL", "http://127.0.0.1:50021").rstrip("/")
VOICEVOX_SPEAKER = int(os.getenv("JARVIS_VOICEVOX_SPEAKER", "47"))
VOICEVOX_SPEED = float(os.getenv("JARVIS_VOICEVOX_SPEED", "1.2"))

# 他のWebサイトからこのローカルサーバーを勝手に呼ばれないよう、起動ごとの合言葉を画面にだけ埋め込む
TOKEN = secrets.token_urlsafe(24)

assistant: Assistant | None = None
chat_lock = threading.Lock()

# 画面へ「呼ばれた」などの通知を送るための購読キュー
subscribers: set[queue.Queue] = set()
subscribers_lock = threading.Lock()


def broadcast(event: dict) -> None:
    with subscribers_lock:
        for q in subscribers:
            q.put(event)


def bring_window_to_front() -> None:
    """Jarvis のウィンドウ（Edge/Chrome のアプリウィンドウ）を最小化から戻して前面に出す。"""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        title = ctypes.create_unicode_buffer(256)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == "Chrome_WidgetWin_1" and title.value.startswith("Jarvis") and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    for hwnd in found[:1]:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # Windows は裏のアプリが前面を奪うのを制限しているため、Alt キー操作を1回挟んで許可を得る
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)


def on_wake(score: float) -> None:
    print(f"「Hey Jarvis」を検知しました（スコア {score:.2f}）")
    if chat_lock.locked():
        return  # 返答の処理中は無視
    bring_window_to_front()
    broadcast({"type": "wake"})


wake_listener = WakeWordListener(on_wake, lambda warning: broadcast({"type": "mic", "warning": warning}))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # アクセスログは出さない
        pass

    # --- 応答ヘルパー ---
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _authorized(self) -> bool:
        # EventSource はヘッダーを付けられないため、通知用の購読だけはクエリでも受け付ける
        query_token = parse_qs(urlparse(self.path).query).get("token", [None])[0]
        if TOKEN not in (self.headers.get("X-Jarvis-Token"), query_token):
            self._json(403, {"error": "forbidden"})
            return False
        return True

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # --- ルーティング ---
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
            html = html.replace("__JARVIS_TOKEN__", TOKEN).replace("__JARVIS_MODEL__", MODEL)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/memory":
            if self._authorized():
                self._json(200, {"memories": load_memories()})
        elif path == "/api/wakeword":
            if self._authorized():
                self._json(200, _wakeword_status())
        elif path == "/api/events":
            if self._authorized():
                self._events()
        else:
            self._json(404, {"error": "not found"})

    def _events(self) -> None:
        """画面が開いている間つなぎっぱなしにし、呼びかけ検知などを通知する。"""
        q: queue.Queue = queue.Queue()
        with subscribers_lock:
            subscribers.add(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                try:
                    self._sse(q.get(timeout=15))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # 接続が切れていないかの確認
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with subscribers_lock:
                subscribers.discard(q)
            self.close_connection = True

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._authorized():
            return
        if path == "/api/chat":
            self._chat(str(self._read_json().get("message", "")).strip())
        elif path == "/api/reset":
            with chat_lock:
                _get_assistant().reset()
            self._json(200, {"ok": True})
        elif path == "/api/tts":
            self._tts(str(self._read_json().get("text", "")).strip())
        elif path == "/api/wakeword":
            if self._read_json().get("enabled"):
                wake_listener.start()
            else:
                wake_listener.stop()
            self._json(200, _wakeword_status())
        else:
            self._json(404, {"error": "not found"})

    def _chat(self, message: str) -> None:
        if not message:
            self._json(400, {"error": "empty message"})
            return
        if not chat_lock.acquire(blocking=False):
            self._json(409, {"error": "busy"})
            return
        try:
            # Server-Sent Events 形式で応答を少しずつ送る
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            events = None
            try:
                events = _get_assistant().chat(message)
                for event in events:
                    self._sse(event)
                self._sse({"type": "done"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # 画面側が中断した。履歴は chat() 側で巻き戻される
            except anthropic.APIError as e:
                self._sse({"type": "error", "text": describe_error(e)})
            except Exception as e:  # 予期しない失敗も画面に伝える
                self._sse({"type": "error", "text": describe_error(e)})
            finally:
                if events is not None:
                    events.close()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            chat_lock.release()
            self.close_connection = True

    def _tts(self, text: str) -> None:
        if not text:
            self._json(400, {"error": "empty text"})
            return
        try:
            wav = voicevox_synthesize(text)
        except OSError as e:  # VOICEVOX が起動していない・応答しない
            self._json(502, {"error": f"VOICEVOX に接続できません（{e}）"})
            return
        self._send(200, wav, "audio/wav")

    def _sse(self, data: dict) -> None:
        self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()


def voicevox_synthesize(text: str) -> bytes:
    """VOICEVOX エンジンで text を読み上げた WAV を作る。"""
    params = urlencode({"text": text, "speaker": VOICEVOX_SPEAKER})
    req = urllib.request.Request(f"{VOICEVOX_URL}/audio_query?{params}", method="POST")
    with urllib.request.urlopen(req, timeout=10) as res:
        query = json.load(res)
    query["speedScale"] = VOICEVOX_SPEED
    req = urllib.request.Request(
        f"{VOICEVOX_URL}/synthesis?speaker={VOICEVOX_SPEAKER}",
        data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def _wakeword_status() -> dict:
    return {
        "enabled": wake_listener.running,
        "error": wake_listener.error,
        "warning": wake_listener.warning,
        "device": wake_listener.device_name,
    }


def _get_assistant() -> Assistant:
    global assistant
    if assistant is None:
        assistant = Assistant()
    return assistant


def _find_app_browser() -> str | None:
    """アプリモード（アドレスバーなしのウィンドウ）で開ける Edge / Chrome を探す。"""
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    return next((c for c in candidates if c and os.path.exists(c)), None)


def open_window(url: str) -> None:
    browser = _find_app_browser()
    if browser:
        # 「Hey Jarvis」で起動したときなど、クリックなしでも音声を再生できるようにする
        subprocess.Popen([browser, f"--app={url}", "--window-size=1100,800", "--autoplay-policy=no-user-gesture-required"])
    else:
        webbrowser.open(url)


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"ポート {PORT} は使用中です。Jarvis が既に起動していないか確認するか、.env の JARVIS_PORT を変えてください。")
        sys.exit(1)

    url = f"http://{HOST}:{PORT}/"
    print(f"Jarvis GUI 起動完了（{MODEL} / effort={EFFORT}）: {url}")
    print("このウィンドウを閉じるか Ctrl+C で終了します。")
    if WAKEWORD_ON:
        wake_listener.start()
        print("「Hey Jarvis」と呼びかけるとマイクが起動します。")
    if "--no-browser" not in sys.argv:
        open_window(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
