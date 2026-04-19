# WebSupport (tactgroup.net) エンドポイント一覧
<!-- 最終更新: 2026-04-16 Chrome DevTools MCP による実地探査 -->

## 認証
- POST `/contents/class/login/login.php` — ログイン (classAccount, classPassword, btnLogin.x, btnLogin.y)
- セッション: PHPSESSID cookie
- エンコーディング: EUC-JP (HTML), CP932 (CSV)
- Google Analytics: `_ga`, `_ga_50781TQBM9` cookie
- JS: `/contents/class/js/default.js`（共通JS、全ページで読み込み）
- GTM: `G-50781TQBM9`
- **⚠ ログイン失敗を繰り返すとアカウントロック。リトライ禁止。**

## 生徒受付管理 (applicant)
- GET `/contents/boshu/class/applicant/applicantListPre.php` — 一覧前処理（セッション確立に必要）
- GET/POST `/contents/boshu/class/applicant/applicantList.php` — 一覧表示（検索条件POSTで絞込み）
  - **検索フォーム詳細** (2026-04-16 DevTools探査で確認):
    - `reception_time_start` (id=startEntryDate) — 受付日開始
    - `reception_time_end` (id=endEntryDate) — 受付日終了
    - `boshu_type` (select) — 受付タイプ: ""=全て, 1=資料請求, 2=学習相談・教室見学, 3=無料体験授業
    - `name` (text) — 氏名検索
    - `tel` (text) — 電話番号検索
    - `mail` (text) — メールアドレス検索
    - `open_flag` (select, id=open_flag) — 開封状態: ""=全て, 0=未開封, 1=開封
    - `view_count` (text, default=50) — 表示件数
    - `memo` (text) — メモ検索
    - `status[N]` (checkbox+hidden) — ステータス絞込み（前列）: status[1], status[2], status[3], status[9], status[10], status[4]
    - `statusbackrow[N]` (checkbox+hidden) — ステータス絞込み（後列）: statusbackrow[11], statusbackrow[12], statusbackrow[5], statusbackrow[6], statusbackrow[7]
    - `search` (hidden, value=1) — 検索フラグ
    - 絞込み解除: `window.location.replace('./applicantListPre.php')`
  - 一覧テーブル: 51行×15列（1行目=ヘッダ、50行=データ、view_count=50時）
  - テーブルCSSクラス: `table`（6番目のtable要素）
  - 新規登録ボタン: `location.href='./applicantNew.php'` (onclick)
- GET `/contents/boshu/class/applicant/applicantDetail.php?rei={ID}&num={N}` — 詳細表示
- POST `/contents/boshu/class/applicant/applicantNew.php` — 新規登録（3ステップ）
  1. GET applicantNew.php → 入力画面（hiddenフィールド収集）
  2. POST applicantNew.php + btnToConfirm → 確認画面（EUC-JPエンコード必須）
  3. POST applicantNew.php + 確認画面のhidden → 確定
  - **エンコーディング: EUC-JP**（UTF-8で送ると文字化けしてバリデーションエラー）
  - selectフィールドは数値IDで送信（contact_channel: HP=1,その他=0 等）
  - 受付日時: `YYYY-MM-DD HH:MM` 形式（ハイフン区切り、スラッシュ不可）
  - inspire[N]（認知動機）とmotive[N]（問合せ動機）は最低1つ必須
  - ✅ 実装完了: EUC-JPエンコードPOSTで確認画面→btnRegisterで確定
  - 注意: Chromeで同時にWebSupportを開いているとセッション競合でPOSTが失敗する
  - チェックボックス: hidden field (value=0) があるので、inspire%5B4%5D=1 のようにURL encoded []で送信
  - 認知動機: inspire[4]=ホームページ, 問合せ動機: motive[4]=インターネット検索
  - select値: contact_channel(HP=1), inquiry(その他=0), relationship(父=1,母=2), sex(男=1,女=2), grade(小1=1...高3=12), prefecture(埼玉=11)
