# Compose/uvの導入と動作の検証記録

検証日：2026-10-04（日本時間）。実行コード・ビルド設定・依存の基点は
`c41af0eee7ced57c43f6e391fcf0c7c7837eadf4`です。検証時の文書は`ea3badf`に記録しています。

既存の設定やデータをコピーしない環境で、Compose/uvによる起動と合成入力の一連の操作を確認しました。
利用条件を判断できるよう、ホストから引き継いだもの、コマンド、結果、未検証の範囲を以下に記載します。
同梱物の内容とライセンスは[配布内容と依存物の確認](publication-audit.md)を参照してください。

## 今回新しく用意したものと、引き継いだもの

| 対象 | 検証条件 |
|---|---|
| ソース | 新しい作業ディレクトリへGitの追跡ファイルと導入手順の修正を展開。既存の`.env`、DB、TS、録画、仮想環境をコピーしない |
| Compose | 新しいproject `sdr-issue6-demo`、新しい`app-data` volume、localhost:18460。`env -i`でPATH/HOMEと指定したポート・Originだけを渡す |
| build | `build --no-cache`でAPT/Python依存を取得し、合成13/14ch TSをイメージ内で生成。Ubuntu/uvの固定digestのベースイメージは既存Dockerストアから使用し得る |
| uv | 新しい`.venv`と空の`UV_CACHE_DIR`へlockfileから取得。360秒TSを2本新規生成。新しい保存先とlocalhost:18461で起動 |
| ホストから引継ぎ | Ubuntu 24.04/x86_64、カーネル、Docker Engine 29.8.1、Compose v5.5.1、uv 0.12.18、OS Python 3.12.3、ホストに導入済みのFFmpeg/ffprobe 6.1.1 |
| ブラウザー検証 | Playwright 1.58.0を専用一時環境へ取得。既存Chromium 145.0.7632.6とGit外の共有ライブラリを使用。製品の依存には追加しない |
| 実機・研究元 | 実機は操作せず、過去の受入れ済み根拠を使用。既存native imageのimport/依存通知をネットワーク・機器なしの一時コンテナで確認 |

**新しいOSをインストールして検証した結果ではありません。** ホストのDocker、Python、FFmpeg、
ブラウザーと共有ライブラリは引き継いでいます。一方、アプリの設定・DB・入力素材と保存先、
Pythonの仮想環境は新しく作り、開発者の既存データに依存しないことを確認しました。
GNU Radio/C++はuvへ混ぜず、別の固定ネイティブ環境として扱います。今回のuv起動は合成入力です。

## コマンドと結果

ソースディレクトリで実行します。ブラウザー実行ファイルと補助ライブラリのパスは
検証環境に合わせて指定します。以下のproject・ポートも、既存の使用がないことを確認して使います。

```sh
# ソースだけの新しいディレクトリから。既存のデータや設定をコピーしない
SDR_PORT=18460 SDR_ORIGIN=http://localhost:18460 docker compose -p sdr-issue6-demo build --no-cache
SDR_PORT=18460 SDR_ORIGIN=http://localhost:18460 docker compose -p sdr-issue6-demo up -d --wait

# uvの依存とデモも新規作成
UV_CACHE_DIR=/tmp/sdr-issue6-uv-cache uv sync --locked
uv run --locked python scripts/generate-demo.py
uv run --locked python scripts/generate-demo.py --output data/demo/demo-14.ts --channel 14

# 専用uv APIを起動し、実画面の操作と異常経路を確認して停止
uv run --no-project --python 3.12 --with playwright==1.58.0 \
  python scripts/smoke-webui.py --chromium /path/to/chromium \
  --source data/demo/demo.ts --data-dir /path/to/new-ui-data --port 18461

# Composeで同じ実画面の操作を確認（下記の呼出しを使用）
# 製品UIと独立したA/V・期限・hash検査
uv run --locked python scripts/smoke-stage2.py --origin http://localhost:18460 \
  --source /path/to/compose-generated-demo.ts

docker compose -p sdr-issue6-demo down
# 同じproject/volumeで再起動し、局・録画保持と自動RXなしを確認後、再度down
SDR_PORT=18460 SDR_ORIGIN=http://localhost:18460 docker compose -p sdr-issue6-demo up -d --wait
docker compose -p sdr-issue6-demo down
```

Composeの実画面確認では、`scripts/smoke-webui.py`の`exercise()`をimportし、
`SimpleNamespace(origin="http://localhost:18460")`をserver引数へ渡しました。
`sync_playwright()`でChromium、1280×900のcontext、pageを作り、
`exercise(page, context, server, source, None)`を実行してfinallyでbrowserを閉じます。
sourceはこの専用コンテナの`/opt/demo/demo.ts`を`docker cp`で取り出した合成データです。
既存サーバーを再起動する`faults()`はComposeには適用していません。
uvの試験では既存のスクリプト全体を実行し、自分が作ったAPIだけを再起動しています。

