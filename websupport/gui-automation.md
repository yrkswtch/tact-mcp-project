# WebSupport GUI自動化ノート

Chrome DevTools MCP等でWebSupport（tactgroup.net）のブラウザ操作を自動化する際の知見。

## セッション管理

- ログイン: `POST /contents/class/login/login.php`
- `btnLogin.x` / `btnLogin.y` パラメータが必須（type=imageのsubmitボタン座標）。ないとログイン失敗する
- セッションタイムアウト時のレスポンスに「ログインタイムアウト」が含まれる
- MCP（server.py）の `_get_session()` にタイムアウト検知→自動再ログインを実装済み

## エンコーディング

- HTML: EUC-JP
- CSV: CP932
- POSTデータ: EUC-JPエンコード必須。requestsの `data=dict` ではなく、手組みのURLエンコード文字列を送る
- フィールド名に `[]` を含むもの（`inspire[4]` 等）はURLエンコードが必要

## 新規登録 (applicantNew.php)

- 2ステップ更新: Step1 = `btnToConfirm`（確認画面）、Step2 = `btnRegister`（確定）
- `btnRegister` を送らないと登録されない
- 必須フィールド: 保護者カナ(sei/mei)、生徒カナ(sei/mei)、認知動機(inspire)、問合せ動機(motive)、問合せ内容(inquiry)、生徒との関係性(relationship)
- 必須フィールドが空だとエラーなしに入力画面に戻される

## 更新 (applicantEdit.php)

- 同じく2ステップ更新
- inspire/motiveのhidden: checkboxの前にhidden(value=0)がある。最低1つは1にしないとバリデーションで弾かれる

## SafetyMail (sfm.tactgroup.net)

- WebSupportからSSO経由でアクセス: `GET /contents/sfm/sso/ie_class.php`
- WebSupportのセッションCookieが必要
