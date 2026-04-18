# SKS GUI自動化ノート

Chrome DevTools MCP等でSKSのブラウザ操作を自動化する際の知見。

## ログイン

- URL: `http://tacs.tacsvpn/sks.wpp`（デフォルト）
- **ログインボタンを押す前に** window.open上書きJSを実行すること

## window.open上書き（全画面共通）

SKSはwindow.openでポップアップを多用する。Chrome DevToolsでは新規ウィンドウを追跡できないため、新規タブに変換する。

```javascript
const _origOpen = window.open.bind(window);
window.open = function(url, name, features) {
  if (!url || url === '' || url === 'about:blank') return window;
  return _origOpen(url, '_blank');
};
```

**重要:**
- `location.href = url`（同タブ遷移）にしてはいけない。子画面の「終了」ボタン（window.close）で閉じたとき、元の画面に戻れなくなる
- ページ遷移するとJS上書きがリセットされるため、遷移先でも再設定が必要
- `win = null` をログインページで実行しておくと「SKS画面は既に開かれています」ダイアログを防げる

## ダイアログ対策

SKSには2種類のダイアログがある:

| 種類 | 例 | 問題 |
|------|-----|------|
| **独自モーダル** | 問い合わせ管理「更新して宜しいですか？」「選択されていません」 | `#_overRideModalOK` クリックで対処可 |
| **ネイティブalert/confirm** | PCS「目標単元を選択してください」「登録しました」 | JSスレッドをブロックし、`evaluate_script`が返らなくなる |

### ネイティブダイアログの置き換え（推奨）

ネイティブのalert/confirmはJSスレッドをブロックするため、DevToolsからの操作が止まる。`handle_dialog`で閉じられる場合もあるが、`evaluate_script`が返らないタイミングでは呼べない。

**解決策:** ページ内にモーダルHTMLを注入し、alert/confirm/form.submitを上書きする。ページ遷移（document.write）するたびに再注入が必要。

**動作ルール:**
- **alert（OKのみ）**: 1秒表示→自動でOKを押す。人間が目で確認する時間を与えつつ止めない
- **confirm（OK/キャンセル）**: 2択を表示したまま残す。5秒待って自動でOK。人間がキャンセルを押せば中断
- **form.submit後のalert**: fetchでPOSTを受け、レスポンス内のalertをモーダルで1秒表示してからページを書き込む

```javascript
// --- モーダルHTML注入 ---
if (!document.getElementById('_injectedModal')) {
  var div = document.createElement('div');
  div.id = '_injectedModal';
  div.innerHTML = '<div style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:9999;display:none;justify-content:center;align-items:center;" id="_modalOverlay"><div style="background:white;padding:20px;border-radius:5px;min-width:300px;text-align:center;"><p id="_modalMsg"></p><button id="_overRideModalOK" style="padding:5px 20px;margin-top:10px;">OK</button><button id="_modalCancel" style="padding:5px 20px;margin-top:10px;margin-left:10px;display:none;">キャンセル</button></div></div>';
  document.body.appendChild(div);
}

// --- alert: 1秒表示→自動閉じ ---
window.alert = function(msg) {
  document.getElementById('_modalMsg').textContent = msg;
  document.getElementById('_modalOverlay').style.display = 'flex';
  var c = document.getElementById('_modalCancel'); if (c) c.style.display = 'none';
  return new Promise(function(resolve) {
    document.getElementById('_overRideModalOK').onclick = function() {
      document.getElementById('_modalOverlay').style.display = 'none'; resolve();
    };
    setTimeout(function() {
      document.getElementById('_modalOverlay').style.display = 'none'; resolve();
    }, 1000);
  });
};

// --- confirm: 2択表示、5秒後自動OK ---
window.confirm = function(msg) {
  document.getElementById('_modalMsg').textContent = msg;
  document.getElementById('_modalOverlay').style.display = 'flex';
  var c = document.getElementById('_modalCancel'); if (c) c.style.display = 'inline';
  return new Promise(function(resolve) {
    var resolved = false;
    document.getElementById('_overRideModalOK').onclick = function() {
      if (!resolved) { resolved = true; document.getElementById('_modalOverlay').style.display = 'none'; resolve(true); }
    };
    document.getElementById('_modalCancel').onclick = function() {
      if (!resolved) { resolved = true; document.getElementById('_modalOverlay').style.display = 'none'; resolve(false); }
    };
    setTimeout(function() {
      if (!resolved) { resolved = true; document.getElementById('_modalOverlay').style.display = 'none'; resolve(true); }
    }, 5000);
  });
};

// --- form.submit: fetchで受けてalertを1秒モーダル表示 ---
HTMLFormElement.prototype.submit = function() {
  var form = this;
  var formData = new FormData(form);
  var action = form.action || location.href;
  var method = (form.method || 'GET').toUpperCase();
  fetch(action, {
    method: method,
    body: method === 'POST' ? new URLSearchParams(formData) : undefined,
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
  }).then(function(r) { return r.text(); }).then(function(html) {
    var alertMatch = html.match(/alert\s*\(\s*["']([^"']+)["']\s*\)/);
    if (alertMatch) {
      document.getElementById('_modalMsg').textContent = alertMatch[1];
      document.getElementById('_modalOverlay').style.display = 'flex';
      var c = document.getElementById('_modalCancel'); if (c) c.style.display = 'none';
    }
    html = html.replace(/([^_])alert\s*\(/g, '$1console.log("ALERT:", ');
    setTimeout(function() {
      var el = document.getElementById('_modalOverlay');
      if (el) el.style.display = 'none';
      document.open(); document.write(html); document.close();
    }, alertMatch ? 1000 : 0);
  });
};
```

