# 運用

## 設定とデータ

`.env`とDBはアプリ実行ユーザーだけが読めるようにします。CLIはumask 0077を設定し、DB本体は0600で作成します。SQLiteにはUTCのタイムゾーン付きISO形式を保存し、UIと集計境界はAsia/Tokyoを使います。既定の保存先は`./data/timecard.db`です。BotとCLIは同じ.env・作業ディレクトリを使用してください。

設定変更は.env編集後のBot再起動で適用します。ログはjournaldで確認します。SDKの詳細ログとトークンの出力は避けています。Slackエラー時はOutbox IDや通知IDを手掛かりに確認してください。

- ダッシュボードが更新されない：Botのチャンネル参加、`chat:write`、設定したチャンネルID、Outboxの未処理レコードを確認。
- DMが来ない：`im:write`、利用者ID、通知レコードのstatusとattempt_countを確認。
- App Homeが空：Home Tab、`app_home_opened`購読、ユーザー登録、ワークスペースIDを確認。
- 未確認から戻したい：App Homeの「まだいる」または最新DMのボタンを利用。
- ユーザーを無効化できない：本人が現在のセッションを終了してから再実行。
- 同じDBのBotが起動済み：手動起動とsystemdの両方を起動していないか確認。ロックファイル自体は削除せず、稼働中プロセスを停止。
- 起動時の権限エラー：DBと保存ディレクトリの所有者・書込権限を確認。

## 通知・同期の復旧

通知はPENDING→SENDING→SENTで送信します。送信成功時だけsent_atが入り、30分後にEXPIRED・セッションUNCONFIRMEDへ進みます。無回答の同一サイクルに再通知しません。回答によってANSWERED、別の確認・退室によってCANCELLEDになります。

送信失敗はFAILEDとし、1分から最大1時間の指数バックオフで再試行します。起動時にはSENDINGをFAILEDへ戻します。Slack送信直後に停止した場合はDMが重複する可能性がありますが、同じ通知IDの回答でDBが重複更新されることはありません。回答済み・取消済みのDMはボタンを外して終了表示へ更新します。この更新もOutboxから再試行します。未確認の通知は引き続き回答できるためボタンを残します。

DB変更と同じトランザクションでOutboxを保存します。通常はボタン操作後にすぐ同期し、失敗分は毎分のジョブから1分〜1時間のバックオフで再試行します。Slack APIはDBトランザクションの外で呼び出します。

ダッシュボードの投稿成功後、tsのDB保存前に停止した場合も重複メッセージが生じ得ます。保存済みtsは再起動後も再利用し、チャンネル・ワークスペースが変わった場合は新規作成します。余分なメッセージは管理者がSlackで削除してください。

## 保持期間と削除

既定は90日です。`RETENTION_DAYS`を変更すると期限切れ削除の基準が変わります。自動削除ジョブはなく、管理者が以下を定期的に明示実行します。

```bash
uv run timecard purge-history --actor U0123456789
```

指定期限より前に退室したセッションを削除します。日付をまたいで期限にかかるセッションは、退室日時が期限より前になるまで保持します。関連通知・監査イベントは外部キーで連鎖削除し、期限より古い完了済みOutboxと削除済みセッションの操作受領記録も整理します。未終了セッション・未処理Outboxは保持します。削除件数をログとCLIに出力します。履歴削除によりその期間の集計値も変わります。

任意の期限を指定する場合は、`purge-history --actor U... --before 2026-01-01T00:00:00+00:00`を使います。未来の期限、タイムゾーンなしの期限、MEMBERは拒否します。削除前に必要なバックアップを確認してください。

## バックアップ

稼働中のDB本体だけをファイルコピーするとWALの変更を取りこぼすことがあります。SQLiteのオンラインバックアップAPIを使います。

```bash
mkdir -p backups
chmod 700 backups
uv run python - <<'PY'
import os
import sqlite3
from datetime import datetime, UTC
from pathlib import Path
from timecard.config import Config

os.umask(0o077)
config = Config.load()
target = Path("backups") / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".db")
with sqlite3.connect(config.database_path) as source:
    with sqlite3.connect(target) as destination:
        source.backup(destination)
print(target)
PY
```

バックアップにも個人履歴が含まれます。DBと同じ保持方針を適用し、期限切れ履歴を残す古い世代を削除してください。特に手動で履歴を削除した場合、削除対象を含むバックアップも削除するか、各バックアップにも同じ削除を適用します。Gitや共有フォルダに置きません。バックアップから復元すると削除済み履歴が復活し得るため、復元後・再開前にも保持ポリシーに従って削除してください。

復元はBotを停止し、DB本体をバックアップから戻します。停止後の古い`-wal`・`-shm`を退避し、所有者と0600権限を確認してから再起動してください。初回の復元検証は本番DBとは別の保存先で実施します。
