# API・状態・WebUIとの接点

実装の仕様は`src/sdr_dtv_poc/models.py`と`GET /openapi.json`です。
#18・#27〜#29のWebUI担当は、既存の`static/vendor`にある固定Vue/hls.jsを利用できます。
現在の`index.html`/`entry.js`は起動・診断・合成デモの疎通確認だけを行う仮の入口です。
画面設計、局一覧のUI、HLSプレイヤー、録画画面はこの段階で実装していません。

## 共通の操作

公開先は既定で`http://localhost:8000`です。`Host`は設定したOriginのauthorityと
完全一致する必要があります。`127.0.0.1`のURLを使う場合も`SDR_ORIGIN`をそのURLに
合わせます。別端末からはSSHトンネル等を使い、接続したブラウザーから見たOriginを
指定します。末尾の`/`は付けません。転送されたHost/Originを信用せず、CORSは許可しません。

1. `GET /api/bootstrap`で起動単位の`csrf_token`と実装済み機能を取得する。
2. 変更操作は同じOriginから、`Origin`と`X-CSRF-Token`を付けて送る。
   ブラウザーはOriginを送ります。CLIクライアントでは両方を明示します。
3. 長時間操作は202とIDを返す。状態はGETでpollingし、終端でpollingを停止する。
4. 連打・再送は同一`request_id`（UUID）を使う。同一入力なら同じIDを返し、
   入力を変えて同じrequest IDを再利用すると409。停止は終端後を含めて冪等。

```json
{"request_id":"11111111-1111-4111-8111-111111111111",
 "input_kind":"synthetic","source_id":"demo","duration_seconds":60}
```

エラーは`{"code":"session_busy","message":"Operation unavailable"}`形式です。
入力や例外の全文を応答へ反映しません。局名はデータとして扱い、Vueの補間または
`textContent`で表示してください。`v-html`/`innerHTML`へ渡しません。

| 操作 | 現在の応答 |
|---|---|
| `GET /api/health` | API起動確認。受信成功の意味はない |
| `GET /api/bootstrap` | 操作トークン、live/HLS/recordingの実装可否 |
| `GET /api/diagnostics?input_kind=synthetic` | 保存先の空き容量、合成入力、FFmpeg、native、ボード、カード、CASを別々に表示 |
| `GET /api/services` | 架空の`Synthetic Test`のみ。実測局一覧の保存は#14 |
| `POST /api/sessions` | synthetic/saved_tsは202、liveは501。既存sessionと競合すれば409 |
| `GET /api/sessions`、`GET /api/sessions/{id}` | 履歴・状態。未知UUIDは404、不正UUIDは422 |
| `POST /api/sessions/{id}/stop` | 停止を受理して202。子回収後に終端へ遷移 |
| `GET /api/artifacts/{id}`、`…/download` | 完了した登録済みTSのメタデータと内容 |
| `GET /api/artifacts/{id}/files/{filename}` | 登録済みHLSの許可したmemberだけ。生成は未実装 |
| `POST /api/scans`、`GET /api/scans/{id}`、`POST /api/scans/{id}/stop` | #14向け予約。現在501 |
| `POST /api/recordings`、`GET /api/recordings`、`GET /api/recordings/{id}` | #16向け予約。現在501 |
| `POST /api/recordings/{id}/stop`、`GET /api/recordings/{id}/playback`、`…/download` | #16/#17向け予約。現在501 |

予約操作はOpenAPIで入力を定義しますが、成功や空の録画一覧を偽装せず501を返します。
追加の未知フィールドは拒否します。HTTPに故障注入、任意パス・URL・コマンド、
復元ロックの解除機能はありません。

## 状態と期限

session/scan/recording/artifactは別々のUUIDを使います。`input_kind`は
`synthetic`、`saved_ts`、`live`であり、ファイル解析をliveと表示しません。
`source_id`は設定に登録したIDです。`stage`は入力・転送・保存・回収・完了を表し、
`state`、`end_reason`、`restore`とは分けます。故障した段階は`error_stage`に保持します。

```text
starting → running → stopping → completed
    └────────┴───────────┴──→ failed
再起動時の未完了レコード → interrupted（自動再開しない）
```