- POST `/contents/boshu/class/applicant/applicantDetail.php?rei={ID}&num={N}` — 受付開始 (btnAccept=1)
- POST `/contents/boshu/class/applicant/download.php` — CSVダウンロード (btn_download)
- GET/POST `/contents/boshu/class/applicant/applicantEdit.php?rei={ID}&num=1` — 編集（2ステップ）
  1. GET applicantEdit.php?rei={ID}&num=1 → 編集画面（全フィールドpre-fill済み）
  2. POST applicantEdit.php?rei={ID}&num=1 + btnToConfirm=更新内容を確認する → 確認画面
  3. POST applicantEdit.php?rei={ID}&num=1 + btnRegister=登録する → 確定（Detailにリダイレクト）
  - **EUC-JPエンコード必須**（applicantNewと同じ）
  - **必須フィールド**: 保護者姓カナ(parent_kana_sei)のみ（空なら「ア」で補完）、inspire/motive（最低1つ=1）、inquiry、relationship、sex、grade、prefecture
    - ※ parent_kana_mei / student_kana_sei / student_kana_mei は**任意**。空欄OK、「ア」を入れないこと
  - **日時フィールド**: `trial_time` / `interview_time` 等は `YYYY-MM-DD HH:MM` **ハイフン区切り**。スラッシュ形式はエラー文言なしでフォームに戻される
  - **体験関連フィールド名**: `trial_time`（体験日時）/ `trial_support_staff`（体験担当講師）/ `trial_memo`（体験時メモ）
  - フォーム全体を送信する必要あり（変更フィールドだけでなく全hidden+input+select+textarea+checkbox）
  - settle_batch.py の settle_one() に完動する実装あり
  - ✅ 実装完了: applicant_update_memo, applicant_update ツール
- POST `/contents/boshu/class/applicant/applicantDelete.php` — 削除（一覧画面から）
  - **削除は一覧画面のフォームからPOST**（個別詳細画面からではない）
  - フォームaction: `./applicantDelete.php`, method: POST
  - パラメータ: `btn_delete=削除する` + `rce[N]=内部ID`
  - **内部ID（rce value）≠ 問合せNO（rei）**: 一覧HTMLのcheckboxに付いたvalueは独自の内部ID
  - 対応関係は一覧HTML内の `<a href="applicantDetail.php?rei={問合せNO}">` と同じ行の `<input type="checkbox" name="rce[N]" value="{内部ID}">` から取得
  - onclick: `confirm('削除してもよろしいですか？')` — JS確認ダイアログ（サーバー側バリデーションなし）
  - 検索結果画面でも削除可能（search=1のPOST後の一覧にもcheckbox+削除ボタンあり）
  - **実装手順**: (1) GET applicantListPre.php → (2) POST applicantList.php で検索 → (3) HTMLからrce value取得 → (4) POST applicantDelete.php
- POST `/contents/boshu/class/applicant/mailListPre.php` — メール通知
  - フォーム名: `classMailForm`
  - パラメータ: `btn_mail=メール通知する`
  - ※一覧画面のメール通知ボタンから遷移

## CSVフィールド (58列, CP932)
問合せNO, 受付日時, 教室CD, 教室名, 開封状況, 開封日, ステータス, 問合せ経路,
サービス申込日, 受付タイプ, 問合せ内容, 初回問合せ対応者, 保護者氏名（漢字）,
保護者氏名（カナ）, 生徒との関係性, 生徒氏名（漢字）, 生徒氏名（カナ）, 電話番号,
メールアドレス, お子さまの性別, お子さまの学年, 学校名, 郵便番号, 都道府県, ご住所,
建物名, ご質問・ご要望, 通塾目的, 希望科目, 通塾経験, 認知動機, 認知動機（その他）,
問合せ動機, 教室訪問申込日, 資料送付日, メモ, 資料発送業者, 資料発送番号, 追跡確認用URL,
面談日時, 面談担当者, 面談時メモ, 体験日時, 体験担当講師, 体験時メモ, 再面談日時,
再面談担当者, 再面談時メモ, 返答日時, 返答時メモ, 結果, 入会成約日, クーリングオフ日,
個別週回数, 集団, デバイス, 最終更新日

