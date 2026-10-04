# 開発環境

初期の実機不要の開発対象はPython 3.12です。Pythonはuvで選択し、
pyenvを必須にしません。`.python-version`と`requires-python`を合わせ、
`uv.lock`で実行・開発依存を固定します。CIのuvは0.12.18です。

## 検査運用の方針（2026-10-02）

Ruff・mypy・pytestは維持し、このPoCの重要な動作を少ない手間で確認するために使います。
検査の範囲・頻度や追加依存は、実装の規模とリスクに合わせます。

- Ruffは整形、未使用import、基本的なミスの検出に使う。
- mypyはAPI、状態管理、adapterの境界を中心に使う。全コードへのstrict適用を初期条件にせず、型検査の効果と負担に応じて対象・厳しさを決める。
- pytestは開始・停止、300秒期限、二重開始・排他、異常終了、再起動時の回収等の重要な動作に絞る。カバレッジの数値目標や、実装をなぞるだけのテストを追加しない。
- コミット前はGitleaksとRuff、push前・CIではmypyとpytestも実行する。
- HTTPX等の追加依存は、APIテストなど実際に使う実装と一緒に導入する。将来用の先回り追加を避ける。

2026-10-02にhookの実行頻度を上記方針へ揃え、未使用のHTTPXをdev依存から削除しました。
現在の`scripts/`と`tests/`に対するmypy strict設定は、検査が通っているため維持しています。

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

`pyproject.toml`のdevグループにRuff、mypy、pytestをまとめています。
HTTPXはAPIテストと同時に追加しました。FastAPI・Pydantic・Uvicornを実行依存として
`uv.lock`で管理します。API・合成TSアダプター・Compose起動は実装済みです。
合成/保存TSからのHLS生成・録画・再生も実装済みです。WebUIは実機入力にも対応し、#5で実機の検証を完了しました。起動はREADME、接点は
[API仕様](api.md)、固定native環境は[受信処理](receiver.md)を参照してください。

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

GNU Radio・C++復調ブロック・FFmpeg・ボード接続のホスト準備は、uvでの
Python依存導入とは別です。uvの管理PythonからOS導入のGNU Radioを
そのままimportできる保証はありません。受信backendの固定したバージョン、Python ABI、
共有ライブラリ、ライセンスとビルド手順を確認してadapterの実行環境を決めます。
`--system-site-packages`による暗黙の取り込みを標準にはしません。

通常のテストとCIでは実機を操作しません。機密検査に加え、合成TSによるAPI、
開始・停止・EOF・session期限・排他・異常終了・DB障害・再起動時の回収と
Host/Origin/CSRF・ファイル配信を検査します。#16の300秒期限は時計を注入した試験で確認済みです。
WebUIのAPI・HLS接続は#29で確認済みです。#19ではシステム全体の異常経路を照合し、
実時間300秒の実機録画は#22と#37で確認済みです。
現在の着手手順は[#6への引継ぎ](issue-6-handoff.md)を参照してください。
mock成功と実機RX・受信中A/Vの成功を分けて記録します。

CI設定は`.github/workflows/sensitive-data.yml`です。
Actionsは現在無効で、今回も有効化しません。非公開設定とブランチ保護も変更しません。
将来これらを変える場合は管理者の明示的な承認を得て、実際のCI結果を確認します。
fork PRのSecret制約は公開前チェックの手順に従います。

## 導入時の検証記録

2026-10-01（日本時間）、UbuntuのPython 3.12.3で開発依存を導入しました。
`sh scripts/check.sh`は成功：Ruffのlint・整形検査、mypy strict、
pytestの合成回帰試験10件、working・全ローカル履歴の機密検査を確認しました。
現在のindexに対する`.githooks/pre-commit`、hookと共通スクリプトの`sh -n`、
`git diff --check`も成功しました。仮想環境と各ツールcacheはGit除外を確認済みです。
GitHub Actionsの実行、必須check設定、native依存・実機受信の検証は未実施です。

### 設定調整とGitHubへの保存（2026-10-02）

コミット前のmypy実行と未使用のHTTPXを外し、関連する依存もlockfileから削除しました。
調整後のコミット前hookとpush前の共通チェックは成功しました。
pytestの合成回帰試験は10件成功し、機密検査はコミット対象・working・全履歴で成功しました。

公開前に引継ぎ文書から個人のRF接続条件を除去し、GNU GPLの本文との一致を確認しました。
開発環境・公開用文書を作業ブランチへコミット・pushして
[PR #1](https://github.com/hayatky/sdr-dtv-poc/pull/1)にまとめました。
GitHub Actionsはリポジトリ側で無効になっており、CIは未実行です。
有効化の明示的な承認を得てからCIを確認します。既定ブランチへの統合は別の操作です。

## Issue #3で追加した起動環境（2026-10-04）

[検証記録](issue-3-validation.md)に対象の状態・コマンド・成功・制限を記録します。
追加のPython依存は[一覧](dependencies.md)を参照してください。
HTTPX 0.28.1を使うTestClientにはStarletteから非推奨警告が出ますが、現行の検証は成功しています。
依存の更新時に代替クライアントの互換性を確認します。

単一APIプロセスから合成/保存TSのPythonワーカーを起動します。重い復調をAPI内で
実行しません。保存先はGit外で、APIの終了時に子ワーカーを回収します。
ローカルの一時テストは一意の保存先・ポート・Compose project名を使い、他の受信処理や
利用者のデータを停止・削除しません。


## 段階2のバックエンド検証

[実装と検証記録](issue-4-backend-validation.md)に検証対象、成功、失敗・未実施を記載します。
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

## WebUI経由の検証（#29）

`scripts/smoke-webui.py`は実画面のボタンをChromiumで操作します。
専用APIを起動し、終了時には自分が起動したプロセスだけを停止します。
既存の保存先を拒否し、検証で作った録画は削除しません。
起動中の別API・他担当のCompose project・volumeは使いません。
合成入力の生成・実行例・確認範囲は[検証記録](issue-29-validation.md)を参照してください。
`tests/webui-lifecycle.js`の通信・プレイヤーの回帰試験もこのスクリプトがChromium内で実行します。
Playwrightは補助ツールとして一時環境で使い、製品依存やコンテナへ追加しません。
通常の`sh scripts/check.sh`はChromiumを起動しないため、この結果とは別に記録してください。

## 実機用Composeの検証（段階3）

通常の共通検査は実機へ接続しません。実機用の固定ソース、専用ホスト準備、
非rootコンテナ、有限時間の試験と終了手順は[live-receiver.md](live-receiver.md)、
成功・失敗・未実行の区別は[issue-5-validation.md](issue-5-validation.md)を参照してください。
研究元のPythonソースは`prepare-live.py`でGitオブジェクトから別途抽出し、起動時もhashを確認します。
native imageの共有ライブラリはこのPythonファイルのhash照合には含まれないため、
既存の固定ビルド手順とimage ID・package記録を併用します。
