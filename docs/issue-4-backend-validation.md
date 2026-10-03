# 段階2バックエンド（#13〜#17）の実装と検証

確認日: 2026-10-04（日本時間）。作業元は段階1のmain `579e7f9`。
独立worktree・`codex/stage2-backend`で実装し、共有checkoutと他担当のファイルは変更していません。
検証対象コミットと最終の必須hookの結果はPR本文に記録します。実機は操作していません。

## 担当Issueと実装

| Issue | 実装・確認 | 実機・後続へ残るもの |
|---|---|---|
| #13 | 共通flockと子へのFD継承、期限、録画終了→HLS/入力子回収→復元結果→保存→排他解放、復元不明ゲート、interrupted/partial、HLS公開停止 | 実際の研究CLIと同じinodeの共有設定、baselineと独立readback、実機停止・復元 |
| #14 | 合成の13/14ch、範囲指定、進捗・中止、未実行/未検出、SQLiteへのサービス保存、別TSの同じservice ID、保存情報からの選局 | RFスキャン、実測TMCC、実放送のARIB局名・リモコン番号の取得 |
| #15 | 同じTSから有限キューでH.264/AAC HLS、セッション別URL、遅い下流・変換失敗・CAS未設定の分類、録画の継続 | 外部CASの接続、実放送の12セグHLS、長時間の遅延・品質、人の視聴 |
| #16 | 単調時計の最大300秒、残り入力時間、手動停止、競合拒否、packet境界、容量・write/flush/close/DB失敗、partial、録画停止後の視聴 | 実時間300秒と放送TSの継続時間・品質は#22 |
| #17 | 一覧、ファイル照合、登録IDによるオリジナル配信、別HLS変換、重複・180秒期限・容量・子回収、再生故障と録画状態の分離 | カード条件、Safari、人の保存再生、実放送での確認 |

#4全体と#18は完了扱いにしません。liveは501、saved_tsの範囲scanも501です。
研究元のwrapperは未確定な再配布条件のまま取り込んでいません。独自CAS・復調器も追加していません。

## 自動試験

`sh scripts/check.sh`でRuff・整形・mypy、pytest、working/history機密検査を実行します。
追加試験では次を確認します。

- 異なるAPI保存先でも同じdevice lockを使う競合、親がFDを閉じても子が保持する排他、
  復元unknown、停止猶予超過、録画終了と復元・解放の順序。
- 時計を制御した299.9秒/300秒の録画、手動停止、残り時間不足、禁止操作、TS区間のbyte一致。
- write/flush/close失敗、空き容量不足、SQLite確定失敗時のrollback、再起動時のpartial、schema移行。
- スキャンの中止・未検出でも保存局を保持し、同じservice IDの別TSを混同しない。
- HLSキュー満杯、出力上限、CAS未設定、プレイリスト更新競合、再起動後の公開停止。
- 録画のpartial・欠損・コーデック失敗・変換期限。再生失敗でオリジナルや録画状態を変更しない。
- Host/Origin/CSRFと安全なファイル配信を維持し、追加POSTにも保護を適用。

独立レビューで、録画close失敗時に後続cleanupが中断する経路、プレイリストとmembersの
更新競合、再起動後の古いHLS公開を修正しました。修正後の限定レビューでも解消を確認しています。

## 合成TSと供給中のA/V

合成TSは`generate-demo.py`で各360秒を一度のFFmpeg実行で生成します。
13/14というラベル、ONID 1、TSID 13/14、service ID 1、MPEG-2/MP2、1 Mbps、
320×180/25fps、音声440/880 Hzです。実際の放送素材は使いません。単純連結・ループはしません。
旧80 MB上限では50 Mbpsの入力を600秒扱えないため、session保存は4 GB、録画は2 GBにしました。
HLS32 MiB、録画再生256 MiB、空き128 MiBの下限と履歴数の上限は有限です。

専用ポート・保存先・Compose projectで実行したコマンド:

```sh
uv sync --locked
uv run --locked python scripts/generate-demo.py
uv run --locked python scripts/generate-demo.py --output data/demo/demo-14.ts --channel 14
# READMEのuv起動コマンドで、専用のSDR_DATA_DIR/SDR_ORIGIN/portを指定
uv run --locked python scripts/smoke-stage2.py --origin http://localhost:18324 --source data/demo/demo.ts
SDR_PORT=18325 SDR_ORIGIN=http://localhost:18325 docker compose -p sdr-dtv-stage2-backend config --quiet
SDR_PORT=18325 SDR_ORIGIN=http://localhost:18325 docker compose -p sdr-dtv-stage2-backend up --build -d --wait
uv run --locked python scripts/smoke-stage2.py --origin http://localhost:18325 --source data/demo/demo.ts
```

