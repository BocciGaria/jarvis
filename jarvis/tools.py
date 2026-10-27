"""Jarvis が使えるローカルツール（時刻・メモ帳・画面表示）。"""

import json
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import certifi

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MEMORY_FILE = DATA_DIR / "memory.json"
CONVERSATIONS_FILE = DATA_DIR / "conversations.json"

# 会話中のツール実行と、裏で動く自動記憶（memorizer）が同時にファイルを書き換えないようにする
MEMORY_LOCK = threading.RLock()

# Claude に渡すローカルツール定義。Web 検索（Anthropic 側で実行）は tools_for() で追加する。
TOOLS = [
    {
        "name": "get_current_time",
        "description": "現在の日付・時刻・曜日を取得する。日付や時間に関する質問に答える前に使う。",
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "save_memory",
        "description": "ユーザーについて覚えておくべき情報（好み、予定、名前など）を長期メモに保存する。",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "保存する内容"}},
            "required": ["note"],
        },
    },
    {
        "name": "list_memories",
        "description": "長期メモに保存されている内容を番号付きで一覧する。",
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "delete_memory",
        "description": "長期メモから指定番号（list_memories の番号）の項目を削除する。",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"index": {"type": "integer", "description": "削除する番号（1始まり）"}},
            "required": ["index"],
        },
    },
    {
        "name": "show_display",
        "description": (
            "ユーザーの画面にパネルを表示する。一覧・手順・表・数値・URL・コード・画像など、"
            "耳で聞くより目で見たほうが分かりやすい情報があるときに使う。"
            "声では要点だけを話し、細かい内容はこのパネルに任せる。ちょっとした会話では使わない。"
            "写真や画像を見せてと頼まれたら、URL を本文に書くのではなく必ず image_url に渡して画像そのものを表示する。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "パネルの見出し（短く）"},
                "body": {
                    "type": "string",
                    "description": "本文。改行可。行頭「- 」で箇条書き、**太字**、URL はリンクになる",
                },
                "image_url": {
                    "type": "string",
                    "description": (
                        "表示する画像の URL（http/https、任意）。画像ファイルの URL が分からなければ、"
                        "その画像が載っている Web ページの URL でよい（ページの代表画像を取り出して表示する）"
                    ),
                },
            },
            "required": ["title"],
        },
    },
]


# 普段の会話を担うモデルだけに渡す。呼ばれたらそのターンを上位モデルでやり直す（assistant.py）
HANDOFF_TOOL = {
    "name": "use_smart_model",
    "description": (
        "この依頼を、より賢い上位モデルに任せる。次のような込み入った依頼のときは、何も話さずに最初にこれだけを呼ぶ: "
        "複数の情報を調べて比べる・まとめる調べ物、計画やスケジュールの立案、込み入った相談や判断、"
        "長い文章の作成・要約・翻訳、コード・計算・論理的な推論、正確さが大事な専門的な質問（医療・法律・お金など）。"
        "あいさつ・雑談・簡単な質問・時刻の確認・メモの保存・一度の検索で済む調べ物は自分で答え、これは使わない。"
    ),
    "eager_input_streaming": True,
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "上位モデルに任せる理由（短く）"}},
        "required": [],
    },
}


def tools_for(model: str, handoff: bool = False) -> list:
    """モデルに合った Web 検索ツールを加えたツール一覧を返す。handoff=True なら上位モデルへの引き継ぎも加える。
    動的フィルタ付きの web_search_20260209 は Haiku 系では使えないため、基本版に切り替える。"""
    search_type = "web_search_20250305" if "haiku" in model else "web_search_20260209"
    extra = [HANDOFF_TOOL] if handoff else []
    return TOOLS + extra + [{"type": search_type, "name": "web_search", "max_uses": 5}]


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_json(path: Path, data: list) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")  # 書き込み途中で落ちても元のファイルが壊れないように
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_memories() -> list[str]:
    return _load_json(MEMORY_FILE)


def _save_memories(memories: list[str]) -> None:
    _save_json(MEMORY_FILE, memories)


def apply_memory_changes(add: list[str], update: list[dict]) -> None:
    """自動記憶の結果を反映する。update は {"index": 1始まりの番号, "note": 新しい内容}。"""
    with MEMORY_LOCK:
        memories = load_memories()
        for item in update:
            if 1 <= item["index"] <= len(memories) and item["note"].strip():
                memories[item["index"] - 1] = item["note"].strip()
        for note in add:
            if note.strip() and note.strip() not in memories:
                memories.append(note.strip())
        _save_memories(memories)


def load_conversations() -> list[dict]:
    """過去の会話の要約（古い順）。各要素は {"id", "started", "updated", "summary"}。"""
    return _load_json(CONVERSATIONS_FILE)


def save_conversation_summary(session_id: str, started: str, summary: str) -> None:
    """セッションの要約を上書き保存する（なければ追加）。"""
    with MEMORY_LOCK:
        conversations = load_conversations()
        record = {
            "id": session_id,
            "started": started,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "summary": summary.strip(),
        }
        for i, c in enumerate(conversations):
            if c.get("id") == session_id:
                conversations[i] = record
                break
        else:
            conversations.append(record)
        _save_json(CONVERSATIONS_FILE, conversations)


