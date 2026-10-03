# Issue #3の実装と検証記録

確認日: 2026-10-04（日本時間）。対象: PR #1を統合したmain
`ef240803845cdad23c3f76216bdc5e7c7b2fccef`からの本PRの変更。
最終的な検証対象コミットとpush前hookの結果はPR本文に記載します。

## Issueとの対応

| Issue | 実装・受け入れ | 残る範囲 |
|---|---|---|
| #3 | API・デモ・操作保護・固定native診断・起動環境を本PRにまとめた | レビューとmainへの統合は管理者。今回はマージしない |
| #7 | PR #1のmain統合をGitHubとローカルHEADで照合。既存hook、Python 3.12、公開前検査を維持 | Actionsは無効。公開設定・ブランチ保護も変更しない |
| #8 | 固定研究コミットの別取得、hash付きビルド入力抽出、native再構築とimport/CLI診断、TS仕様 | 研究元自作wrapperの再配布条件、liveアダプターと機器での検証 |
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
- GitHub Actionsは無効なのでCI未実行。設定変更、image/Releaseの配布、PRマージはしない。
- ブラウザーによる画面操作、HLS再生、Chromium/SafariのA/V、実機の到達性・受信・
  RX復元・他局・300秒録画は未実施。合成APIの成功をこれらの達成とは記載しない。