## トップページ
- GET `/contents/class/menu/top.php` — トップページ（3セクション: TOPICS/TOOL/LINK）
  - **ページ構造** (2026-04-16 DevTools探査で確認):
    - フレームレス単一ページ構成（iframe/frame なし）
    - 全TOPICS記事が1ページに展開（2020年頃まで数年分、HTML 3.5MB+、要素52,000+）
    - ページネーションなし — 全件取得は1回のGETで完了
    - TOPICS/TOOL/LINKのセクション区切りはCSSクラス（`topicsText`等）で表現。a11yツリー上はフラットなStaticTextの羅列
    - リンク総数: 1,839件
    - フォーム: 1件のみ（`frm_cmsie` — schoolie-net CMSへのSSO）
    - 記事内リンクパターン: `/contents/ie-online/DOWNLOAD_FILES/files/YYYYMMDD_{name}/` 配下にzip/pdf/xlsx/docx
    - 動画コンテンツ: `/gyoumusche/tact/mv/ie{YYYYMMDD}.html` パターン
  - **サイドバー固定リンク**:
    - ロゴ → `top.php`
    - ログアウト → `/contents/class/login/login.php`
    - メッセージボックス → `/contents/web_message/class/receive-box/list.php`
    - 教室サポート → `/contents/ie-online/class/qa/top.php`
    - 教室アカウント → `/contents/class/accountMaintenance/classAccountListPre.php`
    - 講師アカウント → `/contents/class/accountMaintenance/userAccountListPre.php`
    - WBT → `/contents/class/menu/wbtTop.php`
    - 生徒受付管理 → `/contents/boshu/class/applicant/applicantListPre.php`
    - TOPページ → `top.php`
    - 受発注システム → `/contents/class/menu/orderTop.php`
    - SFM(SSO) → `/contents/sfm/sso/ie_class.php`
    - SHPリマインダー → `/contents/tools/class/shpReminder.php`
    - ETS回答用紙DL → `/contents/tools/class/etsSheetDownload2.php`
    - PCサポート → `http://tact-net.jp/pcsupport/islclient.html`
  - **ヘッダーピックアップバナー**:
    - 理念動画 → `/gyoumusche/tact/mv/philosophy/index.html`
    - 成功事例動画 → `/contents/success_case/class/movie/pre.php`
    - 行動予定 → `search.php?categoryID=102`
    - ライセンスQS → `search.php?categoryID=129`
    - スタートアップ研修 → `search.php?categoryID=61`
  - **LINK（ページ末尾の固定リンク集）**:
    - 犯罪抑止動画リンク
    - GW休業e-learning案内
    - 3大アップシステムご意見窓口 → Google Forms
    - 漢検・英検・数検・教科書検索
    - 連絡先: pc-support@ysg.co.jp

## メッセージボックス (web_message)
- GET `/contents/web_message/class/receive-box/list.php?page={N}` — 受信箱一覧（1ページ20件、全2442件+）
  - 開封状態: `wm_icon_unopened.gif` (alt=未開封) / `wm_icon_open.gif` (alt=開封済み)
- GET `/contents/web_message/class/receive-box/detail.php?mid={ID}` — 受信メッセージ詳細（本文・添付）**※GETするだけで既読になる**
- POST `/contents/web_message/class/receive-box/toTrashBox.php` — メッセージ削除（ゴミ箱移動）
  - パラメータ: `webMessageAccountID={内部ID}`
  - 各メッセージに個別のdeleteFormフォーム（`name=deleteForm`）が紐づいている
  - `webMessageAccountID`はメッセージ一覧のHTML内に埋め込み（`mid`とは異なるID体系の可能性あり）
  - onclick: `confirm('削除してもよろしいですか？')` — JS確認ダイアログ
