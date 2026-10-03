# 公開前の機密・個人情報チェック

Gitleaks **8.30.1**の標準認証情報ルールを保持し、`.gitleaks.toml`で
Linux/macOS/Windowsの個人ホームパス、秘密設定・秘密鍵ファイル名を追加検出します。
`scripts/check-sensitive.py`はホームディレクトリ名も、大文字小文字を区別せず
部分一致で禁止します。名前単独・複合語・ファイル名も対象です。
実名を公開設定・fixtureへ記載しません。
非公開禁止語は標準ルールの例外対象となるlockfile等でも、補助検査で必ず遮断します。

## 初回準備

1. [公式Release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)から
   OS/CPUに合うGitleaksを取得し、公式checksumと照合してPATHへ配置します。
   このcheckoutではGit外の`data/tools/gitleaks`も検査スクリプトが自動検出します。
   バイナリをGitへ追加しません。uvも別途必要です。
   `uv sync --locked`で開発依存を準備します。
2. `git config --local core.hooksPath .githooks`で両hookを有効にします。
   既存のhooksPathがある環境では、既存hookの内容を確認して統合してください。
3. ローカルでは実行ユーザーのホーム名を自動検出します。追加の非公開氏名・
   ホスト名・メール等は、改行区切りの`PRIVATE_IDENTIFIERS`環境変数へ設定できます。
   値をコマンド引数、公開文書、ログへ記載しないでください。
4. GitHub Actionsのrepository secret `PRIVATE_IDENTIFIERS`に、禁止するホーム名と
   必要な追加識別子を改行区切りで設定します。既存値は置き換えず追記してください。
   CIはこのSecretが空なら失敗します。fork PRにはSecretが渡らないため失敗します。
   forkのコードをSecret付きの`pull_request_target`で実行しないでください。
   レビュー・ローカル検査後に信頼済みの内部ブランチで検証します。

## 日常のフロー

```sh
# 未追跡・非ignoreファイルも含む現在の内容
uv run --locked python scripts/check-sensitive.py working
# 全ローカルrefの履歴（削除済みの漏洩も対象）
uv run --locked python scripts/check-sensitive.py history
# 必要な対象だけをgit addした後、コミットされるindex全体
uv run --locked python scripts/check-sensitive.py staged
git diff --cached --stat
git diff --cached
```

`pre-commit`はstagedとlint・整形検査、`pre-push`は型検査・テストを含む
`sh scripts/check.sh`（workingとhistoryも含む）を実行します。
push/PRのActionsでは全履歴をcheckoutして同じ検査を実行します。
Actionsは現在無効です。今回の作業では有効化、公開設定、ブランチ保護を変更しません。
将来の必須check設定は、管理者の明示的な承認とCIの実行確認後に扱います。
ローカルhookはcloneごとに有効化が必要で、CIはpush後の検査です。
hookの無効化・`--no-verify`に依存した運用をしないでください。

ignore済みのローカルデータはworking検査から除外しますが、既追跡ファイルは検査します。
ホームの例は`/home/<USER>`、`/Users/<USER>`、`C:\Users\<USER>`、
または相対パスにします。`.env.example`はファイル名だけの例外で、内容の認証情報は検査します。
`gitleaks:allow`コメントと`.gitleaksignore`は検査の迂回に使えません。
baselineによる既存漏洩の一括免除も設けません。

## 失敗時と検出限界

検出時はrule IDと行番号だけを表示し、値・ファイル名・commit author・生ログ・
report artifactは公開しません。ローカルで変更内容を確認し、匿名化・除去して再検査します。
設定・実行エラーも失敗として扱います。規則を広く緩めて通すのではなく、
正当な第三者著作権表示等の誤検出は理由と対象を確認して狭い例外をレビューします。

既に公開された認証情報は、削除だけで解消したとせず失効・更新と公開範囲の確認が必要です。
履歴改変は他者の作業へ影響するため、個別に合意して行います。

Gitleaksは任意の氏名・住所・電話番号や画像・EXIF、放送素材、全形式の認証情報を
網羅的に識別できません。Issue/PR本文・添付、Release等もこのGit検査の対象外です。
AGENTS.mdの公開禁止事項に沿った内容確認を併用してください。
非公開識別子は未登録の別ユーザー名を名前単独では検出できませんが、
個人ホームの絶対パスは共通ルールで検出します。

## 検証

```sh
uv run --locked pytest
```

自作合成データのみで、認証情報・ホームパス・禁止語・ファイル名・部分staging・
削除済み履歴の遮断、placeholderの許可、CI設定欠落時の失敗を確認します。

2026-10-01（日本時間）の導入時確認：固定したバージョンのchecksum照合、合成回帰試験10件、
現在のworkingと全ローカル履歴、現在のindexに対するpre-commit hook、
`sh -n .githooks/pre-commit .githooks/pre-push`、`git diff --check`は成功しました。
このcheckoutのhookを有効化し、GitHubの非公開Secretも登録済みです。
Actions実行と必須checkの保護ルール設定は未実施です。
Actionsの無効状態を維持します。CIや保護ルールを変更する場合は別途承認が必要です。
