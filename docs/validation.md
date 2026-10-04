# 検証結果と対応環境

2026-10-04（日本時間）。このPoCを導入・評価する際に参照する検証記録をまとめています。
操作方法は[README](../README.md)、実機の準備は[実機からWebUIへ接続する手順](live-receiver.md)、
USB切断後の対応は[復旧手順](recovery.md)を参照してください。

## 記録の読み分け

| 記録 | 確認できること |
|---|---|
| [実機を既定にしたCompose起動](validation-compose-live.md) | 同梱ソースからのビルド、準備サービスの排他・停止復元、実機を使わない起動確認。従来の実機検証と区別 |
| [以前のCompose/uvによる導入検証](validation-install.md) | 新しい設定・保存先での合成デモ、実画面の操作、起動・停止、ホストから引き継いだ環境 |
| [実機での検証](validation-live.md) | スキャン、局の保存、代表2チャンネルの視聴、録画、設定の復元 |
| [受信品質の比較](validation-reception-quality.md) | チャンネル別ゲインとHLS起動時のキューの修正、300秒録画の品質、USB切断の失敗記録 |
| [配布内容と依存物の確認](publication-audit.md) | 同梱物・別途取得する依存物、出典とライセンス、ローカルイメージの確認範囲 |

合成デモは実機での受信を示しません。機械的なA/V検査と人による視聴、
新しいアプリ保存先での導入と新規OSへの導入も、それぞれ区別しています。

## 検証済みの範囲と制限

| 対象 | 確認済み | 残る制限 |
|---|---|---|
| 合成デモ | Compose/uv、UI/API、HLS、録画・再生・ダウンロード、期限・排他・異常終了。新しい設定・保存先でも確認 | Docker・ホストOS・ブラウザー等は既存。新規OSの導入試験ではない |
| 実機スキャン | 13〜52chを探索、8物理ch・21サービス保存、再起動後の局と実測設定の保持 | 全検出サービスのA/Vを保証しない。局名未取得や復調できないchあり |
| 実機の代表ch | 21ch・27chの受信中A/V、切替、停止・RX復元・独立読戻し・共通排他 | 確認したUbuntu 24.04/x86_64とPluto SDR nano互換ボード。別OS/ボード/FWは未確認 |
| 修正後の27ch録画 | 300.028秒、631,963,692 bytes、同区間のnative TSとダウンロードのhash一致。5分間の再生をユーザー確認 | 選択サービスCC 0、全体では対象外PIDにCC 1件。録画端のdecode警告あり |
| Chromium | 145.0.7632.6、機械的な映像・非ゼロ音声・時刻進行、合成UI操作 | 人の品質確認とは区別 |
| Safari | 21ch/27chの視聴・録画再生、修正後27chの約30秒のライブ映像ノイズなしをユーザー確認 | Safariの詳細バージョンは未記録。修正後5分録画の報告では再生ブラウザーも未指定で、推測しない |
| USB接続 | USB-Cケーブルと別PCポートの構成で300秒録画・停止・後始末成功 | 以前の構成で2回切断。原因と恒久安定性は#38で継続。ケーブル単独の効果とはしない |

#38の原因切り分けは継続します。有限試験の成功だけでは、原因の確定や恒久的な安定性を示せません。
過去の復元unknownは失敗記録として保持しますが、
その後の復旧APIによる復元・独立読戻しと最終試験の後始末は成功しています。
実機を再度使う場合は、その時点の機器・既存ジョブ・復元状態を再確認します。

## 検証対象の実装

実機の品質修正は`452859d`（チャンネル別RXゲイン）と`28ac043`（HLS開始時のキュー）です。
PR #39でmain `c41af0e`へ統合され、この実装を使ってCompose/uvの導入試験を行いました。
研究元Pythonソースは`0b00caacacacd63f95b284575fa843583085f78f`の12ファイルをhashで照合します。
この過去の検証では研究元wrapperと外部CASを別途取得しました。
現在の通常構成は必要なwrapperをGPL-3.0-or-laterで同梱し、外部CASを固定した公開上流から
ローカルビルドします。新しい起動経路の確認範囲は[別の検証記録](validation-compose-live.md)を参照してください。

## 継続している課題

USB切断の原因と恒久的な安定性は[PoC #38](https://github.com/hayatky/sdr-dtv-poc/issues/38)で調査しています。
受信処理の一部をボード内へ移す[研究元#74](https://github.com/hayatky/hlfec-sdr-lab/issues/74)と、
CATV経由のBS受信は将来の拡張です。前者は対象ボードで未実証、後者はこのWebUIでは未実装です。

## 過去の記録

以下は整理前の固定コミットにある記録です。実装の経緯や個別試験の詳細を調べる際に参照してください。
記録当時の未実装・確認待ちを、現在の未達条件として扱わないでください。
現在の操作・仕様はREADME、操作ガイド、API・受信処理の資料を参照してください。

| 内容 | 過去の詳細 |
|---|---|
| 初期構想と研究元からの引継ぎ | [初期コンテキスト](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/PROJECT-CONTEXT.md) |
| 基盤とバックエンド | [基盤の検証](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/issue-3-validation.md)、[スキャン・録画の検証](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/issue-4-backend-validation.md) |
| WebUIの実装 | [模擬画面の設計](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/webui-design.md)、[API接続の仕様と引継ぎ](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/issue-29-handoff.md)、[API接続の検証](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/issue-29-validation.md) |
| 実機での検証と品質改善 | [実機試験の全記録](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/issue-5-validation.md)、[品質比較の全記録](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/issue-37-validation.md)、[実機検証の引継ぎ](https://github.com/hayatky/sdr-dtv-poc/blob/2bf44f85e943dbdc7d00f71b584561d3b1639cfd/docs/issue-5-handoff.md) |
