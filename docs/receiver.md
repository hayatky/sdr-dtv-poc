# 採用する受信処理とアダプター

この段階では実機を使いません。APIで実装した入力は`synthetic`と、管理者が
ローカル設定へ登録した`saved_ts`です。`live`は501を返します。
研究元の復調器を複製・再実装せず、次の固定ソースを将来の実機アダプターで再利用します。

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
```

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

実機の復元`unknown`/`failed`は次の開始を遮断します。HTTPに解除操作はありません。
`pending → verified`は実際の読戻し照合が必要です。合成/保存TSでは`not_required`です。
停止猶予内に正常終了しなければ子を回収してpartialを残します。

研究元の現行live経路は全量IQを保存しています。通常のPoCで全量IQ保存を必須にせず、
有限FIFOから復調へ供給する変更が後続で必要です。既存の経路が既に保存なしで動くとは
扱いません。診断IQを残す場合だけ目的・容量・期限を設定します。

未実施: 機器への到達性、baseline読取、実機排他の共有、RX設定・復元、他物理ch、
受信中のHLS、300秒録画、Mac/Safariでの再生。native importの成功はこれらの証拠ではありません。
