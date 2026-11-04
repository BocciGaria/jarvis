"""Jarvis の頭脳部分：Claude API との会話とツール実行（ターミナル版・GUI版で共通）。"""

import os
from collections.abc import Iterator

import anthropic
from dotenv import load_dotenv

from jarvis.memory import memory_context
from jarvis.tools import run_display, run_tool, tools_for

load_dotenv()

from jarvis.memorizer import log_exchange, new_session  # noqa: E402  .env の設定を読んでから取り込む

MODEL = os.getenv("JARVIS_MODEL", "claude-haiku-5-5")  # 普段の会話
SMART_MODEL = os.getenv("JARVIS_SMART_MODEL", "claude-sonnet-5-5")  # 込み入った依頼。空にすると切り替えない
ROUTING = bool(SMART_MODEL) and SMART_MODEL != MODEL
MODEL_LABEL = f"{MODEL} → {SMART_MODEL}" if ROUTING else MODEL
EFFORT = os.getenv("JARVIS_EFFORT", "low")  # low | medium | high | xhigh | max
USER_NAME = os.getenv("JARVIS_USER_NAME", "")
USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _request_params(model: str, handoff: bool) -> dict:
    params = {"model": model, "tools": tools_for(model, handoff)}
    # 安全分類器に断られた場合、Anthropic 推奨のモデルでサーバー側が自動リトライする。
    # Haiku にはサーバー側フォールバックがないため付けない。
    if "haiku" not in model:
        params |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    return params


# 普段は MODEL が答え、込み入った依頼だと判断したら use_smart_model を呼んでそのターンを SMART_MODEL に任せる
BASIC_PARAMS = _request_params(MODEL, handoff=ROUTING)
SMART_PARAMS = _request_params(SMART_MODEL, handoff=False) if ROUTING else BASIC_PARAMS

SYSTEM_PROMPT = """あなたは「Jarvis（ジャーヴィス）」、ユーザー専属の有能で落ち着いたAIアシスタントです。
- 基本は日本語で、丁寧かつ簡潔に話します。少しだけウィットを効かせても構いません。
- 最新情報や事実確認が必要なときは web_search を使います。
- 日付や時刻が関わる質問では get_current_time で現在時刻を確認します。
- 会話はすべて自動で記録され、大事なことは裏で分析・分類されて長期記憶の文書（タスク・ユーザーの人格・朝会と夕会のアジェンダなど）に整理されます。なので、あなたが記憶について気を回す必要はありません。「記録しますか」「メモに残しましょうか」「覚えておきましょうか」などと尋ねたり提案したりせず、記録や保存の仕組みにも触れずに、信頼できる相棒として自然に会話を続けます。
- save_memory は、ユーザーにはっきり「覚えておいて」と頼まれたときだけ使います。そのときも「承知しました」程度に軽く受け止めます。「忘れて」と頼まれたら forget_memory を使います。
- 長期記憶の文書にある内容は、ユーザーをよく知る相棒として自然に踏まえて話します。目次にある資料の中身が必要なときは read_memory で開きます。
- 以前の会話そのものを確かめてほしいと頼まれたときや、記憶にない細部が必要なときだけ、search_archive で過去の会話ログを探します。
- 朝会と夕会は、ユーザーが「朝会を終わろう」「夕会を終わろう」などとはっきり締めを指示するまで続けます。やりとりが短く続いても、確認する項目が一通り済んでも、時間の目安を過ぎても、あなたの判断では締めません。指示があるまでは「ここまでにします」「お疲れさまでした」「また明日」のような締めの言葉を言いません。話すことが尽きたように見えたら「ほかにありますか」と一言聞いて待ちます。
- 分からないことは推測で断定せず、分からないと伝えます。
- 返答は音声で読み上げられます。Markdownの装飾や長い箇条書きは避け、話し言葉として自然な短めの文章にします。
- 一覧・手順・表・数値・URL・コード・画像など、見せたほうが分かりやすい情報は show_display で画面に表示し、声では「画面に出しました」と要点だけを伝えます。
- 写真や画像を見せてと頼まれたら web_search で探し、画像ファイルか、その画像が載っているページの URL を show_display の image_url に渡して画像そのものを表示します。URL だけを見せて済ませません。"""


