"""自動記憶：ユーザーに頼まれなくても、会話から覚えておくべきことを保存する。

会話中は API を使わず、やりとりを data/logs/pending.jsonl に追記するだけ。
1時間に1回、Claude Code デスクトップアプリのスケジュールタスクが organize.pyw を使って
  1. prepare_batch() で未整理ログと整理の指示を受け取り
  2. Claude 自身が結果の JSON を作り
  3. apply_batch() で長期メモ（data/memory.json）と会話ごとの要約（data/conversations.json）に反映する
（Claude Pro などのサブスクリプションの範囲で動き、API 料金はかからない）。
整理済みのログは data/logs/YYYY-MM.jsonl に移す。
"""

import json
import os
import time
from datetime import datetime

from jarvis.tools import (
    DATA_DIR,
    MEMORY_LOCK,
    apply_memory_changes,
    load_conversations,
    load_memories,
    save_conversation_summary,
)

AUTO_MEMORY_ON = os.getenv("JARVIS_AUTO_MEMORY", "on").lower() not in ("off", "0", "false", "no")

LOG_DIR = DATA_DIR / "logs"
PENDING_LOG = LOG_DIR / "pending.jsonl"
PROCESSING_LOG = LOG_DIR / "processing.jsonl"  # 整理中。失敗したら次回そのまま再処理する
ORGANIZER_LOG = LOG_DIR / "organize.log"

ORGANIZE_PROMPT = """あなたはAIアシスタント「Jarvis」の記憶係です。
ユーザーとJarvisの会話ログを読み、今後の会話で役立つ情報を長期メモに残し、会話ごとの要約を更新してください。

今日の日付: {today}

# 現在の長期メモ（番号付き）
{memories}

# 会話ログ（会話ごと。「これまでの要約」はすでに整理済みの部分）
{sessions}

# ルール
- add: 長期メモに新しく加える事実。ユーザー本人の好み・習慣・家族や知人・仕事や学校・持ち物・健康・予定・進行中の計画・決めたこと・頼まれごとなど、次回以降の会話でも役立つ個人的な情報だけ。
- 一般知識の質問とその答え、雑談、その場限りの依頼、Jarvisの発言だけに基づく推測は保存しない。
- すでに長期メモにある内容は add しない。内容が変わった・矛盾する場合は update でその番号を書き換える。
- 「明日」「来週」などの相対的な日付は、各発言の日時をもとに具体的な日付に直して書く。
- 各メモは「〜は〜」の形の短い1文にする。何もなければ add も update も空配列にする。
- summaries: ログにある会話それぞれについて、これまでの要約と新しいログを合わせた会話全体の要約（300字以内）。話題、決まったこと、未解決のことがわかるように書く。session_id はログに書かれたものをそのまま使う。

# 出力形式（このJSONだけを書く）
{{"add": ["新しいメモ", ...], "update": [{{"index": 番号, "note": "書き換え後のメモ"}}, ...], "summaries": [{{"session_id": "...", "summary": "..."}}, ...]}}"""

def new_session() -> tuple[str, str]:
    """新しい会話の (session_id, 開始日時) を返す。"""
    now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S-%f"), now.strftime("%Y-%m-%d %H:%M")


def log_exchange(session_id: str, started: str, user_text: str, assistant_text: str) -> None:
    """1往復のやりとりを未整理ログに追記する（API は呼ばない）。"""
    if not AUTO_MEMORY_ON or not user_text.strip():
        return
    entry = {
        "session_id": session_id,
        "started": started,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "user": user_text,
        "assistant": assistant_text,
    }
    with MEMORY_LOCK:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with PENDING_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def prepare_batch() -> str | None:
    """未整理ログを整理中に移し、整理の指示文を返す。整理するものがなければ None。
    前回の整理が終わっていなければ、その分をもう一度返す。"""
    if not PROCESSING_LOG.exists():
        if not PENDING_LOG.exists():
            return None
        _rename_with_retry(PENDING_LOG, PROCESSING_LOG)
    entries = _processing_entries()
    if not entries:
        _archive_processing()
        return None
    return _build_prompt(entries)


def apply_batch(result: dict) -> str:
    """整理結果（ORGANIZE_PROMPT の出力形式）を記憶に反映し、整理中のログを保管する。"""
    _validate_result(result)
    entries = _processing_entries()
    apply_memory_changes(result["add"], result["update"])
    started = {e["session_id"]: e["started"] for e in entries}
    for s in result["summaries"]:
        if s["session_id"] in started and s["summary"].strip():
            save_conversation_summary(s["session_id"], started[s["session_id"]], s["summary"])
    _archive_processing()
    return (
        f"{len(entries)}件のやりとりを整理しました"
        f"（メモ追加 {len(result['add'])}件・更新 {len(result['update'])}件・要約 {len(result['summaries'])}件）"
    )


def _validate_result(result) -> None:
    """形の崩れた結果でメモを壊さないよう、反映前に確認する。"""
    def ok(cond: bool, message: str) -> None:
        if not cond:
            raise ValueError(f"整理結果の形式が正しくありません: {message}")

    ok(isinstance(result, dict), "JSON オブジェクトではありません")
    ok(isinstance(result.get("add"), list) and all(isinstance(n, str) for n in result["add"]), "add は文字列の配列")
    ok(
        isinstance(result.get("update"), list)
        and all(isinstance(u, dict) and isinstance(u.get("index"), int) and isinstance(u.get("note"), str)
                for u in result["update"]),
        "update は {index: 整数, note: 文字列} の配列",
    )
    ok(
        isinstance(result.get("summaries"), list)
        and all(isinstance(s, dict) and isinstance(s.get("session_id"), str) and isinstance(s.get("summary"), str)
                for s in result["summaries"]),
        "summaries は {session_id: 文字列, summary: 文字列} の配列",
    )


def _processing_entries() -> list[dict]:
    if not PROCESSING_LOG.exists():
        raise FileNotFoundError("整理中のログがありません。先に prepare を実行してください")
    return [json.loads(line) for line in PROCESSING_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def _archive_processing() -> None:
    # 整理済みのログは月ごとのファイルに残す
    archive = LOG_DIR / f"{datetime.now():%Y-%m}.jsonl"
    with archive.open("a", encoding="utf-8") as f:
        f.write(PROCESSING_LOG.read_text(encoding="utf-8"))
    PROCESSING_LOG.unlink()


def _build_prompt(entries: list[dict]) -> str:
    memories = load_memories()
    summaries = {c.get("id"): c.get("summary", "") for c in load_conversations()}
    sessions: dict[str, list[dict]] = {}
    for e in entries:
        sessions.setdefault(e["session_id"], []).append(e)

    blocks = []
    for session_id, items in sessions.items():
        lines = [f"## session_id: {session_id}（開始 {items[0]['started']}）"]
        lines.append(f"これまでの要約: {summaries.get(session_id) or '（まだなし）'}")
        for e in items:
            lines.append(f"[{e['time']}] ユーザー: {e['user']}")
            lines.append(f"[{e['time']}] Jarvis: {e['assistant'] or '（テキストの返答なし）'}")
        blocks.append("\n".join(lines))

    return ORGANIZE_PROMPT.format(
        today=datetime.now().strftime("%Y年%m月%d日"),
        memories="\n".join(f"{i}. {m}" for i, m in enumerate(memories, 1)) or "（なし）",
        sessions="\n\n".join(blocks),
    )


def _rename_with_retry(src, dst) -> None:
    # Jarvis がちょうど追記中だと Windows では名前を変えられないため、少し待って再試行する
    for attempt in range(10):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.5)
