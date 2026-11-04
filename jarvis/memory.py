"""Jarvis の記憶：Markdown 文書としての長期記憶と、整理が終わった会話ログの保管庫。

  data/memory/*.md            会話を始めるたびに全文を読み込む文書（タスク・人格・朝会/夕会など。構成は整理のたびに変わる）
  data/memory/reference/*.md  普段は読まず、必要なときだけ read_memory で開く資料（目次だけ常に見せる）
  data/logs/pending.jsonl     未整理の会話ログと、「覚えておいて」「忘れて」の依頼
  data/archive/               整理済みの会話ログ・整理前の記憶のバックアップ（頼まれない限り読み込まない）
"""

import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MEMORY_DIR = DATA_DIR / "memory"
REFERENCE_DIR = MEMORY_DIR / "reference"
LOG_DIR = DATA_DIR / "logs"
PENDING_LOG = LOG_DIR / "pending.jsonl"
PROCESSING_LOG = LOG_DIR / "processing.jsonl"  # 整理中。失敗したら次回そのまま再処理する
ARCHIVE_DIR = DATA_DIR / "archive"
ARCHIVE_LOG_DIR = ARCHIVE_DIR / "logs"
BACKUP_DIR = ARCHIVE_DIR / "memory-backups"
KEEP_BACKUPS = 30

# 会話中のツール実行と、裏で動く自動記憶（memorizer）が同時にファイルを書き換えないようにする
MEMORY_LOCK = threading.RLock()

# 「名前.md」か「reference/名前.md」。Windows でファイル名に使えない文字と、先頭のドットは受け付けない
DOC_PATH = re.compile(r'^(reference/)?[^/\\:*?"<>|.][^/\\:*?"<>|]{0,60}\.md$')
REQUEST_LABEL = {"note": "覚えておいて", "forget": "忘れて"}


def memory_budget() -> int:
    """常時読み込む文書の合計文字数の目安（.env を読んだ後に参照するため関数にしている）。"""
    return int(os.getenv("JARVIS_MEMORY_BUDGET", "8000"))


# ---------- 記憶の文書 ----------
def doc_path(rel: str) -> Path:
    rel = rel.strip().replace("\\", "/")
    if not DOC_PATH.match(rel):
        raise ValueError(f"文書のパスが正しくありません: {rel!r}（「名前.md」か「reference/名前.md」）")
    return MEMORY_DIR / rel


def load_docs(reference: bool = False) -> dict[str, str]:
    """{パス: 内容} を名前順で返す。reference=True なら資料のほう。"""
    folder, prefix = (REFERENCE_DIR, "reference/") if reference else (MEMORY_DIR, "")
    if not folder.exists():
        return {}
    return {prefix + p.name: p.read_text(encoding="utf-8").strip() for p in sorted(folder.glob("*.md"))}


def read_doc(rel: str) -> str | None:
    path = doc_path(rel)
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def doc_title(content: str) -> str:
    first = content.strip().splitlines()[0] if content.strip() else ""
    return first.lstrip("#").strip()


def memory_context() -> str:
    """システムプロンプトに添える長期記憶。常時文書の全文と、資料の目次。"""
    limit = memory_budget() * 2  # 整理で目安に収まらなかったときの上限。超えた分は目次に回す
    docs, refs = load_docs(), load_docs(reference=True)
    parts, closed, used = [], [], 0
    for path, content in docs.items():
        if used + len(content) <= limit:
            parts.append(f'<document path="{path}">\n{content}\n</document>')
            used += len(content)
        else:
            closed.append(path)
    closed += list(refs)
    contents = docs | refs
    if closed:
        parts.append(
            "必要なときに read_memory で開ける資料:\n"
            + "\n".join(f"- {p}: {doc_title(contents[p])}" for p in closed)
        )
    requests = pending_requests()
    if requests:
        parts.append(
            "まだ記憶に整理されていない、ユーザーからの直接の依頼:\n"
            + "\n".join(f"- [{r['time']}] {REQUEST_LABEL[r['kind']]}: {r['text']}" for r in requests)
        )
    return "\n\n".join(parts)


def apply_doc_changes(write: list[dict], delete: list[str]) -> None:
    """整理結果を反映する。パスはすべて確認してから、バックアップを取って書き換える。"""
    targets = [(doc_path(w["path"]), w["content"].strip() + "\n") for w in write]
    removals = [doc_path(d) for d in delete]
    with MEMORY_LOCK:
        _backup()
        for path in removals:
            path.unlink(missing_ok=True)
        for path, content in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")  # 書き込み途中で落ちても元のファイルが壊れないように
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)


def _backup() -> None:
    """構成ごと作り変えるので、書き換える前の記憶を丸ごと残しておく（古いものから消す）。"""
    if not MEMORY_DIR.exists() or not any(MEMORY_DIR.rglob("*.md")):
        return
    shutil.copytree(MEMORY_DIR, BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S"), dirs_exist_ok=True)
    for old in sorted(BACKUP_DIR.iterdir())[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


# ---------- 未整理ログ ----------
def append_log(entry: dict) -> None:
    with MEMORY_LOCK:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with PENDING_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def log_request(kind: str, text: str) -> None:
    """「覚えておいて」(note)・「忘れて」(forget) の依頼を、次の整理で反映されるよう残す。"""
    append_log({"kind": kind, "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text.strip()})


def pending_requests() -> list[dict]:
    """整理待ち・整理中の依頼（整理されるまではシステムプロンプトに直接見せる）。"""
    with MEMORY_LOCK:
        entries = read_log(PROCESSING_LOG) + read_log(PENDING_LOG)
    return [e for e in entries if e.get("kind") in REQUEST_LABEL]


# ---------- 整理済みログの保管庫 ----------
def archive_entries(entries: list[dict]) -> None:
    ARCHIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (ARCHIVE_LOG_DIR / f"{datetime.now():%Y-%m}.jsonl").open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def search_archive(query: str, limit: int = 8) -> list[str]:
    """保管庫の会話ログから、すべての語を含むやりとりを新しい順に探す。"""
    words = [w.lower() for w in query.split() if w]
    found = []
    for path in sorted(ARCHIVE_LOG_DIR.glob("*.jsonl"), reverse=True) if ARCHIVE_LOG_DIR.exists() else []:
        for e in reversed(read_log(path)):
            if e.get("kind") in REQUEST_LABEL:
                text = f"{REQUEST_LABEL[e['kind']]}: {e['text']}"
            else:
                text = f"ユーザー: {e.get('user', '')}\nJarvis: {e.get('assistant', '')}"
            if all(w in text.lower() for w in words):
                found.append(f"[{e.get('time', '')}] {text[:400]}")
                if len(found) >= limit:
                    return found
    return found
