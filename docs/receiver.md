# 採用する受信処理とアダプター

APIは`synthetic`、管理者が登録した`saved_ts`、および設定済みの`live`に対応します。
実機を使う構成・同梱する固定ソース・停止復元は[live-receiver.md](live-receiver.md)を参照してください。
以下は同梱ソースと公開上流を使うビルドの構成です。
元の研究リポジトリは今後も非公開で、利用者による取得は不要です。
受信処理の出典・固定コミット・ハッシュ・ライセンスは[同梱ソースの説明](../native/README.md)を参照してください。

## 固定するソースと環境

- 同梱するビルドスクリプトの由来となる非公開ソースのコミット: `b1dcbf3688db79f149ff3a255639a36860ec3924`
- 上流: https://github.com/git-artes/gr-isdbt 、コミット
  `56b2556c14ecc5d710070f969fda7a2deae65d8b`。基底runtimeの旧ワンセグ部分は
  `261019a65f5ac09144a81f0800f9a80bdc88e539`。
- ホスト対象: Ubuntu 24.04、x86_64/SSE2。ARMとDocker Desktopの実機利用は未検証。
- native側: Ubuntu 24.04 digest、GNU Radio `3.10.9.2-1.1ubuntu2`、
  GSL `2.7.1+dfsg-6ubuntu2`、pybind11 `2.11.1-2`、make `4.3-4.1build2`。
  OS Python 3.12で拡張をビルド・importします。完全な導入結果はimage内の
  `/opt/packages.lock`、変換したソースのhashは`/opt/wideband-source/source-manifest.json`。
- API側: uvの独立したPython 3.12仮想環境と`uv.lock`。
  `--system-site-packages`を使わず、native側とPythonのimport環境を分けます。

`native/build/runtime/prepare_source.py`と`native/build/wideband/prepare_source.py`が
固定した公開上流から受信ブロックを抽出・変換します。OFDM探索境界、Viterbi状態、TMCC境界等の
既存の修正を含みます。元の著作権表示・LICENSE・COPYINGと、変換前後のハッシュを保持します。

## 実機を使わないビルドと診断

リポジトリ直下で`docker compose build`を実行すると、同梱したPythonソースと公開上流から
通常構成のイメージを作ります。この操作ではサービスや受信を開始しません。
実際の起動・停止は[実機の導入手順](live-receiver.md)、合成デモは[README](../README.md)を参照してください。
APIからgit・Docker・sudoは呼びません。

ビルド中にGNU Radioと受信拡張のimport、および同梱Pythonソースのハッシュを照合します。
importやハッシュ一致だけでは、実機での復調・映像音声再生は確認できません。
合成無信号IQによる有限入力の試験と、実機での試験は
[通常構成の検証記録](validation-compose-live.md)で区別しています。

