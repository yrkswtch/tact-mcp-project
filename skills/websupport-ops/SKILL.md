---
description: WebSupport（tactgroup.net）の生徒受付管理・メッセージ・OKS発注等を操作する
---

# WebSupport 運用

「問い合わせ一覧」「問い合わせ検索」「新着確認」等と言われたら実行する。

## 概要

- URL: WebSupport（環境変数 WEBSUPPORT_URL、デフォルト: https://www.tactgroup.net）
- 認証方式: POST login.php + セッションCookie (PHPSESSID)
- エンコーディング: EUC-JP (HTML), CP932 (CSV)
- セッションタイムアウト: 自動検知→再ログイン実装済み

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
