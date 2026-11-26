# Ubuntuでの常時稼働

## 配置

既定のサービスファイルは`~/timecard`への配置を想定します。別ディレクトリの場合は`WorkingDirectory`、`EnvironmentFile`、`ExecStart`を変更してください。`uv`のパスも`command -v uv`で確認します。

```bash
cd ~/timecard
uv sync --frozen
cp .env.example .env
chmod 600 .env
```

.envに本番専用Appの情報とDBパスを記入します。設定後にDBを初期化し、本番利用者を登録してください。開発用のDBとトークンは流用しません。`uv run timecard run`で手動確認した後、Ctrl-Cで終了します。

## systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp deploy/timecard.service ~/.config/systemd/user/timecard.service
systemctl --user daemon-reload
systemctl --user enable --now timecard.service
systemctl --user status timecard.service
journalctl --user -u timecard.service -f
```

サービスは`uv run --frozen --no-sync`でインストール済み環境を起動します。初回起動前と更新時に`uv sync --frozen`が必要です。設定不備など終了コード2の場合は再起動ループを止めるため自動再起動しません。修正後に`systemctl --user restart timecard.service`を実行します。

ログアウト後もユーザーサービスを稼働させたい場合、ユーザーの常駐設定が必要な環境があります。

```bash
loginctl enable-linger "$USER"
```

このコマンドはログアウト後のユーザーサービス継続と起動時のユーザーマネージャー起動を許可します。必要性を確認したうえで利用者が実行してください。権限設定によっては管理者対応が必要です。

## 更新と停止

```bash
systemctl --user stop timecard.service
# DBバックアップ後、ソースを更新する
uv sync --frozen
uv run pytest
uv run ruff check .
systemctl --user start timecard.service
```

起動時にバージョン付きマイグレーションを適用します。SIGTERM/SIGINTでスケジューラーの実行中ジョブを待ち、Socket Modeを閉じます。DBファイルに対応したOSファイルロックによりBotの重複起動を拒否します。CLIはBotと同時利用できます。

マシンが停止・スリープしている間はSlack操作と通知を処理できません。復帰・再起動後にSQLiteから通知を復元します。通知期限は実際の送信成功時刻から計算し、未送信期間だけで未確認にしません。
