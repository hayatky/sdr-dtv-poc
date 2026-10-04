# 実機を既定にしたCompose起動の検証

通常の `docker compose up --build -d --wait` を実機向けに変更し、
合成デモを `docker compose -f compose.demo.yaml up --build -d --wait` へ分離しました。
専用の検証環境を使用しています。実機試験では既存サービスを一時停止し、
既存の保存データを保全したまま、試験後に元のコンテナと接続状態へ戻しました。

## ソースと権限

- 必要な受信ソース14ファイルを `native/` へ収録。自作部分はGPL-3.0-or-later。
  元のコミットとhash、ライセンス・帰属表示の追加をmanifestへ記録。
- 非公開リポジトリからの匿名取得は404となることを確認したため、その依存を除去。
  現在のDockerfileは公開上流だけから追加依存を取得。
- Web APIは非root・capabilityなし。RNDISとPC/SCの準備は別サービス。
  ホストのサービス停止、Docker socket、特権コンテナ、FW書込み、受信の自動開始は行わない。

## 実施した検証

| 検証対象 | コマンド・方法 | 結果 |
|---|---|---|
| 共通検査 | `UV_CACHE_DIR=/tmp/sdr-default-live-uv sh scripts/check.sh` | Ruff・整形・mypy成功、pytest 110成功・1 skip、working/history機密検査成功 |
| Composeの解決 | `docker compose config --quiet`、`docker compose -f compose.demo.yaml config --quiet` | 成功 |
| ホスト準備・復元 | `--network none`の専用コンテナにdummy interfaceと偽のUSB情報を与え、起動後に`docker stop` | 既定UID・指定UIDの両方で準備成功、終了コード0、接続の復元と排他解放を確認 |
| PC/SCのアクセス | 同じ専用コンテナで非root UIDからUNIX socketへ接続 | 成功。カードアクセスや復号の成功を示すものではない |
| 既存PC/SCの中継 | ダミーのUNIXサーバーと隔離コンテナを使って接続・権限・接続中の停止を確認 | 指定UIDからの往復成功、中継先の接続UIDはroot、別UIDの接続拒否、停止・復元は終了コード0 |
| 異常条件 | `tests/test_host_service.py` | 12成功。使用中の設定を維持、外部変更時の復元拒否、ワーカーが排他保持中・RX復元不明時は接続を維持 |
| 同梱ソース | `tests/test_native_bundle.py` | 2成功。14ファイルの集合・hash・構文と改変の拒否 |
| 通常イメージのビルド | `docker compose -p sdr-default-live-validation build` | 成功。既存のnativeイメージや非公開ソースへの認証を使わず、GNU Radio・受信拡張・CAS・TSDuckを構築 |
| 通常構成全体の起動 | `docker compose ... up --build -d --wait`。検証用overrideで機器・ホストnetworkだけを隔離したダミーへ置換 | 両サービスhealthy。bootstrapは`live_backend`・`live_available=true`、session一覧は空。APIのUIDは10001、実効capabilityは0 |
| Composeの停止順序 | 同じ専用構成で`docker compose stop`、ログと終了コードを確認後に`down` | APIが先に停止（SIGTERM、終了コード143）。続いて準備サービスがネットワークを復元し、終了コード0 |
| 合成IQによるnative動作 | 新しいイメージを`--network none`で起動し、1,048,576標本のゼロ値IQを30秒の上限付きで入力 | 成功。`unverified_no_valid_tmcc`、有効TMCC 0、TS 0 byteで有限入力を終了。放送波の受信試験ではない |
| 明示的なデモ | `docker compose -f compose.demo.yaml -p sdr-explicit-demo-validation up -d --wait`（専用ポート） | build・healthy・`synthetic_backend`・`live_available=false`を確認。停止成功 |

サンドボックス内の共通検査はAPIテストで進行せず中止しました。
上表の成功は、実機に接続しない同じ検査をサンドボックス外で実施した結果です。
skipはホスト上にTSDuckがない場合の試験です。Starlette/HTTPXの既存の非推奨警告が1件あります。

## 新しい構成での実機試験

Ubuntu 24.04 / x86_64、既存の対応ボード1台・RX配線・カードリーダーを使用しました。
既存サービスに実行中ジョブがないこと、共有排他が空いていること、RX復旧待ちでないこと、
保存先の空き容量を確認して一時停止しました。新しいAPIは空の専用ボリュームを使い、
既存の共有排他ディレクトリとその所有者だけを環境ファイルで指定しています。
チャンネル設定は同梱の空プリセットを使い、既存の局一覧や受信設定を移植していません。

起動は `docker compose --env-file <LOCAL_ENV> -p <TEST_PROJECT> up -d --wait`。
ビルド済みの最終イメージを使い、ボードのRNDIS設定と既存PC/SCソケットの再利用は
新しい `receiver-host` が担当しました。ホストのPC/SCサービス・認証ポリシーは変更していません。

| 対象 | 結果 |
|---|---|
| 実画面の入力選択 | Chromium 145.0.7632.6で「実機ライブ」のラジオボタンが有効であることを確認 |
| 21chスキャン | 3サービスを検出。`completed`、RX復元は`verified` |
| ライブ再生 | Chromiumで312フレーム、非ゼロ音声peak約0.576、測定区間の再生時刻が4.000秒進行 |
| 20秒録画 | `completed`、`partial=false`、期限で自動停止、42,090,568 bytes |
| 録画再生 | Chromiumで154フレーム、非ゼロ音声peak約0.602、測定区間の再生時刻が4.000秒進行 |
| 受信停止 | `completed`、`partial=false`、RX復元`verified`。復旧要求なし |
| 構成の停止と復元 | 準備サービスの終了コード0。RNDISのifindex・MAC・flags・アドレスが開始前と一致。共有排他を再取得でき、元のコンテナを再起動。PC/SCは開始前と同じsocket active / service inactive |

ブラウザー試験は既存の `smoke-browser.py` をローカルで実機のサービス選択と20秒録画へ
変更して実施しました。既存の検証用共有ライブラリをブラウザープロセスだけに指定し、
ホストへ追加インストールしていません。放送画像・音声・TSや生ログは公開していません。
受信データは専用のGit外保存領域へ保持しています。

## 制限

今回の確認は1物理チャンネル・短時間録画と、機械的なブラウザー再生です。
新しい起動経路での300秒録画、人による品質確認、録画全区間の無欠落は確認していません。
過去の300秒・人による視聴確認は別の起動経路の結果として区別します。
新規OSの導入、USBの長期安定性、Docker Desktop/rootless Docker/ARMは未検証です。
RNDISを他の受信処理が使用中の場合は、競合を解消してから起動してください。
