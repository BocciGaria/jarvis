# Jarvis

Claude API（既定は Claude Sonnet 5.5）を使った個人 AI アシスタントです。音声で話しかけられる GUI 版と、ターミナル版があります。

## できること

- 日本語での会話（会話の流れを覚えています）
- 「Hey Jarvis」と呼びかけるとマイクが起動（GUI 版）
- 音声入力と音声での返答（GUI 版）
- Web 検索で最新情報を調べる
- 現在の日付・時刻を確認する
- 長期メモ：「〜を覚えておいて」と言うと `data/memory.json` に保存され、次回以降も使われます
- 自動記憶：頼まなくても会話を `data/logs/pending.jsonl` に記録し、1時間に1回 Claude Code デスクトップアプリのスケジュールタスク（Claude Pro などのサブスクリプション）で整理して、好み・予定・決めたことなどを長期メモに追加・更新します。会話ごとの要約も `data/conversations.json` に残り、次回起動時（またはリセット後）は直近の会話を覚えた状態で始まります。整理に API 料金はかかりません（下の「自動記憶の設定」を参照）

## 初回セットアップ

1. https://console.anthropic.com/ で API キーを作成する
2. `.env.example` をコピーして `.env` を作り、`ANTHROPIC_API_KEY=` にキーを貼り付ける

（依存パッケージは `.venv` にインストール済みです。入れ直すときは `.venv\Scripts\python.exe -m pip install -r requirements.txt`）

## 自動記憶の設定

Claude Code デスクトップアプリの「スケジュールタスク」（ローカル）で、1時間に1回整理させます。

1. Claude デスクトップアプリの **Code** タブ → サイドバーの **Scheduled**（または **Routines**）→ **New task**
2. **Name**：`jarvis-memory`、指示欄に下の「タスクの指示」を貼り付け
3. **Advanced settings** の **Only on this computer** をオン、**Local folder** にこの Jarvis フォルダを選ぶ
4. **Trigger** で **Hourly** を選び、**Create**
5. 作成後に **Run now** を押し、許可を求められたコマンドやファイル書き込みは「常に許可（always allow）」にする（次回から止まらずに動きます）

タスクの指示：

```
Jarvis の会話ログを整理して、長期メモと会話の要約を更新してください。
1. `.venv/Scripts/python.exe organize.pyw prepare` を実行する。「整理する会話はありません」と出たら、何もせずに終了する。
2. 表示された指示とルールに従って結果の JSON を作り、`data/logs/result.json` に書き込む。
3. `.venv/Scripts/python.exe organize.pyw apply data/logs/result.json` を実行し、表示された結果を一言で報告する。
   形式エラーが出たら JSON を直して、もう一度 apply する。
data/memory.json や data/conversations.json は直接編集しないこと。
```

- アプリが起動していて PC が起きている間だけ動きます。スリープ中に飛ばされた分は、次の実行でまとめて整理されます
- 整理の分は Claude Pro の利用枠を使います（`.env` の API キーは使いません）
- 結果は `data/logs/organize.log` に記録されます。失敗したログは消さずに、次回もう一度整理します
- 整理済みのログは `data/logs/2026-10.jsonl` のように月ごとに残ります
- 今すぐ整理したいときは、タスクの詳細画面で **Run now**

## 起動

| 起動ファイル | 内容 |
|---|---|
| `jarvis.bat` | GUI 版。Edge（なければ Chrome）の専用ウィンドウが開きます。黒いウィンドウを閉じると終了 |
| `jarvis-cli.bat` | ターミナル版 |

### PC 起動時の自動起動

スタートアップフォルダ（`Win+R` → `shell:startup`）に `Jarvis.lnk` と `VOICEVOX.lnk` を置いてあり、Windows にサインインすると両方が最小化で起動します。やめるときはそのショートカットを削除するか、タスクマネージャーの「スタートアップ アプリ」で無効にしてください。

## GUI 版の使い方

