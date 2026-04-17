# 教室HP (schoolie-net) GUI自動化ノート

Chrome DevTools MCP等でschoolie-net CMS（https://www.schoolie-net.jp/console/）を操作する際の知見。

## ログイン

- URL: `https://www.schoolie-net.jp/console/`
- ID: 教室コード / PW: 教室固有パスワード
- 通常のフォームPOSTでログイン

## CMS編集の注意点

### 講師スロット

- 講師スロットは**写真と紐づいている**
- 推測で配列を組まず、CMS編集画面で氏名を確認してから構成する
- 写真の順番とテキストの順番がずれると、別人の写真に名前が付く

### リンク制限

- 独自リンク（TimeRex / Google Forms等）の掲載は禁止
- schoolie-net公式フォームのみ使用可

### HTMLインジェクション

- 全5タブ・全textareaフィールドでHTML/JS/CSSインジェクション可能（サニタイズなし）
- `<script>`, `<iframe>`, `<style>`, `<img>`（外部src）等すべて通る
- CKEditorフィールド（timetable1/fee1）は一部タグが消される（`<div>`, `<h3>`, `<form>`等）
- プレーンtextareaフィールドは完全にサニタイズなし

### 版管理

- テストは版コピー（`/console/editions/{教室ID}/copy/{版ID}`）→ 編集 → プレビューで本番に影響なく実施可能
- 一時保存（status=1）まで自動化OK。承認依頼（status=2）は人間が行う
- 版の状態: 0=下書き、1=一時保存、2=承認依頼中、3=公開中

## MCP (server.py) ツール

- `schoolie_get_versions`: 版一覧取得
- `schoolie_get_fields`: フィールド値取得（prefix指定でフィルタ可）
- `schoolie_update_fields`: 部分更新（指定フィールドのみ上書き、他は既存値維持）
- `schoolie_request_approval`: 承認依頼

## 定期更新が必要なコンテンツ

- キャンペーン期限
- 開校日程
- 検定日程
- 合格実績
- 講師紹介
