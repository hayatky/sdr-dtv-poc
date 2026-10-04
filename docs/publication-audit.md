# ソースと導入資料の公開候補の確認

2026-10-04（日本時間）、Issue #25/#26のために確認しました。
基点は`c41af0eee7ced57c43f6e391fcf0c7c7837eadf4`、公開候補は本書を含むPRのheadです。
確定コミットID、PR作成後の検査、残る操作はPR本文にも記録します。
公開設定はprivateのままで、Release作成・コンテナpush・Actionsや保護設定の変更は行いません。

## 公開候補と除外するもの

| 対象 | 判断 |
|---|---|
| 本リポジトリの自作ソース、設定例、導入・検証資料、同梱フロント資産、合成画面例 | 今回の公開候補。Git履歴・既存ブランチとGitHub本文も公開切替の影響範囲として確認 |
| 合成デモの生成スクリプト | 公開候補。FFmpegのtestsrc2と正弦波を使い、放送入力を読まない。生成TSはGitへ追加しない |
| 研究元wrapperと変換スクリプト | 再配布条件未確定。固定Gitオブジェクトから権限のある利用者が別途取得。ソース・既定デモimageに同梱しない |
| 外部CAS、受信FW/FPGA、カード関連データ、放送IQ/TS・派生映像音声 | Git・添付・配布候補から除外。CASのライセンス確認をカード関連の独自開発許可へ拡張しない |
| wheel、実行物、デモ用/実機用コンテナ | **今回配布しない**。GPL/LGPL等の対応ソース、Ubuntuパッチ、全構成要素の表示・ビルド情報を配布一式として準備していない |
| タグ、Release、添付、registry | 新規作成・配布を予定しない。既存GitHub Packagesの一覧は権限不足で未確認。そこに対象がないとは主張しない |

## GitとGitHub本文・画像の確認

- 監査開始時の到達可能な41コミット・追跡99ファイルとその変更、リモート5ブランチを列挙しました。
  リモート先端は全て検査したローカル履歴へ含まれ、タグは0件でした。
  履歴を含むファイル名・媒体拡張子を確認し、画像は現在と同じJPEG 4件でした。
- `.env.example`、`examples/live.env.example`、`examples/live.json`はplaceholderとlocalhostを使い、
  個人の実接続先やcredentialを含まないことを内容確認しました。READMEと引継ぎの追加差分も確認対象です。
- author/committerのメールは全てGitHub noreply、表示名は所有者の公開アカウントまたはGitHubでした。
  正当な著作権表示や公開アカウントを匿名化のために削除していません。
- `docs/images`の4画像を目視し、架空の局名・合成表示であることを確認しました。
  JPEGはJFIFとICC profileだけでEXIF/XMP/コメントなし。過去の画像blobも現在とSHA-256一致です。
- `gh api --paginate`で全39 Issue/PR（PR 9件）、issueコメント31件、reviewコメント20件、
  review本文16件を取得しました。本文・タイトル106件の機密検査と、URL・添付記法・
  個人ホーム・私用ネットワーク表記等の検索、該当内容の確認を行いました。
  添付URLはなく、画像記法はレビューの優先度badgeだけでした。
- Release 0、Actions実行0、Actions artifact 0、Pages/Wikiなしを確認しました。
  既存PackagesはAPIが403（`read:packages`不足）となり未確認です。権限や設定を変更していません。
- 過去の編集履歴・削除済み添付は通常の本文APIから取得しておらず、監査したとは記載しません。
  PR作成後は今回の本文も追加で確認します。管理者の公開操作直前にはその後の変更がないか照合が必要です。

コマンドは`git ls-files`、`git log --all`、`git rev-list --all`、`git diff`、
`gh api --paginate 'repos/hayatky/sdr-dtv-poc/issues?state=all&per_page=100'`、`.../issues/comments`、
`.../pulls/comments`と各PRの`reviews`、`.../releases`、`.../actions/artifacts`等です。
API取得JSON全体をGitleaksへ渡した最初の検査では、GitHub APIの`/users/`メタデータを
個人ホームパスとして検出しました。ルールは緩めず、公開対象のタイトル・本文を抽出し直して
106件を検査し、成功しました。ホーム名の補助検索でも一致はありませんでした。
これは画像や未登録の識別子まで自動検出できるという意味ではありません。

`docs/sensitive-data.md`に従うworking/history検査は`sh scripts/check.sh`で成功しました。
コミット直前のindex全体への`uv run --locked python scripts/check-sensitive.py staged`も成功しました。
最終コミットのhookでも同じindex検査を行います。
検出値・生ログ・API取得JSON・イメージtar・テスト用DBはGit・PRへ追加しません。
全ファイルの目視だけで漏洩を網羅したという主張ではなく、機械検査と対象別の内容確認を組み合わせた結果です。

## build contextとローカルイメージ

`.dockerignore`は全除外から必要なソース・静的資産・lockfile・README・license・
生成スクリプトだけを許可します。`.git`、`.env`、data、artifacts、画像例や研究元の取得物は含めません。
DockerfileのCOPY先と許可対象を照合し、ソースだけのディレクトリから`build --no-cache`を実行しました。

