# timecard

少人数グループ向けのSlack在室管理Bot。Python 3.12 / uv / SQLite / Socket Modeで動作します。

## セットアップ

UbuntuにPython 3.12と[uv](https://docs.astral.sh/uv/getting-started/installation/)を用意し、このディレクトリで実行します。

```bash
uv sync --frozen
cp .env.example .env
chmod 600 .env
```

[Slack設定手順](docs/SLACK_SETUP.md)に従ってAppを作成し、`.env`のSlackトークン、ワークスペースID、対象チャンネルIDを設定してください。Botを対象のプライベートチャンネルに招待します。

```bash
uv run timecard init-db
uv run timecard add-user U0123456789 --admin
uv run timecard add-user U0987654321
uv run timecard list-users
uv run timecard run
```

初回起動時にダッシュボードを投稿します。メッセージは手動でピン留めしてください。以降は同じメッセージを更新し、削除されていた場合に再作成します。

## 操作

- ダッシュボードまたはApp Homeのボタンで登録します。App Homeでは不在時に「入室する」、在室中・未確認時に「まだいる」「退室する」を表示します。共有ダッシュボードは全員共通のため両方の入退室ボタンを表示し、不正な状態の操作は拒否します。
- App Homeには本人の今日・今週・今月の確定時間、現在の暫定時間、10件単位の履歴を表示します。
- 最終確認から6時間経つとDMが届きます。「まだいる」「退室した」「退室時刻を指定」で回答します。
- 送信成功から30分無回答で「未確認」になります。同じ確認サイクルでは再通知しません。
- 終了済みセッションはApp Homeから退室日時を修正できます。日付・時刻は日本時間です。
- 「未確認」のセッションは退室するまで確定時間に含めません。日付が変わっても自動退室しません。

設定を短縮して通知動作を確認する場合は、`CONFIRMATION_INTERVAL_MINUTES=1`、`CONFIRMATION_TIMEOUT_MINUTES=1`とし、Botを再起動します。判定は毎分のため、最大約1分の遅れがあります。

## 管理

```bash
uv run timecard disable-user U0987654321
uv run timecard purge-history --actor U0123456789
uv run timecard purge-history --actor U0123456789 --before 2026-01-01T00:00:00+00:00
```

未終了セッションがある利用者は無効化できません。本人に退室処理を依頼してください。`purge-history`は登録済みADMINを指定し、既定では90日より前に終了したセッションと関連通知・監査記録を削除します。`RETENTION_DAYS`で保持期間を変更できます。削除は明示コマンド実行時に行い、未終了セッションは残します。

CLIはUbuntuアカウントの所有者を信頼する管理インターフェースです。`--actor`は操作対象の権限確認・ログ用の指定であり、独立した本人認証ではありません。Ubuntuアカウントを他の利用者と共有しないでください。

## 開発と検証

```bash
uv sync --frozen
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

テストは一時SQLite DB、注入可能な時計、モックのSlack APIを使います。トークンや実Slackワークスペースは必要ありません。実環境の確認項目は[Slack設定手順](docs/SLACK_SETUP.md)にあります。

アプリケーション層はSlack SDKに依存しません。SQLiteを正とし、`BEGIN IMMEDIATE`と部分UNIQUEインデックスで同時更新を保護します。DB更新・監査記録・重複操作の受領記録・Outbox作成を同じトランザクションで行い、Slack APIはトランザクション外で呼び出します。

[仕様と設計](SPEC.md) / [常時稼働・移行](docs/DEPLOYMENT.md) / [運用・バックアップ](docs/OPERATIONS.md)

実Slackへの接続とUbuntuサービスとしての動作は、利用環境のトークン設定後に確認してください。
