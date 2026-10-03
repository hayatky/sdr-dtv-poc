# Issue #29: WebUIを実API・HLSへ接続するための引継ぎ

更新日: 2026-10-04（日本時間）。対象はPR #31の段階2バックエンドとPR #32のWebUIです。
PR #31・#32はmainの`a90eedc`までに統合済みで、先行Issue #28・#14・#17も完了しています。
最新のmainから作業を始め、開始前にstatus・HEAD・remote・既存変更を確認してください。
本書とAPI仕様を先に読み、画面設計内の仮のJSONを実APIの応答とは扱わないでください。

## 達成済みの範囲と担当

- #13〜#17: 合成TSのスキャン、保存サービスからの選局、供給中HLS、最大300秒の録画、
  一覧・ダウンロード・派生HLS再生。根拠は[バックエンド検証記録](issue-4-backend-validation.md)。
- #27・#28: 三つのタブ、状態・失敗の案内、模擬データによる操作。
  根拠は[画面設計と検証記録](webui-design.md)。今の画面はAPIを呼ばず、A/Vも再生しません。
- #29: `static/api.js`のHTTP実装、`static/player.js`、`static/app.js`の状態反映と画面操作の確認。
  新規JSの配信には`app.py`の`UI_FILES`への追加が必要です。
- #18・#4は未完了です。全体の異常経路は#19、実機での受信・300秒録画は#21・#22、
  実機を使ったブラウザー確認は#23へ引き継ぎます。live入力は501、外部CASは未接続です。

Node.js/npm・ビルド・CDNは追加せず、同梱Vue 3.5.22とhls.js 1.6.13を使います。
受信処理の複製・実機操作・Actions有効化はこの接続作業に含めません。

## 仮のUI応答と実APIの対応

実装の基準は[API仕様](api.md)、`models.py`、起動したアプリの`/openapi.json`です。

| 現在のUIの仮定 | 実APIと接続時の対応 |
|---|---|
| `GET /api/status` | 未実装。sessions/scans/recordingsの一覧から動作中IDを取得可能。復元ゲート全体・保存先の正確な空き容量・サーバーが判定する残り入力時間は一覧だけでは得られない。下記の扱いを確認する |
| 選局の`service_ref` | `POST /api/sessions`へServiceの`id`を`service_key`として送る。`duration_seconds:600, enable_hls:true`を明示する（省略時は60秒） |
| Sessionの`service_name/physical_channel/service_ref` | `session.service.name/physical_channel/id`。直接入力で開始した場合はserviceがnull。放送の整数IDは`selected_service_id` |
| `health.signal/ts/cas/hls` | 同じ集約項目はない。`stage`・`bytes_received/ts_started_at`・`hls.state/error_stage/error_code`で判定し、RFやカードの未確認を正常と推定しない |
| `remaining_seconds` | Sessionでは未提供。`deadline_at`からの表示は推定に限り、ファイルの残量不足は`insufficient_session_time`で案内。UIの時計を停止制御に使わない |
| Serviceの`remote_control_key` | `remote_control_key_id`。取得できない値はnull。`detected_at`と`current_reception:false`は保存した過去の観測 |
| Scanの`done_channels/found_services/saved` | `completed_channels/total_channels`、results内の`service_ids`。`saved`はない。中止・未検出・失敗を含む終了時にサービス一覧を再取得する。検出した局は順次保存される |
| スキャン結果の`stage/services` | `state: not_run/not_detected/detected`と`service_ids`。局の詳細は`GET /api/services`で解決する |
| スキャン入力 | `input_kind:synthetic`を明示し、bootstrapの`scan_presets.synthetic`（13・14）を初期候補にする。live・saved_tsの範囲スキャンは未対応 |
| Recordingの`service_name/service_id/size_bytes` | `service.name`、`selected_service_id`、`bytes_written`。serviceはnullの場合もある |
| `playback_available/download_available` | `state=completed`、`partial=false`、`file_available`、`download_url`を照合。partialや欠損のダウンロード・再生は409 |
| 録画中の受信停止 | 録画は`failed/partial/source_ended`。録画だけの手動停止は`completed/requested`。先に受信を止めた結果を正常録画と表示しない |
| 録画の再生 | CSRF付きPOST `/api/recordings/{id}/playback`で開始し、GETで状態取得。Playbackの`url`を使用。失敗でも元のRecordingの状態を変えない |
| 仮の`recording_active/scan_active/recording_time_insufficient` | 実APIは`recording_busy/scan_busy/insufficient_session_time`。`device_busy`、`restore_unverified`、`playback_busy`、`storage_full`等もAPI仕様に従う |