**注意:**
- `document.write`後にモーダルHTML・上書きが全て消えるため、遷移後に再注入が必要
- SKS本体（.wpp）ページには元々独自モーダル（`#_overRideModalOK`）が存在するが、PCS系統図（ssk2ドメイン）には存在しないため、この注入が必要
- window.openの上書きも同時に行うこと（別セクション参照）

## 問い合わせ管理 (tryers)

### 検索→編集→更新

1. `listup.wpp` を開く
2. 検索フォーム（fm1）に条件を入力 → 検索ボタン
3. 結果行の `onclick="sel(this,'{教室コード}:{No}')"` をクリック → `selcd` がセットされる
4. 編集ボタン → `edit()` → fm3に既存データが読み込まれる
5. フィールドを変更 → 登録ボタン → 独自モーダルでOK

### 削除

- `remove()` は確認ダイアログなしで即削除する
- `cmd=remove&code={教室コード}:{No}` のPOST
- **`cmd=update`で空データを送ると、検索からは消えるが外部生検索(IEB012)にゴミ（空行）が残る。削除は必ず`cmd=remove`を使う**

### 外部生検索 (IEB012)

- IEB040（外部生登録）の「検索」ボタンで開くダイアログ内iframe
- 「ログインエラー timeout」が表示されることがあるが一時的なエラー。ダイアログを閉じて再度検索ボタンを押せば表示される。再ログインは不要

## PCS系統図 (PcsMenu.do)

### 処理状態インジケータ

2つの状態表示がある:

**Status0〜Status2（背景色）:**

| 要素ID | ラベル | 意味 |
|--------|--------|------|
| `Status0` | 1:問題作成済 | 緑=現在このフェーズ |
| `Status1` | 2:採点済み | 緑=現在このフェーズ |
| `Status2` | 3:カリキュラム作成済み | 緑=現在このフェーズ |

- 緑 = `rgb(128, 255, 128)` — 現在のフェーズ
- 白 = `rgb(255, 255, 255)` — 未到達
- 未設定（style.background が空） = 初期状態（問題未作成）
- **注意: プログレスバーではなく「現在地」を示す。採点済みになるとStatus0は白に戻る**

**SMSG（`<input name="SMSG">`）:**

| SMSG値 | 状態 |
|--------|------|
| 空 | 初期（問題未作成） |
| `問題が印刷できます` | 問題作成済み |
| `結果帳票が印刷できます` | 採点済み |
| `カリキュラム作成を押して` | 予定登録済み |
| `単元を選択し問題作成を` | サイクル完了 |

### 単元チェックボックスの操作

**`.checked = true` だけでは不十分。** 各チェックボックスのonclickに `tgSelectChange(stage, code, checked)` が紐づいており、この関数がTangenListの内部状態とDOM背景色を更新する。`.checked`を直接変更してもDOMイベントが発火しないため、`doCheckboxes()`がchecks文字列を生成する際にチェック済みと認識されない。

正しい方法:
```javascript
const cb = document.getElementById('tg1201_31');
cb.checked = true;
tgSelectChange('1', '1201_31', true);  // onclickのハンドラと同じ関数を呼ぶ
```

全単元を一括チェック:
```javascript
const cbs = document.querySelectorAll('input[type="checkbox"][name^="tg"]');
for (const cb of cbs) {
  if (!cb.checked) {
    cb.checked = true;
    const m = cb.getAttribute('onclick').match(/tgSelectChange\('(\d+)',\s*'([^']+)',/);
    if (m) tgSelectChange(m[1], m[2], true);
  }
}
```

全解除は `cmd_alloff()` で可能。解除後の背景色は `clWhite` になる。

**MCP（Python POST）の場合**: HTML内の `doCheckbox("1", "1201_31", "color|Navy")` パターンを正規表現で抽出して色情報を取得し、checksを直接構築する。TangenListのJS変数は使わない。GUI操作とは別のアプローチ。

### TangenList

- `TangenList` はObject型（Arrayではない）。`TangenList['1']`, `['2']`, `['3']` にステージ別の単元データが格納
- `for (const key in TangenList[stage])` で列挙する（`for...of` ではない）
- 各単元: `{ cname: '色クラス名', snm: '単元名', ... }`
- ページ正規遷移（pcs.wpp → pcs_start.wpp → Pcs.do）でデータが初期化される
- `mode=undefined` 等の不正パラメータでリロードするとTangenListが空のまま描画される

### 採点画面 (PcsSaiten.do)

- window.openで開くため、事前にwindow.open上書きが必要（新規タブ化）
- 正解数入力（`correctcnt({問題コード})`） → 「登録/修正」ボタン → `doRegist()` → form submit
- **「終了」ボタンはwindow.close()を呼ぶ。同タブ遷移で開いているとタブごと消える**
- 終了後、PCSメニュータブで「更新」を押して状態を反映させる

### ⑤予定登録

- `cmd_updy()` は単元チェックボックスがチェックされていないと「目標単元を選択してください」のネイティブalertが出る
- checksの生成は `doCheckboxes(GMODE, stg, 'checked')` が内部的に行う
- `tgSelectChange()` を呼ばずに `.checked = true` しただけだと、checksが空になりサーバーが無視する

### ⑥カリキュラム作成 (PcsCurriculum.do)

- window.openで開く（新規タブ化必要）
- `tukikaisu`（月回数）に**必ず4を入力**。0だと登録失敗する
- 登録後、PCSメニューで「更新」を押して次のサイクルに進む
