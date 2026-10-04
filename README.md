# sdr-dtv-poc

日本の地上波をSDRで受信し、WebUIからスキャン・選局・視聴・短いTS録画を行う
実験的なPoCです。合成TSに加え、UHF全範囲（13〜52ch）の実機スキャンと、検出した局の視聴・録画を実装しました。
WebUIから診断、合成13・14chのスキャン、保存した局の選局、HLSの視聴、最大300秒の
手動TS録画、録画の再生とオリジナルTSのダウンロードを操作できます。
実機用のローカルDocker Composeでは、固定した研究元の受信処理と外部CASを使います。
[導入・停止手順](docs/live-receiver.md)と[実機を含む検証記録](docs/issue-5-validation.md)を参照してください。
USB切断後に視聴できない場合は、[WebUIで復旧する手順](docs/recovery.md)を参照してください。
Safariでの両チャンネルの視聴・録画再生はユーザー確認済みです。
その際の画質改善要望を受け、映像8 Mbps・最大12 Mbpsへ変更し、録画の手動削除を追加しました。
周期的な映像ノイズとTS欠落は[Issue #37](https://github.com/hayatky/sdr-dtv-poc/issues/37)で
比較・修正し、変更後の主観的な画質を確認します。反復USB切断後の機器確認と復旧も残っています。

[Issue #29の実装と引継ぎ](docs/issue-29-handoff.md)、
[UI接続の検証記録](docs/issue-29-validation.md)を参照してください。
全体の進捗は[Issue #2](https://github.com/hayatky/sdr-dtv-poc/issues/2)で管理します。
PR #34はmainの`dd60fb2`へ統合済みで、#4・#18・#29は完了しました。
進行中の[段階3のIssue #5](https://github.com/hayatky/sdr-dtv-poc/issues/5)は、
#19の合成入力と#20の別チャンネルの先行確認を経て、実機での検証へ進みました。
[現在の引継ぎと残作業](docs/issue-5-handoff.md)を参照してください。
#19・#20の成果は受け入れ済みで、#21〜#23と#37を確認してから親#5の完了を判定します。

## Docker Composeで合成デモを起動

対象はUbuntu 24.04 / x86_64です。Docker EngineとComposeを用意して実行します。
初回buildはPython依存とFFmpegを取得し、自作の映像・音声から各360秒の連続したTSを2種類生成します。
研究元repo、SDRボード、カード、放送素材、Node.js/npmは不要です。

```sh
docker compose up --build -d --wait
# ブラウザーで http://localhost:8000 を開く
# 使用後は停止する（保存用volumeは保持する）
docker compose down
```

通常の画面は実APIへ接続します。「接続を確認する」→「スキャンを開始する」→
保存された局の「視聴する」の順に操作してください。スキャンの既定対象は合成13・14chです。
映像はテストパターン、音声は440 Hzまたは880 Hzの正弦波で、実機での受信ではありません。
自動再生が拒否された場合は、プレイヤーの再生ボタンを押してください。
録画は最大5分でサーバーが停止します。入力の残量が305秒未満なら開始を拒否するため、
長く視聴した後は受信を停止して選局し直してください。録画だけを停止すると視聴は続きます。

Vue 3.5.22 / hls.js 1.6.13は同梱済みで、実行時CDNやフロントのビルドは不要です。
画面だけの模擬デモは `http://localhost:8000/?mode=mock` で明示的に選べます。
このモードはAPIによる受信・録画とA/V再生を行いません。通信失敗で自動的に切り替わることはありません。
[WebUIの設計](docs/webui-design.md)も参照してください。

公開ポートは127.0.0.1だけです。URLのHost/Originを検証するため、既定では
`localhost`を使ってください。ポートやホスト表記を変える場合は`.env.example`を
参考に`SDR_PORT`と`SDR_ORIGIN`を一致させます。例:

```sh
SDR_PORT=18000 SDR_ORIGIN=http://localhost:18000 docker compose up -d --wait
```

コンテナはUID/GID 10001の非root、read-only filesystemで実行します。
デバイス・Docker socket・特権モードは使いません。SQLite・オリジナルTS・HLSは`app-data`
volumeへ保存し、アプリで出力量と空き容量を監視します。起動時に自動RXしません。
`docker compose down -v`は保存データを削除するため、通常の停止には使いません。

## uvで開発・起動

Python 3.12、uv 0.12.18を使います。UbuntuでFFmpegを別途導入してください。
uvだけではFFmpeg、GNU Radio、C++の受信ブロックは導入されません。

```sh
sudo apt-get install ffmpeg=7:6.1.1-3ubuntu5
uv sync --locked
# 次の2行は初回だけ実行する。既存ファイルがある場合は下記の説明を参照する
uv run --locked python scripts/generate-demo.py
uv run --locked python scripts/generate-demo.py --output data/demo/demo-14.ts --channel 14
uv run --locked uvicorn sdr_dtv_poc.app:create_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log \
  --timeout-graceful-shutdown 10
```

別ターミナルでAPI・TS出力まで確認できます。

```sh
uv run --locked python scripts/smoke-api.py
```

生成スクリプトは、出力先のTSまたは同名のJSONが存在するとエラーで終了します。
生成済みならJSONの`duration_seconds:360`と`physical_channel_label:13/14`を確認し、
生成コマンドを省略して使います。旧8秒デモ等からの再生成は、新しいディレクトリに
13chの`demo.ts`と14chの`demo-14.ts`を作り、`SDR_DEMO_PATH`を新しい`demo.ts`へ変更します。
14chの入力は13chのファイル名の末尾に`-14`を加えたTSとして解決されます。
停止はCtrl+Cです。終了を待ってから再起動してください。APIは必ず単一プロセスで使います。
詳細は[API仕様](docs/api.md)、[受信処理の固定と診断](docs/receiver.md)を参照してください。

## 製品UIを使わずに一連の操作を確認

別担当と保存先・ポート・Compose projectを共有しないでください。専用サーバーで
`uv run --locked python scripts/smoke-stage2.py --origin http://localhost:8000`
を実行すると、合成scan、選局、供給中HLSのA/Vデコード、8秒録画、派生HLSのA/V、
オリジナルのhash一致を確認します。ブラウザー・人による視聴確認とは別です。
この検査は新しいsession・録画を作り、完了データは自動削除しません。

旧8秒デモは5分録画を開始できません。新しい出力先へ360秒のデモを生成し、
`SDR_DEMO_PATH`で選んでください。単純連結や無限ループでは延長しません。

## 開発前の確認

Gitleaks 8.30.1を準備し、既存hookを確認してから本cloneのhookを有効にします。

```sh
git config --local core.hooksPath .githooks
sh scripts/check.sh
```

[開発環境](docs/development.md)、[公開前の検査](docs/sensitive-data.md)、
[Issue #3の検証記録](docs/issue-3-validation.md)、
[段階2の検証・UIへの引継ぎ](docs/issue-4-backend-validation.md)を参照してください。
GitHub Actionsは無効のままです。ローカルの検証成功をCI実行成功とは記載しません。

研究元の実機成果、PoCの合成デモ、実機での視聴・録画は別の根拠として記録します。
macOS/WindowsのDocker Desktopで受信バックエンドを動かすことは未検証です。
Safariの確認は、Ubuntuで動くバックエンドへSSH転送で接続した結果です。
受信処理の自作wrapper等の配布条件も確認が必要で、既定imageに同梱していません。

## License

Copyright (c) 2026 hayatky

This project's original code is licensed under the GNU General Public License,
version 3 or (at your option) any later version (`GPL-3.0-or-later`). See
[LICENSE](LICENSE). New original sources carry an SPDX identifier.

Third-party components retain their own copyright notices and licenses. See
[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) and the [dependency inventory](docs/dependencies.md).
This change distributes source and local build instructions, not container images
or third-party binaries. Before binary/image distribution, provide corresponding
source and build instructions as required by each included component's license.
