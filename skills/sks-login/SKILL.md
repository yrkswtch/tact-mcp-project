---
description: SKS (WEB-SKS) に自動ログインする
---

# SKSログイン

「SKSにログインして」と言われたら実行する。

## 手順

1. Chromeで SKS ログインページを開く（環境変数 SKS_BASE_URL のドメイン + `/sks.wpp`）

2. **ログインボタンを押す前に** 以下のJavaScriptを実行:
   ```javascript
   win = null;
   const _origOpen = window.open.bind(window);
   window.open = function(url, name, features) {
     if (!url || url === '' || url === 'about:blank') return window;
     return _origOpen(url, '_blank');
   };
   ```
   - `win = null`: 「SKS画面は既に開かれています」ダイアログを防ぐ
   - `window.open` 上書き: 新規ウィンドウを新規タブで開く
   - **`window.name = "SKSMAIN"` は設定しない**（同タブ遷移になり閉じたとき戻れない）

3. ログインIDを入力（userConfigの sks_account）

4. パスワードを入力（userConfigの sks_password）

5. 「ログイン」ボタンをクリック

6. **新規タブ**でメインメニューが開くことを確認

## 注意

- メインメニューでも `window.open` 上書きを再設定すること（ページ遷移でリセットされる）
- SKSの詳細な操作ノウハウは `docs/sks-gui-automation.md` を参照