- GET `/contents/web_message/class/trash-box/list.php` — ゴミ箱一覧
- **一覧テーブル構造** (2026-04-16 DevTools探査で確認):
  - テーブル: 41行×5列（ヘッダ1行 + メッセージ20行×2構造 = 実質20件/ページ）
  - 列: [開封アイコン] [空] [件名(リンク)] [受信日] [削除ボタン]
  - ページネーション: `?page={N}` パラメータ（11ページ区切り表示、全2470件）
  - 総件数: `（1～20件/全{N}件）` 形式で表示
  - 受信BOX / 削除済みBOX のタブ切替

## OKS 受発注システム
- POST `/contents/class/menu/clauseAgree.php` — 利用規約同意（OKS利用前に必要）
- GET `/contents/class/menu/orderTop.php` — OKSトップ（利用規約画面）

### 備品 (bihin)
- GET `/contents/oks/class/bihin/item/list.php?page={N}` — 備品一覧（4ページ、商品区分検索・キーワード検索可）
  - 一覧テーブル8列目に「在庫なし」表示（在庫切れ商品は詳細ページに数量入力・カート追加ボタンが出ない）
- GET `/contents/oks/class/bihin/item/detail.php?iid={ID}` — 備品詳細
- POST `/contents/oks/class/bihin/item/itemCheckOfAjax.php` — 在庫チェック（Ajax、カート追加前にJS経由で呼ばれる）
- POST `/contents/oks/class/bihin/item/detailItemAdd.php` — カートに追加 (iid, unit)
- GET `/contents/oks/class/bihin/cart/list.php` — カート一覧・数量変更
  - POST `cart/itemEditAll.php` — 再計算（数量変更後）→ GET cart/list.php にリダイレクト
  - GET `cart/itemDel.php?iid={ID}` — カートから商品削除
  - 「注文手続きへ」→ GET order/list.php に遷移
  - 「注文を続ける」→ GET item/list.php に遷移
- GET `/contents/oks/class/bihin/order/list.php` — 注文内容確認（最終確認画面）
  - 「注文を確定する」ボタンで発注確定（POST）
  - 「商品・数量を変更する」でカートに戻る
  - 「商品・数量を変更する」→ GET cart/list.php に戻る
  - **⚠ 「注文を確定する」は取り消し不可**

### OKS注文フロー
```
detail.php?iid={ID} → POST itemCheckOfAjax.php → POST detailItemAdd.php
  → GET detail.php（カート反映） → GET cart/list.php（カート一覧）
  → GET order/list.php（注文確認） → POST order確定（未検証）
```

### 教材 (kyouzai)
- GET `/contents/oks/class/kyouzai/item/listPre.php` → `list.php` — 教材一覧前処理
- GET `/contents/oks/class/kyouzai/item/list.php?page={N}` — 教材一覧（122ページ）
- GET `/contents/oks/class/kyouzai/item/detail.php?iid={ID}` — 教材詳細
- GET `/contents/oks/class/kyouzai/cart/list.php` — 教材カート
- GET `/contents/oks/class/kyouzai/order/list.php` — 教材発注履歴

### 見積 (estimate)
- GET `/contents/oks/class/estimate/item/listPre.php` → `list.php` — 見積商品一覧前処理
- GET `/contents/oks/class/estimate/item/list.php` — 見積商品一覧
- GET `/contents/oks/class/estimate/order/list.php` — 見積発注履歴
- ※ `/contents/oks/class/estimate/cart/list.php` — 404

## チラシ・印刷
- GET `/contents/class/menu/chirashiTop.php` — チラシトップ
  - 教室HP管理（外部リンク: navi.schoolie-net.jp）へのフォーム
  - マニュアル各カテゴリへのリンク
- GET `/contents/class/menu/insatsuTop.php` → `http://sls.1915.jp/top.php` — 印刷（外部サイト: SLS）

