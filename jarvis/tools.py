"""Jarvis が使えるローカルツール（時刻・メモ帳）。"""

import json
import threading
from datetime import datetime
from pathlib import Path

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
]


def tools_for(model: str) -> list:
    """モデルに合った Web 検索ツールを加えたツール一覧を返す。
    動的フィルタ付きの web_search_20260209 は Haiku 系では使えないため、基本版に切り替える。"""
    search_type = "web_search_20250305" if "haiku" in model else "web_search_20260209"
    return TOOLS + [{"type": search_type, "name": "web_search", "max_uses": 5}]


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
    return None


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

    return f"不明なツール: {name}", True