def build_system() -> str:
    """システムプロンプトに長期記憶を添える（会話開始・リセット時に固定し、キャッシュを保つ）。"""
    parts = [SYSTEM_PROMPT]
    if USER_NAME:
        parts.append(f"ユーザーの名前: {USER_NAME}")
    memory = memory_context()
    if memory:
        parts.append("# 長期記憶\n" + memory)
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
        self.record = True  # False の間はやりとりをログに残さない（自動で記憶されない）
        self.reset()

    def reset(self) -> None:
        self.messages: list = []
        self._made_with: dict[int, int] = {}  # 応答メッセージ → それを生成したリクエスト設定（id で対応づけ）
        self.session_id, self.started = new_session()
        self.system = build_system()  # 新しく保存・整理されたメモや会話の要約も反映

    def chat(self, user_input: str) -> Iterator[dict]:
        """応答を {"type": "text"|"tool"|"display"|"notice"|"usage", ...} のイベントとして yield する。
        失敗・中断時は会話履歴を直前の完了状態に戻してから例外を送出する。"""
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})
        try:
            yield from self._respond(turn_start)
        except BaseException:
            self._drop_unfinished_turn()
            raise
        if self.record and len(self.messages) > turn_start:  # 途中で取り消されなかったターンだけログに残す
            log_exchange(self.session_id, self.started, user_input, self._turn_text(turn_start))

    def _turn_text(self, start: int) -> str:
        """ターン内の Jarvis の返答テキストと、画面に表示したパネルの内容をつなげる。"""
        parts = []
        for m in self.messages[start:]:
            if m["role"] != "assistant":
                continue
            for b in m["content"]:
                kind = getattr(b, "type", None)
                if kind == "text":
                    parts.append(b.text)
                elif kind == "tool_use" and b.name == "show_display" and isinstance(b.input, dict):
                    # 声では「画面に出しました」としか言わないため、パネルの中身も整理の材料に残す
                    panel = f"\n［画面に表示: {b.input.get('title', '')}］\n{b.input.get('body') or ''}"
                    if b.input.get("image_url"):
                        panel += f"\n（画像: {b.input['image_url']}）"
                    parts.append(panel.rstrip() + "\n")
        return "".join(parts).strip()

    def _respond(self, turn_start: int) -> Iterator[dict]:
        json_retries = 0
        params = BASIC_PARAMS
        usage = dict.fromkeys(USAGE_FIELDS, 0)  # このターンで消費したトークン（API 呼び出しごとに加算）
        while True:
            try:
                with self.client.beta.messages.stream(
                    max_tokens=64000,
                    system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                    messages=self._messages_for(params),
                    output_config={"effort": EFFORT},
                    **params,
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield {"type": "text", "text": event.text}
                    response = stream.get_final_message()
                json_retries = 0
                for field in USAGE_FIELDS:
                    usage[field] += getattr(response.usage, field, None) or 0
                yield {"type": "usage", **usage, "total": sum(usage.values())}
            except ValueError:
                # ツール入力の JSON が全く解析できなかった。同じターンを再送する（回数制限あり）。
                json_retries += 1
                if json_retries > 2:
                    yield {"type": "notice", "text": "ツール入力の解析に失敗しました"}
                    self._drop_unfinished_turn()
                    return
                continue

            # 思考ブロックなども含めて丸ごと履歴に残す（次のリクエストで必要）
            message = {"role": "assistant", "content": response.content}
            self._made_with[id(message)] = id(params)
            self.messages.append(message)

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
            if any(b.name == "use_smart_model" for b in tool_uses):
                # このターンの途中経過を捨て、ユーザーの発言から上位モデルでやり直す
                del self.messages[turn_start + 1 :]
                params = SMART_PARAMS
                yield {"type": "tool", "name": "use_smart_model"}
                continue

            results = []
            for block in tool_uses:
                yield {"type": "tool", "name": block.name}
                if block.name == "show_display":
                    output, is_error, display = run_display(block.input)
                    if display:
                        yield display
                else:
                    output, is_error = run_tool(block.name, block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error}
                )
            self.messages.append({"role": "user", "content": results})

    def _messages_for(self, params: dict) -> list:
        """別のモデル・ツール一覧で生成された応答から思考ブロックを外す。
        思考ブロックの署名は生成時のツール一覧に結びついており、異なる設定で送り返すと 400 になる。"""
        messages = []
        for m in self.messages:
            if m["role"] == "assistant" and self._made_with.get(id(m), id(params)) != id(params):
                content = [
                    b for b in m["content"] if getattr(b, "type", None) not in ("thinking", "redacted_thinking")
                ]
                m = {"role": "assistant", "content": content or [{"type": "text", "text": "…"}]}
            messages.append(m)
        return messages

    def _drop_unfinished_turn(self) -> None:
        """失敗したターンを履歴から取り除き、最後に完了した応答の状態に戻す。"""
        while self.messages:
            last = self.messages[-1]
            if last["role"] == "assistant" and not any(
                getattr(b, "type", None) == "tool_use" for b in last["content"]
            ):
                break
            self.messages.pop()
