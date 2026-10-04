# 採用する受信処理とアダプター

APIは`synthetic`、管理者が登録した`saved_ts`、および設定済みの`live`に対応します。
実機を使う構成・追加の固定ソース・停止復元は[live-receiver.md](live-receiver.md)を参照してください。
以下はnative imageの基点と、実機を使わずに行える準備手順です。
研究元の復調器を複製・再実装せず、固定ソースを再利用します。

## 固定するソースと環境（#8）

- 研究元: https://github.com/hayatky/hlfec-sdr-lab
- コミット: `b1dcbf3688db79f149ff3a255639a36860ec3924`
- tree: `598b61553e5882062bdd4b6055b2ab4d11fdc91d`
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

研究元の`runtime/prepare_source.py`と`wideband/prepare_source.py`が固定上流から
受信ブロックを抽出・変換します。OFDM探索境界、Viterbi状態、TMCC境界等の研究元の
変更をそのまま利用します。PoC側で変換を再実装しません。
`THIRD_PARTY_NOTICES.md`に記載した研究元自作ファイルの配布条件が未確定なので、
これらをGitや既定のデモimageには同梱しません。権限のある利用者が別途取得する手順です。

## 実機を使わない再構築と診断

次の準備はホストの開発者が実行します。APIからgit・Docker・sudoを呼びません。
既存checkoutを変更せず、空のGit外ディレクトリへ取得してください。

```sh
mkdir -p data/receiver-source
git -C data/receiver-source init
git -C data/receiver-source remote add origin https://github.com/hayatky/hlfec-sdr-lab.git
git -C data/receiver-source fetch --depth=1 origin b1dcbf3688db79f149ff3a255639a36860ec3924
uv run --locked python scripts/prepare-receiver.py data/receiver-source

docker build -t sdr-dtv-native-base:issue3 data/receiver-build/runtime
docker build -t sdr-dtv-native-wideband:issue3 data/receiver-build/wideband
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 --memory 1g \
  --user 10001:10001 sdr-dtv-native-wideband:issue3 \
  python3 -c 'from gnuradio import gr; import hlfecwideband; print(gr.version())'

docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 64 --memory 1g \
  --user 10001:10001 \
  -v "$PWD/data/receiver-build/files:/receiver:ro" \
  sdr-dtv-native-wideband:issue3 python3 /receiver/wideband/file_receiver.py --help

# 合成IQで復調処理の組立て・入力・EOFを確認（実機と放送素材は使わない）
uv run --locked python scripts/smoke-receiver.py
```

`smoke-receiver.py`は1,048,576標本（8 MiB）のゼロ値の`cf32_le`を一時生成し、
B階層の処理を30秒の上限付きで起動します。ネットワーク・デバイスを接続せず、
非rootで動かし、出力と合成IQは終了後に一時ディレクトリごと削除します。
無信号なので、受信処理の終了コード1、`unverified_no_valid_tmcc`、有効TMCC 0件、
TS出力0 byteを期待します。この結果を確認した検査スクリプトは終了コード0を返します。
復調処理の接続と有限入力の終了を確認するもので、正常な放送波の復調・TS回復や
実機での受信成功を示す試験ではありません。

