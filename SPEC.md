# timecard 仕様・実装設計

Version: 1.0  
Platform: Ubuntu / Python 3.12 / uv  
Application: Slack Attendance Management System

## 目的・範囲

研究室等の約5人のグループ向けに、Slack内で入退室登録・在室共有・個人の時間集計を完結させる。自動検知、独立Web画面、OAuthログイン、給与計算、位置情報、他人の詳細履歴閲覧は対象外。

SQLiteを唯一の正しいデータ源とし、表示名ではなくSlackユーザーIDを利用する。Application LayerはSlack SDKに依存しない。

## レイヤーと責務

| レイヤー | モジュール | 責務 |
|---|---|---|
| Domain | domain | 状態、日時の正規化、利用者向けエラー |
| Application | services/attendance | 状態遷移、本人の退室時刻修正、監査、重複操作処理 |
| Application | services/statistics | 日・週・月の確定時間計算 |
| Application | services/notifications | 確認サイクル作成、配送状態、タイムアウト、再試行 |
| Application | services/users | 登録、無効化、管理者権限、明示的な保持期限削除 |
| Persistence | database | 接続、排他トランザクション、SQLマイグレーション、リポジトリ |
| Interface | slack/handlers, slack/app | Socket Modeイベント、アクセス検証、ack |
| Presentation | slack/blocks, home, modals | ダッシュボード、個人画面、修正モーダル |
| Integration | slack/sync | Slack API、永続Outboxの消化 |
| Runtime | main, scheduler, cli | 起動、重複起動防止、毎分ジョブ、終了、運用コマンド |

## 利用者と権限

ADMINとMEMBERを管理する。両者とも自身の登録・確認・集計・退室時刻修正を行える。他人の履歴閲覧・修正を提供しない。履歴削除はADMINのみ。

登録済み・有効ユーザー、設定したワークスペースIDを各イベントで検証する。チャンネル操作は設定チャンネルとDMに限定する。個人履歴は本人のApp Homeにだけ公開する。

CLIは信頼されたUbuntuアカウント所有者が利用する。ユーザー登録・無効化・環境設定をSlack内の管理UIには公開しない。未終了セッションがあるユーザーの無効化は拒否し、本人の退室処理を促す。

## 状態遷移

| 現在 | 操作 | 結果 |
|---|---|---|
| ABSENT | 入室 | セッション作成、PRESENT |
| PRESENT | 入室 | 二重登録を拒否 |
| UNCONFIRMED | 入室 | 既存セッションの確認・退室を促す |
| PRESENT / UNCONFIRMED | 退室 | 終了時刻保存、ABSENT、未処理通知取消 |
| ABSENT | 退室・確認 | 状態変更なし |
| PRESENT / UNCONFIRMED | 確認 | PRESENT、最終確認時刻更新、旧通知取消 |
| PRESENT | 送信成功から30分無回答 | UNCONFIRMED |

開いているセッションは利用者ごとに最大1件。日付が変わっても自動終了しない。操作を変えない場合も受領記録を保存し、同じ退室イベントの再配送が後続セッションを終了することを防ぐ。

## 記録・修正・集計

入室時にcheck_in_atとlast_confirmed_atを操作時刻に設定する。通常退室は操作時刻を使い、確認DMでは本人が過去の退室日時を指定できる。終了したセッションはApp Homeから退室時刻のみ修正可能。入室時刻の修正はv1では提供しない。

退室時刻は入室以降かつ現在以前で、他のセッションと重複しないことを検証する。セッション所有者をDBトランザクション内で照合する。修正前後の値を監査イベントに保存する。

時刻はすべてタイムゾーン付きdatetimeを使い、DBにはUTC・固定桁のISO文字列を保存する。表示と期間境界はAsia/Tokyo。週開始は月曜日。集計期間は半開区間[start,end)であり、終了済みセッションごとに以下を加算する。

```
max(0, min(check_out, end) - max(check_in, start))
```

未終了・未確認セッションは確定時間に含めず、現在セッションの全経過時間を「暫定」と明示して別表示する。履歴は本人に10件単位でページングする。

## 通知

最終確認から360分で確認DMを送信する。間隔・期限は環境変数で変更可能。毎分判定する。

ボタンは「まだいる」「退室した」「退室時刻を指定」。通知IDをボタン値に含め、所有者・現在セッション・確認サイクル・通知状態を回答時に検証する。古い通知・回答済み通知では状態変更しない。

