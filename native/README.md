# 受信処理の固定ソース

初回のDockerビルドが非公開リポジトリの認証や既存のローカルイメージに依存しないように、
必要なPythonソース14ファイルだけを収録しています。復調器の新規実装ではありません。

- `receiver/`：研究元の `0b00caacacacd63f95b284575fa843583085f78f` から、
  既存のlive adapterが使う12ファイルを抽出。
- `build/`：研究元の `b1dcbf3688db79f149ff3a255639a36860ec3924` から、
  固定したgr-isdbtソースを抽出・変換するスクリプト2ファイルを抽出。
- `manifest.json`：元のパス・コミット・SHA-256、収録した内容のSHA-256と変更点。

自作部分は権利者の指定によりGPL-3.0-or-laterで提供します。
ライセンス全文はリポジトリルートの `LICENSE` にあります。
元の実行処理は変更せず、SPDX表示と下記の帰属表示を追加しています。
上流の構造と差分を保つため、自動整形・Ruffの対象からは除外しています。
ハッシュ、Python構文、nativeビルド、import、有限入力の動作を別途検証します。
更新時に受信データ・設定・ログを一緒にコピーしないでください。

## 上流への帰属

`receiver/pocs/isdb-t-ts/wideband/multiplex_model.py` のクロックモデルは、
gr-isdbtの `utils/multiplex_frame_pattern.py`（Pablo Flores Guridi、2017）を
参照しています。上流はGPL-3.0-or-laterで、元の著作権表示とライセンス通知を
`UPSTREAM-LICENSE` に保持しています。

- ソース： https://github.com/git-artes/gr-isdbt/blob/56b2556c14ecc5d710070f969fda7a2deae65d8b/utils/multiplex_frame_pattern.py
- 通知： https://github.com/git-artes/gr-isdbt/blob/56b2556c14ecc5d710070f969fda7a2deae65d8b/LICENSE

C++受信ブロックはこのディレクトリへ複製せず、Dockerfileが公開上流の固定コミットから
取得します。変換スクリプトは元の著作権表示・LICENSE・COPYINGを保持し、
変換前後のhashをイメージ内へ記録します。既存のOFDM探索境界・Viterbi状態等の修正も
この固定スクリプトに含まれます。外部CASは別途、固定した公開上流を無改変でビルドします。

収録内容には機器の個体情報、秘密、放送IQ/TS、映像・音声、研究用の設定・ログを含めません。
ボード接続用の既定アドレス・USB VID/PIDは既存の受信処理が使う製品の接続条件です。
任意のネットワークや別のボードへ自動適用しません。
