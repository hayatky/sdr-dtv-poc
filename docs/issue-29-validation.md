# Issue #29: WebUIとAPI・HLSの接続確認

確認日: 2026-10-04（日本時間）。作業元はmain `b8e9259`。
独立worktree・`codex/issue-29-api-hls`で実装し、共有checkoutは変更していません。
実装の接点・残る範囲は[引継ぎ](issue-29-handoff.md)を参照してください。
確定した検証対象コミットと最終結果は本書末尾とPR本文へ記録します。

## 環境・再現手順

Ubuntu 24.04 / x86_64、Python 3.12.3、FFmpeg 6.1.1-3ubuntu5、
Playwright Python 1.58.0、Chromium 145.0.7632.6で確認します。
Chromiumの不足ライブラリと日本語フォントはGit外の一時ディレクトリを使いました。
アプリの依存・ホストのインストール状態は変更していません。

合成TSは引継ぎの手順で13chの`demo.ts`と14chの`demo-14.ts`を同じディレクトリに生成します。
各360秒、1 Mbps、320×180/25fps、MPEG-2/MP2、440/880 Hzの自作映像・音声です。
既存のTSやJSONを上書きしません。実際の放送素材は使いません。

```sh
uv sync --locked
uv run --locked python scripts/generate-demo.py --output data/issue-29-input/demo.ts
uv run --locked python scripts/generate-demo.py --output data/issue-29-input/demo-14.ts --channel 14
# 実行ごとに新しいdata-dirを指定する。専用ポートを空けておく。
uv run --no-project --python 3.12 --with playwright==1.58.0 \
  python scripts/smoke-webui.py --chromium /path/to/chromium \
  --source data/issue-29-input/demo.ts --data-dir data/ui-validation \
  --port 18330
sh scripts/check.sh
```

`smoke-webui.py`は専用APIを起動し、`SDR_DEMO_PATH`を`--source`と一致させます。
保存先が既存の場合やポートが使用中の場合は拒否します。自分で起動したAPIだけを停止し、
検証で作ったデータは削除しません。`--images`を指定すれば合成画面をGit外へ保存できます。
Playwrightは製品依存ではなく、通常のpytestには含めていません。
`scripts/smoke-browser.py`とは別に、製品UIのボタン・タブ・リンクを使う検証です。

## UI経由で確認する範囲

| 対象 | 検証方法 |
|---|---|
| 診断・スキャン | 診断で受信を開始しないこと、bootstrapの13・14ch、進捗と2サービス、中止後の保存局保持 |
| 保存局の選局 | Service.id、600秒・HLS有効の要求、別チャンネルの選局、同じsession IDで時刻を対応付ける |
| HLS | 供給中の映像フレーム、非ゼロ音声、初回playing、安定後の5サンプル、videoのDOM同一性 |
| 録画 | 300秒指定を短時間で手動停止、停止後の視聴継続、録画中の選局禁止、派生再生 |
| ダウンロード | UIのリンクから取得したbyte数とSHA-256が、入力のsource_offset_bytesからの同一区間と一致 |
| 途中終了 | 受信を先に停止するとfailed/partial/source_ended、再生・ダウンロードを無効化 |
| 再読込・別タブ | 一覧取得だけでsession・録画が増えないこと |
| 通信断・応答喪失 | offlineから読取りで回復、受理後に失った応答をrequest_idで回収し、停止後の再選局で新規IDになること |
| 拒否理由 | insufficient_session_time、storage_full、recording_busyの409応答を注入してUIの案内を確認 |
| API再起動 | 専用APIを強制終了・再起動しinterrupted/partial/server_restart、旧CSRFの403、bootstrap更新後も自動開始しないこと |
| ページの寿命 | 読取りを保留したままpagehide、遅い終了処理からpollingが再開しないこと、pageshowで一つの読取りループが再開すること |
| プレイヤーの回帰 | 制御した標準HLS/hls.jsで自動再生拒否、旧イベント・旧Promise、同じ対象での非再生成、終了時のdestroy/source解放 |
| 表示 | Chromiumで実画面を確認し、375pxでも横にはみ出さないこと |

拒否理由の注入はUIの試験であり、実際にディスクを満杯にした結果ではありません。
300秒の停止制御は既存バックエンド試験を使い、今回の短い録画を実時間300秒の結果とは扱いません。
Safari、実機、外部CAS、人による音声の聴取・品質判定は未実施です。

## 実装中に見つかった問題と修正

- 終了済みsessionの取得ごとに録画プレイヤーまで再生成される問題を修正しました。
  受信側の遷移と録画再生の対象を分離しています。
- 再生開始の通信失敗後にGET playbackの404でoffline表示が残る問題を修正しました。
  基礎APIの接続成功と、再生の未開始を分けています。
- 結果不明の開始を一覧で回収した後もrequest_idが残る問題を修正しました。
  一覧中のrequest_idを確認して保留を解消します。
- Chromiumが標準HLSを`maybe`と申告し、供給中HLSで非対応エラーを返しました。
  このエラー時だけ一度hls.jsへ切り替えます。失敗を無条件に再試行しません。
  録画HLSでは同じChromiumの標準経路でも再生できました。Safariの結果ではありません。
- ページや通信の復帰では、選択中の録画Playbackも取得してからプレイヤーを再接続します。

