---
description: 教室HP（schoolie-net CMS）を更新する
---

# 教室HP更新

「教室HP更新して」「HP更新」等と言われたら実行する。

## 概要

schoolie-net CMS（https://www.schoolie-net.jp/console/）の教室HPを更新する。

## MCPツール

- `schoolie_get_versions` — 版一覧取得
- `schoolie_get_fields` — フィールド値取得（prefix指定でフィルタ可）
- `schoolie_update_fields` — 部分更新（指定フィールドのみ上書き、他は既存値維持）
- `schoolie_request_approval` — 承認依頼

## 手順

1. `schoolie_get_versions` で現在の版を確認
2. `schoolie_get_fields` で更新対象のフィールド値を取得
3. `schoolie_update_fields` で更新（一時保存まで自動）
4. **承認依頼は人間が行う**（自動化しない）

## 定期更新が必要なコンテンツ

- キャンペーン期限
- 開校日程
- 検定日程
- 合格実績
- 講師紹介

## 注意

- 講師スロットは写真と紐づいている。推測で配列を組まずCMS編集画面で氏名を確認してから構成する
- 独自リンク（TimeRex/Google Forms等）の掲載は禁止。schoolie-net公式フォームのみ使用可
- 詳細は `docs/classroom-endpoints.md` と `docs/classroom-gui-automation.md` を参照
