"""Jarvis: Claude API を使った対話型パーソナルアシスタント（ターミナル版）。"""

import sys

import anthropic

from jarvis.assistant import MODEL, Assistant, describe_error
from jarvis.tools import load_conversations, load_memories

COMMANDS_HELP = """コマンド:
  /reset   会話履歴をリセット
  /memory  保存されたメモを表示
  /history 過去の会話の要約を表示
  /help    このヘルプを表示
  /exit    終了（Ctrl+C でも可）"""


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")

    assistant = Assistant()

    print(f"Jarvis 起動完了（{MODEL}）。ご用件をどうぞ。 /help でコマンド一覧")
    while True:
        try:
            user_input = input("\nあなた > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nJarvis: またいつでもお呼びください。")
            break
        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            print("Jarvis: またいつでもお呼びください。")
            break
        if user_input == "/history":
            conversations = load_conversations()
            print("\n".join(f"[{c['started']}] {c['summary']}" for c in conversations) or "会話の記録はまだありません")
            continue
        if user_input == "/help":
            print(COMMANDS_HELP)
            continue
        if user_input == "/reset":
            assistant.reset()
            print("会話履歴をリセットしました。")
            continue
        if user_input == "/memory":
            memories = load_memories()
            print("\n".join(f"{i}. {m}" for i, m in enumerate(memories, 1)) or "メモはまだありません")
            continue

        print("\nJarvis > ", end="", flush=True)
        try:
            for event in assistant.chat(user_input):
                if event["type"] == "text":
                    print(event["text"], end="", flush=True)
                elif event["type"] == "tool":
                    print(f"\n  ⚙ {event['name']}", flush=True)
                elif event["type"] == "notice":
                    print(f"\n[{event['text']}]", end="")
            print()
        except KeyboardInterrupt:
            print("\n[中断しました]")
        except anthropic.APIError as e:
            print(f"\n[{describe_error(e)}]")


if __name__ == "__main__":
    main()
