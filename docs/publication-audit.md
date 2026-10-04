# 配布内容と依存物の確認

このリポジトリはソースとローカルでのビルド・導入手順を提供します。
実機を利用するために別途取得するものと、生成したイメージを再配布する際の制限をまとめます。

## 同梱するものと、別途用意するもの

| 対象 | 提供方法と利用条件 |
|---|---|
| 自作ソース・設定例・導入資料 | Gitで提供。自作コードはGPL-3.0-or-later |
| Vue/hls.jsのproduction資産 | 固定したバージョンを無改変で同梱。元の著作権表示・LICENSEと取得元/hashのmanifestを保持 |
| 合成デモ | 生成スクリプトを同梱。FFmpegのtestsrc2と正弦波からTSをローカル生成し、放送入力は使わない |
| 画面例 | 架空の局名と合成データを使用。現在の操作ガイドは実APIで再生・録画した画面。放送の番組画像は含まない |
| 研究元wrapperと変換スクリプト | 同梱しない。再配布条件は未確定で、権限のある利用者が固定Gitオブジェクトから別途取得 |
| 外部CAS | 同梱しない。実機で必要な場合に固定した既存OSSを無改変で別途ビルド |
| FW/FPGA、カード関連データ、放送IQ/TS・映像音声 | 同梱しない。受信したデータと再生用派生物は利用者のローカル保存先で管理 |
| wheel、第三者実行物、デモ用/実機用コンテナ | 配布しない。ソースと手順からローカルで構築 |

依存物のライセンスは自作コードのGPL宣言に置き換わりません。
イメージや実行物を再配布する場合は、GPL/LGPL等で必要となる対応ソース、Ubuntuパッチ、
各構成要素の通知とビルド情報を別途揃える必要があります。本リポジトリはその配布一式を提供していません。

## 内容確認の対象と結果

2026-10-04、`c41af0eee7ced57c43f6e391fcf0c7c7837eadf4`の実装と依存物を確認しました。
導入検証時の文書は`ea3badf`に記録しています。

- 設定例はplaceholderとlocalhostを使い、利用者が自分の環境に合わせて指定する構成です。
- `docs/images`のJPEG 4件を目視し、架空の局名と合成表示であることを確認しました。
  metadataはJFIFとICC profileで、EXIF/XMP/コメントはありませんでした。
- 操作ガイドに追加したPNG 3件は、`c41af0e`と同じ実行コードを持つ`0e9d7e8`のWebUIを
  Chromium 145.0.7632.6で撮影しました。新しいDB・保存先と合成TSを使い、実APIでスキャン・視聴・
  短時間録画・録画再生を行っています。既存サービスや実機を使用していません。
  ブラウザーの配色をdark、表示領域を1280×960に設定し、ページ全体を保存しました。
  日本語・配色・表示内容を目視確認しました。合成テスト映像のため、ぼかしなどの画像加工はしていません。
  PNGのチャンクはIHDR/IDAT/IENDのみで、EXIFやテキストmetadataはありません。
  日本語の描画にはNoto Sans CJK（SIL Open Font License 1.1）を使用しています。
  フォントの実体やブラウザーは同梱せず、描画された画面だけを掲載しています。
- `docs/sensitive-data.md`のworking/history/staged検査は成功しました。
  自動検査は全ての個人情報や素材の権利を判断できないため、画像・設定例・同梱物の内容確認も行っています。

## ビルド入力とローカルイメージの確認

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
これは`c41af0e`の実行コード・Dockerfile・lockfileと、導入検証時のREADMEでビルドしたローカル検証物です。
配布用イメージではなく、同じ実装に対する内容確認と動作確認の記録です。
OS package一覧は`/opt/packages.lock`、通知は`/usr/share/doc/*/copyright`に残ります。
この内容検査の成功を、イメージ配布に必要な対応ソース一式の準備完了とは扱いません。

## 依存物の照合

[依存一覧](dependencies.md)と[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)を基準にしました。

| 対象 | 今回照合した取得物・結果 | 配布の境界 |
|---|---|---|
| Python 28依存 | distribution metadata、実バージョン、同梱licenseを一覧と照合。pathspecはLICENSE本文でMPL-2.0を確認。新しいuv環境でもlockfileどおり取得 | 依存を改変せず、wheel内の表示を保持。第三者wheelそのものをReleaseへ追加しない |
| Vue 3.5.22 / hls.js 1.6.13 | 固定npm tarballとApache-2.0全文を再取得し、manifestの5ファイル全てでSHA-256と実ファイルの完全一致。MIT/Apache-2.0表示を保持 | 同梱するproduction資産とlicenseだけ。Node/npm・ブラウザー実行物は配布しない |
| Hatchling 1.29.0、uv 0.12.18、Gitleaks 8.30.1 | Hatchlingは新規buildで取得。uvは固定digestのimageと既存ホスト、Gitleaksは既存の検査用実行物を使用 | ビルド・検査の補助。ツール自体は同梱しない |
| FFmpeg | 新規デモimage内で6.1.1、APT `7:6.1.1-3ubuntu5`、`--enable-gpl`とlibx264、通知ファイルを確認 | Ubuntuの対応ソースとパッチ、全依存の条件を揃えるまでイメージ/実行物配布は除外 |
| GNU Radioと受信拡張 | 既存固定imageを機器・ネットワークなしで起動しimport確認。GNU Radio `3.10.9.2-1.1ubuntu2`、GSL `2.7.1+dfsg-6ubuntu2`、pybind11 `2.11.1-2`、make `4.3-4.1build2`、通知を照合 | 今回はnative imageを再構築していない。既存の固定ビルド手順と過去の検証を参照 |
| 研究元とgr-isdbt | 固定Gitオブジェクトのlive 12ファイル、基点のbuild入力4ファイルのhash一致。gr-isdbtの2固定コミットのLICENSE/COPYINGはGPL-3.0-or-later | gr-isdbtの条件を研究元独自wrapper全体へ広げない。変換内容・hash・ビルドはreceiver.mdと生成manifestに対応 |
| libaribb25 | cleanな固定コミット`dc1d96a90ea554d8997b238fd6712eccf553cdb3`とApache-2.0本文を照合 | 無改変の別途ローカル利用。実行物とソースの再ビルド同一性は今回未検証 |
| TSDuck | `v3.45-4798`の上流LICENSEとBSD-2-Clause表示を照合。Dockerfile.liveの固定deb/通知hashと対応 | 今回debは再取得していない。ホストの実ツール試験はskip、実機用imageの過去結果と区別 |

固定ソースの取得・変換・ビルド方法は[receiver.md](receiver.md)、
実機wrapper/CASの別途取得とmountは[live-receiver.md](live-receiver.md)にあります。
上流URLだけを対応ソースの提供一式の代わりにはしません。
