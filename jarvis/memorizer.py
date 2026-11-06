"""自動記憶：会話ログを分析・分類し、長期記憶の Markdown 文書（data/memory）に整理する。

会話中は API を使わず、やりとりを data/logs/pending.jsonl に追記するだけ。
1時間に1回、Claude Code デスクトップアプリのスケジュールタスクが organize.pyw を使って
  1. prepare_batch() で未整理ログ・現在の記憶の文書などの材料を受け取り
  2. Claude 自身がスキル organize-memory のルールに従って文書の書き換え（新設・統合・改名・削除を含む）を JSON で作り
  3. apply_batch() で data/memory に反映する
（Claude Pro などのサブスクリプションの範囲で動き、API 料金はかからない）。
整理済みのログは data/archive/logs/YYYY-MM.jsonl に移し、頼まれない限り読み込まない。
"""

import os
import time
from datetime import datetime

from jarvis import gcal, gtasks
from jarvis.memory import (
    LOG_DIR,
    PENDING_LOG,
    PROCESSING_LOG,
    REQUEST_LABEL,
    append_log,
    apply_doc_changes,
    archive_entries,
    doc_path,
    load_docs,
    memory_budget,
    read_log,
)

AUTO_MEMORY_ON = os.getenv("JARVIS_AUTO_MEMORY", "on").lower() not in ("off", "0", "false", "no")

ORGANIZER_LOG = LOG_DIR / "organize.log"

# 整理の手順とルールの正本はスキル（.claude/skills/organize-memory/SKILL.md）。ここでは材料だけを渡す
ORGANIZE_MATERIALS = """今日: {today}
常時文書の合計: 現在{used}字（目安 {budget}字以内）

# 現在の文書
{documents}

# 新しい会話ログ（未整理）
{sessions}

# ユーザーからの直接の依頼
{requests}

# Google ToDo リストと同期するタスク一覧（現在）
{tasks}

# Google カレンダーの今後の予定（読み取りのみ。ここにある予定は events に書かない）
{events}"""


def new_session() -> tuple[str, str]:
    """新しい会話の (session_id, 開始日時) を返す。"""
    now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S-%f"), now.strftime("%Y-%m-%d %H:%M")


def log_exchange(session_id: str, started: str, user_text: str, assistant_text: str) -> None:
    """1往復のやりとりを未整理ログに追記する（API は呼ばない）。"""
    if not AUTO_MEMORY_ON or not user_text.strip():
        return
    append_log({
        "session_id": session_id,
        "started": started,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "user": user_text,
        "assistant": assistant_text,
    })


def prepare_batch() -> str | None:
    """未整理ログを整理中に移し、整理の材料を返す。整理するものがなければ None。
    前回の整理が終わっていなければ、その分をもう一度返す。"""
    if not PROCESSING_LOG.exists():
        if not PENDING_LOG.exists():
            return None
        _rename_with_retry(PENDING_LOG, PROCESSING_LOG)
    entries = _processing_entries()
    if not entries:
        PROCESSING_LOG.unlink()
        return None
    return _build_materials(entries)


def apply_batch(result: dict) -> str:
    """整理結果（スキル organize-memory の出力形式）を記憶の文書に反映し、整理中のログを保管庫に移す。"""
    _validate_result(result)
    entries = _processing_entries()
    events = result.get("events") or []
    apply_doc_changes(result["write"], result["delete"])
    gcal.queue_events(events)  # 追加は整理の後の gcal.push() で行う
    gtasks.set_tasks(result["tasks"], moved=[e["task_key"] for e in events if e.get("task_key")])
    archive_entries(entries)
    PROCESSING_LOG.unlink()
    return (
        f"{len(entries)}件の記録を整理しました"
        f"（書き換え {len(result['write'])}件: {', '.join(w['path'] for w in result['write']) or 'なし'}"
        f" ／ 削除 {len(result['delete'])}件: {', '.join(result['delete']) or 'なし'}）"
    )


def _validate_result(result) -> None:
    """形の崩れた結果で記憶を壊さないよう、反映前に確認する。"""
    def ok(cond: bool, message: str) -> None:
        if not cond:
            raise ValueError(f"整理結果の形式が正しくありません: {message}")

    ok(isinstance(result, dict), "JSON オブジェクトではありません")
    ok(
        isinstance(result.get("write"), list)
        and all(isinstance(w, dict) and isinstance(w.get("path"), str) and isinstance(w.get("content"), str)
                and w["content"].strip() for w in result["write"]),
        "write は {path: 文字列, content: 空でない文字列} の配列",
    )
    ok(
        isinstance(result.get("delete"), list) and all(isinstance(d, str) for d in result["delete"]),
        "delete は文字列の配列",
    )
    for path in [w["path"] for w in result["write"]] + result["delete"]:
        doc_path(path)  # パスが正しくなければ ValueError
    ok("tasks" in result, "tasks がありません（未完了のタスクを毎回全件書いてください）")
    gtasks.validate_tasks(result["tasks"])
    gcal.validate_events(result.get("events") or [])
    ok(not result.get("events") or gcal.is_configured(), "Google カレンダーが未連携なので events は書けません")
    written = {doc_path(w["path"]) for w in result["write"]}
    ok(not any(doc_path(d) in written for d in result["delete"]), "同じ文書を write と delete の両方に入れています")


def _processing_entries() -> list[dict]:
    if not PROCESSING_LOG.exists():
        raise FileNotFoundError("整理中のログがありません。先に prepare を実行してください")
    return read_log(PROCESSING_LOG)


def _build_materials(entries: list[dict]) -> str:
    docs = load_docs() | load_docs(reference=True)
    used = sum(len(c) for p, c in docs.items() if not p.startswith("reference/"))
    documents = "\n\n".join(
        f'<document path="{p}" chars="{len(c)}">\n{c}\n</document>' for p, c in docs.items()
    ) or "（まだありません。会話の内容から文書を作ってください）"

    sessions: dict[str, list[dict]] = {}
    requests = []
    for e in entries:
        if e.get("kind") in REQUEST_LABEL:
            requests.append(f"- [{e['time']}] {REQUEST_LABEL[e['kind']]}: {e['text']}")
        else:
            sessions.setdefault(e["session_id"], []).append(e)
    blocks = []
    for session_id, items in sessions.items():
        lines = [f"## 会話（開始 {items[0]['started']}）"]
        for e in items:
            lines.append(f"[{e['time']}] ユーザー: {e['user']}")
            lines.append(f"[{e['time']}] Jarvis: {e['assistant'] or '（テキストの返答なし）'}")
        blocks.append("\n".join(lines))

    return ORGANIZE_MATERIALS.format(
        today=datetime.now().strftime("%Y年%m月%d日"),
        budget=memory_budget(),
        used=used,
        documents=documents,
        sessions="\n\n".join(blocks) or "（なし）",
        requests="\n".join(requests) or "（なし）",
        tasks=gtasks.tasks_for_prompt(),
        events=gcal.upcoming_for_prompt(),
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
