# 実機からWebUIへ接続する

## 通常起動

初回利用はリポジトリ直下で `docker compose up --build -d --wait` を実行し、
`http://localhost:8000`を開きます。対象はUbuntu 24.04 / x86_64、rootful Docker Engine、
対応するボード1台と既存のRX配線です。スクランブルされた放送にはカードとUSBカードリーダーが必要です。
Dockerだけで同梱した固定ソースと公開上流からビルドし、設定ファイルもイメージへ用意します。
以下の「ローカルの準備」以降は、既に個別の設定・ソース・CASを管理している利用者向けの
従来の `compose.live.yaml` の手順です。通常起動と同時に実行しないでください。

通常構成の `receiver-host` は、対象USBのRNDISへ一時アドレスを設定するための
`NET_ADMIN`と、保存先・ソケットを準備するための`CHOWN`・`FOWNER`・`DAC_OVERRIDE`だけを持ちます。
ホストにPC/SCソケットがあれば、rootで動く固定のUNIXソケット中継を介して再利用します。
中継先はホストのPC/SCだけで、接続元はこの構成の非root APIが使う0600のソケットです。
ホストのPC/SCに対してはrootとして接続するため、この構成を使う利用者にカード利用を許可する運用が前提です。
ホストの認証ポリシーやソケット権限は変更しません。通常のソケット接続に伴うサービスの自動起動はあり得ます。
PC/SCがない場合だけコンテナ内のpcscdがUSBカードリーダーへアクセスします。ホストのサービスや恒久設定は変更せず、
`privileged`、Docker socket、sudo、ホストのsystemd操作は使いません。
`app`にはこれらの権限とUSBデバイスを渡しません。HTTPはlocalhostだけに公開します。
機器接続の準備が成功してからAPIを起動しますが、これは放送波の受信成功を意味しません。

`docker compose down`はAPIを先に停止し、受信ワーカーの終了を待ってから
一時アドレスを削除し、RNDISを元のdown状態へ戻します。
未解決のRX復旧記録や終了しないワーカーがある場合は接続を残して失敗します。
復旧記録を削除して再開せず、[復旧手順](recovery.md)に従ってください。
通常構成は自動のUSB再接続・受信再開を行いません。

## 既存環境との共存

初めて使うPCでは、共有する排他・復旧記録をホストの `/var/lib/sdr-dtv-poc/device` に作成します。
Compose project名や保存先を変えても同じ場所を使い、ホスト準備自体にも共通の排他があります。
研究ツールで同じボードを使っていた場合は、`.env`の`SDR_DEVICE_DIR`を研究ツールの
実際の `data/receiver` の絶対パスへ設定し、`SDR_UID`と`SDR_GID`をその所有者に合わせます。
既存のファイルを移動・削除したり、新しい排他ディレクトリで回避したりしないでください。
従来のホスト準備・受信を正常に終了し、復元を確認してから新しい構成へ移行します。
既存の録画や局一覧は移動せず、通常構成は専用の`live-data`ボリュームを作成します。

## 通常起動でのエラーと対処

`docker compose logs receiver-host`で短いエラー理由を確認できます。
ログやローカル設定をそのまま公開しないでください。

| エラー | 確認すること |
|---|---|
| `expected_one_sdr_board` / `expected_one_rndis_interface` | 対応ボード1台のUSB接続とRNDIS認識 |
| `host_pcsc_not_socket` / `pcsc_start_failed` | ホストのPC/SCソケットが有効か、カードリーダーが認識されているか。既存サービスの設定は変更しない |
| `rndis_in_use` / `board_subnet_in_use` | 従来の受信ツール・ホスト準備が残っていないか。既存の接続を無条件に削除しない |
| `device_busy` / `host_preparation_in_use` | 別の受信・復旧・Composeが動いていないか |
| `directory_owner_mismatch` | 既存の共有ディレクトリの所有者と`SDR_UID`・`SDR_GID`が一致しているか |
| `host_restore_unverified` / `rx_restore_unverified` | 接続またはRXの復元を確認できていない。残した接続と復旧記録を調べ、復旧手順で照合する |

ホストに既存の受信設定がある場合は、上記の競合解消が先に必要です。
機器の認識や電波の品質までDockerの起動だけで保証するものではありません。
## 従来の手動構築を使う場合

