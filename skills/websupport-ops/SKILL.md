---
description: WebSupport（tactgroup.net）の生徒受付管理・メッセージ・OKS発注等を操作する
---

# WebSupport 運用

「問い合わせ一覧」「問い合わせ検索」「新着確認」等と言われたら実行する。

## 概要

- URL: WebSupport（環境変数 WEBSUPPORT_URL、デフォルト: https://www.tactgroup.net）
- 認証方式: POST login.php + セッションCookie (PHPSESSID)
- エンコーディング:
  - HTML: EUC-JP 宣言、実体は euc-jisx0213（JIS X 0213拡張文字を含む）
  - CSV ダウンロード: **Shift_JIS_2004 (JIS X 0213)**。cp932 で読むと拡張文字が化ける
  - POST 送信: `euc-jisx0213` エンコード必須（`_euc_encode` ヘルパー）
- セッションタイムアウト: 自動検知→再ログイン実装済み

## 文字コードの落とし穴

WebSupportは **JIS X 0208** に限定されたEUC-JPを宣言しているのに、実体は **JIS X 0213** 拡張文字を扱える euc-jisx0213 / shift_jis_2004 でデータを入出力している。そのためブラウザ表示とサーバー内部で一致しないケースがある。

### 観測例（2026-04-23）
生徒氏名「河野 萊駕」を登録したところ、サーバーCSVは `ee45` (shift_jis_2004の萊)、HTMLは `fba6` (euc-jisx0213の萊) を返すが、Chromeの標準EUC-JPデコーダは `fba6` を **珉 (U+73C9)** と誤デコードする。

### 対処ルール
1. **CSV読み取り**: 必ず `shift_jis_2004` で decode する。`cp932` は使わない（萊→珉 のように別字に化ける）
2. **POST送信**: `euc-jisx0213` で URL エンコード（`_euc_encode` を使う）
3. **JIS X 0208 外の文字**: ブラウザが正しく表示できない場合は、JIS X 0208 にある異体字（例: 萊→莱 U+83B1、髙→高、﨑→崎）に置き換える。置き換え時は元の文字を必ず記録（メモ欄や備考）。
4. **既存データの化け確認**: CSVを `shift_jis_2004` で読んだ結果と、ブラウザで表示される文字が違う場合は、WHATWG EUC-JP デコーダのフォールバック問題。対処3を適用。

## MCPツール

### 生徒受付管理
- `applicant_list` — 問い合わせ一覧取得
- `applicant_detail` — 問い合わせ詳細
- `applicant_search` — 名前・電話・メールで検索
- `applicant_new_count` — 未開封件数
- `applicant_download_csv` — CSV一括ダウンロード
- `applicant_register` — 新規登録
- `applicant_update` — フィールド更新
- `applicant_update_memo` — メモ欄更新
- `applicant_delete` — 削除

### メッセージボックス
- `message_list` / `message_detail` / `message_search`

### SafetyMail (SSO経由)
- `sfm_attendance_list` — 出席簿
- `sfm_student_list` / `sfm_student_detail` — 生徒情報
- `sfm_inbox` / `sfm_inbox_detail` — 連絡帳受信
- `sfm_sendbox` / `sfm_sendbox_detail` — 連絡帳送信
- `sfm_ganbaru_list` / `sfm_ganbaru_detail` — がんばるポイント

### OKS受発注
- `oks_bihin_list` / `oks_bihin_detail` — 備品
- `oks_kyouzai_list` / `oks_kyouzai_detail` — 教材
- `oks_cart_add` / `oks_cart_view` / `oks_order_list` — カート・発注

### その他
- `top_page` — TOPICS記事一覧
- `manual_categories` / `manual_search` — マニュアル検索
- `movie_list` / `movie_detail` / `movie_download_url` — 動画

## 注意

- ログインに繰り返し失敗するとアカウントロックされる。失敗時は絶対にリトライしない
- 詳細は `docs/websupport-endpoints.md` と `docs/websupport-gui-automation.md` を参照
