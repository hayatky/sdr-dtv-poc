# Issue #5への引継ぎ

更新日: 2026-10-04（日本時間）。PR #36のレビュー後の引継ぎです。
段階2はPR #31・#32・#34、実機への接続と追加操作はPR #36を参照してください。
実装・過去の実機試験・今回の機器なしの回帰試験を区別し、親#5は未完了とします。

## 完了した範囲と残る作業

| Issue | 受け入れた成果 | 完了判定・残る作業 |
|---|---|---|
| #19 | 合成TSのCompose/uv起動、UIとAPIの一連の操作、300秒期限、主要な異常経路 | 完了。下記の対応表と検証記録を参照。全資源上限へ達する長時間実験は未実施 |
| #20 | 固定研究元コード、21ch/27chのTS/SI、共通8秒以上のA/V、旧TS混入なし、停止とRX復元 | 先行成果の受入れは完了。研究元PR #76自体のマージは別作業 |
| #21 | 全UHFスキャン、8物理ch・21サービス保存、再起動後の設定保持、代表chのライブA/V・切替 | 継続。反復USB切断後の機器確認と実機での復旧操作、#37の品質確認が残る |
| #22 | 21chの実時間300秒録画、同じ区間のPCR/A/V、ダウンロードhash一致、手動停止後の視聴 | 継続。#21の残項目と、#37で修正後の同時視聴・300秒録画・再生の品質を確認 |
| #23 | Chromiumの機械的A/V、変更前のSafariで21ch/27chの視聴・録画再生をユーザー確認 | 継続。現行の画質設定・局名・復旧操作等のSafari確認と、人による品質確認が残る |
| #37 | 保存PoC TSで定常区間にもCC不連続を確認。比較手順をIssueに記載 | PR #36のマージ後に別途対応。#5を閉じる前の必須作業 |

### #19の根拠と確認範囲

[段階2の検証](issue-4-backend-validation.md)、[WebUIの検証](issue-29-validation.md)、
[段階3の検証](issue-5-validation.md)に対象コミット・コマンド・結果があります。
同じ試験を繰り返す代わりに、以下の既存結果とレビュー時の回帰試験を対応付けました。

| 確認する動作 | 根拠 |
|---|---|
| Compose/uvで診断・スキャン・保存・再起動・選局・HLS・録画・再生・ダウンロード・停止 | 段階2とWebUIの検証記録、`scripts/smoke-webui.py` |
| 連打・複数タブ・再読込み、録画中の禁止操作、自動RXなし | `tests/test_app.py`、`tests/webui-lifecycle.js`、WebUI検証記録 |
| 画面からの取得が止まっても録画期限を守る | 専用ComposeでHLS非取得のまま300.013秒の合成録画。実際のページcloseは別の実機試行で確認 |
| 容量・書込み・DB・受信ワーカー異常、partialと再起動 | `tests/test_stage2.py`の録画・再起動試験、`tests/test_app.py`のadapter・DB試験 |
| CAS/FFmpeg異常、遅い配信側、HLS保持上限 | `test_cas_flush_failure_is_not_completed`、`test_media_queue_cas_and_retention`、段階3のfake consumer試験 |
| 停止猶予、子回収、共通排他、復元不明時の開始拒否 | `test_stop_timeout_blocks_restart`、`tests/test_live.py`、`tests/test_discovery.py`、`tests/test_recovery.py` |

合成/模擬試験は実機を操作しません。今回の追加試験も自作データだけを使います。
実時間300秒の合成結果を実機の証拠とはせず、時計を注入した期限試験とも区別します。

### #20の採用コードと設定

研究元コミットは`0b00caacacacd63f95b284575fa843583085f78f`です。
`live_sources.py`で12ファイルのSHA-256を固定し、`prepare-live.py`で抽出・照合します。
[研究元のコードと検証記録](https://github.com/hayatky/hlfec-sdr-lab/blob/0b00caacacacd63f95b284575fa843583085f78f/docs/stream-sink-poc-validation.md)を
照合しました。研究元#62・PR #76は確認時点でOPENですが、Issueの状態だけで成果を判定していません。

21chは521142857 Hz・TSID32740・service1056、27chは557142857 Hz・TSID32736・
service1024。双方Mode3/GI1/8、A=QPSK2/3・TI4・1seg、B=64QAM3/4・TI2・12segです。
共通A/Vは8.659167秒・8.756489秒で、相手chのサービスが混入しないことと停止復元を確認済みです。
PoCは実測設定を`Service.profile`へ保存し、`LiveProfile.from_service`から選局へ渡します。
局のIDと設定名を分け、設定名変更や局名追加でも同一局のIDを維持します。
この二つは確認済みの代表chであり、製品の対応範囲を2chに制限しません。

## 次に進める順序

1. 最新main、作業ツリー、既存プロセスと保存先を確認する。過去のIQ/TS・録画・DBを保全する。
2. #37の保存データ比較は機器を使わず開始できる。比較対象の受信区間とコードを一致させる。
   調査の具体的な手順と完了条件は[#37](https://github.com/hayatky/sdr-dtv-poc/issues/37)を使う。
3. 実機を使う前に、反復切断後の発熱・USB接続を人が確認したこと、他処理の利用状況、
   USB/RNDIS・RF配線・RX baseline・共通排他を確認する。[復旧ガイド](recovery.md)に従い、
   別接続の読戻しで復元を確かめる。unknown/failedのまま受信を再開しない。
4. #37の修正後に同じ区間のTS品質・ライブ視聴・同時300秒録画・録画再生を照合する。
   代表chを替えた退行確認、停止・復元・排他解放、Chromium/Safariと人の確認を記録する。
5. #21〜#23と#37の完了条件を再判定し、その後に親#5を判定する。

今回のレビューでは新規RX、機器への復旧書込み、既存の復旧待ちマーカーの解除は行っていません。
実機用の起動・停止は[導入手順](live-receiver.md)、検証コマンドと結果は
[検証記録](issue-5-validation.md)を参照してください。Web APIからsudoやDockerを操作しません。
TX、Flash/FW/FPGAや永続設定の変更は通常RXに含めません。

放送素材、生ログ、個体識別子、個人の環境情報は公開しません。
研究元wrapperとCASは別途取得し、配布条件の未確認部分を既定imageへ持ち込みません。
Actionsは無効でCI未実行です。公開設定・ブランチ保護は変更しません。
