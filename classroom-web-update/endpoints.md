# 教室HP管理CMS (schoolie-net.jp) エンドポイント一覧

## エンコーディング
- HTMLレスポンス: **UTF-8**（charset=utf-8宣言）
- WindowsのPython stdoutがCP932のため日本語出力が文字化けする
- **対策**: `PYTHONIOENCODING=utf-8` 環境変数を設定するか、`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')` をスクリプト冒頭に入れる
- POSTデータ送信: UTF-8で問題なし

## 認証
- GET `https://www.schoolie-net.jp/console/` — ログイン画面
  - フォーム: ログインID（text）、パスワード（password）、ログイン（submit）
- POST `https://www.schoolie-net.jp/console/` — ログイン → リダイレクト GET `/console/top`
- GET `/console/users/logout/` — ログアウト → リダイレクト `/console`
- 認証情報は各教室のスキルファイルを参照
- **User-Agentヘッダー必須**（requestsデフォルトだと403）

## トップ
- GET `/console/top/` — お知らせ一覧（ページネーション対応、9ページ）

## 教室版管理
- GET `/console/editions/{教室ID}/view/1` — 版一覧（ページID、ステータス、状態、公開日、更新者）
- GET `/console/editions/{教室ID}/edit/{版ID}` — 教室ページ編集（345フィールド）
  - ボタン: プレビュー / コピーして新規作成 / 版管理に戻る / 一時保存 / 保存後承認 / 保存後承認依頼 / 削除
  - フォーム共通: _method(hidden), _csrfToken(hidden), status(hidden), start_date, end_date, edition_memo[memo]
- POST `/console/editions/{教室ID}/edit/{版ID}` — 保存（フォームデータ送信）
  - hidden field `status`: 0=未保存, 1=一時保存, 2=保存後承認依頼
  - JS: `$(".status").click → $("#status").val(data-status) → form.submit()`
  - User-Agentヘッダー必須（requestsデフォルトだと403）
- GET `/console/editions/{教室ID}/add/1` — 新規作成
- GET `/console/editions/{教室ID}/copy/{版ID}` — 版コピー → リダイレクト edit/{新版ID}
- POST `/console/editions/{教室ID}/delete/{版ID}` — 版削除（ブラウザではconfirmダイアログ→POST）

## 編集タブ・フィールド一覧

### 詳細情報タブ (classroom_detail)
- target[] (checkbox) — 対象学年
- course[] (checkbox) — コース
- facility[] (checkbox) — 設備
- access (textarea) — アクセス情報
- native_school1 (textarea) — 近隣小学校
- native_school2 (textarea) — 近隣中学校
- native_school3 (textarea) — 近隣高校
- success_record3 (textarea) — 中学受験合格実績
- success_record4 (textarea) — 高校受験合格実績
- success_record5 (textarea) — 大学受験合格実績
- detail_class_images[0-9][explains] (textarea) — 教室画像説明文

### お知らせタブ (classroom_info)
- introduction (textarea) — 教室紹介文
- info_details[campaign][N][header] (textarea, **HTML可**) — キャンペーン見出し
- info_details[campaign][N][content] (textarea) — キャンペーン内容
- info_details[campaign][N][introduction] (textarea) — キャンペーン補足
- info_details[topics][N][header] (textarea, **HTML可**) — トピックス見出し
- info_details[topics][N][content] (textarea) — トピックス内容
- info_details[topics][N][introduction] (textarea) — トピックス補足
- info_details[classroom][N][header] (textarea, **HTML可**) — 教室情報見出し
- info_details[classroom][N][content] (textarea) — 教室情報内容
- info_details[free][N][header] (textarea, **HTML可**) — フリー枠見出し
- info_details[free][N][content] (textarea) — フリー枠内容

### スタッフ情報タブ (classroom_staff)
- greetings_header (textarea, **HTML可**) — 教室長挨拶見出し
- greetings (textarea, **HTML可**) — 教室長挨拶本文
- staff_details[N][header] (textarea, **HTML可**) — スタッフ見出し
- staff_details[N][content] (textarea, **HTML可**) — スタッフ紹介文

#### ⚠ スタッフスロットと写真の対応（重要）
CMS上の「講師情報 N」はフォーム配列 `staff_details[N-1]`（0始まり）に対応する。
各スロットには**氏名・写真**がCMS側で個別に紐づいているため、header/contentを書くindexを間違えると**写真と紹介文がズレる**。
講師の追加・削除・並び替えがあった場合は、**必ずCMS編集画面（GETで取得）の氏名フィールドと表示順位を確認してから**配列を更新すること。

### 体験談情報タブ (classroom_experiences)
- [N][header] (textarea) — 体験談見出し
- [N][content] (textarea) — 体験談内容

### コース・時間割・料金タブ (classroom_course)
- timetable_flag (radio) — 時間割 表示/非表示
- timetable_info (textarea) — 時間割紹介文
- timetable1 (textarea, **CKEditor**) — 時間割HTML
- timetable_annotation (textarea) — 時間割注釈
- fee_flag (radio) — 授業料 表示/非表示
- fee_info (textarea) — 授業料紹介文
- fee1 (textarea, **CKEditor**) — 授業料HTML

## アカウント設定
- GET `/console/users/classroom_user_edit/{ユーザーID}` — メールアドレス・パスワード変更
  - POST で更新

## プレビュー・公開ページ
- GET `/console/editions/{教室ID}/preview/{版ID}` — プレビュー（新しいタブで開く）
- GET `/classrooms/detail/{教室ID}/` — 公開中の教室ページ
- 教室IDは教室固有値（各教室のスキルファイルを参照）

## 資料
- GET `/cms_pdf/ie_classroom_update.pdf` — 教室HP更新マニュアル

## ⚠ 独自リンク禁止（2026-04-10判明）
CMSに掲載するリンクは **schoolie-net公式フォームのみ** 使用可。
TimeRex、Google Forms等の外部サービスへの独自リンクは禁止。
- 公式フォームURL: `https://www.schoolie-net.jp/form/entry.php?mode=2&ccd=5558`
- 予約系ボタン: 「学習相談・教室見学はこちら」
- 検定問い合わせ: 「検定に関するお問い合わせはこちら」（3検定まとめて1リンク）

## ⚠ XSS脆弱性（2026-04-07検証）
プレーンtextareaフィールドはサーバー側でサニタイズされず、HTMLがそのままレンダリングされる。
CKEditorフィールド（timetable1/fee1）はCKEditorが一部タグを整形するが、script/style/iframeは通過する。
テストは版コピー→編集→プレビューで本番に影響なく実施可能。
