# API・状態・WebUIとの接点

段階2のバックエンドは、段階1のmainから単独で起動できます。実装の仕様は
`src/sdr_dtv_poc/models.py`と`GET /openapi.json`です。スキャン・HLS・録画・録画再生は
合成入力で利用できます。`live`は501で、機器を操作しません。`saved_ts`は管理者が
登録した入力の視聴・録画に対応し、範囲スキャンには未対応です。

`static/`とWebUIの設計は別担当です。本書のAPIは実装済みですが、製品UIとの接続は#29、
一連の統合検証は#19です。UIの模擬データの項目が、本書とOpenAPIにない場合は未合意です。

## 操作の保護とID

- `GET /api/bootstrap`で起動単位の`csrf_token`、`mode=synthetic_backend`、
  `live_available=false`、`scan_available/hls_available/recording_available=true`を取得します。
  プリセットは`scan_presets.synthetic=[13,14]`、`uhf=[13,...,52]`です。
- 変更操作には`Origin`と`X-CSRF-Token`を付けます。Hostは設定したOriginのauthorityと
  完全一致が必要です。CORS・転送Hostは使いません。CLIはOriginも明示します。
- 開始は202とUUIDを返します。同じ入力と`request_id`の再送は同じIDを返し、異なる入力なら
  `409 request_id_reused`です。停止は終端後も冪等です。再生の開始は録画IDで重複を抑止します。
- session、scan、recording、playback、artifactは別のUUIDです。サービスの`id`は保存キーで、
  放送の整数`service_id`とは異なります。任意パス・URL・コマンドをAPIへ渡す機能はありません。
- エラーは`{"code":"recording_busy","message":"Operation unavailable"}`です。
  例外の全文や要求に含まれる秘密は返しません。未知UUIDは404、不正な入力は422です。
- 局名はVueの補間か`textContent`で表示し、HTMLとして解釈しません。

## 実装したAPI

| 操作 | 応答・用途 |
|---|---|
| `GET /api/health`、`/api/bootstrap` | 起動・機能・CSRF。受信成功の証明ではない |
| `GET /api/diagnostics?input_kind=synthetic` | 保存先、FFmpeg、入力、native・ボード・カードを別々に表示。デバイスI/Oなし |
| `POST /api/scans` | 202 Scan。idle時だけ開始 |
| `GET /api/scans`、`/api/scans/{id}` | 履歴・進捗・チャンネルごとの結果 |
| `POST /api/scans/{id}/stop` | 202 Scan。現在のprobeを回収して終了 |
| `GET /api/services` | SQLiteに保存したServiceの一覧。未スキャンは空配列 |
| `POST /api/sessions` | 202 Session。`service_key`指定時は旧sessionの回収後に選局 |
| `GET /api/sessions`、`/api/sessions/{id}` | 状態・HLS URL・復元状態・履歴 |
| `POST /api/sessions/{id}/stop` | 202 Session。録画終了と子回収を進める |
| `POST /api/recordings` | 202 Recording。受信中のTSを分岐して保存 |
| `GET /api/recordings`、`/api/recordings/{id}` | 履歴・進捗・ファイルの有無 |
| `POST /api/recordings/{id}/stop` | 202 Recording。録画だけを停止し、視聴は継続 |
| `GET /api/recordings/{id}/download` | 完了したオリジナルTS。partial・欠損は409 |
| `POST /api/recordings/{id}/playback` | 202 Playback。別の派生HLSの生成を開始。同じ録画なら同じジョブ |
| `GET /api/recordings/{id}/playback` | Playbackの状態だけを取得。開始前は404 |
| `GET /api/artifacts/{id}`、`…/download` | 登録したメタデータ・完了TS |
| `GET /api/artifacts/{id}/files/{filename}` | 許可したHLSメンバーだけを配信 |

予約済みだったGETの再生APIで変換を起動しません。副作用は新設したPOSTへ分け、CSRFで保護します。
再生失敗を自動再試行せず、同じ録画へのPOSTは失敗を含む既存ジョブを返します。

## スキャン・サービス・選局（#14）

```json
{"request_id":"11111111-1111-4111-8111-111111111111",
 "input_kind":"synthetic","channels":[13,14,15],"duration_seconds":180}
```

`channels`は13〜52の重複しない配列です。全範囲は13〜52を列挙します。未指定は合成用13・14。
不正な範囲・重複・空配列は422です。合成用の物理ch表記は架空の割当てで、RF検出ではありません。
13は`demo.ts`、14は同じディレクトリの`demo-14.ts`です。その他は`not_detected`になります。
局名・program IDはFFprobeがTSのSIから読んだ値を使い、固定の地域別局名を補いません。

