---
description: PCS（パーソナルカリキュラムシステム）の操作 - 問題作成、採点、カリキュラム登録
---

# PCS操作

PCSの問題作成・採点・カリキュラム登録を行う。

## MCP経由（GUIなし）

### 問題作成
```
pcs_create_problem(student_code, selected_units, kyoukakb, auto_complete_cycle=True)
```
- `auto_complete_cycle=True`: 前回サイクル未完了なら自動的に採点(0点)→カリキュラム(4回)→更新してから問題作成
- checksのCRLF・色情報・testflg自動判定は server.py が内部で処理

### 採点
```
pcs_saiten(student_code, scores, kyoukakb)
```
- scores: 全問題の正解数をカンマ区切り（空なら全問0点）

### カリキュラム登録
```
pcs_curriculum(student_code, kyoukakb)
```
- 月回数(tukikaisu)は必ず4。0だと登録失敗する

### PDF取得・印刷
```
pcs_print_mondai(student_code, kyoukakb)  # 問題PDF
pcs_print_kaitou(student_code, kyoukakb)  # 解答PDF
pcs_print_pdf(pdf_path, paper, nup)       # プリンタ印刷
```

## 処理状態の確認

PCSメニュー（PcsMenu.do）で以下を確認:

**Status0〜Status2 背景色:**
- 緑 `rgb(128, 255, 128)` = 現在のフェーズ
- 白/未設定 = 未到達
- 注意: プログレスバーではなく「現在地」。採点済みになるとStatus0は白に戻る

**SMSG（input要素）:**
| 値 | 状態 |
|----|------|
| 空 | 初期（問題未作成） |
| 問題が印刷できます | 問題作成済み |
| 結果帳票が印刷できます | 採点済み |
| カリキュラム作成を押して | 予定登録済み |
| 単元を選択し問題作成を | サイクル完了 |

## GUI操作の注意

- 単元チェックボックスは `.checked = true` だけでは不十分。`tgSelectChange(stage, code, true)` を呼ぶ必要がある
- ネイティブ alert/confirm はモーダルHTMLに置き換えて対処（詳細は `docs/sks-gui-automation.md`）
- checksの改行コードはCRLF必須（ネイティブform submitは自動変換、fetchでは手動変換が必要）
- 詳細は `docs/sks-endpoints.md` のPCSセクションと `docs/sks-gui-automation.md` を参照