def _validate(name: str, args) -> str | None:
    """ストリーミング時の部分パースに備え、入力の形を確認する。問題があればエラー文を返す。"""
    if not isinstance(args, dict):
        return "入力がオブジェクトではありません"
    if name == "save_memory" and not (isinstance(args.get("note"), str) and args["note"].strip()):
        return "note（文字列）が必要です"
    if name == "delete_memory" and not isinstance(args.get("index"), int):
        return "index（整数）が必要です"
    if name == "show_display":
        if not (isinstance(args.get("title"), str) and args["title"].strip()):
            return "title（文字列）が必要です"
        body, image_url = args.get("body"), args.get("image_url")
        if not ((isinstance(body, str) and body.strip()) or isinstance(image_url, str)):
            return "body か image_url のどちらかが必要です"
        if isinstance(image_url, str) and not image_url.startswith(("http://", "https://")):
            return "image_url は http:// か https:// で始まる URL にしてください"
    return None


# 画像の取得に使う。ブラウザ以外からのアクセスを断るサイトがあるため、まずはブラウザと同じ名乗りで取得し、
# 断られたら（Wikimedia のように偽装を嫌うサイト向けに）正直な名乗りでもう一度試す
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
JARVIS_UA = "Jarvis/1.0 (personal voice assistant)"
PAGE_LIMIT = 2 * 1024 * 1024  # 代表画像を探すときに読むページの上限（バイト）
# Windows の証明書ストアが古いと HTTPS の検証に失敗するため、anthropic と同じ certifi の証明書を使う
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def fetch_url(url: str, limit: int) -> tuple[str, str, bytes]:
    """url を取得して (最終的な URL, Content-Type, 本文の先頭 limit バイト) を返す。"""
    for ua in (BROWSER_UA, JARVIS_UA):
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "image/*,text/html;q=0.9,*/*;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as res:
                return res.geturl(), res.headers.get_content_type(), res.read(limit)
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429) or ua == JARVIS_UA:
                raise


class _ImageFinder(HTMLParser):
    """ページの代表画像（og:image など）と、本文中の最初の画像を探す。"""

    META_KEYS = ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")

    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}
        self.first_img = ""

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in self.META_KEYS and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "link" and a.get("rel", "").lower() == "image_src" and a.get("href"):
            self.meta.setdefault("image_src", a["href"])
        elif tag == "img" and not self.first_img:
            src = a.get("src") or a.get("data-src") or ""
            small = any(a.get(k, "").isdigit() and int(a[k]) < 100 for k in ("width", "height"))  # アイコン類は除く
            if src and not src.startswith("data:") and not src.lower().endswith(".svg") and not small:
                self.first_img = src

    def best(self) -> str:
        for key in (*self.META_KEYS, "image_src"):
            if key in self.meta:
                return self.meta[key]
        return self.first_img


def resolve_image(url: str) -> str | None:
    """画像の URL ならそのまま、Web ページの URL ならページの代表画像の URL を返す。見つからなければ None。"""
    try:
        final, content_type, data = fetch_url(url, PAGE_LIMIT)
    except (OSError, ValueError):
        return None
    if content_type.startswith("image/"):
        return final
    if content_type not in ("text/html", "application/xhtml+xml"):
        return None
    finder = _ImageFinder()
    finder.feed(data.decode("utf-8", errors="replace"))
    found = finder.best()
    return urljoin(final, found) if found else None


def run_display(args) -> tuple[str, bool, dict | None]:
    """show_display を実行して (結果テキスト, エラーかどうか, 画面に送る表示イベント) を返す。"""
    error = _validate("show_display", args)
    if error:
        return f"INVALID_INPUT: {error} (受け取った入力: {json.dumps(args, ensure_ascii=False)})", True, None
    body = args.get("body") if isinstance(args.get("body"), str) else ""
    requested = args.get("image_url") if isinstance(args.get("image_url"), str) else ""
    image_url = resolve_image(requested) if requested else ""
    event = {"type": "display", "title": args["title"].strip(), "body": body, "image_url": image_url or ""}
    if requested and not image_url:
        if not body.strip():
            return f"画像を取得できませんでした（{requested}）。別の画像やページの URL で試してください", True, None
        return f"本文は表示しましたが、画像は取得できませんでした（{requested}）", False, event
    if image_url and image_url != requested:
        return f"画面に表示しました（ページの代表画像 {image_url} を表示）", False, event
    return "画面に表示しました", False, event


def run_tool(name: str, args) -> tuple[str, bool]:
    """ツールを実行して (結果テキスト, エラーかどうか) を返す。"""
    error = _validate(name, args)
    if error:
        return f"INVALID_INPUT: {error} (受け取った入力: {json.dumps(args, ensure_ascii=False)})", True

    if name == "get_current_time":
        now = datetime.now().astimezone()
        weekdays = "月火水木金土日"
        return now.strftime(f"%Y年%m月%d日（{weekdays[now.weekday()]}） %H:%M:%S %Z"), False

    if name == "save_memory":
        with MEMORY_LOCK:
            memories = load_memories()
            memories.append(args["note"].strip())
            _save_memories(memories)
        return f"保存しました（{len(memories)}件目）", False

    if name == "list_memories":
        memories = load_memories()
        if not memories:
            return "メモはまだありません", False
        return "\n".join(f"{i}. {m}" for i, m in enumerate(memories, 1)), False

    if name == "delete_memory":
        with MEMORY_LOCK:
            memories = load_memories()
            index = args["index"]
            if not 1 <= index <= len(memories):
                return f"番号 {index} は存在しません（1〜{len(memories)}）", True
            removed = memories.pop(index - 1)
            _save_memories(memories)
        return f"削除しました: {removed}", False

    if name == "show_display":
        output, is_error, _ = run_display(args)
        return output, is_error

    return f"不明なツール: {name}", True
