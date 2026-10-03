# Issue #3の実装と検証記録

本書は段階1・PR #30時点の検証記録です。8秒デモや当時の未実装APIの記載は履歴として保持します。
現在の機能は[API仕様](api.md)、後続の実装・検証は[段階2の記録](issue-4-backend-validation.md)、
現在の作業入口は[#29への引継ぎ](issue-29-handoff.md)を参照してください。

確認日: 2026-10-04（日本時間）。対象: PR #1を統合したmain
`ef240803845cdad23c3f76216bdc5e7c7b2fccef`からの本PRの変更。
最終的な検証対象コミットとpush前hookの結果はPR本文に記載します。

## Issueとの対応

| Issue | 実装・受け入れ | 残る範囲 |
|---|---|---|
| #3 | API・デモ・操作保護・固定native診断・起動環境を本PRにまとめた | PR #30のレビュー・統合後に子Issueの完了条件と照合する |
| #7 | PR #1のmain統合をGitHubとローカルHEADで照合。既存hook、Python 3.12、公開前検査を維持 | Actionsは無効。公開設定・ブランチ保護も変更しない |
| #8 | 固定研究コミットの別取得、hash付きビルド入力抽出、native再構築とimport/CLI診断、合成IQでの処理の接続・EOF、TS仕様 | 正常な放送波の復調は今回の検査対象外。研究元自作wrapperの再配布条件、liveアダプターと機器での検証は後続 |
| #9 | FastAPI/Pydantic・SQLite・共通アダプター・状態と期限・OpenAPI・後続APIの予約 | scan/HLS/recordingは501または未実装。UIとの最終照合は#18/#29 |
| #10 | 自作映像/音声の生成、有限TSの子ワーカー、診断API、故障試験 | 実機に対する通信・カード診断はnot_checked。自動RXしない |
| #11 | Host/Origin/CSRF、登録IDだけのTS/HLS配信、リンク・領域外参照の拒否 | HLS生成・ブラウザーでの再生は後続 |
| #12 | Compose/uv起動、非root・localhost公開、volume保持、停止時の子回収 | native/live環境の単一imageへの統合、別OSでの実機利用 |

## 成功した確認

- `uv sync --locked`でPython 3.12の依存を導入。
- `uv run --locked python scripts/generate-demo.py`で映像testsrc2、音声440 Hz、
  320×180/25fps、MPEG-2/MP2、muxrate 1 Mbps、8秒の合成TSを生成。
- `ffprobe`で映像・音声と8.010022秒を確認し、`ffmpeg -i data/demo/demo.ts
  -map 0:v:0 -map 0:a:0 -f null -`で両ストリームをエラーなくデコード。
  人による視聴や実機受信は意味しない。
- 合成TSは999,220 byte、SHA-256
  `9a6b7660879fb024badca5345e84a5e4e2b8581d516f4d57ad5fe909a5bf165f`。
  条件とhashはGit外の生成manifestにも記録。
- `uv run --locked uvicorn … --workers 1`で起動し、`scripts/smoke-api.py`で
  UI入口・診断・非同期開始・EOF・TSダウンロード・サイズ/hashの一致を確認。
- `docker compose config --quiet`、`build`、`up -d --wait`、`down`。
  検証用の独立したCompose project/ポート/volumeを使用。
- Composeの同じsmoke、再起動後の完了session・TS保持、稼働中SIGTERM後の
  `failed/partial/server_shutdown`を確認。EOF後の子ワーカー数は0。
- Dockerの実設定: UID/GID 10001、read-only、capabilities削除、
  公開127.0.0.1のみ、停止猶予35秒。デバイス、Docker socket、特権指定なし。
- 固定研究コミットから最小の4ビルド入力を取り出してbase/wideband imageを再構築。
  `--network none --read-only --cap-drop ALL --user 10001:10001`で
  Python 3.12.3 / GNU Radio 3.10.9.2 / hlfecwidebandのimportを確認。
  同じ条件で固定`wideband/file_receiver.py --help`が正常終了。
  研究元の既存checkoutや実機は変更・操作していない。
- Host・Origin・トークン、ファイル配信、停止/期限とDB整合性を独立レビュー。
  指摘されたDB保存失敗時の誤った完了表示を修正し、修正後のレビューでも確認。
  SQLiteのartifact登録後の失敗を注入し、transactionのrollbackを追加確認。

## 最終の共通検査

`sh scripts/check.sh`でRuff・整形・mypy・pytest・working/history機密検査を実行します。
commit時にはstagedの機密検査とRuff、push前には同スクリプトがhookから実行されます。
本PRの最終実装を含む作業ツリーに対して成功しました。Ruff・整形・mypy、pytest 29件
（API等19件、既存の機密検査10件）、working/historyの機密検査が成功しています。
`git diff --check`、hookと共通スクリプトのシェル構文、同梱JS/LICENSEのmanifest hash照合も
成功しました。すでに成功した個別試験を独立レビュー側で重複実行していません。
commit/push時の必須hookは無効化せず実行します。

## 失敗・制限・未実行

- サンドボックス内のTestClient実行は進行せず中断。スレッド・子プロセスが動く
  通常環境で上限を付けた同じ試験は成功。サンドボックスの停止を試験成功に含めない。
- HTTPX 0.28.1のTestClientにはStarletteから非推奨警告がある。検証は成功しており、
  警告を隠していない。互換性を確認した依存更新は別途行う。
- nativeの一部ビルド層はDocker cacheを再利用。固定ファイルhashと起動診断は確認したが、
  全APT/Git配布物の新規ダウンロードやbit-for-bitの再現を証明するものではない。
- 研究元コードのうち独自のPython wrapper/変換の配布条件は未確定。
  本PRではGit・デモimageへ取り込まず、権限がある人のローカル取得・診断だけを案内。
- GitHub Actionsは無効なのでCI未実行。設定変更とimage/Releaseの配布は行わない。
- ブラウザーによる画面操作、HLS再生、Chromium/SafariのA/V、実機の到達性・受信・
  RX復元・他局・300秒録画は未実施。合成APIの成功をこれらの達成とは記載しない。

## PR #30のレビューで追加した確認（2026-10-04）

- Host/Origin/CSRFとIDによるファイル配信を独立レビューし、修正を要する指摘なし。
  セッション・DB・ワーカーと起動手順は主担当が確認した。
- 初期コミット`98af627`で`sh scripts/check.sh`が成功。pytest 29件、警告1件。
  Actionsが無効であることをGitHubの設定でも確認した。
- native環境のimport/`--help`だけでは処理の組立てを検証できないため、
  `cf32_le`のゼロ値1,048,576標本（8,388,608 byte）をB階層の処理へ入力した。
  読み取り専用コンテナではFFTWのロックファイルを作れず、起動に失敗した。
- `scripts/smoke-receiver.py`を追加し、コンテナの子プロセスの書き込み先を
  有界tmpfsへ切り替えた。同じ入力で起動・EOF・結果ファイル生成を確認した。
  有効TMCCは0件、TSは0 byte、受信処理は`unverified_no_valid_tmcc`と終了コード1を返す。
  この期待結果を確認した検査スクリプト自体は成功する。無信号の検査であり、
  TS回復や映像音声、実機での受信の成功を意味しない。
- 入力SHA-256: `2daeb1f36095b44b318410b3f4e8b5d989dcc7bb023d1426c492dab0a3053e74`。
  既存の固定imageを利用し、準備済みPythonソースのmanifest hashも照合した。
  ネットワーク・実機を接続せず、非root・read-only・512 MiBメモリー・1 CPU・
  30秒上限で実行した。追加の再ビルドや有効なISDB-T波形からのTS回復は未実施。
- 再現手順とFFTWの書き込み先の根拠を`docs/receiver.md`へ追記した。
  修正後の最終コミットと共通検査の結果はPR #30に記録する。
- GitHubの自動レビューで、ファイル入力の子ワーカーを強制終了した場合や停止時に
  非zeroで終了した場合も正常完了として扱う問題が指摘された。
  停止の失敗を`failed/partial/worker_failed`として保存し、artifactを登録しないよう修正した。
  ファイル入力に機器の復元は不要なので`restore=not_required`を維持する。
  stdinのEOF後に異常終了する子と、EOFを無視して強制終了が必要になる子を使い、
  子の回収・状態・DB・artifact非公開を確認する回帰試験を追加した。
  入力処理中に先に異常終了した場合は、元の故障段階を停止処理で上書きしないことも確認する。
