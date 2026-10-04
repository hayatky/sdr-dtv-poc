# 実機からWebUIへ接続する

Ubuntu 24.04 / x86_64 / Docker Engineで確認した開発者向けのローカル経路です。
対象は確認済みの1ボード、既存のSLAVE/RNDIS接続、1物理ch・1録画です。
macOS、Windows、別ボード/FWへの互換性は未確認です。普段の合成デモは既存の
`compose.yaml`を使います。実機用は独立した`compose.live.yaml`を使います。

## ソースと処理

研究元の基点`b1dcbf3688db79f149ff3a255639a36860ec3924`で固定したnative imageを
[receiver.md](receiver.md)の手順でビルドします。実機用Pythonソースは
[研究元PR #76](https://github.com/hayatky/hlfec-sdr-lab/pull/76)の
`daf2c700df652e97e6e4a1e52f36d8b5361f9d6e`を使います。
`live_sources.py`の11ファイルのSHA-256を起動時に照合し、相違があれば開始しません。
研究元のwrapperの再配布条件は未確定なので、Gitや配布imageへコピーせず、
利用権限のある人が別途取得して読み取り専用でbind mountします。

経路はIIOD → ci16_le 6.4 MS/s → 既存80/63変換 → cf32_le 512000000/63 S/s →
既存B階層復調 → 188 byte TSです。独立した復調器を複製しません。
B階層TSはA/B全階層の元の多重TSではありません。受信TSを保存し、録画は同じ
TSのbyte列を分岐します。HLSと録画再生だけに外部CASとFFmpegを使います。

IQは64 MiBの有限キューへ渡し、全量保存しません。処理遅れ5秒、TS未消費32 MiB、
native TSファイル2 GB、IQ書込み停止1.5秒等の上限で失敗として止めます。
TSの初期同期前の不正packetは最大約100 packetだけ調べ、5 packet連続同期から
渡します。除外量を記録し、元のnative TSもGit外に保持します。途中では再同期せず
不正packetをAPIが失敗として扱います。RS廃棄の推定値は画面に警告表示します。

受信ワーカーは共有`.device.lock`のFDを継承し、inodeも照合します。研究元の
未終了job・復元unknown/failedと復旧記録も検査します。stdin切断と期限で停止し、
CLOSE・復元の後に別接続でRX設定を読み戻します。復元不明なら次回開始を遮断します。
APIからsudo・Docker socketを操作しません。

## ローカルの準備

既存の研究checkoutやデータを上書きせず、新しいGit外ディレクトリを使います。

```sh
# 別途取得した研究checkoutへ固定コミットを取得する（checkoutは変更しない）
git -C /path/to/research fetch origin daf2c700df652e97e6e4a1e52f36d8b5361f9d6e
uv run --locked python scripts/prepare-live.py /path/to/research data/live-source
mkdir -p data/live-config data/live-app data/live-host
chmod 700 data/live-config data/live-app data/live-host
cp examples/live.json data/live-config/live.json
cp examples/live.env.example data/live.env
```

`data/live.env`のUID/GIDと各パスを実環境へ設定します。`SDR_RESEARCH_DEVICE_DIR`は
研究CLIが実際に使うdata/receiverと同じディレクトリです。別のlock用ディレクトリを
新設してはいけません。configの21ch/27chは今回の実測値であり、別地域へそのまま
適用しません。登録外のチャンネルは未受信のまま拒否します。

CASはlibaribb25 `dc1d96a90ea554d8997b238fd6712eccf553cdb3`の無改変ビルドを使います。
上流 https://github.com/tsukumijima/libaribb25 のソース、ライセンス通知、ビルド記録を
ローカルで保管し、導入prefixを`SDR_CAS_DIR`へ指定します。
`bin/arib-b25-stream-test`と`lib/libaribb25.so`等が必要です。オプションは
`-m 0 -p 0 -s 0 -v 0`で、EMM処理や詳細出力を使いません。カードの改造・複製・
鍵抽出やCAS実装の変更は行いません。CAS終了コードだけで復号成功とはせず、
HLSの生成と実際のA/Vで確認します。このPRはCASやnative imageを配布しません。

## ホストの一時準備と起動

対象ボード・既存RF配線・他のRXジョブ・USB/RNDIS・空き容量を確認します。
RXと録画・native TSをそれぞれ保存するため、5分の試験でも数GBの空きを確保します。
`sudo -n true`が成功する環境で、次を専用ターミナルから実行します。

```sh
sudo -n bash scripts/live-host-window.sh "$PWD/data/live-host" /path/to/shared/device-directory
```

このスクリプトは既存の接続状態がdown/IPv4なし、pcscd.socketがactiveかつ
pcscd.serviceがinactiveである場合だけ動きます。対象USBを列挙し、既存RNDISへ
研究元が確認する一時アドレスを設定します。既存PCSC socketを一時停止し、
0700のprivate directory内の0600 socketを使うpcscdを起動します。
Polkitを無効にするのはこのprivate socketの有界daemonだけです。
恒久的な権限・sudoers・認証期限は変更しません。別のPCSC利用中は開始しません。
`SDR_PCSC_DIR`は`data/live-host/pcsc`の絶対パスにします。

`host-ready`が作られてから別ターミナルで起動します。

```sh
docker compose --env-file data/live.env -f compose.live.yaml up -d --build
# http://localhost:18335 を開く
```

コンテナはホストnetworkを使いますが、HTTPは127.0.0.1:18335だけで待ち受けます。
非root、capabilityなし、読み取り専用root、メモリー3 GiB、CPU8、PID128が上限です。
専用ホスト準備は約57分で終了へ進み、RXがあれば共有lockの解放を最大650秒待ちます。
解放できない場合はrouteを残してエラーとし、復旧のための接続を壊しません。

「接続確認・スキャンの入力元」で「実機ライブ」を選び、「実機の登録済みチャンネル」を
スキャンします。実機用構成ではこの入力元を初期選択しますが、受信開始はボタン操作時だけです。
対象は表示された登録済みチャンネルに限り、未登録チャンネルを含むUHF全範囲の自動探索には
現在対応していません。接続診断での未確認表示は、その項目を診断では測定しない意味です。
保存された局を選んで
視聴し、最大5分の録画を開始できます。局名をSIから取得できないときは不明と表示し、
過去の検出と現在の受信を分けます。録画中は選局・スキャン・二重録画を拒否します。
受信期限は600秒、録画期限は単調時計の300秒です。画面を閉じても期限は有効です。

## 停止と人による確認

画面で受信を停止し、restoreがverifiedであることを確認します。その後に
Composeを停止し、最後にホスト準備を終了します。順序を逆にしません。

```sh
docker compose --env-file data/live.env -f compose.live.yaml down
touch data/live-host/host-stop
cat data/live-host/host-cleanup
```

RNDISの一時IPv4が消え、linkがdown、pcscd.socketが以前どおりactiveになり、
RX子プロセスと共有lockが残っていないことも確認します。復元unknown/failedなら
マーカー削除で解除せず、jobのbaselineと独立した読戻しを照合して復旧記録を残します。

受信TS・録画・HLS・native resultは指定data directoryに残ります。Gitには追加しません。
録画タブで再生とTSダウンロードを行えます。人は映像の乱れ、音切れ、音ずれを確認し、
Chromiumの機械的frame/audio/time測定と分けて記録してください。
Safariの視聴・録画再生は2026-10-04にユーザー確認済みで、画質改善後の再確認は残ります。
録画一覧の「削除する」から録画TSと再生用ファイルを手動削除できます。
録画中・変換中は削除できません。詳しい範囲は[APIの説明](api.md)を参照してください。
