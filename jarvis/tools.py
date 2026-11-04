"""Jarvis が使えるローカルツール（カレンダー・記憶・画面表示）。"""

import json
import ssl
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

import certifi

from jarvis import icloud
from jarvis.memory import doc_title, load_docs, log_request, read_doc, search_archive

# Claude に渡すローカルツール定義。Web 検索（Anthropic 側で実行）は tools_for() で追加する。
TOOLS = [
    *icloud.CALENDAR_TOOLS,
    {
        "name": "save_memory",
        "description": (
            "ユーザーに「覚えておいて」とはっきり頼まれた内容を、すぐに記憶に残す（次の整理で適切な文書に反映される）。"
            "会話は自動で記録・整理されるため、頼まれていないときは使わず、保存するかどうかをユーザーに尋ねることもしない。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "覚えておく内容"}},
            "required": ["note"],
        },
    },
    {
        "name": "forget_memory",
        "description": "ユーザーに「忘れて」と頼まれた内容を、記憶のすべての文書から消すよう依頼する（次の整理で反映される）。",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"what": {"type": "string", "description": "忘れる内容"}},
            "required": ["what"],
        },
    },
    {
        "name": "list_memories",
        "description": "記憶の文書と資料の一覧（パスと見出し）を最新の状態で取得する。",
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_memory",
        "description": "記憶の文書や資料（reference/〇〇.md）を最新の内容で読む。目次にある資料の中身が必要なときに使う。",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文書のパス（例: タスク.md, reference/完了したタスク.md）"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_archive",
        "description": (
            "整理済みの過去の会話ログをキーワードで探す。ユーザーに以前の会話そのものを確かめるよう頼まれたときや、"
            "記憶の文書にない細部が必要なときだけ使う。"
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "探す語（空白区切りで、すべてを含むものを探す）"}},
            "required": ["query"],
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


def _validate(name: str, args) -> str | None:
    """ストリーミング時の部分パースに備え、入力の形を確認する。問題があればエラー文を返す。"""
    if not isinstance(args, dict):
        return "入力がオブジェクトではありません"
    required = {"save_memory": "note", "forget_memory": "what", "read_memory": "path", "search_archive": "query"}
    key = required.get(name)
    if key and not (isinstance(args.get(key), str) and args[key].strip()):
        return f"{key}（文字列）が必要です"
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
    if name in icloud.TOOL_NAMES:
        return icloud.run_tool(name, args)

    error = _validate(name, args)
    if error:
        return f"INVALID_INPUT: {error} (受け取った入力: {json.dumps(args, ensure_ascii=False)})", True

    if name == "save_memory":
        log_request("note", args["note"])
        return "記憶しました（次の整理で適切な文書に反映されます）", False

    if name == "forget_memory":
        log_request("forget", args["what"])
        return "忘れるよう記録しました（次の整理ですべての文書から消されます）", False

    if name == "list_memories":
        docs = load_docs() | load_docs(reference=True)
        if not docs:
            return "記憶の文書はまだありません", False
        return "\n".join(f"- {path}: {doc_title(content)}" for path, content in docs.items()), False

    if name == "read_memory":
        try:
            content = read_doc(args["path"])
        except ValueError as e:
            return str(e), True
        if content is None:
            return f"{args['path']} はありません。list_memories で一覧を確認してください", True
        return content, False

    if name == "search_archive":
        found = search_archive(args["query"])
        return "\n\n".join(found) if found else f"「{args['query']}」を含む過去の会話は見つかりませんでした", False

    if name == "show_display":
        output, is_error, _ = run_display(args)
        return output, is_error

    return f"不明なツール: {name}", True
