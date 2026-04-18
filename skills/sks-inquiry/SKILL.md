---
description: WebSupportの問い合わせをSKSの問い合わせ管理に登録する
---

# SKS問い合わせ登録

「問い合わせをSKSに登録して」「SKSに問合せ転記して」等と言われたら実行する。

## 概要

WebSupport（tactgroup.net）の問い合わせデータを、SKS（WEB-SKS）の問い合わせ管理（tryers）に登録する。

## 手順

### 1. WebSupportから最新の問い合わせを取得

WebSupport MCPの `applicant_list` を使用。

### 2. SKSの既登録データと照合

SKS MCPの `sks_inquiry_search` で照合。
- 照合キー: 生徒名（空白除去）で比較
- 同日に複数件ある場合は電話番号も併用して区別すること

### 3. 未登録者をSKSに登録

SKS MCPの `sks_inquiry_register` を使用。

#### フィールド対応表（WebSupport → SKS）

| WebSupport | SKS | 備考 |
|---|---|---|
| 生徒氏名 | seitosm | 空の場合は保護者名の名字 |
| 保護者氏名 | hogoshasm | |
| 受付日時 | imtoiawasedt | YYYY/MM/DD形式 |
| 電話番号 | telno | 自動ハイフン整形 |
| 郵便番号 | impostalcd | 空なら住所から逆引き |
| 都道府県+市区町村 | ad1 | |
| 番地 | ad2 | |
| 建物名 | ad3 | |
| 学年 | schoolkb + grade | 半角・全角どちらでもOK（自動正規化） |

### 4. 登録前に人間に確認

「X件登録しますか？」→ OK → 実行。一括処理前に1件テストすること。

## 注意

- 問い合わせ管理での削除は必ず `cmd=remove` を使う。`cmd=update` で空データを送るとゴミレコードが残る
- 詳細は `docs/sks-endpoints.md` の「問い合わせ管理」セクション参照