uvとComposeの両方で、3チャンネル中2サービスを保存し、TS供給中のHLS A/Vデコード、
8秒録画の期限停止、その後の視聴継続、録画の派生HLS A/Vデコードが成功しました。
初回の観測ではHLS準備までuv 2.354秒、Compose 2.370秒。録画1,000,160 byte、
FFprobeの時刻範囲8.210022秒でした。8秒の書込み区間とmuxの時刻範囲は別の測定値です。
録画区間と入力のbyte一致、ダウンロードhash、変換前後のオリジナル不変を確認しました。
FFmpegで映像と音声を両方指定してデコードし、エラーログがないことも確認しました。

## Chromiumの自動再生確認

`scripts/smoke-browser.py`とPlaywright Python 1.58.0、既存Chromium 145.0.7632.6で、
製品UIに依存せず、同一Originのhls.jsとvideo要素を使いました。アプリに追加依存はありません。
不足したOSライブラリは一時ディレクトリへ展開し、ホストのインストール状態を変更していません。

- 合成入力の供給中にライブ映像262フレームと非ゼロ音声（最大振幅約0.171）を観測。
- 起動直後は最初のsegmentだけで再生したためバッファ待ちがあり、最初の5秒だけを使った
  進行判定は失敗しました。これを無かったことにせず、7秒の起動観測を保存してから別に評価しました。
- その後の5サンプルでは再生時刻が6.275秒から10.277秒へ進み、`liveSyncPosition-currentTime`は
  約−3.005〜−2.993秒でした。負値はhls.jsの同期目標より再生位置が先にある意味で、
  放送時刻からの遅延ではありません。この短い観測だけで長時間の遅延増加を否定しません。
- 録画再生では129フレーム・非ゼロ音声（最大振幅約0.170）と、約4秒の再生時刻進行を確認。
- Web Audioの出力gainは0で、数値によるデコード確認です。人が映像を見て音を聴いた確認ではありません。

ブラウザーの初回起動は共有ライブラリ不足で失敗しました。依存を補った後の成功と区別します。
Safari、人による品質・音ずれ・音切れの確認は未実施です。

## UI担当（#29）・統合検証（#19）への引継ぎ

- APIの具体例、全フィールド、状態・期限・終了理由・復元状態・エラーは[API仕様](api.md)。
  生成されるOpenAPIとPydanticモデルを使い、模擬データの仮定をそのまま接続しないでください。
- Serviceの保存キー`id`をSessionStartの`service_key`へ渡します。整数service IDとは別です。
  選局後は新sessionの`hls.url`を待ち、旧プレイヤーとpollingを破棄します。
- 録画中は切替・scan・二重録画をUIでも無効化し、APIの409も処理します。
  5分録画はsession 600秒で開始しても、入力残量や経過時間次第では拒否されます。
- 再生開始はCSRF付きPOST、状態取得はGET。録画の完了と再生変換の失敗を別に表示します。
  `file_available=false`、partial、`hls.state=failed/interrupted`は正常再生として扱いません。
- `ready_at`はサーバーの配信準備です。UIの`playing`時刻、起動時のバッファ待ち、
  安定後の遅延・音切れを別に採ってください。今回の短いブラウザー試験を#19全体の完了としません。
- 再起動、停止失敗、ディスク不足、DB障害、CAS未設定、別API保存先の排他は回帰試験があります。
  実機では実際の子・readback・共通lockの再取得を追加照合してください。

## 制限・実施していないこと

実機通信、RF受信、設定変更、研究元への書込み、実際の研究CLIとの同時排他試験、外部CAS接続、
実時間300秒の録画、Safari、人の再生確認、製品UIへの接続は未実施です。
Linux以外の親終了通知、可変bitrateのPCR同期、全SI/ARIB文字列も未対応です。
Actionsは無効のためCI未実行。公開設定・ブランチ保護・Issueの完了状態は変更していません。
ここまではPR作成時点の記録です。マージ前の追加確認は末尾に記載します。

## 確定した実装コミットでの最終確認

