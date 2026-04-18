---
description: NKS日報メールの作成と問合せ転記（NKS所属教室長向け）
---

# NKS日報メール作成

「日報メール作成して」「日報メール」等と言われたら実行する。

**注意: このスキルはNKS（日本教育スクールIE）所属の教室長のみが対象です。全教室長が使うわけではありません。**

## 概要

1. WebSupportの問合せをExcelの日報問合リストに転記
2. 日報Excelから数値を集計
3. Gmailスレッドに返信形式の下書きを作成（送信しない）

## 前提

- google-workspace MCP（Gmail API）が設定されていること
- 日報Excelファイルが所定のパスに存在すること
- `scripts/nippou_mail.py` と `scripts/excel_writer.py` を別途設置すること

## 実行

```bash
# 問合せ転記（WebSupport → Excel）
PYTHONIOENCODING=utf-8 python3 scripts/excel_writer.py

# 日報メール下書き作成
PYTHONIOENCODING=utf-8 python3 scripts/nippou_mail.py
```

## 自動判定ルール

- 14:30〜24:00 → 当日の日報
- 0:00〜14:30 → 前日の日報

## 注意

- 送信しない。下書き作成のみ
- 生徒別一覧は前回メールと同じファイルを添付する（推測しない）
- 月末在籍・翌月申し出は前回メールの値を踏襲する
- スクリプトのパスや日報Excelのパスは各教室の環境に合わせて修正が必要