`docker image save`した専用デモイメージの8レイヤーをtarとして読み、全エントリーのパス、
アプリ領域の内容、メディア・DB・設定・Git履歴の混入を確認しました。
アプリ領域に個人ホーム名や除外したデータはなく、デモの媒体はビルドで生成した
`/opt/demo/demo.ts`と`demo-14.ts`だけでした。画像や放送データのCOPYはありません。
image configのユーザーは10001:10001、Composeは非特権・read-only・localhostのみでした。

検査したイメージのconfig digestは
`sha256:dad9ab6fe49600ebc054deacc5cf23c66a39f91fbfdc1c763fc0f96fd7d9f454`です。
これは基点と引継ぎ時のREADMEでビルドしたローカル検証物です。後続の文書だけの変更を
含むイメージを配布候補とはせず、実行コード・Dockerfile・lockfileが同じ範囲の動作根拠に使います。
OS package一覧は`/opt/packages.lock`、通知は`/usr/share/doc/*/copyright`に残ります。
この内容検査の成功を、イメージ配布に必要な対応ソース一式の準備完了とは扱いません。

## 依存物の照合

[依存一覧](dependencies.md)と[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)を基準にしました。

| 対象 | 今回照合した取得物・結果 | 配布の境界 |
|---|---|---|
| Python 28依存 | distribution metadata、実バージョン、同梱licenseを一覧と照合。pathspecはLICENSE本文でMPL-2.0を確認。新しいuv環境でもlockfileどおり取得 | 依存を改変せず、wheel内の表示を保持。第三者wheelそのものをReleaseへ追加しない |
| Vue 3.5.22 / hls.js 1.6.13 | 固定npm tarballとApache-2.0全文を再取得し、manifestの5ファイル全てでSHA-256と実ファイルの完全一致。MIT/Apache-2.0表示を保持 | 同梱するproduction資産とlicenseだけ。Node/npm・ブラウザー実行物は配布しない |
| Hatchling 1.29.0、uv 0.12.18、Gitleaks 8.30.1 | Hatchlingは新規buildで取得。uvは固定digestのimageと既存ホスト、Gitleaksは既存の検査用実行物を使用 | ビルド・検査の補助。既存ホストのツールを配布候補へ追加しない |
| FFmpeg | 新規デモimage内で6.1.1、APT `7:6.1.1-3ubuntu5`、`--enable-gpl`とlibx264、通知ファイルを確認 | Ubuntuの対応ソースとパッチ、全依存の条件を揃えるまでイメージ/実行物配布は除外 |
| GNU Radioと受信拡張 | 既存固定imageを機器・ネットワークなしで起動しimport確認。GNU Radio `3.10.9.2-1.1ubuntu2`、GSL `2.7.1+dfsg-6ubuntu2`、pybind11 `2.11.1-2`、make `4.3-4.1build2`、通知を照合 | 今回はnative imageを再構築していない。既存の固定ビルド手順と過去の検証を参照 |
| 研究元とgr-isdbt | 固定Gitオブジェクトのlive 12ファイル、基点のbuild入力4ファイルのhash一致。gr-isdbtの2固定コミットのLICENSE/COPYINGはGPL-3.0-or-later | gr-isdbtの条件を研究元独自wrapper全体へ広げない。変換内容・hash・ビルドはreceiver.mdと生成manifestに対応 |
| libaribb25 | cleanな固定コミット`dc1d96a90ea554d8997b238fd6712eccf553cdb3`とApache-2.0本文を照合 | 無改変の別途ローカル利用。実行物とソースの再ビルド同一性は今回未検証 |
| TSDuck | `v3.45-4798`の上流LICENSEとBSD-2-Clause表示を照合。Dockerfile.liveの固定deb/通知hashと対応 | 今回debは再取得していない。ホストの実ツール試験はskip、実機用imageの過去結果と区別 |

固定ソースの取得・変換・ビルド方法は[receiver.md](receiver.md)、
実機wrapper/CASの別途取得とmountは[live-receiver.md](live-receiver.md)にあります。
上流URLだけを対応ソースの提供一式の代わりにはしません。

## 残る判断と操作

1. PRの最終headと監査範囲をレビューし、mainへ統合する。統合後のコミットIDを記録し、
   候補からソースや設定が変わった場合は影響する検証・機密検査を追加する。
2. 管理者が、ソース・導入資料・同梱資産・現在のGit履歴/ブランチ・Issue/PRを公開対象とし、
   Release、実行物、コンテナ配布を含めないことを確認する。
3. 確認後に限り、`hayatky/sdr-dtv-poc`のvisibilityをprivateからpublicへ変更する。
   想定する操作は`gh repo edit hayatky/sdr-dtv-poc --visibility public --accept-visibility-change-consequences`。
   **この操作は未実施です。** 既存Packagesは今回の配布・設定変更の対象にしません。
4. 認証なしでリポジトリ・固定コミットのREADME・導入資料・ソースを参照できることを確認し、
   公開URL・対象コミット・結果を#26へ記録する。実機用研究元の取得権限は別条件として残す。
5. 上記の公開と参照確認が終わるまでは#26・#6・#2を閉じない。Actionsは無効のままとする。

この管理者確認は#26と今回の依頼で明示された公開前の確認です。
PR作成だけでは一般公開やPoC全体の完成にはなりません。