初期の検証スクリプトはCSP下の待機式、CSSの装飾を含むボタン名、応答注入の引数で失敗しました。
音声測定用AudioContextを早く閉じて再生時刻へ影響させた箇所も修正しました。
これらの失敗した試行と、修正後の成功を区別します。CSPや本体の音声設定は緩和していません。

## 先行した機械的A/V確認

実装中の合成試験では、供給中288フレーム・音声最大振幅約0.131、安定後の5サンプルで
7.347→11.355秒の進行を確認しました。同じsessionの開始からplayingまで約3.255秒、
HLS準備まで約2.342秒でした。同期目標との差は約−2.997〜−2.993秒です。
これはhls.jsの同期目標との差で、放送時刻からの絶対遅延ではありません。
録画再生は159フレーム・音声最大振幅約0.131、2.208→6.216秒の進行、
ダウンロード2,070,068 byteの入力区間一致を確認しました。

Web Audioの測定出力gainは0です。製品UIは音声を無効にしませんが、この試験は数値での
A/Vデコード確認であり、人が映像を見て音を聴いた確認ではありません。
スクリーンショットによる画面確認と音声の聴取も別です。
最終コミットの結果は以下へ追記し、この途中状態の値を最終結果に読み替えません。

## Composeと回帰試験

専用projectで次を実施し、build・起動・health・静的ファイル配信・CSP維持・自動sessionなし・停止を確認しました。
このCompose確認は標準HLSのフォールバック修正後で、後続の模擬モードと復帰順序の小修正前です。
通常のAPI・Docker構成には変更がありません。volumeは保持しました。

```sh
SDR_PORT=18331 SDR_ORIGIN=http://localhost:18331 \
  docker compose -p sdr-dtv-issue29 config --quiet
SDR_PORT=18331 SDR_ORIGIN=http://localhost:18331 \
  docker compose -p sdr-dtv-issue29 up --build -d --wait
# /、api.js、player.js、app.js、hls.min.js、OpenAPIの200とCSPを確認
SDR_PORT=18331 SDR_ORIGIN=http://localhost:18331 \
  docker compose -p sdr-dtv-issue29 down
```

既存のバックエンド56件はPR #31・#32の記録と区別し、今回の変更を含む状態で共通検査を実行します。
Actionsは無効のままでCIは未実行です。検証済みを意味するCI checkはありません。

## 引継ぎ

#19へシステム全体の異常・資源・長時間試験、#21・#22へ実機の受信・300秒録画、
#23へSafari、実機を使うブラウザー確認、人によるA/V・音ずれ・音切れ確認を引き継ぎます。
短い機械的確認から長時間の安定性や実機互換性は主張しません。
研究元、機器、CASの実装、公開設定、Actions、ブランチ保護は変更していません。

## 共通検査の結果

この変更を含む状態で`sh scripts/check.sh`が成功しました。
Ruff・整形・mypy、pytest **56件**、working/historyの機密検査を確認しました。
Starlette TestClientの既存の非推奨警告1件は残ります。
最初のサンドボックス内実行は最初のAPI試験で進行せず、中断しました。
通常の実行環境で実行し直した上記結果と区別しています。
コミット前には追加対象の内容、staged diff、機密検査を確認します。

## 確定した実装での最終確認

検証対象は **`3e7cd7eaca40d41b8408ed49127dd259577faf20`** です。
上記の`smoke-webui.py`を新しい保存先・専用ポートで実行し、全項目が成功しました。
以降のコミットはこの結果の記録だけで、実装・検証スクリプトは同じです。

- 供給中HLS: 288フレーム、非ゼロ音声の最大振幅約0.131、安定後の5サンプルで
  再生時刻7.347→11.355秒。同じsessionの開始から最初のTSは約0.03秒、
  HLS準備までは2.341秒、最初のplayingは3.259秒でした。
- hls.jsの同期目標との差は約−2.989〜−2.987秒でした。供給中は標準HLSの
  非対応エラーからhls.jsへ一度切り替わり、その後のA/Vを確認しました。
- 録画再生: Chromiumの標準HLS経路で159フレーム、音声最大振幅約0.131、
  2.208→6.211秒の進行を確認しました。再生要求からplayingまでは1.038秒でした。
- ダウンロード: 2,070,068 byteが入力の同じ録画区間とSHA-256まで一致しました。
- 診断、スキャンと中止、局の切替、録画の手動停止後の視聴継続、録画再生、
  再読込、別タブ、通信断、受信先行停止のpartialが成功しました。
- 受理された開始応答の喪失とrequest_idの回収、3種の409、専用APIの強制終了・再起動と
  旧CSRFの403、pagehide/pageshowの試験が成功しました。
- 再生POSTの通信失敗とGETの404から接続状態が回復すること、再生変換のCAS失敗を
  録画自体の失敗にしないこと、同じ失敗ジョブへPOSTし続けないことを注入試験で確認しました。
- `tests/webui-lifecycle.js`の通信、標準HLS/hls.jsの終了処理・旧イベント・自動再生拒否が成功しました。
  標準HLSのイベントを制御した試験はSafariの検証ではありません。
- 実画面の合成映像をChromiumの画像で確認しました。375px幅でも横へのはみ出しはありません。
  人による音声の聴取、音ずれ・長時間品質、Safari、実機は未実施です。

stagedの機密検査、コミット前hook、差分の内容確認も成功しました。
push前hookで最終HEADに対する共通検査を実行し、結果をPR本文へ記録します。
