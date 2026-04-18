---
description: SKS生徒名簿一覧から特定の生徒の情報画面を開く
---

# SKS 生徒情報表示

「〇〇さんの情報を開いて」「生徒情報を見て」等と言われたら実行する。

## 手順

1. SKSにログイン済みであること（sks-login スキル参照）

2. 生徒名簿一覧（IEB030.wpp）を開く

3. 全選択ボタンを押してから「表示」ボタンを押す

4. 以下のJSを実行して新規ウィンドウを新規タブ化し、beforeunloadを無効化:
   ```javascript
   window.name = "IEB030";
   const _origOpen = window.open.bind(window);
   window.open = function(url, name, features) {
     if (!url || url === '' || url === 'about:blank') return window;
     return _origOpen(url, '_blank');
   };
   Event.prototype.__defineSetter__('returnValue', function(){});
   ```

5. 生徒名を検索（氏名フィールド name属性: seitosm）
   - 検索は必ずUnicodeコードポイントで行う（文字誤認識防止）

6. 該当行をクリック → 生徒登録ボタン（bn010）をクリック → IEB010.wppに遷移

## 注意

- 生徒情報画面は新規タブで開くようにすること（同タブ遷移だと閉じたとき戻れない）
- 詳細は `docs/sks-gui-automation.md` 参照
