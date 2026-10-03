# 依存物の出典とライセンス

2026-10-04、`uv.lock`から導入したdistribution metadataと同梱licenseを照合しました。
Python配布物はPyPIの各プロジェクトを出典とし、取得URL・配布物ごとのSHA-256は
`uv.lock`にあります。依存コードは変更していません。dev依存はアプリimageへ入れません。
各著作権表示・licenseファイルをwheelのdist-infoに保持します。

| distribution | バージョン | ライセンス |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| ast_serialize | 0.11.2 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.142.2 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.0 | MIT |
| librt | 0.16.0 | MIT |
| mypy | 2.3.1 | MIT |
| mypy_extensions | 1.1.0 | MIT |
| opentelemetry-api | 1.45.0 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pathspec | 1.1.1 | MPL-2.0 |
| pluggy | 1.6.0 | MIT |
| pydantic | 2.13.5 | MIT |
| pydantic_core | 2.46.5 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| ruff | 0.16.9 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |

ビルド時はHatchling 1.29.0（MIT）、uv 0.12.18（MIT OR Apache-2.0）を使用します。
Gitleaks 8.30.1（MIT）は既存の開発・公開前検査だけで、アプリへ同梱しません。

Vue 3.5.22はMIT、hls.js 1.6.13はApache-2.0です。npm registryのバージョン固定tarballから
必要なproductionファイルと元のLICENSEだけを取り出し、Node/npmは実行していません。
`src/sdr_dtv_poc/static/vendor/manifest.json`に取得元と各ファイルのSHA-256を記録しています。
VueはCSPでevalを許可せず使えるruntime-onlyのglobal buildを採用しました。
UI担当はテンプレートの実行時コンパイルではなく、Vueの`h()`等を使ってください。
hls.jsの元配布物のLICENSEとApache-2.0全文も同じディレクトリに保持します。

OSのFFmpeg・GNU Radioと研究元は[THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)と
[受信処理](receiver.md)を参照してください。ソース提供が必要なbinary/imageは今回配布しません。

段階2の実行依存は追加していません。FFmpegの既存ビルドでlibx264とAACを使用します。
任意のブラウザー検証にだけ[Playwright Python 1.58.0](https://github.com/microsoft/playwright-python)
を一時環境で使いました。distributionのLicense-Expressionと同梱LICENSEはApache-2.0です。
アプリのlockfile・イメージへ追加せず、ブラウザーや検証用バイナリも本リポジトリから配布しません。