全体状態APIを追加する場合は、Managerが持つ動作中ID・復元ゲート・保存先異常と、
サーバーが判定する残り時間だけを返す小さな読み取りAPIとしてバックエンド担当へ提案してください。
現行APIだけで進める場合は、一覧とdiagnosticsで判明する範囲だけを表示し、
復元未確認や録画の可否を推測で許可せず、POSTの拒否理由を必ず反映します。
この不足を埋めるためにUIの仮の応答一式をサーバーへ移植する必要はありません。

## 実装と確認の順序

1. HTTPモードを明示的に追加し、bootstrapでCSRFを取得する。変更要求に`X-CSRF-Token`を付ける。
   失敗時に模擬データへ自動切替しない。通信結果が不明な開始要求を再送する際は同じrequest_idを使う。
   サーバー再起動による403ではbootstrapを更新して状態を照合し、受信を自動再開しない。
2. スキャン→保存局一覧→選局を接続する。APIの選局処理が旧sessionの回収を待つ。
   旧応答をIDと操作世代で破棄し、過去の局名・HLS URLを新sessionへ混ぜない。
3. 安定したvideo要素を置き、native HLSまたは同梱hls.jsで再生する。停止・切替・画面破棄で
   HLSインスタンスとイベントを解除する。pagehide中に進行中pollingのfinallyからタイマーを再開しない。
4. 録画開始・停止、一覧、ダウンロード、派生再生を接続する。300秒の停止はサーバーが行う。
   再生要求は同じ録画IDなら既存ジョブを返すので、失敗を無限に再試行しない。
5. 再読込・複数タブ・通信断からの復帰で、一覧から動作中の状態を表示するだけにする。
   画面を開く操作で新規session・scan・recordingを作らない。
6. `started_at/ts_started_at/hls.ready_at`とvideoの`playing`を同じsession IDで対応付ける。
   自動再生拒否は利用者の再生操作へ案内する。A/V、初回再生、安定後の時刻進行は別々に確認する。

## 起動と検証

以下では専用ディレクトリへ各360秒の合成TSを初回だけ生成します。出力TSまたは同名JSONが
既にあると生成スクリプトはエラーで終了します。既存JSONの`duration_seconds`と`physical_channel_label`を確認して
生成を省略するか、両チャンネルを別の新しいディレクトリへ生成してください。
他担当と保存先・ポートを共有せず、実機も使用しません。画面だけのデモは生成不要ですが、
API・HLSを確認するときは2種類のTSとFFmpegが必要です。

```sh
uv sync --locked
uv run --locked python scripts/generate-demo.py --output data/issue-29-input/demo.ts
uv run --locked python scripts/generate-demo.py --output data/issue-29-input/demo-14.ts --channel 14
SDR_DEMO_PATH=data/issue-29-input/demo.ts \
  SDR_DATA_DIR=data/issue-29 SDR_ORIGIN=http://localhost:18329 \
  uv run --locked uvicorn sdr_dtv_poc.app:create_app --factory \
  --host 127.0.0.1 --port 18329 --workers 1 --no-proxy-headers --no-access-log \
  --timeout-graceful-shutdown 10
```

別ターミナルから、UI操作前のバックエンド確認ができます。これらもsessionと録画を作るため、
UIの確認とは順番に実行してください。

```sh
uv run --locked python scripts/smoke-stage2.py --origin http://localhost:18329 --source data/issue-29-input/demo.ts
sh scripts/check.sh
```

画面は`http://localhost:18329/`で開きます。接続実装前は模擬データのままです。
合成13chの`demo.ts`と14chの`demo-14.ts`は同じディレクトリに置きます。
サーバーの`SDR_DEMO_PATH`と検査の`--source`には、必ず同じ13chのTSを指定してください。
入力が360秒でも、録画開始が遅れると305秒の残量を確保できません。5分録画を試す際は
選局直後に開始し、残量不足は`insufficient_session_time`として案内します。
Ctrl+Cでサーバーを停止し、子プロセスの終了を待ちます。保存データは自動削除しません。

ブラウザーでは一連の操作、供給中HLSの映像・音声、録画再生・ダウンロード、切替・停止、
再読込・複数タブ、通信断・API再起動、拒否された操作、partial、旧イベントの破棄を確認します。
既存の`smoke-browser.py`はAPI側HLSの機械的確認です。WebUI経由の操作確認を代替しません。
Safariを実行できなければnative HLS経路の実装と未確認環境を区別し、#19・#23へ渡します。
対象コミット・コマンド・成功/失敗/未実行を記録し、実機受信・人による視聴を推定しません。
Actionsは無効のままです。検証記録には個人のパス、生ログ、放送素材を含めません。