GNU RadioのFFTW初期化には書き込み可能な一時領域が必要です。読み取り専用コンテナで
診断する場合も、子プロセスのホーム・一時領域を確認してください。過去の合成無信号試験では
子プロセスだけ`env -u HOME -u APPDATA`で起動し、`/tmp`のtmpfsを使用しました。
詳細は[FFTW初期化](https://github.com/gnuradio/gnuradio/blob/v3.10.9.2/gr-fft/lib/fft.cc)と
[appdata_path](https://github.com/gnuradio/gnuradio/blob/v3.10.9.2/gnuradio-runtime/lib/sys_paths.cc)を参照してください。

APTの間接依存は取得日時で変化し得るため、完全に同一なイメージの再生成は保証しません。
検証には実際のイメージID、`/opt/packages.lock`、変換ソースのmanifestを記録します。
イメージはローカルでビルドする構成で、配布していません。

`prepare-receiver.py`・`prepare-live.py`・`smoke-receiver.py`は、以前に取得済みの非公開Gitオブジェクトや
旧ビルド出力を使う保守用ツールです。一般の利用者向けの取得・導入手順には含めません。
旧経路の検証は[過去の記録](validation.md#過去の記録)に保持しています。

## TSとプロセスの境界

| 対象 | 入出力・責任 |
|---|---|
| API共通アダプター | `start()`、`read() -> bytes`、`stop() -> Restore`。TSは188 byte、先頭0x47、順番を維持。EOFは空bytes、故障は例外 |
| 合成/登録済みTS | `sdr_dtv_poc.worker`をPython子プロセスで起動。stdoutはTSのみ。一定muxrateに基づいて供給し、一度だけEOFまで読む。stdin切断で親の終了を検知 |
| 同梱するファイル入力処理 | `wideband/file_receiver.py INPUT OUTPUT_DIR --stage ts --layers b`。引数はホスト側の固定設定から組み立てる。APIから任意引数を受けない |
| nativeのIQ入力 | `cf32_le`、I/Q交互、1複素標本8 byte、512000000/63 S/s。受信時の`ci16_le`とは別。6.4 MS/sからの変換は同梱した80/63処理を再利用 |
| nativeの出力 | 初期採用はB階層の`layer_b.ts`、188 byte TS。これは全階層の元の多重TSではない。A/B多重復元は初期必須条件にしない |
| nativeの結果 | `result.json`と終了コード、FEC/TS品質を照合。早期終了、RS廃棄、partial、復元不明を成功へ読み替えない |

Mode/GI、変調、符号率、TIは実測TMCCから決める必要があります。研究で確認した
27chの条件を他局へ固定適用しません。nativeの`--help`の既定値を選局仕様にはしません。
元の研究用`run-live.sh`/`run-file.sh`はsudo/Dockerを呼ぶため、Web APIから実行しません。
実機用イメージではネイティブ環境を同じコンテナへ配置し、OS Pythonの子プロセスとして起動します。
同じコンテナ内のAPI用Python仮想環境とは分離しています。設定と起動方法は[実機の導入手順](live-receiver.md)を参照してください。

## 所有者・停止・復元

非公開の旧研究ツールでは、`Jobs`が共通のdevice lock、owner、recovery-requiredを
扱い、`worker.py`はbaseline読取、設定・読戻し、有限取得、CLOSE・復元を担います。
`recovery.py`は実値を照合してから復元します。実機アダプターではこの意味を保ち、
研究ツールと同じ物理デバイスの排他を共有する必要があります。
デモの`.api.lock`は単一APIのための排他で、研究ツールとの実機排他ではありません。

実機の復元`unknown`/`failed`は次の開始を遮断します。無条件に解除する操作はありません。
現在は[復旧手順](recovery.md)のAPIで実際の復元と独立読戻しを行い、成功した場合だけ開始できます。
`pending → verified`は実際の読戻し照合が必要です。合成/保存TSでは`not_required`です。
停止猶予内に正常終了しなければ子を回収してpartialを残します。

初期の研究元経路は全量IQを保存していました。現在のPoCは有限キューとFIFOで復調へ供給し、
通常受信で全量IQを保存しません。スキャン等の診断IQを残す場合は目的・容量・期限を設定します。

このnative環境の構築試験だけでは、機器への到達性、RX設定・復元、受信中HLSや録画は確認しません。
これらの実機検証は[実機での検証記録](validation-live.md)と[品質改善の記録](validation-reception-quality.md)を参照してください。

## 共通排他と終了処理

旧研究ツールの`src/receiver/jobs.py`は`Jobs.__init__`で
`data/receiver/.device.lock`を`flock(LOCK_EX | LOCK_NB)`し、`Jobs.start`で同じFDを
workerへ継承します。`recovery-required.json`がある場合と、復元pending/unknown/failedの
記録がある場合は開始を遮断します。`recovery.py`は同じ排他を取得し、baseline・設定後の値・
現在値を照合します。既存の`recovery.json`がある場合は根拠のない再試行を拒否します。

PoCの`device_lock.py`はこのファイル名・flock・FD継承と互換の境界を独立実装しました。
受信に必要な固定ソースは`native/receiver`に同梱しています。通常のCompose構成ではホストの
`/var/lib/sdr-dtv-poc/device`を共通の排他・復旧記録に使います。既存の研究ツールと同じボードを
使う場合は、[共存手順](live-receiver.md#既存環境との共存)に従って`SDR_DEVICE_DIR`を
既存の排他ディレクトリへ指定します。合成検証で使う`SDR_DATA_DIR/device`は研究機器との共通排他ではありません。
APIから排他先の登録やホストの権限変更はしません。
合成試験では一時ディレクトリ、実機試験では実際の研究用lockと同じinodeを使い、
実機受信中にホスト側flockがbusyになることも確認しました。

- sessionとscanは同じ排他を使います。入力子ワーカーが継承FDを持つ間は、親が終了しても
  別プロセスは取得できません。APIの保存先が違っても共通のlock先なら競合します。
- 合成入力workerはstdin切断を監視し、出力の停滞時にも終了を確認します。独立した600秒上限も
  持ちます。FFmpeg/FFprobeはLinuxの親終了通知で強制回収し、通常終了では必ずwaitします。
- 停止では録画終了、HLS子回収、入力子回収とadapterの復元結果、DB確定、lock FDを閉じる順で
  処理します。`LOCK_UN`で子の所有を解除しません。
- 復元不明時はPoCのジョブIDを含む`recovery-required.json`を排他保持中に作り、次の開始を遮断します。
  既存の研究側の記録は上書きしません。PoCのIDを研究側の復旧ジョブとして扱うことはできません。
- 再起動時は未完了session/scanをinterrupted、録画をinterrupted/partialにします。HLS URLを
  無効化してartifactの公開も止め、RX・変換を自動再開しません。残った入力子の排他が解放される
  前の開始はdevice_busyです。別プロセスをPIDだけで判断してkillする機能はありません。

実機ではbaselineの取得、復元書込みと独立した読戻し、共通FDの継承を行います。
録画確定時刻、子の終了コード、CLOSE結果、baseline/現在値の一致、停止後のlock再取得を
照合しています。詳細と未確認の異常条件は[実機での検証記録](validation-live.md)を参照してください。
切断を無条件に再接続する機能はありません。HTTPによる復旧は、実際の設定復元と読戻し照合が成功した場合だけ制限を解除します。