- **「Hey Jarvis（ヘイ・ジャーヴィス）」**と呼びかける → チャイムが鳴ってウィンドウが前面に出るので、続けて用件を話します
- マイクボタン（または **Ctrl+Space**）を押して話しても OK → 話し終わると自動で送信されます
- 文字で入力して Enter でも送れます
- 初回はマイクの使用許可を求められるので「許可」を選んでください
- **🔊 読み上げ**：返答を音声で読み上げるか（右の一覧で声を選べます。既定は「VOICEVOX:ナースロボ_タイプT」。ほかに Windows/ブラウザの声も選べ、「Keita」「Nanami」の Natural 音声がおすすめ）
- ナースロボ_タイプTの声で話すには [VOICEVOX](https://voicevox.hiroshiba.jp/) をインストールして起動しておいてください（Jarvis より先でも後でも OK）。起動していないときは自動でブラウザの声に切り替わります
- **🔁 会話モード**：返答を読み上げ終わると自動でマイクが再開し、続けて話せます
- 読み上げ中にマイクボタンか **Esc** で読み上げを止められます
- **👂 Hey Jarvis**：呼びかけ検知の ON/OFF。OFF にするとマイクを解放します
- 呼びかけの検知は [openWakeWord](https://github.com/dscripka/openWakeWord) を使い PC 内だけで行うため、待機中の音声は外部に送られません（検知モデルは初回起動時に `models/` へ自動ダウンロード）
- 呼びかけに反応しにくい／誤反応が多いときは `.env` の `JARVIS_WAKEWORD_THRESHOLD` を調整してください
- Bluetooth ヘッドセットをマイクに使っていると、検知中はヘッドセットが通話モードになり、音楽などの音質が下がります。気になるときは 👂 を OFF にするか、PC 内蔵マイクを Windows の既定の録音デバイスにしてください
- 用件の音声認識はブラウザの機能を使うため、インターネット接続が必要で、音声は Microsoft（Chrome の場合は Google）のサービスで文字に変換されます

## ターミナル版のコマンド

| コマンド | 内容 |
|---|---|
| `/reset` | 会話履歴をリセット（新しいメモも反映） |
| `/memory` | 保存されたメモを表示 |
| `/history` | 過去の会話の要約を表示 |
| `/help` | ヘルプ |
| `/exit` | 終了 |

## 設定（`.env`）

| 変数 | 既定値 | 説明 |
|---|---|---|
| `JARVIS_MODEL` | `claude-sonnet-5-5` | 使うモデル（下の表を参照） |
| `JARVIS_EFFORT` | `low` | 考える深さ（`low` / `medium` / `high` / `xhigh` / `max`）。低いほど速く安い |
| `JARVIS_USER_NAME` | なし | あなたの呼び名 |
| `JARVIS_AUTO_MEMORY` | `on` | 会話をログに記録して自動で記憶するか（`off` で無効） || `JARVIS_RECALL_CONVERSATIONS` | `5` | 会話開始時に思い出す過去の会話の数（`0` で無効） |
| `JARVIS_PORT` | `8765` | GUI 版が使うポート番号 |
| `JARVIS_WAKEWORD` | `on` | 「Hey Jarvis」検知を起動時に有効にするか（`off` で無効） |
| `JARVIS_WAKEWORD_THRESHOLD` | `0.5` | 検知の厳しさ（0〜1）。上げると誤反応が減り、下げると反応しやすくなる |
| `JARVIS_VOICEVOX_SPEAKER` | `47` | VOICEVOX の声の番号（47 = ナースロボ＿タイプＴ ノーマル、48 = 楽々、49 = 恐怖、50 = 内緒話、2 = 四国めたん ノーマル） |
| `JARVIS_VOICEVOX_SPEED` | `1.2` | VOICEVOX の話す速さ |
| `JARVIS_VOICEVOX_URL` | `http://127.0.0.1:50021` | VOICEVOX エンジンの場所 |

| モデル | 料金（入力 / 出力、100万トークンあたり） | 向いている使い方 |
|---|---|---|
| `claude-sonnet-5-5`（既定） | $2 / $10 | 日常の会話・調べ物。バランス重視 |
| `claude-opus-5-5` | $4 / $20 | 込み入った相談や調べ物 |
| `claude-haiku-5-5` | $0.10 / $0.50 | とにかく安く。Web 検索は基本版になり、断られた際の自動リトライはなし |

## ファイル構成

- `jarvis/assistant.py` — Claude API との会話とツール実行（共通部分）
- `jarvis/tools.py` — ツール（時刻・メモ）と Web 検索の定義
- `jarvis/memorizer.py` — 会話ログの記録と、整理結果の反映（自動記憶・会話の要約）
- `organize.pyw` — スケジュールタスクが使う整理用コマンド（`prepare` / `apply`）
- `jarvis/gui.py` — GUI 版のローカルサーバー
- `jarvis/wakeword.py` — 「Hey Jarvis」の呼びかけ検知
- `jarvis/web/index.html` — GUI 画面（音声入力・読み上げ・アニメーション）
- `jarvis/main.py` — ターミナル版
