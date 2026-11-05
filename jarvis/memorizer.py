"""自動記憶：会話ログを分析・分類し、長期記憶の Markdown 文書（data/memory）に整理する。

会話中は API を使わず、やりとりを data/logs/pending.jsonl に追記するだけ。
1時間に1回、Claude Code デスクトップアプリのスケジュールタスクが organize.pyw を使って
  1. prepare_batch() で未整理ログ・現在の記憶の文書・整理の指示を受け取り
  2. Claude 自身が文書の書き換え（新設・統合・改名・削除を含む）を JSON で作り
  3. apply_batch() で data/memory に反映する
（Claude Pro などのサブスクリプションの範囲で動き、API 料金はかからない）。
整理済みのログは data/archive/logs/YYYY-MM.jsonl に移し、頼まれない限り読み込まない。
"""

import os
import time
from datetime import datetime

from jarvis import gtasks
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

ORGANIZE_PROMPT = """あなたはAIアシスタント「Jarvis」の記憶係です。
ユーザーとJarvisの会話ログを分析し、Jarvisの長期記憶（Markdown文書の集まり）を最新の状態に作り直してください。
Jarvisはこの記憶を読み、ユーザーを深く理解した頼れる相棒として振る舞います。

今日: {today}

# 記憶の仕組み
- 常時文書（「名前.md」）: Jarvisが会話を始めるたびに全文を読む。いまの会話で役立つことを置く。合計{budget}字以内が目安（現在{used}字）。
- 資料（「reference/名前.md」）: 普段は読まず、Jarvisが必要なときだけ開く。完了したことの記録、数字の履歴、たまにしか使わない詳細など。資料のパスと1行目の見出しは目次としてJarvisに常に見えるので、見出しで中身が分かるようにする。
- 整理が終わった会話ログは保管庫に移り、頼まれない限り読まれない。残すべきことは必ず文書に書く。

# 現在の文書
{documents}

# 新しい会話ログ（未整理）
{sessions}

# ユーザーからの直接の依頼（「Google ToDo リストで」はユーザーがスマホなどで行った操作や、Jarvis が会話中に行った操作で、すでに下のタスク一覧に反映済み）
{requests}

# Google ToDo リストと同期するタスク一覧（現在）
{tasks}

# 分類と分析のルール
- 内容を話題ごとに文書へ分類する。例: タスク、ユーザーの人格、朝会・夕会のアジェンダ、資産管理、健康管理、目標管理。ほかにも仕事・人間関係・趣味・住まい・学びなど、会話に合わせて必要な文書を作る。
- 構成は固定しない。ユーザーの興味や話題の重みが変わったら、文書の新設・統合・分割・改名・資料への移動・削除を自由に行う。中身が1〜2行しかない文書は作らず、近い文書にまとめる。
- ログ形式（日時ごとの出来事の羅列）では書かない。「いまどうなっているか」が分かる状態として、見出し・箇条書き・表で整理する。
- 書き写すのではなく分析して書く。タスクは期限・優先度・状態（未着手／進行中／完了）・次の一手で整理し、期限の近い順に並べる。目標はタスクとのつながりや進み具合を書く。資産や健康は数値の推移や傾向、気をつけることを書く。
- ユーザーの人格には、性格・価値観・興味・生活のリズム・話し方や受け答えの好みなど、やりとりから読み取れることを書く。本人が言ったことと推測（「〜と思われる」）は書き分ける。
- 朝会・夕会のアジェンダには、進め方の決まりごとに加え、次の朝会・夕会で確認・相談すべき具体的な項目（期限が近いタスク、明日に回したこと、気がかりなど）を書く。
- 各文書は「# 見出し」で始め、次の行に「更新: YYYY-MM-DD」を書く。
- 「明日」「来週」などの相対的な日付は、発言の日時をもとに具体的な日付に直す。過ぎた予定や完了したタスクは常時文書から外し、残す価値があれば資料に移す。
- 話の途中で終わったこと（答えてもらえていない質問など）は、次の会話で続けられるよう関係する文書に書き、片付いたら消す。
- 残さないもの: 一般知識の質問とその答え、雑談、その場限りの依頼、Jarvisの発言だけに基づく推測。
- Jarvis の発言にある「［画面に表示: 見出し］」以下は、声の代わりに画面のパネルで見せた内容。ユーザーが選んだ・決めた・頼んだことに関わる部分だけを参考にし、検索結果や一般的な情報の一覧はそのまま保存しない。
- 「覚えておいて」と頼まれた内容は必ずどこかの文書に反映する。「忘れて」と頼まれた内容は、すべての文書と資料から消す。
- 常時文書の合計が目安を超えそうなら、細部を資料に移すか要約して収める。
- tasks には、タスクの文書と同じ内容の未完了タスクを毎回全件書く。これが Google ToDo リストに同期される。載せなかったタスクは完了扱いになるので、完了・取りやめになったものだけを外す。
- tasks の key は、現在のタスク一覧にあるものは必ずそのまま使う（「g-」で始まるものも）。新しいタスクには短い英数字とハイフンの key を新しく付け、一度外した key は使い回さない。
- tasks の title はスマホの一覧で見て分かる短い名前にする。時刻があれば title に含める（例: 18:00 上司と1on1）。due は期限の日付（YYYY-MM-DD）で、はっきりしなければ null にして notes に書く。notes には次の一手を短く書く。定期タスクは、いま動いている回だけを入れる。

# 出力形式（このJSONだけを書く）
{{"write": [{{"path": "タスク.md", "content": "# タスク\\n更新: ...\\n\\n..."}}, ...], "delete": ["消す文書のパス", ...], "tasks": [{{"key": "nenmatsu-chosei", "title": "...", "due": "2026-11-15", "notes": "..."}}, ...]}}
- write には新しく作る文書と書き換える文書だけを全文で入れる。変更のない文書は入れない。
- 改名や資料への移動は、新しいパスで write し、古いパスを delete に入れる。
- パスは「名前.md」か「reference/名前.md」。名前に / \\ : * ? " < > | は使わない。"""


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
    """未整理ログを整理中に移し、整理の指示文を返す。整理するものがなければ None。
    前回の整理が終わっていなければ、その分をもう一度返す。"""
    if not PROCESSING_LOG.exists():
        if not PENDING_LOG.exists():
            return None
        _rename_with_retry(PENDING_LOG, PROCESSING_LOG)
    entries = _processing_entries()
    if not entries:
        PROCESSING_LOG.unlink()
        return None
    return _build_prompt(entries)


def apply_batch(result: dict) -> str:
    """整理結果（ORGANIZE_PROMPT の出力形式）を記憶の文書に反映し、整理中のログを保管庫に移す。"""
    _validate_result(result)
    entries = _processing_entries()
    apply_doc_changes(result["write"], result["delete"])
    gtasks.set_tasks(result["tasks"])
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
    written = {doc_path(w["path"]) for w in result["write"]}
    ok(not any(doc_path(d) in written for d in result["delete"]), "同じ文書を write と delete の両方に入れています")


def _processing_entries() -> list[dict]:
    if not PROCESSING_LOG.exists():
        raise FileNotFoundError("整理中のログがありません。先に prepare を実行してください")
    return read_log(PROCESSING_LOG)


def _build_prompt(entries: list[dict]) -> str:
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

    return ORGANIZE_PROMPT.format(
        today=datetime.now().strftime("%Y年%m月%d日"),
        budget=memory_budget(),
        used=used,
        documents=documents,
        sessions="\n\n".join(blocks) or "（なし）",
        requests="\n".join(requests) or "（なし）",
        tasks=gtasks.tasks_for_prompt(),
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