対象は`ff2e15ba0203613888d3bb9c31247db9d617d1fa`です。後続コミットでは本記録の追記、下記の機密検査テストの環境分離、録画再生の終了処理におけるDB例外の処理を追加しています。
`sh scripts/check.sh`が成功し、Ruff・整形・mypy、pytest **51件**、working/historyの
機密検査を確認しました。既存のStarlette TestClient非推奨警告1件は残ります。
TestClientはサンドボックス内で45秒の上限に達して中断し、通常環境では成功しました。

最終コードで専用Composeを再ビルドし、`smoke-stage2.py`と`smoke-browser.py`を実行しました。
HLS準備は2.366秒、録画の確定まで8.010秒、TSは1,000,160 byte・時刻範囲8.210022秒でした。
TSの区間一致・変換前後の不変、ライブ/録画のA/Vデコードも成功しました。
Chromiumではライブ261フレーム、録画129フレーム、両方の音声サンプルを確認しています。
安定後の5サンプルでライブ再生時刻は約4秒進み、同期目標との差は約−2.994〜−2.991秒でした。

合成sessionと300秒指定の録画が稼働中に、専用コンテナだけを次の操作で異常終了・再起動しました。

```sh
docker compose -p sdr-dtv-stage2-backend kill -s SIGKILL app
SDR_PORT=18325 SDR_ORIGIN=http://localhost:18325 \
  docker compose -p sdr-dtv-stage2-backend up -d --wait
```

再起動後のAPIでsessionと録画が`interrupted/partial/server_restart`、保存済みbyte数が非ゼロ、
HLS URLが無効・artifactが404、録画の履歴件数が不変、自動開始なしを確認しました。
その後の明示的な1秒sessionは正常終了し、残存lockで永続的に塞がれていないことも確認しました。
これは合成データを使った異常終了試験であり、実機の復元試験ではありません。
既存の`smoke-api.py`も新しい360秒入力に対して8秒のsession期限で成功しました。


### pre-push hookで判明した既存テストの不具合

通常実行の共通検査は成功しましたが、最初のpushはhook内の機密検査テストで失敗し、
送信されませんでした。hookの`GIT_DIR`等が一時リポジトリ用のsubprocessへ継承され、
テストのGit操作が作業元へ向いていました。テストが作った未公開の合成コミット・indexと
設定変更だけを直前の確認済み状態へ戻し、共有mainと実装ファイルが不変であることを確認しました。

`tests/test_sensitive_scan.py`で一時リポジトリのGit操作・scan subprocessからGit環境変数を
除外しました。検出ルールやhookは緩和していません。hookと同じ環境変数を明示した再検証で、
10件の機密検査試験が成功し、HEAD・index・共有Git設定が変わらないことを確認しました。
最終pushでは必須hookを改めて実行し、結果をPR本文に記録します。

### 録画再生の終了処理におけるDB例外

再生変換の期限到達後、派生artifactをpartialにするDB書込みが失敗しても、
再生ジョブの終了時刻・`database_error/storage`・URL無効化をメモリー上に記録し、
DBが書込み可能なら保存するようにしました。保存先の異常も開始拒否へ反映します。
この経路の例外注入試験を追加し、録画の状態とオリジナルTSが変わらないことも確認します。
Compose・ブラウザーの上記結果は`ff2e15b`時点です。この追加変更はDB障害時の終了処理に限られ、
共通検査の対象は52件になります。最終コミットと検査結果はPR本文に記録します。

## PR #31のマージ前レビュー（2026-10-04）

GitHubのレビューで指摘された2件を修正しました。録画開始時は録画ファイルだけでなく、
同じ時間に増えるセッションのTS、HLS、稼働中の録画再生の容量も見積もります。
Mediaの終了済みフラグは、子プロセスと補助タスクの回収が終わってから設定します。
回収の待機中にキャンセルされた場合も、再度の終了処理で回収を完了できます。

開始に必要な空き容量の境界（HLSあり・なし）と、子回収の待機中のキャンセルからの
再実行を回帰試験に追加しました。正常な変換の仕様は変更していません。
逆順の操作でも録画の必要容量を消費しないよう、録画中に再生を開始するときは
録画とセッション保存の残り増加量も確認します。この容量境界も回帰試験で確認します。
独立レビューでは、既知の2件以外に追加のマージ阻害問題は見つかりませんでした。
最終のチェック結果とコミットはPR本文に記録します。実機、Safari、人による視聴確認は未実行です。
