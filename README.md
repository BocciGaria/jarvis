# Jarvis

Claude API（普段の会話は Claude Haiku 5.5、込み入った依頼は Claude Sonnet 5.5）を使った個人 AI アシスタントです。音声で話しかけられる GUI 版と、ターミナル版があります。

## できること

- 日本語での会話（会話の流れを覚えています）
- 「Hey Jarvis」と呼びかけるとマイクが起動（GUI 版）
- 音声入力と音声での返答（GUI 版）
- Web 検索で最新情報を調べる
- 現在の日付・時刻を確認する
- iCloud カレンダーの予定を確認・追加・変更・削除する（下の「iCloud カレンダーの設定」を参照）
- Google ToDo リストのタスクを確認・追加・変更・完了にし、Jarvis が管理するタスクと同期する（下の「Google ToDo リストの設定」を参照）
- 自動記憶：会話はすべて `data/logs/pending.jsonl` に記録され、1時間に1回 Claude Code デスクトップアプリのスケジュールタスク（Claude Pro などのサブスクリプション）で分析・分類して、長期記憶の Markdown 文書に整理します。整理に API 料金はかかりません（下の「自動記憶の設定」を参照）
  - 文書は話題ごとに分かれます（タスク・ユーザーの人格・朝会/夕会のアジェンダ・資産管理・健康管理・目標管理など）。構成は固定ではなく、会話の内容に合わせて整理のたびに新設・統合・改名されます
  - `data/memory/*.md`：会話を始めるたびに全文を読み込む文書（合計の目安は `JARVIS_MEMORY_BUDGET` 字）
  - `data/memory/reference/*.md`：普段は読み込まず、必要なときだけ Jarvis が開く資料（完了したタスクの記録など）
  - 整理が終わった会話ログは `data/archive/logs/` に移り、「前に話した〜を調べて」と頼んだときだけ検索されます
  - 整理で書き換える前の記憶は `data/archive/memory-backups/` に残ります（直近30回分）
- 「〜を覚えておいて」「〜を忘れて」と頼むと、すぐに反映され、次の整理で文書にも反映されます

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
Jarvis の会話ログを分析・分類して、長期記憶の文書を更新してください。
1. `.venv/Scripts/python.exe organize.pyw prepare` を実行する（Google ToDo リストからの取り込みもここで行われる）。「整理する会話はありません」と出たら、何もせずに終了する。
2. 表示された指示とルールに従って、Google ToDo リストと同期する tasks も含めた結果の JSON を作り、Write ツールで `data/logs/result.json` に UTF-8 で書き込む（シェルのリダイレクトは文字化けするので使わない）。
3. `.venv/Scripts/python.exe organize.pyw apply data/logs/result.json` を実行し（Google ToDo リストへの反映もここで行われる）、表示された整理と同期の結果を一言で報告する。
   形式エラーが出たら JSON を直して、もう一度 apply する。「失敗しました」と出て直せないときは、そのまま理由を報告して終了する（ログは残り、次回もう一度整理される）。