以下はUbuntu 24.04 / x86_64 / Docker Engineで確認した開発者向けのローカル経路です。
対象は確認済みの1ボード、既存のSLAVE/RNDIS接続、1物理ch・1録画です。
macOS、Windows、別ボード/FWへの互換性は未確認です。通常の実機起動は`compose.yaml`、
合成デモは`compose.demo.yaml`、以下の手動構築は`compose.live.yaml`を使います。

## ソースと処理

元の研究リポジトリは今後も非公開です。以下の旧経路は、必要なGitオブジェクトと
旧native imageを既に保持している保守担当者だけを対象とします。新規利用者は冒頭の通常起動を使ってください。
旧構成では`b1dcbf3688db79f149ff3a255639a36860ec3924`を基点とするnative imageと、
`0b00caacacacd63f95b284575fa843583085f78f`に由来するPythonソースを使います。
`live_sources.py`の12ファイルのSHA-256を起動時に照合し、相違があれば開始しません。
通常構成では必要なwrapperを`native/receiver`へGPL-3.0-or-laterで同梱し、
ライセンス表示追加後のハッシュ集合も照合します。以下の従来構成では、
既に取得済みのソースを読み取り専用でbind mountします。由来と収録後のハッシュは
[同梱ソースの説明](../native/README.md)で確認できます。

経路はIIOD → ci16_le 6.4 MS/s → 既存80/63変換 → cf32_le 512000000/63 S/s →
既存の階層別復調 → 188 byte TSです。独立した復調器を複製しません。
B階層TSはA/B全階層の元の多重TSではありません。受信TSを保存し、録画は同じ
TSのbyte列を分岐します。HLSと録画再生だけに外部CASとFFmpegを使います。

IQは64 MiBの有限キューへ渡し、全量保存しません。処理遅れ5秒、TS未消費32 MiB、
native TSファイル2 GB、IQ書込み停止1.5秒等の上限で失敗として止めます。
TSの初期同期前の不正packetは最大約100 packetだけ調べ、5 packet連続同期から
渡します。除外量を記録し、元のnative TSもGit外に保持します。途中では再同期せず
不正packetをAPIが失敗として扱います。RS廃棄の推定値は画面に警告表示します。

HLSへの配信キューは2,048個です。通常の1,316 byte単位では約2.57 MiBを保持し、
CAS・codecの起動時に入力を捨てずに待ちます。上限到達や書込み停止2秒では配信を失敗として表示し、
遅延やメモリー使用量を無制限に増やしません。

受信ワーカーは共有`.device.lock`のFDを継承し、inodeも照合します。研究元の
未終了job・復元unknown/failedと復旧記録も検査します。stdin切断と期限で停止し、
CLOSE・復元の後に別接続でRX設定を読み戻します。復元不明なら次回開始を遮断します。
APIからsudo・Docker socketを操作しません。

## ローカルの準備

既存の研究checkoutやデータを上書きせず、新しいGit外ディレクトリを使います。

```sh
# 必要な固定Gitオブジェクトを既に保持している保守担当者向け。新規取得は不要
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
この値は[Issue #37の比較試験](validation-reception-quality.md)で使用した条件であり、全局共通の推奨値ではありません。

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
HLSの生成と実際のA/Vで確認します。CASやnative imageは同梱していません。

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
Safariでは以前の両チャンネルの視聴・録画再生と、修正後27chの約30秒のライブ映像を
ユーザーが確認しています。修正後の5分録画も再生確認済みですが、この報告のブラウザーは
未指定です。現行の各操作の確認範囲と詳細バージョンは#23で照合します。
録画一覧の「削除する」から録画TSと再生用ファイルを手動削除できます。
録画中・変換中は削除できません。詳しい範囲は[APIの説明](api.md)を参照してください。


局名の取得には`Dockerfile.live`でSHA-256を照合して導入するTSDuckが必要です。
既存の実機用コンテナは再ビルドしてください。通常の`Dockerfile`にも同じTSDuckを同梱しており、
ホストへの導入は不要です。「接続を確認する」で局名取得の
ツールの有無を確認できます。表示だけを更新しても過去の未取得名は補完されません。
再スキャンでSDTを取得すると、局一覧のIDを保持して局名を更新します。

従来の`live-start.py`は既存環境との互換性のために残しています。新しく導入する場合は、
[READMEのDocker Compose手順](../README.md#docker-composeで実機を起動)を使ってください。
Web APIからsudoやDockerを呼び出すことはありません。
