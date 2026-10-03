# Issue #29: WebUIのAPI・HLS接続と引継ぎ

更新日: 2026-10-04（日本時間）。作業元はmain `b8e9259`です。
通常のWebUIを実APIへ接続し、既存の三つのタブで合成入力を操作します。
変更と検証の詳細は[検証記録](issue-29-validation.md)、APIの基準は
[API仕様](api.md)、`models.py`、起動したアプリの`/openapi.json`です。
PR #34はmainの`dd60fb2`へ統合済みで、#29・#18・#4は完了しました。
次は[#5への引継ぎ](issue-5-handoff.md)から進めてください。

## 接続したもの

- `static/api.js`: 同一OriginのHTTP通信、bootstrapとCSRF、一覧からの状態取得、
  診断・スキャン・選局・録画・再生。通常はHTTP、`?mode=mock`だけ画面用の模擬データです。
  通信失敗時の自動切替はありません。結果不明の開始要求は同じ操作のrequest_idを保持し、
  明示的な再送に使います。状態取得でrequest_idを確認できた場合は保留を解消します。
- `static/app.js`: 保存したServiceの`id`を`service_key`へ渡し、選局では600秒・HLS有効を明示します。
  スキャンは`input_kind:synthetic`、初期範囲はbootstrapの合成プリセットです。
  局名・番号・Serviceがnullの場合は不明として扱い、推測しません。
- `static/player.js`: 安定したvideo要素に標準HLS、または同梱hls.jsを接続します。
  標準HLSを対応と申告しても実際に非対応エラーを返す環境では、一度だけhls.jsへ切り替えます。
  自動再生拒否は手動の再生ボタンへ案内し、音声を自動的に無効にしません。
- `app.py`: `player.js`だけを既存の`UI_FILES`へ追加しました。API・モデル・CSPは変更していません。
  任意ファイルの配信、Node.js/npm、ビルド、実行時CDN、新しい製品依存は追加していません。

## 状態と終了処理

一覧APIを使うため、`GET /api/status`は追加していません。api.js内の`status()`はHTTPの
エンドポイント名ではなく、sessions/scans/recordingsをまとめる画面用の関数です。
従来の画面項目への変換はこの境界で行います。`health`や`remaining_seconds`を
サーバーが返すフィールドとして扱わないでください。

- Sessionの残り時間は`deadline_at`と端末の時計から計算した表示用の推定です。
  ファイルの実残量や停止制御には使いません。録画開始可否はサーバーが判定します。
- 全体の復元ゲートや正確な空き容量を一覧だけから正常と断定しません。
  観測した復元失敗・診断の保存先異常を表示し、POSTの`restore_unverified`・
  `insufficient_session_time`・`storage_full`・競合等も案内します。
- スキャンは中止・失敗を含め、一覧の再取得で保存された局を反映します。
  保存済みの観測を現在のRF受信成功とは表示しません。
- 選局のPOSTはバックエンドが旧sessionの停止・回収を待ちます。画面は切替中に旧映像を解放します。
  操作世代・対象IDを照合し、古い応答・HLSイベント・playのPromiseを現在の表示へ適用しません。
- タブ切替・受信停止・再生対象の変更・pagehideでプレイヤーを破棄し、イベント・取得・タイマーを終了します。
  video要素自体は画面更新ごとに作り直しません。ページ復帰は状態取得から再開します。
- 読取りのポーリングは一つだけです。pagehide後に古いfinallyから再開しません。
  再読込・通信復旧・別タブを開く操作は一覧取得だけで、受信・スキャン・録画を自動開始しません。
- 403ではbootstrapと現在の状態を再取得します。拒否された変更要求を自動で再実行しません。

## 録画・再生

300秒の期限はバックエンドの単調時計が守ります。UIで短い録画へ黙って変更しません。
録画中は選局・スキャン・二重録画を止め、APIの409も表示します。
録画だけの手動停止は正常完了ですが、受信を先に止める操作では途中終了になることを確認画面に示します。
`failed/partial/source_ended`を完成録画として扱いません。

再生・ダウンロードは`completed`・`partial=false`・`file_available`・`download_url`を照合します。
欠損や未完了は操作できません。再生はCSRF付きPOSTで明示的に開始し、以後はGETと`Playback.url`を使います。
再生変換やブラウザーの失敗は録画そのものの状態と区別し、失敗ジョブへ自動でPOSTし続けません。
ダウンロードはAPIが返した登録済みURLだけを使い、任意のパス・URLを入力させません。

## 起動・観測

専用worktree・保存先・ポートを使います。以下の生成は初回だけです。
TSまたは同名JSONが存在する場合は生成条件を照合して再利用するか、新しい場所を使ってください。

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

ブラウザーで`http://localhost:18329/`を開き、診断→スキャン→選局→録画を操作します。
360秒入力でも、305秒未満になった後は5分録画を開始できません。
API用の`smoke-stage2.py`を使う場合も`--source data/issue-29-input/demo.ts`を指定し、
UI確認と同時には動かさないでください。UI経由の検証は`smoke-webui.py`を使います。

videoの`sdrObservation`には同じsession IDの`started_at`、`ts_started_at`、`ready_at`、
最初の`playing_at`、直近30件の再生時刻・buffered end・hls.jsの同期目標・フレーム数を保持します。
ページ内だけの有界記録で、外部送信しません。プレイヤーと配信端の差を放送時刻からの絶対遅延とは扱いません。

## 後続へ残すもの

- #19: システム全体の異常経路、長時間の遅延・容量・復元と導入条件の統合確認。
  今回の注入試験はUIの応答確認であり、実際の全障害を起こした試験ではありません。
- #21・#22: 実機での受信、復元照合、実時間300秒録画と放送TSの品質。
- #23: Safariでの標準HLS、実機を使うChromium/Safari、人による映像・音声・音ずれ・音切れの確認。
  標準HLSの処理を実装し、制御したイベントで終了処理を試験しても、Safari実行の代わりにはしません。
- liveは501、外部CASは未接続です。研究元・実機・Actions・公開設定・ブランチ保護は変更していません。