- 受信sessionは開始受理から最大600秒。単調時計を用い、UTCの`deadline_at`は表示用。
- scanは最大180秒、録画は最大300秒。これらの実処理と状態テーブルは後続Issue。
- 起動と最初のTSを待つ猶予はそれぞれ最大15秒で、session全体の期限を超えない。
- 停止・子回収は最大5秒。デモワーカーはstdin切断で終了し、2.5秒以内に終わらなければkill/wait。
- APIのHTTP終了猶予10秒、起動中最大15秒＋子回収5秒を考慮し、Composeは35秒待つ。
- EOF、手動停止、session期限は正常終了。ワーカー故障、容量不足、出力上限、DB失敗、
  サーバー停止はpartial。再起動時はinterruptedとして回収し、古い成功へ上書きしない。
- `restore=unknown/failed`は新規開始を409で遮断。合成/保存TSは`not_required`。
  DB書き込み失敗時も開始を遮断し、失敗したメタデータを成功と返さない。
- 一つのsession出力は80,000,000 byteまで、空き容量128 MiB未満で停止する。
  履歴は1000 sessionで新規開始を拒否する。保存済みファイルを自動削除しない。
- 5分録画の開始にはsession残り300秒＋停止猶予5秒が必要とし、不足時は409。
  録画中のscan・選局・二重録画の拒否と共通TSの分岐は#13/#16で実装する。

デモのTS保存はアダプターの疎通を検査する有界な出力です。録画機能の完成を意味しません。
保存した入力が可変ビットレートの場合、設定したbitrateによる供給は近似で、PCR時刻に
基づく再生ではありません。合成TSは1 Mbpsで生成します。

## SQLiteとローカル入力

既定保存先はGit外の`data/app`、Composeでは`app-data` volumeの`/data`です。
`state.sqlite3`はWALを使い、`PRAGMA user_version=1`をトランザクションで初期化します。
未知の新しいschemaは起動を拒否し、黙って書き換えません。将来の更新ではバックアップと
明示的な移行を追加します。現在はsession・artifactだけで、局・録画テーブルは後続です。

`.api.lock`により同じ保存先を持つAPIプロセスの二重起動を拒否します。
`--workers 1`で起動してください。このlockを研究機器の排他としては使いません。

保存TSを使う場合は管理者がGit外のJSONを作り、`SDR_SAVED_SOURCES`で指定します。

```json
{"local_sample":{"path":"data/local/sample.ts","bitrate":1000000}}
```

要求では`input_kind=saved_ts, source_id=local_sample`を使います。
HTTPからパスを登録・変更できません。設定ファイル、TS、カード情報はGitへ入れません。

## ファイル配信の境界（#11）

TSは完了後にartifactを登録し、sessionの更新と同じDBトランザクションで確定します。
partial・未登録・未知IDは配信しません。起動中に停止して0 byteだった場合は完了状態でも
artifactがありません。DB障害時にファイルだけ残っても登録されるまで配信しません。
`/data`のディレクトリ一覧や任意パスを公開するルートはありません。

登録パスは保存領域から相対解決し、`.`/`..`、絶対パス、途中のディレクトリを含む
シンボリックリンクを拒否します。openat/O_NOFOLLOWで開いたファイル記述子を配信し、
チェック後のリンク差し替えも追いません。
HLSはartifactのmemberに登録した相対ファイル名だけを許可します。プレイリストは
64 KiB以下、URI属性・外部URL・絶対パス・未登録segmentを拒否します。
現段階は単純なMPEG-TSメディアプレイリスト用です。暗号化・master playlist・
fMP4用のURI属性は後続の明示的な対応が必要です。HLS生成・ブラウザー再生は未検証です。

利用者は一人を想定し、session単位の認証やユーザーACLはありません。IDは秘密の
認証情報ではありません。localhost/SSHトンネルの内側で使い、インターネットへ公開しません。
ボード・native・カードの診断が`not_checked`の場合は成功ではありません。
この段階の診断APIはデバイスI/Oを一切行わず、設定変更・RX・権限緩和も行いません。
