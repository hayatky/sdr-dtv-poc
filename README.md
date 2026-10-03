# sdr-dtv-poc

日本の地上波をSDRで受信し、WebUIからスキャン・選局・視聴・短いTS録画を行う
実験的なPoCです。現在は**実機を使わない開発基盤（Issue #3）**を実装しています。
合成デモの開始・停止、状態取得、TSダウンロード、接続診断を操作できます。
実機での受信、スキャン、HLS、録画機能とWebUI本体は後続Issueで実装します。

## Docker Composeで合成デモを起動

対象はUbuntu 24.04 / x86_64です。Docker EngineとComposeを用意して実行します。
初回buildはPython依存とFFmpegを取得し、自作の映像・音声から8秒のTSを生成します。
研究元repo、SDRボード、カード、放送素材、Node.js/npmは不要です。

```sh
docker compose up --build -d --wait
# ブラウザーで http://localhost:8000 を開く
# 使用後は停止する（保存用volumeは保持する）
docker compose down
```

画面の「接続診断を表示」「合成デモを開始」「デモを停止」で動作を確認できます。
この画面はAPIとの疎通確認用の入口です。テレビ視聴用UIは#18・#27〜#29で作ります。
Vue 3.5.22 / hls.js 1.6.13は同梱済みで、実行時CDNやフロントのビルドは不要です。
合成映像はテストパターン、音声は440 Hzの正弦波です。実機での受信ではありません。

公開ポートは127.0.0.1だけです。URLのHost/Originを検証するため、既定では
`localhost`を使ってください。ポートやホスト表記を変える場合は`.env.example`を
参考に`SDR_PORT`と`SDR_ORIGIN`を一致させます。例:

```sh
SDR_PORT=18000 SDR_ORIGIN=http://localhost:18000 docker compose up -d --wait
```

コンテナはUID/GID 10001の非root、read-only filesystemで実行します。
デバイス・Docker socket・特権モードは使いません。SQLiteとデモ出力は`app-data`
volumeへ保存し、将来の一時HLS用領域は別の有界tmpfsです。起動時に自動RXしません。
`docker compose down -v`は保存データを削除するため、通常の停止には使いません。

## uvで開発・起動

Python 3.12、uv 0.12.18を使います。UbuntuでFFmpegを別途導入してください。
uvだけではFFmpeg、GNU Radio、C++の受信ブロックは導入されません。

```sh
sudo apt-get install ffmpeg=7:6.1.1-3ubuntu5
uv sync --locked
uv run --locked python scripts/generate-demo.py
uv run --locked uvicorn sdr_dtv_poc.app:create_app --factory \
  --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log \
  --timeout-graceful-shutdown 10
```

別ターミナルでAPI・TS出力まで確認できます。

```sh
uv run --locked python scripts/smoke-api.py
```

生成スクリプトは既存ファイルを上書きしません。生成済みならそのまま使い、再生成時は
`--output data/demo-new/demo.ts`等の新しい保存先を指定して`SDR_DEMO_PATH`を変更します。
停止はCtrl+Cです。終了を待ってから再起動してください。APIは必ず単一プロセスで使います。
詳細は[API仕様](docs/api.md)、[受信処理の固定と診断](docs/receiver.md)を参照してください。

## 開発前の確認

Gitleaks 8.30.1を準備し、既存hookを確認してから本cloneのhookを有効にします。

```sh
git config --local core.hooksPath .githooks
sh scripts/check.sh
```

[開発環境](docs/development.md)、[公開前の検査](docs/sensitive-data.md)、
[Issue #3の検証記録](docs/issue-3-validation.md)を参照してください。
GitHub Actionsは無効のままです。ローカルの検証成功をCI実行成功とは記載しません。

研究元の実機成果と、このPoCの合成デモの成功は別です。実機への到達性・RX復元、
実機でのライブ再生・録画、macOS/WindowsのDocker Desktop、Safariでの再生は未検証です。
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
