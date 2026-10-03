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
HTTPXはAPIテストの実装時に追加します。
FastAPI・Pydantic・Uvicorn等の実行依存は、
アプリ実装で使い始める際に`uv add`で追加します。
現段階はツールと合成検証のみで、API・mock受信・HLS・Compose起動は未実装です。

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

mypyは現在`scripts/`と`tests/`をstrictで検査します。
アプリを追加する際はAPI・状態管理・adapterの境界を優先して対象を決めてください。
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

通常のテストとCIでは実機を操作しません。現在のテストは合成データだけを使う
機密検査の回帰試験です。アプリ実装時には同じadapterの入出力仕様を持つmockで、
開始・停止、単調時計による300秒期限、排他、異常終了、再起動時の回収を検証します。
期限の試験は時計を注入して実時間300秒を待たずに行います。
mock成功と実機RX・受信中A/Vの成功を分けて記録します。

CI設定は`.github/workflows/sensitive-data.yml`です。
GitHubへ反映・実行成功を確認した後、既定ブランチの保護で
`Development checks / checks`を必須checkに設定してください。
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