importと`--help`だけではFFTWの初期化を確認できません。GNU Radio 3.10.9.2は
FFTWの一時ファイルを`appdata_path()`へ作るため、読み取り専用コンテナの既定の
ホームディレクトリでは復調処理の起動に失敗します。検査スクリプトはコンテナ内の
子プロセスだけ`env -u HOME -u APPDATA`で起動し、書き込み先を32 MiBの`/tmp` tmpfsへ
切り替えます。ホストの環境変数や権限は変更しません。後続のnativeワーカーにも
書き込み可能な一時領域が必要です。根拠はGNU Radioの
[FFTW初期化](https://github.com/gnuradio/gnuradio/blob/v3.10.9.2/gr-fft/lib/fft.cc)と
[appdata_path](https://github.com/gnuradio/gnuradio/blob/v3.10.9.2/gnuradio-runtime/lib/sys_paths.cc)です。

`prepare-receiver.py`はHEADや未コミット変更を使わず指定Gitオブジェクトを読み、
ビルド入力4ファイルのSHA-256を照合します。出力は既存ファイルへ上書きしません。
変更するのは基底imageのタグ名だけです。APTの間接依存は取得日時で変化し得るため、
完全に同一なimageの再生成は保証せず、実際のimage IDとpackage一覧を保存してください。
上流ソースとCOPYINGはimage内に保持されます。imageはこのPRでは配布しません。

## TSとプロセスの境界

| 対象 | 入出力・責任 |
|---|---|
| API共通アダプター | `start()`、`read() -> bytes`、`stop() -> Restore`。TSは188 byte、先頭0x47、順番を維持。EOFは空bytes、故障は例外 |
| 合成/登録済みTS | `sdr_dtv_poc.worker`をPython子プロセスで起動。stdoutはTSのみ。一定muxrateに基づいて供給し、一度だけEOFまで読む。stdin切断で親の終了を検知 |
| 研究元ファイル入力 | `wideband/file_receiver.py INPUT OUTPUT_DIR --stage ts --layers b`。引数はホスト側の固定設定から組み立てる。APIから任意引数を受けない |
| nativeのIQ入力 | `cf32_le`、I/Q交互、1複素標本8 byte、512000000/63 S/s。受信時の`ci16_le`とは別。6.4 MS/sからの変換は研究元の80/63処理を再利用 |
| nativeの出力 | 初期採用はB階層の`layer_b.ts`、188 byte TS。これは全階層の元の多重TSではない。A/B多重復元は初期必須条件にしない |
| nativeの結果 | `result.json`と終了コード、FEC/TS品質を照合。早期終了、RS廃棄、partial、復元不明を成功へ読み替えない |

Mode/GI、変調、符号率、TIは実測TMCCから決める必要があります。研究で確認した
27chの条件を他局へ固定適用しません。nativeの`--help`の既定値を選局仕様にはしません。
元の研究用`run-live.sh`/`run-file.sh`はsudo/Dockerを呼ぶため、Web APIから実行しません。
将来は同じアプリimage内にnative環境を配置し、別Pythonの子プロセスとして直接起動します。
その統合と実機入力は#13以降に残ります。

## 所有者・停止・復元

研究元`src/receiver/jobs.py`の`Jobs`は共通のdevice lock、owner、recovery-requiredを
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
これらの後続の実機検証は[段階3の記録](issue-5-validation.md)と[品質改善の記録](issue-37-validation.md)を参照してください。

## 段階2で追加した共通排他と終了処理（#13）

研究元の固定コミットの`src/receiver/jobs.py`は`Jobs.__init__`で
`data/receiver/.device.lock`を`flock(LOCK_EX | LOCK_NB)`し、`Jobs.start`で同じFDを
workerへ継承します。`recovery-required.json`がある場合と、復元pending/unknown/failedの
記録がある場合は開始を遮断します。`recovery.py`は同じ排他を取得し、baseline・設定後の値・
現在値を照合します。既存の`recovery.json`がある場合は根拠のない再試行を拒否します。

PoCの`device_lock.py`はこのファイル名・flock・FD継承と互換の境界を独立実装しました。
研究元のwrapperや実機操作コードを複製していません。既定では合成検証専用の
`SDR_DATA_DIR/device`を使います。**この既定値は研究機器との共通排他ではありません。**
実機統合ではホスト側で`SDR_DEVICE_LOCK_DIR`を研究CLIと同じディレクトリへ指定し、
コンテナでも同じinodeをbind mountする必要があります。APIからその登録や権限変更はしません。
段階2では一時ディレクトリで検証しました。段階3は実際の研究用lockと同じinodeを使い、
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

段階3では実機のbaseline取得、復元書込みと独立したreadback、共通FD継承を接続しました。
録画確定時刻、子の終了コード、CLOSE結果、baseline/現在値の一致、停止後のlock再取得を
照合しています。詳細と未確認の異常条件は[段階3の記録](issue-5-validation.md)を参照してください。
切断を無条件に再接続する機能はありません。HTTPによる復旧は、実際の設定復元と読戻し照合が成功した場合だけ制限を解除します。