Scanの応答例（UUID・時刻は例示）:

```json
{"id":"22222222-2222-4222-8222-222222222222",
 "request_id":"11111111-1111-4111-8111-111111111111","input_kind":"synthetic",
 "state":"running","started_at":"2026-10-04T00:00:00+00:00",
 "deadline_at":"2026-10-04T00:03:00+00:00","ended_at":null,"end_reason":null,
 "restore":"not_required","current_channel":14,"completed_channels":1,"total_channels":3,
 "elapsed_seconds":0.2,"results":[
   {"physical_channel":13,"frequency_hz":473142857,"state":"detected","service_ids":["33333333-3333-4333-8333-333333333333"]},
   {"physical_channel":14,"frequency_hz":479142857,"state":"not_run","service_ids":[]},
   {"physical_channel":15,"frequency_hz":485142857,"state":"not_run","service_ids":[]}]}
```

`elapsed_seconds`はチャンネル完了時・終了時の経過時間です。正確な残り時間は提供しません。
中止は`completed/end_reason=requested`、時間上限は`completed/deadline`として作業の終了を表し、
全チャンネルの検出成功を意味しません。未実行は`not_run`、今回未検出は`not_detected`です。
起動後の途中記録は`interrupted/server_restart`になります。過去のサービス一覧は削除しません。

Serviceの応答例:

```json
{"id":"33333333-3333-4333-8333-333333333333","name":"Synthetic Test 13",
 "input_kind":"synthetic","physical_channel":13,"frequency_hz":473142857,
 "service_id":1,"original_network_id":1,"transport_stream_id":13,
 "remote_control_key_id":null,"source_id":"demo","detection_stage":"ts_si",
 "detected_at":"2026-10-04T00:00:01+00:00",
 "profile":{"bitrate":1000000,"tmcc":"not_checked"},"current_reception":false}
```

保存キーには入力種別・source ID・物理ch・取得したONID/TSID/service IDを使います。
同じservice IDを持つ別TSを混同しません。取得できないONID/TSID・リモコン番号・局名はnull。
現行のPAT/SDT ID読取りは単一TSパケットに収まるsectionに限り、未対応の場合は不明のままです。
`ts_si`はTS/SI確認であり、RF信号、有効TMCC、実際のA/V再生を意味しません。
`current_reception=false`は保存した観測です。現在の受信状態はSessionで判断します。

