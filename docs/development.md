# 開発環境

開発にはPython 3.12を使います。Pythonはuvで選択し、
pyenvを必須にしません。`.python-version`と`requires-python`を合わせ、
`uv.lock`で実行・開発依存を固定します。検査で使うuvは0.12.18です。

## 検査運用の方針

Ruff・mypy・pytestは維持し、このPoCの重要な動作を少ない手間で確認するために使います。
検査の範囲・頻度や追加依存は、実装の規模とリスクに合わせます。

- Ruffは整形、未使用import、基本的なミスの検出に使う。
- mypyはAPI、状態管理、adapterの境界を中心に使う。全コードへのstrict適用を初期条件にせず、型検査の効果と負担に応じて対象・厳しさを決める。
- pytestは開始・停止、300秒期限、二重開始・排他、異常終了、再起動時の回収等の重要な動作に絞る。カバレッジの数値目標や、実装をなぞるだけのテストを追加しない。
- コミット前はGitleaksとRuff、push前・CIではmypyとpytestも実行する。
- HTTPX等の追加依存は、APIテストなど実際に使う実装と一緒に導入する。将来用の先回り追加を避ける。

現行設定では`src/`・`scripts/`・`tests/`をmypy strictで検査しています。
HTTPXはTestClientを使うAPIテストの開発依存として導入しています。

アプリは開発時もDocker Composeで起動します。実機用の`compose.yaml`と
合成デモ用の`compose.demo.yaml`を使い分けます。uvは依存管理・検査・補助ツール用で、
ホストへGNU RadioやC++の受信ブロックを導入する起動方法は提供しません。

## 初回準備

uv 0.12.18とGitleaks 8.30.1を準備してください。
Gitleaksの取得・checksum照合・非公開識別子の設定は
[公開前チェック](sensitive-data.md)を参照してください。

```sh
# Python 3.12がなければ、uvで取得する
uv python install 3.12
# .venvへlockfile通りの開発依存を導入
uv sync --locked
# 既存hookがある場合は内容を確認して統合する
git config --local core.hooksPath .githooks
# ローカル・push前・CI共通の検証
sh scripts/check.sh
```

`pyproject.toml`と`uv.lock`で実行・開発依存を管理します。
起動方法は[README](../README.md)、APIの入出力は[API仕様](api.md)、
ネイティブ環境の固定バージョンとビルド方法は[受信処理](receiver.md)を参照してください。

## 日常のコマンド

```sh
# lint（標準のエラー検出、未使用import、import順序等）
uv run --locked ruff check .
# 整形を適用（ファイルを書き換える）
uv run --locked ruff format .
# 整形の検査だけ
uv run --locked ruff format --check .
# 自作Pythonの型検査
uv run --locked mypy
# 実機不要の合成テスト
uv run --locked pytest
# 上記とGitleaksをまとめて実行
sh scripts/check.sh
```

コミット前hookはindex全体の機密検査と、作業ツリーのlint・整形検査を行います。
部分staging時も機密検査はindexの内容を見ますが、品質検査は未staging部分を含みます。
hookは自動修正しません。push前hookとCIは`sh scripts/check.sh`を実行します。
cloneごとにhookの有効化が必要です。

mypyは`src/`・`scripts/`・`tests/`をstrictで検査します。
API・状態管理・アダプターを検査対象へ追加しました。
外部nativeモジュールの型不足は
該当moduleのstubや限定したoverrideで扱い、全体の型検査を無効にしません。
`.editorconfig`で文字コード・改行・インデントも共有します。

依存の追加は`uv add`、開発依存は`uv add --dev`を使い、
`pyproject.toml`と`uv.lock`の差分を一緒に確認します。
更新時は`uv lock --upgrade-package PACKAGE`で対象を絞り、共通チェックを通します。
CIとhookは`--locked`で、lockfileの不整合を失敗として扱います。

## 実機・native依存とテスト

GNU Radio・C++の受信ブロック・FFmpeg・TSDuckはDockerfileでコンテナ内へ導入します。
OS Pythonで動く受信処理と、APIのPython仮想環境もコンテナ内で分離します。
通常の利用や開発用サーバーの起動に、これらをホストへインストールする必要はありません。
uvの管理PythonへOSのGNU Radioを混ぜる設定も不要です。

通常のテストとCIでは実機を操作しません。機密検査に加え、合成TSによるAPI、
開始・停止・EOF・session期限・排他・異常終了・DB障害・再起動時の回収と
Host/Origin/CSRF・ファイル配信を検査します。300秒期限は時計を注入した試験で確認します。
実時間300秒の実機録画やブラウザーの動作は、別の検証として扱います。
確認済みの環境・結果・制限は[検証結果](validation.md)を参照してください。
模擬試験と、実機での受信・受信中のA/V再生の成功を分けて記録します。

CI設定は`.github/workflows/sensitive-data.yml`です。
Actionsは無効のため、検査は上記のコマンドでローカル実行します。
CIを利用する場合も、実行結果を確認してローカル検査と区別してください。
fork PRのSecret制約は公開前チェックの手順に従います。

## 検証環境の分離

HTTPX 0.28.1を使うTestClientにはStarletteから非推奨警告が出ますが、現行の検証は成功しています。
依存の更新時に代替クライアントの互換性を確認します。

単一APIプロセスから合成/保存TSのPythonワーカーを起動します。重い復調をAPI内で
実行しません。保存先はGit外で、APIの終了時に子ワーカーを回収します。
ローカルの一時テストは一意の保存先・ポート・Compose project名を使い、他の受信処理や
利用者のデータを停止・削除しません。

