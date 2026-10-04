# 実機からWebUIへ接続する

Ubuntu 24.04 / x86_64 / Docker Engineで確認した開発者向けのローカル経路です。
対象は確認済みの1ボード、既存のSLAVE/RNDIS接続、1物理ch・1録画です。
macOS、Windows、別ボード/FWへの互換性は未確認です。普段の合成デモは既存の
`compose.yaml`を使います。実機用は独立した`compose.live.yaml`を使います。

## ソースと処理

研究元の基点`b1dcbf3688db79f149ff3a255639a36860ec3924`で固定したnative imageを
[receiver.md](receiver.md)の手順でビルドします。実機用Pythonソースは
[研究元PR #76](https://github.com/hayatky/hlfec-sdr-lab/pull/76)の
`0b00caacacacd63f95b284575fa843583085f78f`を使います。
`live_sources.py`の12ファイルのSHA-256を起動時に照合し、相違があれば開始しません。
研究元のwrapperの再配布条件は未確定なので、Gitや配布imageへコピーせず、
利用権限のある人が別途取得して読み取り専用でbind mountします。

経路はIIOD → ci16_le 6.4 MS/s → 既存80/63変換 → cf32_le 512000000/63 S/s →
既存の階層別復調 → 188 byte TSです。独立した復調器を複製しません。
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
git -C /path/to/research fetch origin 0b00caacacacd63f95b284575fa843583085f78f
uv run --locked python scripts/prepare-live.py /path/to/research data/live-source
mkdir -p data/live-config data/live-app data/live-host
chmod 700 data/live-config data/live-app data/live-host
cp examples/live.json data/live-config/live.json
cp examples/live.env.example data/live.env
```

`data/live.env`のUID/GIDと各パスを実環境へ設定します。`SDR_RESEARCH_DEVICE_DIR`は
研究CLIが実際に使うdata/receiverと同じディレクトリです。別のlock用ディレクトリを
新設してはいけません。configの`profiles`は任意の短縮プリセットです。空でも全範囲を
探索でき、チャンネルごとに実測した受信設定を局一覧と一緒に保存します。

### チャンネルごとのRXゲイン

`profiles`に指定した`gain_db`（0〜30 dB）は、その物理チャンネルの診断取得、
スキャンでのTS取得、保存済みの局の選局に適用します。プリセット名を変更しても、
同じ物理チャンネルに適用します。Mode・GI等はスキャンで測定した値を保持します。
同じチャンネルに異なるゲインを指定した設定は拒否します。

例えば、27chで測定して30 dBを採用する場合は、`profiles`へ次の項目を追加します。
この値は[Issue #37の比較試験](issue-37-validation.md)で使用した条件であり、全局共通の推奨値ではありません。

```json
"ch27": {
  "channel": 27, "mode": 3, "gi": 0.125,
  "rate_b": 2, "interleave_a": 4, "interleave_b": 2,
  "gain_db": 30
}
```

視聴・スキャン・録画を止めてから設定を編集し、APIを再起動してください。
既存の局を再スキャンしなくても、次の選局から適用します。
受信中はセッションAPIの`receiver_metrics.rx_gain_db`、終了後はGit外の
`live-result.json`で実際の値を確認できます。DBにある局の設定は過去の検出時の記録です。
チャンネル別の指定がない場合、診断取得は従来の20 dB、選局は保存された局のゲインを使います。
ゲインを増やせば常に改善するわけではありません。入力の飽和、TS品質、映像音声を比較して決めてください。

### 外部CASの準備

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

「接続確認・スキャンの入力元」で「実機ライブ」を選び、UHF全範囲（13〜52ch）、
低い側、高い側、または指定範囲をスキャンします。実機構成の初期選択は全範囲です。
受信開始はボタン操作時だけで、全体は最長20分、各チャンネルも有限期限で止まります。
接続診断での未確認表示は、その項目を診断では測定しない意味です。

各チャンネルで0.9秒の診断IQを取得し、既存のCP探索とTMCC復号からMode・GI・階層構成・
符号率・時間インターリーブを調べます。複数のTMCCフレームが一致した設定でTSを取得し、
映像と音声を持つサービスを保存します。B階層64QAMとA階層13セグメントの
QPSK/16QAM/64QAMに対応し、それ以外の階層構成や不安定なTMCCは結果に表示します。
TMCCの検出だけでは視聴可能な局と扱いません。TSの復調失敗はチャンネル単位で表示し、
RX復元を確認して次へ進みます。取得や復元の異常では全体を停止します。
診断IQ・変換後IQ・TSはGit外に残るため、全範囲のスキャンに数GBの空きを確保してください。
保存された局を選んで
視聴し、最大5分の録画を開始できます。局名はスキャン中にA階層も復調し、同じ取得区間のSDTを別に読み取ります。
TSDuck 3.45-4798のCRC・連続性検査とARIB文字復号を使い、B階層のPATのTSIDと
SDTのTSID・サービスIDが一致した局名だけ保存します。A階層13セグメント構成では
同じA階層TSを使います。A側の局情報が欠落してもB側のサービス検出は継続します。
局名をSIから取得できないときは不明と表示し、
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
[USB再接続とWebUIの復旧ボタン](recovery.md)を使います。受信前の設定と別接続での
読み戻しを照合し、復旧記録を残してから制限を解除します。マーカーの手動削除では解除しません。

受信TS・録画・HLS・native resultは指定data directoryに残ります。Gitには追加しません。
録画タブで再生とTSダウンロードを行えます。人は映像の乱れ、音切れ、音ずれを確認し、
Chromiumの機械的frame/audio/time測定と分けて記録してください。
Safariの視聴・録画再生は2026-10-04にユーザー確認済みで、画質改善後の再確認は残ります。
録画一覧の「削除する」から録画TSと再生用ファイルを手動削除できます。
録画中・変換中は削除できません。詳しい範囲は[APIの説明](api.md)を参照してください。


局名の取得には`Dockerfile.live`でSHA-256を照合して導入するTSDuckが必要です。
既存の実機用コンテナは再ビルドしてください。ホストのuvで実行する場合も、
同じ固定バージョンの`tstables`をPATHへ用意します。「接続を確認する」で局名取得の
ツールの有無を確認できます。表示だけを更新しても過去の未取得名は補完されません。
再スキャンでSDTを取得すると、局一覧のIDを保持して局名を更新します。

既存の環境ファイルがある場合は、`uv run --locked python scripts/live-start.py data/live.env`で
一時ホスト準備とComposeの起動をまとめて行えます。上記の手動起動と同時に実行しないでください。
ホスト準備のプロセスだけがsudoを使い、Web APIからsudoやDockerを呼び出すことはありません。