| 検証 | 結果 |
|---|---|
| Compose build/up/health | 成功。非root UID/GID 10001、read-only、非特権、PID上限64、localhostのみ |
| 接続診断・UI・スキャン | Compose/uvとも成功。合成13/14chを保存、中止でも一覧保持、14chへの選局切替 |
| 供給中HLS | Compose 346 frame・音声peak 0.1305、uv 341 frame・0.1261。ウォームアップ後の約4秒で再生時刻が約4秒進行 |
| 録画・録画再生・download | Compose 2,060,856 bytes、uv 2,039,800 bytes。入力の同じ区間とSHA-256一致。再生191/190 frame・非ゼロ音声・時刻進行 |
| 録画だけの手動停止 | 録画はcompleted/requested、partial=falseで正常完了し、視聴が継続。完了した録画は再生・ダウンロード可能 |
| 録画中に受信を停止 | 別chへ切替後の録画で確認。録画はfailed/source_ended、partial=trueとなり、再生・ダウンロードを拒否。受信ごとのIDと表示を分離 |
| APIによる8秒録画 | 8.010984秒で期限終了、1,000,160 bytes、TS継続時間8.210022秒。供給中HLSと録画HLSのA/Vデコード成功、オリジナル不変 |
| UI異常経路（uv） | HTTP失敗の表示、mockへ自動切替なし、再読込・複数タブ・pageshowで開始なし、API再起動後partial/CSRF更新、期限不足・容量不足・二重録画拒否を確認 |
| Compose停止・再起動 | 局2件、録画3件、session/scan各3件のIDを保持。自動RXなし。最後は専用コンテナを停止・削除し、新規volumeは保持 |
| uv停止 | スクリプトfinallyで起動したAPIとブラウザーを回収。テスト用のTS・DBはGit外に保持 |
| 共通検査 | `sh scripts/check.sh`成功。Ruff/整形（62ファイル）、mypy（41ファイル）、pytest 96成功・1skip、working/history機密検査成功 |
| skip・警告 | TSDuckの実ツール試験1件はホスト未導入でskip。Starlette TestClientのHTTPX非推奨警告1件。成功扱いにしない |
| native環境の照合 | 既存の固定imageでGNU Radio 3.10.9.2/hlfecwideband import成功、GNU Radio/GSL/pybind11/makeと著作権ファイルを照合。再構築・実機試験ではない |

初回のuv依存取得とGitHub取得はサンドボックスのDNS制限で失敗し、正規の昇格後に成功しました。
Chromiumは既知の不足ライブラリをGit外の検証用ディレクトリから指定しています。
合成HLSには起動直後の短い待ちがあり、ウォームアップ後の進行を確認しています。
今回の結果は機械的なA/V検査で、人による画質・音質の確認ではありません。
Actionsは無効でCI未実行です。今回、実機の300秒録画、Safari、新しいOS、別ボード/FWは再試験していません。

## 機能ごとの実装と検証の根拠

以下のコミットは実装または統合を指します。
過去のコマンド・実測結果はリンク先の検証記録に保持しています。

| 機能・確認項目 | 実装と根拠・コマンド | 判定と制限 |
|---|---|---|
| READMEからCompose/uv、診断・UI | `777dc1f`（PR #30）、`dd60fb2`（#34）。今回の上記build/up、uv sync、smoke-webui | 成功。新規アプリ環境であり新規OSではない |
| 実機なしデモとHLS、入力の区別 | PR #31/#34、`e1dbc0f`。今回のsmoke-webui/smoke-stage2、[過去のバックエンド検証](validation.md#過去の記録) | 成功。合成、保存TS、実機の表示を区別 |
| 2物理ch以上の実機スキャン・保存・再表示 | `77a534f`、`49e8011`、`452859d`、PR #36/#39。[実機検証](validation-live.md)、[品質比較](validation-reception-quality.md)の実機ComposeとWebUI | 過去の成功を再利用。8物理ch・21サービス、代表21/27ch。全局A/V保証ではない |
| 両chの受信中A/V・切替・時間・品質 | PR #36、`28ac043`、`452859d`。同記録のChromium/WebUI・FFmpeg観測 | 過去の成功。初回待ちと処理遅れを記録、電波からの絶対遅延は未測定。USB原因は#38 |
| 同じTSの手動/300秒録画・視聴継続 | PR #31/#36/#39。実機検証の21ch、品質比較の27chとTS解析、今回の8秒API録画 | 実機27ch 300.028秒・631,963,692 bytes、partial=false。全TSの対象外PIDにCC 1件、録画端に警告 |
| オリジナルdownloadと派生物で再生 | PR #31/#34/#36。既存native区間/hash・download照合、今回の両起動経路のUIとA/Vデコード | 成功。CASと変換は別派生物、オリジナルを保持 |
| 禁止操作・二重RX防止・画面を閉じても期限 | PR #31/#34、[過去のUI検証](validation.md#過去の記録)、実機での300秒録画と今回の共通/UI試験 | 過去の実時間300秒と今回の模擬試験を区別。今回300秒試験を重複しない |
| 実機停止・復元・回収、容量・異常・再起動 | `c3cfbc1`、`7648b43`、PR #39。実機の独立読戻し/共有flock/ホスト後始末、pytestと今回のuv異常試験 | 成功記録と過去のUSB失敗/unknownを保持。未知の異常全般への保証ではない |
| ChromiumとmacOS Safari | PR #34/#36/#39、[#23完了記録](https://github.com/hayatky/sdr-dtv-poc/issues/23#issuecomment-5979299009) | 受入れ済み。Safari詳細バージョンと最終5分録画のブラウザーは未記録。今回のChromium合成確認は別 |

5分録画を問題なく再生できたというユーザーの報告は、人による再生確認です。
TS全体には対象外PIDのCC不連続が1件残り、録画端にはデコード警告があります。
人による確認と、TSの機械的な品質検査は区別して参照してください。