中心周波数は`473142857 + (ch - 13) * 6000000` Hzです。
[ARIB STD-B21 4.6英訳の受信周波数](https://www.arib.or.jp/english/html/overview/doc/6-STD-B21v4_6-E1.pdf)
にある13chの473 + 1/7 MHz、14chの479 + 1/7 MHzを整数Hzへ丸めています。
同資料の旧上限62chは採用せず、今回の対象はIssue #14で指定された13〜52chです。
研究元の固定コミットの式とも一致します。実機の周波数読戻し・他ch対応は未検証です。

保存したサービスを選局:

```json
{"request_id":"44444444-4444-4444-8444-444444444444",
 "service_key":"33333333-3333-4333-8333-333333333333","duration_seconds":600,"enable_hls":true}
```

`service_key`があれば入力種別・source・整数service IDは保存情報から解決します。
旧sessionの停止・回収を待ってから新しいIDで開始します。録画中は同じサービスへの切替も409。
従来の`input_kind/source_id/duration_seconds`指定も利用可能で、保存サービスを指定しない場合の
`selected_service_id`は既定1です。HLS不要の診断だけは`enable_hls=false`にできます。

## セッション・HLS（#13・#15）

```text
starting → running → stopping → completed
    └────────┴───────────┴──→ failed
再起動時の未完了記録 → interrupted（自動再開しない）
```

Sessionには従来の`id/request_id/input_kind/source_id/state/stage/started_at/deadline_at/
ended_at/end_reason/restore/partial/bytes_received/artifact_id/error_stage`に加えて、
`service`（Serviceまたはnull）、`selected_service_id`、`ts_started_at`、`hls`が入ります。
HLS準備後の`hls`例:

```json
{"state":"ready","artifact_id":"55555555-5555-4555-8555-555555555555",
 "url":"/api/artifacts/55555555-5555-4555-8555-555555555555/files/index.m3u8",
 "ready_at":"2026-10-04T00:00:03+00:00","error_code":null,"error_stage":null}
```

- 受信は受理から最大600秒。単調時計が期限を守り、UTCの`deadline_at`は表示用です。
  起動・最初のTSは最大15秒、アダプター停止は5秒、FFmpegの終了待ちは別途5秒です。
- 停止順序は録画ファイルの確定またはpartial化、HLS子回収、入力子回収と復元結果の照合、
  session保存、排他解放です。`restore=pending/unknown/failed`は次の開始を拒否します。
- 合成/保存TSの復元は`not_required`。実機復元を成功としません。復元ゲートにHTTP解除はありません。
- `.api.lock`は同じ保存先の二重APIを拒否します。`.device.lock`は別のプロセス間排他です。
  入力ワーカーにFDを継承し、親終了後も子が残る間は保持します。詳細は[受信処理](receiver.md)。
- 合成TSは1 Mbps・360秒、連続した時刻で生成します。保存TSのbitrateによる供給は近似であり、
  可変bitrateに対するPCR同期を保証しません。EOFで終了し、連結・ループで延長しません。
- 一つの入力をオリジナル保存と録画へ渡し、HLSへは最大256×1316 byte（336,896 byte）の
  キューで渡します。満杯またはFFmpeg入力の2秒停滞は`downstream_slow/transport`です。
  欠落を隠してHLSを続けず、変換を停止します。録画に問題がなければ録画は継続します。
- FFmpegは選択programの最初の映像・音声をH.264/AACへ変換します。HLSは2秒segment、
  一覧6件、削除待ち2件。出力32 MiB・ファイル数16を100ms間隔で監視し、超過時に停止します。
  監視間隔中の出力増分はあり得ます。古いsegmentだけを削除し、録画は削除しません。
- HLSの`starting → ready → completed`は配信側の状態で、ブラウザー再生成功とは別です。
  故障は`failed`と`error_code/error_stage`、再起動は`interrupted`になります。
  URLと保存先はsessionごとに別です。失敗・再起動後の未完了HLSは公開を止めます。
- 暗号化されたTS packetを検出すると`cas_unavailable/cas`。CASは未設定で、独自実装や
  自動カード操作はありません。オリジナルTSは加工しません。変換失敗は`converter_failed`等、
  出力不足は`no_playable_stream`、容量不足は`storage_full/storage`として分けます。
- sessionの有界診断保存は最大4,000,000,000 byte。上限50 Mbps×600秒=3.75 GBに対応します。
  空き128 MiB未満なら停止します。録画とは別のファイルであり、容量は重複します。
- session・scan・recordingの履歴は各1000件まで。保存済みファイルを自動削除しません。
  終了したsessionのHLSも保持するので、容量不足時は新規開始を止めて運用者が保存先を確認します。

遅延測定では、操作受理の`started_at`、最初のTSの`ts_started_at`、HLSの`ready_at`を保存し、
UI側は同じsession IDでvideoの`playing`時刻を採ります。`currentTime`、`buffered.end()`、
hls.jsの`liveSyncPosition`との差を定期観測します。これはプレイヤーと配信端の差で、
放送時刻からの絶対遅延ではありません。増加し続ける傾向を短い単発値で否定しません。

## 録画・再生（#16・#17）

```json
{"request_id":"66666666-6666-4666-8666-666666666666",
 "session_id":"77777777-7777-4777-8777-777777777777","duration_seconds":300}
```

録画はTS到着後のrunning sessionで開始します。既定300秒、1〜300秒で指定できます。
受信期限とファイル入力の残り量の両方に、要求時間＋停止猶予5秒が必要です。
不足は`409 insufficient_session_time`。UIで5分を選んだときに短い録画へ黙って変更しません。

RecordingにはID群、入力種別、選択Serviceと整数service ID、`state/started_at/deadline_at/
ended_at/duration_seconds/elapsed_seconds/bytes_written/partial/end_reason/artifact_id/
download_url/file_available/source_offset_bytes`が入ります。最後の項目は同じ受信TSの
何byte目から保存したかを表し、オリジナル区間の検証に使えます。

完了時の主要フィールド例（応答全体の抜粋）:

```json
{"state":"completed","duration_seconds":300,"elapsed_seconds":300.01,
 "bytes_written":37500000,"partial":false,"end_reason":"deadline",
 "artifact_id":"88888888-8888-4888-8888-888888888888",
 "download_url":"/api/recordings/99999999-9999-4999-8999-999999999999/download",
 "file_available":true}
```

- 開始受理後の単調時計で書込みを制限します。期限後のパケットは保存しません。
  50ms間隔の監視もあり、入力が止まっても期限が働きます。`elapsed_seconds`は確定までの
  実経過時間で、flush等により指定時間を僅かに超える場合があります。
- 手動停止・期限は正常終了。受信の先行停止/EOFは`failed/partial/source_ended`、API終了は
  `server_shutdown`、再起動時の未完了は`interrupted/partial/server_restart`です。
- 録画は最大2,000,000,000 byte（50 Mbps×300秒=1.875 GB）。開始前に録画と同じ時間の
  セッション保存の両方、HLSと稼働中の録画再生の出力上限、空き容量の下限を含めて確認し、
  書込み時も容量・packet境界を確認します。ENOSPC、書込み/flush/close失敗、DB失敗を保存します。
- 録画中は選局・スキャン・二重録画を409で拒否します。録画だけを停止すれば視聴は継続します。
  session停止は録画をpartialとして終了させてから入力を回収します。
- 完了ファイルのrename、artifactと録画メタデータのDB確定後にだけ配信します。SQLiteの失敗では
  artifactを公開せず、新規開始を遮断します。欠損は`file_available=false`で、過去の完了と区別します。
- 録画時間と再生可能時間は一致を保証しません。開始位置のPAT/PMTやキーフレーム待ち、muxの
  時刻範囲をFFprobe/デコードで別測定します。録画用にTSを切り詰めたり再構成したりしません。

再生はPOST `/api/recordings/{id}/playback`後、同じURLをGETでpollingします。
PlaybackはMediaStatusの項目に`id/recording_id/started_at/deadline_at/ended_at`を加えた形です。
`url`はHLS準備後に得られます。`completed`で変換完了、`failed`なら`error_code/error_stage`を表示します。
再生の失敗でRecordingの完了状態やオリジナルTSを変更しません。

変換は同時1件、上限180秒、256 MiB・512ファイルです。入力は登録したオリジナルを読み、
別ディレクトリへ出力します。受信は起動しません。同じ録画の重複要求は既存ジョブを返します。
スクランブル済み録画の後日再生には、別途固定した外部CAS・適合するカード/権限等が必要です。
本実装はそれらを用意せず、CAS失敗として通知します。カードがあれば必ず再生できるとは保証しません。

## SQLite・ファイル配信

schemaは`PRAGMA user_version=2`で、session/artifactにservice/scan/recording/playbackを追加します。
v1からは`state.before-v2.sqlite3`へSQLite backupを作り、transactionでテーブルを追加します。
既存backupがある場合や未知schemaは起動を拒否します。既存backupを自動上書きしません。
APIは単一プロセス・単一接続です。再起動後に自動RX・自動変換をしません。

保存TSは管理者のGit外JSONを`SDR_SAVED_SOURCES`で指定します。

```json
{"local_sample":{"path":"data/local/sample.ts","bitrate":1000000}}
```

HTTPから登録・変更できません。データ・設定・DBをGitへ入れません。
HLSも含む出力は`SDR_DATA_DIR`配下（Composeでは`/data`）です。
登録パスはopenat/O_NOFOLLOWで開き、絶対パス、`.`/`..`、途中を含むシンボリックリンク、
非通常ファイルを拒否します。HLSは64 KiB以内のプレイリストと登録メンバーだけを配信し、
URI属性、外部URL、未登録segmentを拒否します。プレイリストの内容とmembersを一緒に保存し、
FFmpegの更新途中を混在させません。プレイリストには`Cache-Control: no-store`を付けます。
削除済みsegmentは404になります。master playlist、暗号化HLS、fMP4は今回の対象外です。

利用者は一人、localhost/SSHトンネル内を前提とし、ユーザーACLはありません。
IDは認証情報ではありません。インターネットへ公開するサーバーではありません。

## UIが扱う主なエラー

| HTTP / code | UIで必要な操作・意味 |
|---|---|
| 409 `recording_busy` | 録画停止を待つ。選局・scan・二重録画は禁止 |
| 409 `session_busy` / `scan_busy` | 先の操作の停止・回収を待つ |
| 409 `device_busy` | 別プロセスが同じ排他を保持。勝手に停止しない |
| 409 `restore_unverified` | 作業者の復元照合が必要。自動再試行しない |
| 409 `insufficient_session_time` | 要求時間を満たせない。5分録画を開始しない |
| 409 `storage_full` / `recording_output_limit` | 空き容量・有限上限を確認 |
| 409 `recording_incomplete` / `recording_file_missing` / `recording_unavailable` | partial・未完了・欠損を再生成功として扱わない |
| 409 `playback_busy` | 別録画の変換終了を待つ |
| 404 `playback_not_started` | CSRF付きPOSTで再生を開始する |
| 501 `live_not_implemented` / `saved_scan_not_configured` | 今回対応しない入力機能 |
| 503 `database_error` / `source_missing` / `source_not_registered` | 保存先・管理者設定の確認が必要 |

ジョブ開始後の失敗はHTTP 200の状態取得で返ります。HTTPの成功だけで処理成功と表示しません。
EOF・期限・中止、RF/入力、CAS、変換、保存、ブラウザーの故障を分けて表示してください。
EOF後の過去sessionを現在の受信成功として再表示しないでください。