data/memory・data/archive の中身と data/logs の .jsonl ファイルは直接編集しないこと（記憶の書き換えは apply で行う）。
```

- アプリが起動していて PC が起きている間だけ動きます。スリープ中に飛ばされた分は、次の実行でまとめて整理されます
- 整理の分は Claude Pro の利用枠を使います（`.env` の API キーは使いません）
- 結果は `data/logs/organize.log` に記録されます。失敗したログは消さずに、次回もう一度整理します
- 整理済みのログは `data/archive/logs/2026-10.jsonl` のように月ごとに保管されます（Jarvis は頼まれない限り読みません）
- 今すぐ整理したいときは、タスクの詳細画面で **Run now**

## iCloud カレンダーの設定

1. https://account.apple.com の「サインインとセキュリティ」→「アプリ用パスワード」で App 用パスワードを発行する（Apple ID の 2 ファクタ認証が必要です）
2. `jarvis-calendar-setup.bat` を実行して Apple ID と App 用パスワードを入力する。接続を確かめてから Windows の資格情報マネージャーに保存します（`.env` や OneDrive には置きません）
3. Jarvis を起動し直す

- 「来週の予定は？」「金曜15時に歯医者を入れて」「歯医者を16時にずらして」のように頼めます
- 追加はすぐ実行します。変更と削除は内容を伝えて確認し、次の発言で了承してから実行します
- 繰り返し予定と参加者のいる予定は読み取りのみです（全回分の変更や、相手への通知を防ぐため）。カレンダーアプリで操作してください
- 試すときは `.env` の `JARVIS_ICLOUD_CALENDAR` にテスト用カレンダーの名前を入れると、そのカレンダーだけを読み書きします
- やめるときは Windows の「資格情報マネージャー」で `Jarvis-iCloud` を削除し、Apple ID の管理画面で App 用パスワードを取り消してください

## Google ToDo リストの設定

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作り、「API とサービス」→「ライブラリ」で **Google Tasks API** を有効にする
2. 「Google Auth Platform」でアプリを作り（対象は「外部」）、「ブランディング」に次の内容を入力して保存する。ロゴと利用規約は空欄のままでかまいません

   | 項目 | 入力する内容 |
   |---|---|
   | アプリ名 | `Jarvis` |
   | ユーザーサポートメール、デベロッパーの連絡先情報 | 自分のメールアドレス |
   | ホームページ | `https://boccigaria.github.io/jarvis/` |
   | プライバシーポリシー | `https://boccigaria.github.io/jarvis/#privacy` |
   | 承認済みドメイン | `boccigaria.github.io` |

3. 「対象」の画面で **アプリを公開** して本番環境にする。テスト状態のままだと連携が7日で切れます。審査は申請せずに使っているため、許可の画面に「確認されていないアプリ」と出ます
4. 「クライアント」で **デスクトップアプリ** のクライアントを作り、JSON をダウンロードする
5. `jarvis-todo-setup.bat` を実行して JSON のパスを入力し、ブラウザで許可する。資格情報は Windows の資格情報マネージャーに保存します（`.env` や OneDrive には置きません）。終わったら JSON は削除してかまいません
6. Jarvis を起動し直す

手順 2 のページは、このリポジトリの `gh-pages` ブランチの `index.html` を GitHub Pages で公開したものです。Jarvis の説明とプライバシーポリシーを変えるときは、このファイルを更新します。

- 「タスクを見せて」「牛乳を買うを追加して」「年末調整を完了にして」のように頼めます。完了はすぐ実行します（Google ToDo リストのアプリで戻せます）
- 使うのは専用リスト「Jarvis」だけです。ほかのリストは読みも書きもしません
- 1時間ごとの記憶の整理に合わせて同期します
  - 整理の前に、アプリで行った完了・追加・変更・削除を Jarvis のタスクに取り込みます
  - 整理の後に、Jarvis のタスクの変更をリストへ反映します。Jarvis のタスクから外れたものは削除せず完了にします
  - 整理する会話がない回も、アプリでの操作の取り込みと、反映できなかった分の再送は行います
- 同期の状態は `data/todo.json` に、結果は `data/logs/organize.log` に残ります
- 期限は日付だけです（Tasks API が時刻を保存しないため）。時刻はタイトルに入り、時刻どおりの通知が必要なものはカレンダーに入れてください
- 連携を始めた直後の最初の整理で、記憶の文書から同期するタスク一覧を作ります（会話がなくても実行されます）
- やめるときは Windows の「資格情報マネージャー」で `Jarvis-Google` を削除し、Google アカウントの「サードパーティ製のアプリとサービス」でアクセスを取り消してください

## 起動

