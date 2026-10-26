"""Jarvis の頭脳部分：Claude API との会話とツール実行（ターミナル版・GUI版で共通）。"""

import os
from collections.abc import Iterator

import anthropic
from dotenv import load_dotenv

from jarvis.tools import load_conversations, load_memories, run_tool, tools_for

load_dotenv()

from jarvis.memorizer import log_exchange, new_session  # noqa: E402  .env の設定を読んでから取り込む

MODEL = os.getenv("JARVIS_MODEL", "claude-sonnet-5-5")
EFFORT = os.getenv("JARVIS_EFFORT", "low")  # low | medium | high | xhigh | max
USER_NAME = os.getenv("JARVIS_USER_NAME", "")
RECALL_CONVERSATIONS = int(os.getenv("JARVIS_RECALL_CONVERSATIONS", "5"))  # 思い出す過去の会話の数
TOOLS = tools_for(MODEL)

# 安全分類器に断られた場合、Anthropic 推奨のモデルでサーバー側が自動リトライする。
# Haiku にはサーバー側フォールバックがないため付けない。
FALLBACK_PARAMS = (
    {}
    if "haiku" in MODEL
    else {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
)

SYSTEM_PROMPT = """あなたは「Jarvis（ジャーヴィス）」、ユーザー専属の有能で落ち着いたAIアシスタントです。
- 基本は日本語で、丁寧かつ簡潔に話します。少しだけウィットを効かせても構いません。
- 最新情報や事実確認が必要なときは web_search を使います。
- 日付や時刻が関わる質問では get_current_time で現在時刻を確認します。
- 会話の内容は自動的に記録され、定期的に長期メモと会話の要約に整理されます。ユーザーにはっきり「覚えておいて」と頼まれたときだけ save_memory で即座に保存します。
- 長期メモや過去の会話の要約にある内容は、自然に踏まえて話します。
- 分からないことは推測で断定せず、分からないと伝えます。
- 返答は画面に表示され、音声でも読み上げられます。Markdownの装飾や長い箇条書きは避け、話し言葉として自然な短めの文章にします。URLは必要なときだけ示します。"""


def build_system() -> str:
    """システムプロンプトに長期メモを添える（会話開始・リセット時に固定し、キャッシュを保つ）。"""
    parts = [SYSTEM_PROMPT]
    if USER_NAME:
        parts.append(f"ユーザーの名前: {USER_NAME}")
    memories = load_memories()
    if memories:
        parts.append("ユーザーについての長期メモ:\n" + "\n".join(f"- {m}" for m in memories))
    conversations = load_conversations()[-RECALL_CONVERSATIONS:] if RECALL_CONVERSATIONS > 0 else []
    if conversations:
        parts.append(
            "最近の会話の要約（古い順）:\n"
            + "\n".join(f"- {c.get('started', '')}: {c.get('summary', '')}" for c in conversations)
        )
    return "\n\n".join(parts)


def describe_error(e: Exception) -> str:
    """API エラーをユーザー向けの短い文に変換する。"""
    if isinstance(e, anthropic.AuthenticationError):
        return "APIキーが無効です。.env の ANTHROPIC_API_KEY を確認してください"
    if isinstance(e, anthropic.PermissionDeniedError):
        return "このAPIキーには権限がありません"
    if isinstance(e, anthropic.RateLimitError):
        return "レート制限中です。少し待ってから再度お試しください"
    if isinstance(e, anthropic.APIStatusError):
        if "credit" in str(e.message).lower():
            return "APIクレジットが不足しています。Console の Billing でチャージしてください"
        return f"APIエラー {e.status_code}: {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return "ネットワークに接続できません"
    if isinstance(e, anthropic.AnthropicError) and "api_key" in str(e).lower():
        return "APIキーが見つかりません。.env に ANTHROPIC_API_KEY を設定してください"
    return f"エラー: {e}"


class Assistant:
    """1つの会話を保持し、発話ごとに応答をイベントとして順に返す。"""

    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.reset()

    def reset(self) -> None:
        self.messages: list = []
        self.session_id, self.started = new_session()
        self.system = build_system()  # 新しく保存・整理されたメモや会話の要約も反映

    def chat(self, user_input: str) -> Iterator[dict]:
        """応答を {"type": "text"|"tool"|"notice", ...} のイベントとして yield する。
        失敗・中断時は会話履歴を直前の完了状態に戻してから例外を送出する。"""
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})
        try:
            yield from self._respond()
        except BaseException:
            self._drop_unfinished_turn()
            raise
        if len(self.messages) > turn_start:  # 途中で取り消されなかったターンだけログに残す
            log_exchange(self.session_id, self.started, user_input, self._turn_text(turn_start))

    def _turn_text(self, start: int) -> str:
        """ターン内の Jarvis の返答テキストをつなげる。"""
        return "".join(
            b.text
            for m in self.messages[start:]
            if m["role"] == "assistant"
            for b in m["content"]
            if getattr(b, "type", None) == "text"
        )

    def _respond(self) -> Iterator[dict]:
        json_retries = 0
        while True:
            try:
                with self.client.beta.messages.stream(
                    model=MODEL,
                    max_tokens=64000,
                    system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                    messages=self.messages,
                    tools=TOOLS,
                    output_config={"effort": EFFORT},
                    **FALLBACK_PARAMS,
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield {"type": "text", "text": event.text}
                    response = stream.get_final_message()
                json_retries = 0
            except ValueError:
                # ツール入力の JSON が全く解析できなかった。同じターンを再送する（回数制限あり）。
                json_retries += 1
                if json_retries > 2:
                    yield {"type": "notice", "text": "ツール入力の解析に失敗しました"}
                    self._drop_unfinished_turn()
                    return
                continue

            # 思考ブロックなども含めて丸ごと履歴に残す（次のリクエストで必要）
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue  # 長いWeb検索の途中で一時停止 → そのまま続きを依頼
            if response.stop_reason == "refusal":
                yield {"type": "notice", "text": "申し訳ありません、このリクエストにはお応えできません"}
                return

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    yield {"type": "notice", "text": "応答が長すぎたため途中で切れました"}
                return
            if response.stop_reason == "max_tokens":
                # 途中で切れたツール入力は実行しない
                yield {"type": "notice", "text": "ツール入力が途中で切れたため中断しました"}
                self._drop_unfinished_turn()
                return

            results = []
            for block in tool_uses:
                yield {"type": "tool", "name": block.name}
                output, is_error = run_tool(block.name, block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error}
                )
            self.messages.append({"role": "user", "content": results})

    def _drop_unfinished_turn(self) -> None:
        """失敗したターンを履歴から取り除き、最後に完了した応答の状態に戻す。"""
        while self.messages:
            last = self.messages[-1]
            if last["role"] == "assistant" and not any(
                getattr(b, "type", None) == "tool_use" for b in last["content"]
            ):
                break
            self.messages.pop()
