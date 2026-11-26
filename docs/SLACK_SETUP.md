# Slack App設定

## Appを作成する

1. 対象ワークスペースでSlack Appを新規作成します。開発用と研究室用は別Appにします。
2. Socket Modeを有効にします。
3. Basic Information → App-Level Tokensで`connections:write`を付けたトークンを生成します。`xapp-...`を`SLACK_APP_TOKEN`に設定します。
4. OAuth & PermissionsのBot Token Scopesに以下を追加し、ワークスペースにインストールします。
   - `chat:write`：ダッシュボードの投稿・更新、DM、本人向け一時メッセージ。
   - `im:write`：本人とのDMを開く。
5. Bot User OAuth Token（`xoxb-...`）を`SLACK_BOT_TOKEN`に設定します。
6. App HomeでHome Tabを有効にします。
7. Event Subscriptionsを有効にし、Bot Eventsに`app_home_opened`を追加します。
8. Interactivityを有効にします。Socket Modeでは公開Request URLは不要です。
9. AppのBotを指定プライベートチャンネルに`/invite`で招待します。
10. Slackリンク等からワークスペースID（`T...`）、チャンネルID（`C...`または`G...`）を確認し、`SLACK_TEAM_ID`、`SLACK_CHANNEL_ID`に設定します。
11. プロフィールの「メンバーIDをコピー」から利用者IDを取得し、CLIで登録します。

追加スコープ設定後はAppを再インストールしてください。Botには個人履歴を共有チャンネルへ投稿する機能を持たせていません。プライベートチャンネルの参加者はSlack側で管理してください。

公式資料：[Socket Mode](https://docs.slack.dev/tools/bolt-python/concepts/socket-mode)、[Bolt Appの作成](https://docs.slack.dev/tools/bolt-python/creating-an-app/)、[操作のack](https://tools.slack.dev/bolt-python/concepts/acknowledge/)。

## 実機での確認

以下はローカル自動テストとは別に実施します。

- `uv run timecard run`で接続し、チャンネルにダッシュボードが1件作成されることを確認する。
- 入室し、ダッシュボードとApp Homeが更新されることを確認する。
- 退室し、App Homeの確定時間と履歴を確認する（表示は分単位）。
- 再起動し、セッションとダッシュボードメッセージが維持されることを確認する。
- 通知間隔・回答期限を各1分に短縮して再起動し、DM→無回答→未確認→「まだいる」で復帰することを確認する。
- 次の通知から「退室時刻を指定」を開き、日本時間で指定して終了する。
- App Homeで終了したセッションの退室時刻を修正する。不正日時はモーダル内で拒否されることを確認する。
- 登録外ユーザーの操作が拒否されることを確認する。
- テスト後は間隔360分、期限30分に戻して再起動する。

Slack送信が成功してDB保存前にBotが停止するとDMが重複する場合があります。ボタンの通知IDは同じであり、最初の有効な回答だけを適用します。回答済み・取消済みのDMはボタンを外して終了状態を表示します。表示更新に失敗した場合はOutboxで再試行し、更新前のボタンを押しても状態は変わりません。