| 起動ファイル | 内容 |
|---|---|
| `jarvis.bat` | GUI 版。Edge（なければ Chrome）の専用ウィンドウが開きます。サーバーはコンソールなしで裏で動き、Jarvis の画面を閉じると数秒後に自動で終了します（出力は `data/logs/gui.log`）。起動中にもう一度起動すると画面だけ開きます |
| `jarvis-cli.bat` | ターミナル版 |

### スタートメニューから起動

スタートメニュー（`%APPDATA%\Microsoft\Windows\Start Menu\Programs`）に `Jarvis.lnk` を置いてあり、スタートメニューで「Jarvis」と検索して起動できます（右クリック →「スタートにピン留め」も可）。アイコンは `jarvis/web/jarvis.ico` です。

### PC 起動時の自動起動

スタートアップフォルダ（`Win+R` → `shell:startup`）に `Jarvis.lnk` と `VOICEVOX.lnk` を置いてあり、Windows にサインインすると両方が起動します（VOICEVOX は最小化）。やめるときはそのショートカットを削除するか、タスクマネージャーの「スタートアップ アプリ」で無効にしてください。

## GUI 版の使い方

- **「Hey Jarvis（ヘイ・ジャーヴィス）」**と呼びかける → チャイムが鳴ってウィンドウが前面に出るので、続けて用件を話します
- マイクボタン（または **Ctrl+Space**）を押して話しても OK → 話し終わると自動で送信されます
- 文字で入力して Enter でも送れます（Shift+Enter で改行）
- 画面は中央のリアクターだけを表示します。一覧・手順・URL・画像など見せたほうが分かりやすい内容があると、Jarvis が判断してリアクターからパネルを出して表示します（✕ か **Esc** で閉じるとリアクターへ戻ります）
  - パネルはリアクターの中心を避けた空き場所に、新しいものほど中央寄りに並びます。追加・削除のたびに全体の配置を自動で整えます
  - 画面に収まらない分は古いものから重ねて奥に置きます。重なったパネルをクリックすると手前に出せます