## WBT（Web Based Training）
- GET `/contents/class/menu/wbtTop.php` — WBTトップ
  - ETS結果一覧へのリンク
  - ユーザーマニュアルへのリンク
  - 成功事例動画へのリンク

## ETS（テスト結果管理）
- GET `/contents/ets/class/resultList/resultAccountList.php` — テスト結果一覧
- GET `/contents/tools/class/etsSheetDownload2.php` — ETS回答用紙ダウンロード

## マニュアル・資料 (ie-online)
- GET `/contents/ie-online/class/manual/top.php` — マニュアルトップ（100+カテゴリ）
- GET `/contents/ie-online/class/manual/search.php?categoryID={N}` — カテゴリ検索（ページネーション対応）
  - 主要カテゴリ:
    - 61=研修日程・資料（スタートアップ研修バナーのリンク先）
    - 77=月別行動予定
    - 85=販促関連
    - 102=ライン研修資料・月別行動予定（行動予定バナーのリンク先）
    - 129=ライセンス試験概要・QS資料（ライセンスQSバナーのリンク先）

### トップページ左サイドバーのバナーリンク
- 理念ライブラリー → `/gyoumusche/tact/mv/philosophy/index.html`（外部HTML）
- 成功事例ノウハウ → `/contents/success_case/class/movie/pre.php`
- 行動予定・ライン研修 → `search.php?categoryID=102`（書庫カテゴリ）
- ライセンスQS → `search.php?categoryID=129`（書庫カテゴリ）
- スタートアップ研修 → `search.php?categoryID=61`（書庫カテゴリ）
- メッセージボックス → `/contents/web_message/class/receive-box/list.php`
- 教室サポートHP → `https://www.schoolie-net.jp/console/`（外部、教室HP管理CMS。認証情報は各教室のスキルファイルを参照）
- 教室アカウント → `/contents/class/accountMaintenance/classAccountListPre.php`
- 講師アカウント → `/contents/class/accountMaintenance/userAccountListPre.php`
- WBT(動画配信) → `/contents/class/menu/wbtTop.php`
- 生徒受付管理 → `/contents/boshu/class/applicant/applicantListPre.php`
- GET `/contents/ie-online/class/material/top.php` — 素材トップ
- GET `/contents/ie-online/class/movie/top.php` — 動画トップ
- GET `/contents/ie-online/class/qa/top.php` — Q&A（30+カテゴリ）
- GET `/contents/ie-online/class/qa/search.php?categoryID={N}` — Q&Aカテゴリ検索

## ユーザーマニュアル
- GET `/contents/usermanual/class/category/list.php` — カテゴリ一覧（PCS・夢SEED操作、テンプレート等）
- GET `/contents/usermanual/class/file/list.php?categoryID={N}` — カテゴリ内ファイル一覧

## 成功事例動画
- GET `/contents/success_case/class/movie/pre.php` → `list.php` — 動画一覧
- GET `/contents/success_case/class/movie/detail.php?mid={ID}` — 動画詳細

## ツール
- GET `/contents/tools/class/shpReminder.php` — SHPリマインダー

## アカウント管理
- GET `/contents/class/accountMaintenance/classAccountListPre.php` → `classAccountList.php` — 教室アカウント一覧
- GET `/contents/class/accountMaintenance/userAccountListPre.php` → `userAccountList.php` — ユーザーアカウント一覧
- POST `/contents/class/accountMaintenance/userAccountList.php` — ユーザー一覧検索
- POST `/contents/class/accountMaintenance/userAccountEntry.php` — ユーザー登録

## SafetyMail (sfm.tactgroup.net)

### 認証
- SSO: WebSupportログイン後に GET `/contents/sfm/sso/ie_class.php` でSFMへ遷移
- 同一セッションCookieで両方アクセス可能
- 直接ログイン: GET `https://sfm.tactgroup.net/ie-class/login.php`

