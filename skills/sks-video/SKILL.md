---
description: SKSの映像システム管理画面をSSO経由で開く
---

# SKS 映像システム管理

「映像システムを開いて」「映像管理に入って」等と言われたら実行する。

## 手順

1. SKSにログイン済みであること（sks-login スキル参照）

2. メインメニューで以下のJSを実行:
   ```javascript
   const _origOpen = window.open.bind(window);
   window.open = function(url, name, features) {
     if (!url || url === '' || url === 'about:blank') return window;
     return _origOpen(url, '_blank');
   };
   ```

3. 「映像システム管理」リンクをクリック

4. SSO経由で `https://www.ysg-mirai-school.jp/teacher/dashboard` に遷移

## SSO方式

- window.open で `https://www.ysg-mirai-school.jp/sso-login` へGET
- パラメータ: schoolCode / schoolName / userId / timestamp / hash(HMAC)
- SKSが内部的にHMACを生成してSSO URLを構築する