- **💬 ログ**：会話の内容を右側に表示します（既定は非表示）
- 初回はマイクの使用許可を求められるので「許可」を選んでください
- **🔊 読み上げ**：返答を音声で読み上げるか（右の一覧で声を選べます。既定は「VOICEVOX:ナースロボ_タイプT」。ほかに Windows/ブラウザの声も選べ、「Keita」「Nanami」の Natural 音声がおすすめ）
- ナースロボ_タイプTの声で話すには [VOICEVOX](https://voicevox.hiroshiba.jp/) をインストールして起動しておいてください（Jarvis より先でも後でも OK）。起動していないときは自動でブラウザの声に切り替わります
- **🔁 会話モード**：返答を読み上げ終わると自動でマイクが再開し、続けて話せます
- **📝 記録**：オフにすると、その間の会話はログに残らず自動で記憶もされません
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
| `/memory` | 長期記憶の文書を表示 |
| `/record` | 会話の記録のオン・オフを切り替え |
| `/help` | ヘルプ |
| `/exit` | 終了 |

## 設定（`.env`）

| 変数 | 既定値 | 説明 |
|---|---|---|
| `JARVIS_MODEL` | `claude-haiku-5-5` | 普段の会話に使うモデル（下の表を参照） |
| `JARVIS_SMART_MODEL` | `claude-sonnet-5-5` | 込み入った依頼のときに切り替えるモデル。空にすると切り替えず、常に `JARVIS_MODEL` を使う |
| `JARVIS_EFFORT` | `low` | 考える深さ（`low` / `medium` / `high` / `xhigh` / `max`）。低いほど速く安い |
| `JARVIS_USER_NAME` | なし | あなたの呼び名 |
| `JARVIS_AUTO_MEMORY` | `on` | 会話をログに記録して自動で記憶するか（`off` で無効） |
| `JARVIS_MEMORY_BUDGET` | `8000` | 毎回読み込む記憶の文書（`data/memory/*.md`）の合計文字数の目安。整理役はこの範囲に収め、はみ出す細部は資料に移します |
| `JARVIS_ICLOUD_CALENDAR` | なし | 指定すると、この名前の iCloud カレンダーだけを読み書きする。なしならすべてを読み、追加は最初のカレンダーか指定されたカレンダーへ |
| `JARVIS_PORT` | `8765` | GUI 版が使うポート番号 |
| `JARVIS_WAKEWORD` | `on` | 「Hey Jarvis」検知を起動時に有効にするか（`off` で無効） |
| `JARVIS_WAKEWORD_THRESHOLD` | `0.5` | 検知の厳しさ（0〜1）。上げると誤反応が減り、下げると反応しやすくなる |
| `JARVIS_VOICEVOX_SPEAKER` | `47` | VOICEVOX の声の番号（47 = ナースロボ＿タイプＴ ノーマル、48 = 楽々、49 = 恐怖、50 = 内緒話、2 = 四国めたん ノーマル） |
| `JARVIS_VOICEVOX_SPEED` | `1.2` | VOICEVOX の話す速さ |
| `JARVIS_VOICEVOX_PRE_SILENCE` | `0.1` | 各文の話し始めの無音（秒） |
| `JARVIS_VOICEVOX_POST_SILENCE` | `0.1` | 各文の話し終わりの無音（秒）。文と文の間は「終了無音 + 開始無音」になります |
| `JARVIS_VOICEVOX_PAUSE_SCALE` | `1.0` | 「、」などで入る間の長さの倍率（0.5 で半分、2 で倍） |
| `JARVIS_VOICEVOX_URL` | `http://127.0.0.1:50021` | VOICEVOX エンジンの場所 |

| モデル | 料金（入力 / 出力、100万トークンあたり） | 向いている使い方 |
|---|---|---|
| `claude-sonnet-5-5`（上位の既定） | $2 / $10 | 調べ物・相談など。バランス重視 |
| `claude-opus-5-5` | $4 / $20 | 込み入った相談や調べ物 |
| `claude-haiku-5-5`（普段の既定） | $0.10 / $0.50 | 日常の会話。とにかく安く速い。Web 検索は基本版になり、断られた際の自動リトライはなし |

モデルの切り替え：毎回まず `JARVIS_MODEL` が答え、調べて比べる・計画を立てる・込み入った相談・長い文章・コードや計算・専門的な質問など込み入った依頼だと判断したときだけ、そのターンを `JARVIS_SMART_MODEL` がやり直して答えます（画面には「🧠 上位モデルで考え中」と出ます）。次の発言からはまた `JARVIS_MODEL` に戻ります。

## ファイル構成

- `jarvis/assistant.py` — Claude API との会話とツール実行（共通部分）
- `jarvis/tools.py` — ツール（カレンダー・ToDo・記憶・画面表示）と Web 検索の定義。カレンダーと ToDo の本体はそれぞれ `icloud.py`・`gtasks.py`
- `jarvis/icloud.py` — iCloud カレンダーの読み書きと資格情報の登録（`jarvis-calendar-setup.bat` から実行）
- `jarvis/gtasks.py` — Google ToDo リストの操作と同期、資格情報の登録（`jarvis-todo-setup.bat` から実行）
- `jarvis/memory.py` — 長期記憶の文書・未整理ログ・保管庫の読み書き
- `jarvis/memorizer.py` — 会話ログの記録と、整理（分析・分類）の指示づくりと結果の反映
- `organize.pyw` — スケジュールタスクが使う整理用コマンド（`prepare` / `apply`）
- `jarvis/gui.py` — GUI 版のローカルサーバー
- `jarvis/wakeword.py` — 「Hey Jarvis」の呼びかけ検知
- `jarvis/web/index.html` — GUI 画面（音声入力・読み上げ・アニメーション）
- `jarvis/main.py` — ターミナル版