### 出席簿
- GET `/sfm/ie-class/attendance/student/listPre.php` → `list.php` — 出席簿一覧（在席/不在、入退室時刻）
- GET `/sfm/ie-class/attendance/student/detail.php?sid={N}` — 生徒出席詳細
- GET `/sfm/ie-class/attendance/testCard/detail.php` — 打刻テスト

### 連絡帳（メッセージ）
- GET `/sfm/ie-class/message/receive-box/listPre.php` → `list.php` — 受信箱一覧
- GET `/sfm/ie-class/message/receive-box/detail.php?mid={N}` — 受信メッセージ詳細
- GET `/sfm/ie-class/message/send-box/listPre.php` → `list.php?page={N}` — 送信箱一覧（47ページ）
- GET `/sfm/ie-class/message/send-box/detail.php?mid={N}` — 送信メッセージ詳細
- GET `/sfm/ie-class/message/new.php` — 新規メッセージ作成画面
- POST `/sfm/ie-class/message/newRegist.php` — メッセージ送信
  - subject, body, listSendAD(送信先), listGD(学年), listST(生徒), listGP(グループ)
  - schedule_flag(予約送信), schedule_date(Y/m/d), schedule_hour, schedule_minute
  - select_test_card(テストカード送信)

### 生徒基本情報
- GET `/sfm/ie-class/management/student/listPre.php` → `list.php?page={N}` — 生徒一覧
- GET `/sfm/ie-class/management/student/detail.php?sid={N}` — 生徒詳細

### 生徒詳細フィールド
- 生徒名、生徒名（カナ）、学年、生徒区分、更新日時
- 生徒ID（セーフティカードID）、パスワード
- メイン通知メールアドレス、サブ通知メールアドレス、メールエラー状況

### カード管理
- GET `/sfm/ie-class/management/card/spot/setStudent.php` — 外部生カード管理
- GET `/sfm/ie-class/management/card/test/detail.php` — テストカード管理

## がんばるポイント (sfm.tactgroup.net/ganbaru)
- GET `/ganbaru/ie-class/news/top.php` — お知らせ内容
- GET `/ganbaru/ie-class/student/listPre.php` → `list.php?page={N}` — 生徒ポイント一覧（3ページ、ソート対応）
  - ソート: sort=num(ID)/snm(名前)/snk(カナ)/grd(学年)/rlp(ラリーポイント), order=asc/desc
- GET `/ganbaru/ie-class/student/detail.php?sid={N}` — 生徒ポイント詳細（月別ポイント履歴）

## 成功事例・ノウハウ動画
- GET `/contents/success_case/class/movie/pre.php` → `list.php` — 動画一覧（accordion構造）
  - カテゴリ: `<details class="accordion">` > `<div class="accordion-Category">`
  - 動画: `<a href="./detail.php?bcmid={ID}">` > `<div class="movieTitle">`
- GET `/contents/success_case/class/movie/detail.php?bcmid={ID}` — 動画詳細
  - Brightcove埋め込み: `data-video-id="{VIDEO_ID}"`
  - アカウント: 4887491978001、プレイヤー: sJekpxuKD_default
  - 資料PDF: `<a href="...pdf">`
  - Playback API: `GET https://edge.api.brightcove.com/playback/v1/accounts/{ACCOUNT}/videos/{VIDEO_ID}`
    - Header: `Accept: application/json;pk={POLICY_KEY}`
    - Policy Key: プレイヤーJS (`index.min.js`) 内の `BCpk...` 文字列
    - レスポンス `sources[]` にHLS/DASH URL（MP4直リンクなし）
    - DL方法: yt-dlp + ffmpeg で HLS → MP4

## 教室HP管理CMS (schoolie-net.jp)
→ 別MCP `mcp-server-schoolie-net/endpoints.md` に分離済み。
- WebSupportトップページのサイドバーに「教室サポートHP → https://www.schoolie-net.jp/console/」リンクあり

## 外部サイト連携
- `http://sls.1915.jp/top.php` — 印刷発注（insatsuTop.phpからリダイレクト）