通知状態はPENDING、SENDING、SENT、ANSWERED、EXPIRED、FAILED、CANCELLED。送信成功後だけsent_atを保存し、そこから30分を回答期限とする。未送信・失敗通知では未確認にしない。同じサイクルの通知作成をUNIQUE(session_id,cycle_key)で防ぐ。cycle_keyはlast_confirmed_atのUTC文字列。

無回答後のEXPIREDは再送しない。送信失敗は指数バックオフで再試行する。Bot起動時にSENDINGをFAILEDに戻して再試行する。SlackのDM配送は厳密に一度だけを保証しないが、回答によるDB更新は重複させない。

## 永続化・整合性

SQLマイグレーションで次のテーブルを管理する。

| テーブル | 内容 |
|---|---|
| users | Slack ID、表示名、有効・管理者フラグ |
| sessions | 入退室、最終確認、状態、作成・更新時刻 |
| attendance_events | 所有セッション、操作者、種別、発生時刻、metadata、重複防止キー |
| notifications | サイクル、予定・送信・回答時刻、配送状態、Slack ts、試行数 |
| app_settings | schema_version、dashboard_message_ts、dashboard_scope |
| outbox | Slack同期イベント、payload、作成・処理時刻、試行数 |
| operation_receipts | 状態変更なしの操作を含む受領記録 |
| schema_migrations | 適用済みSQLバージョン |

sessionsの外部キー、定義済みstatus、終了有無とstatusの整合、退室>=入室をCHECKで保証する。未終了セッションに部分UNIQUEインデックスを設定する。

各接続でforeign_keys=ON、busy_timeout=5000を設定し、DB初期化時にjournal_mode=WALを設定する。更新はBEGIN IMMEDIATEで直列化し、更新・監査・操作受領・Outboxを一括コミットする。Slack APIはトランザクション外で実行する。

Outboxは送信失敗を永続化し、1分〜最大1時間の指数バックオフで再試行する。通常のSlack操作後はすぐ同期し、復旧・CLIからの変更は毎分ジョブが処理する。Bot内の同期処理はスレッドロックで直列化する。

ダッシュボードtsをDB保存し、同じワークスペース・チャンネルでは更新する。message_not_foundで再作成する。API投稿成功直後・DB記録前の停止では重複投稿の可能性がある。

## 実行・運用

.envから設定を読み、Slack設定とトークンの所属ワークスペースを起動時検証する。秘密情報はDB・監査・ソースに保存しない。DBは0600、CLIはumask 0077で実行する。

BotはDBに対応したOSファイルロックを保持し、重複Botを拒否する。DBマイグレーション→Socket Mode接続→Scheduler起動の順に処理する。SIGTERM/SIGINTでジョブ完了を待ち、接続を閉じる。CLIとBotは同じDBで同時稼働できる。

Ubuntu systemd user serviceで常駐し、異常終了時に再起動する。journalctlでログを確認する。環境に応じて利用者がlingerを設定する。

## 保持・管理コマンド

デフォルト保持期間は90日でRETENTION_DAYSから変更する。purge-historyを管理者が明示実行し、期限より前に終了したセッション、関連通知・監査を削除する。未終了セッションは削除しない。削除件数をログに残す。バックアップにも同じ保持方針を適用する。

```text
timecard init-db
timecard add-user <ID> [--admin] [--name NAME]
timecard list-users
timecard disable-user <ID>
timecard purge-history --actor <ADMIN_ID> [--before AWARE_ISO_TIMESTAMP]
timecard run
```

詳細な設定、導入、復旧・バックアップ手順はREADMEとdocsを参照する。

## 検証・完了条件

pytestで状態遷移、UTC/JST・日週月境界、修正・重複拒否、アクセス制御、同時入室、重複イベント、ロールバック、再起動、Outbox再試行、通知失敗・送信直後停止復旧を検証する。外部時計を注入し、Slack APIはモック化する。Ruffのlint・format確認を必須とする。

実環境ではSocket Mode接続、ダッシュボード、入退室、App Home集計、確認DMと未確認・復帰、修正、再起動継続、systemd常駐、環境設定のみでの研究室移行を確認する。実Slackのトークン未設定の環境では、この実機確認を自動テスト完了と混同しない。