## 合成入力によるAPI・HLSの検証

`scripts/smoke-stage2.py`は起動済みの専用APIで合成入力だけを操作し、FFmpegによる
供給中HLS・録画再生のA/Vを検査します。`--source data/demo/demo.ts`を渡せば録画区間と
入力のbyte一致も確認します。完了ファイルは保存し、既存データを削除しません。

ブラウザーの自動検証は開発補助の`scripts/smoke-browser.py`です。製品UIを使わず、
同一Originで既存のhls.jsを読み、videoのフレーム数、時刻の進行、Web Audioの非ゼロ音声、
配信端との差を記録します。人による視聴確認にはしません。

```sh
# 任意の補助検証。既存のChromiumと必要なOSライブラリを使う。
# プロジェクト依存・イメージへPlaywrightを追加しない。
uv run --no-project --python 3.12 --with playwright==1.58.0 \
  python scripts/smoke-browser.py --origin http://localhost:18324 \
  --chromium /path/to/chromium
```

ブラウザー検証はAPIの専用保存先を使い、録画と再生用派生物を作ります。ffprobe・ffmpegの
ログ全文は公開せず、検査スクリプトは成功の指標または失敗理由の分類を出力します。
通常のpytestはFFmpeg/Chromiumの導入成功を意味しません。補助検証を別に実施してください。

## WebUI経由の検証

`scripts/smoke-webui.py`は実画面のボタンをChromiumで操作します。
テスト専用のAPIを一時的に起動し、終了時には自分が起動したプロセスだけを停止します。
これは開発者向けの回帰試験であり、利用者向けのサーバー起動方法ではありません。
既存の保存先を拒否し、検証で作った録画は削除しません。
起動中の別API・他担当のCompose project・volumeは使いません。
実行例は以下のとおりです。FFmpegとChromium、およびChromiumの実行に必要なOSライブラリを別途用意します。
`tests/webui-lifecycle.js`の通信・プレイヤーの回帰試験もこのスクリプトがChromium内で実行します。
Playwrightは補助ツールとして一時環境で使い、製品依存やコンテナへ追加しません。
通常の`sh scripts/check.sh`はChromiumを起動しないため、この結果とは別に記録してください。

```sh
# 出力先とdata-dirは未使用の場所を選ぶ。既存のTSやJSONは上書きしない。
uv run --locked python scripts/generate-demo.py --output data/ui-input/demo.ts
uv run --locked python scripts/generate-demo.py --output data/ui-input/demo-14.ts --channel 14
uv run --no-project --python 3.12 --with playwright==1.58.0 \
  python scripts/smoke-webui.py --chromium /path/to/chromium \
  --source data/ui-input/demo.ts --data-dir data/ui-validation --port 18330
```

この検証は、診断・スキャン・選局・HLS・短時間録画・再生・ダウンロードに加え、
再読込み・別タブ・通信断・応答喪失・拒否応答・API再起動・プレイヤー終了を確認します。
拒否応答の注入は、実際のディスク容量不足や実機の故障を起こした試験とは区別してください。
録画は手動で短時間停止するため、この実行だけでは実時間300秒の確認にはなりません。

起動済みの専用APIを使う検証では、以下のように入力TSとOriginを指定します。
通常運用のAPIへ実行しないでください。

```sh
uv run --locked python scripts/smoke-stage2.py --origin http://localhost:18324 \
  --source data/ui-input/demo.ts
```

初回再生時刻や配信端との差、映像フレーム、音声振幅、ダウンロードのhashは機械的な指標です。
人による音声の聴取や画質の確認、電波から画面までの絶対遅延と混同しないでください。
過去に確認した条件と結果は[導入・動作の検証](validation-install.md)を参照してください。

## 実機用Composeの検証

通常の共通検査は実機へ接続しません。実機用の固定ソース、専用ホスト準備、
非rootコンテナ、有限時間の試験と終了手順は[live-receiver.md](live-receiver.md)、
成功・失敗・未実行の区別は[実機での検証記録](validation-live.md)を参照してください。
通常の構成は同梱した`native/receiver`を使い、起動時にhashを確認します。
`prepare-live.py`は、取得済みの非公開Gitオブジェクトを持つ保守担当者向けの補助ツールです。
元の研究リポジトリは今後も非公開で、通常の開発・導入でこのツールや取得権限は必要ありません。
native imageの共有ライブラリはこのPythonファイルのhash照合には含まれないため、
既存の固定ビルド手順とimage ID・package記録を併用します。


## 通常のCompose起動と同梱ソース

通常の `compose.yaml` / `Dockerfile` は実機用です。合成検証は
`docker compose -f compose.demo.yaml up --build -d --wait` で明示します。
従来の `compose.live.yaml` / `Dockerfile.live` と `live-start.py` は既存の手動管理環境向けです。

`native/`は受信処理の固定スナップショットです。元の構造と動作を保つためRuffの対象から
除外していますが、共通pytestでファイル集合・SHA-256・構文・改変検出を確認します。
実処理のimportとネイティブビルドはDockerで別途確認します。
同梱元に秘密・個体情報・放送素材がないことを確認し、更新時も `manifest.json` と
`live_sources.py` のハッシュ、ライセンス通知を照合してください。
`host_service.py`の試験は偽の接続情報と一時ディレクトリを使い、実機やホスト設定を変更しません。
