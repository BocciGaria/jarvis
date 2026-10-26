"""会話ログの整理（Claude Code デスクトップアプリのスケジュールタスクから1時間に1回使う）。

  .venv/Scripts/python.exe organize.pyw prepare
      未整理のログと整理の指示を表示する（なければ「整理する会話はありません」）
  .venv/Scripts/python.exe organize.pyw apply data/logs/result.json
      Claude が作った結果の JSON を長期メモと会話の要約に反映する
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from jarvis.memorizer import LOG_DIR, ORGANIZER_LOG, apply_batch, prepare_batch  # noqa: E402  .env を読んでから取り込む


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with ORGANIZER_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M}] {message}\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    command = sys.argv[1] if len(sys.argv) > 1 else ""

    if command == "prepare":
        prompt = prepare_batch()
        print(prompt or "整理する会話はありません")
    elif command == "apply" and len(sys.argv) > 2:
        result_file = Path(sys.argv[2])
        try:
            message = apply_batch(json.loads(result_file.read_text(encoding="utf-8")))
        except Exception as e:  # ログは消さずに残るので、直して再実行できる
            log(f"失敗しました: {e}")
            print(f"失敗しました: {e}")
            sys.exit(1)
        result_file.unlink()
        log(message)
        print(message)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
