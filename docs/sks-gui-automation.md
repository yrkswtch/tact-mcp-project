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

## 外部生登録 (IEB040) — 問合せデータからの取り込み

GUI 上部の「問合せデータ [問合せ管理起動] [検索]」セクションから、SKS問合せ管理(tryers) に登録済みの問合せをロードして外部生として登録できる。

### 問合せ検索ダイアログ (IEB012)

「問合せデータ [検索]」ボタンで `subwin/IEB012.wpp?p=1` がモーダル iframe で開く。構造:
- 氏名検索 textbox（部分一致）→「表示」ボタンで検索結果が下の iframe に表示される
- 結果行の `<a href="javascript:">` リンクをクリック → trsel 系で `localparam` に値がセットされる
- ダイアログ下部の **「選択」ボタン** をクリック → 親フォーム（IEB040）に問合せ情報が転記され、ダイアログが閉じる
- **「終了」ボタン**はキャンセル（外側「×close」も同様）

### 自動転記される項目

問合せ「選択」時に親 IEB040 フォームに転記されるフィールド:
- `seitosm`（生徒氏名）
- `hogoshasm`（保護者氏名）
- `seitograde`（学年）
- `impostalcd`（郵便番号）
- `ad1` / `ad2`（住所）
- `telno`（電話番号）

転記**されない**ので手動入力が必要なフィールド:
- **`seitokm`（フリガナ半角カナ）**: **必須**（サーバー側バリデーションあり）
- `seitosex`（性別、既定「男」）
- `imdatebirth`（生年月日）
- 外部生区分（講習会生／ETS体験生のラジオ）

### 「追加／修正」押下時の確認ダイアログ

外部生新規登録で「追加／修正」を押すと、独自モーダルが2連続で出る:

#### 1. 「登録してよろしいですか？」確認ダイアログ
> [OK] / [キャンセル]

OK で確定する。

#### 2. 「生徒コード：XXX を登録しました。」完了ダイアログ
> 生徒コード：26G036 を登録しました。
> [OK]

採番された外部生コード（例: `26G036`）が表示される。OK 押下後、フォームは登録済みレコードの状態（生徒番号フィールドに新コードが入る）になる。

採番された `gaibuseicd` は `formmain.gaibuseicd.value` で取れる（ダイアログのテキストを読まなくても識別可能）。

### MCP プログラマブル等価フロー

GUI 操作は `sks_gaibusei_register_from_inquiry()` MCPツールで完全再現できる。
- 問合せ NO（IEB012 の検索結果に出る `No` 列の値）と、必須補完項目（kana, birth, sex, memo）を渡す
- 内部実装: `IEB040.wpp?mode=toiawase&kyoshitsucd={教室}&seitocd={教室}:{問合せNo}` でフォームをロード → POST cmd=regist
- 詳細は `sks-endpoints.md` の「問合せ → 外部生 転送」セクション参照

## 生徒登録 (IEB010) — 外部生→内部生 取り込み

GUI 上部の `外部生 [code] [取込] [検索]` セクションで外部生コードを指定して「追加／修正」を押すと、外部生→内部生 として登録される。

### 外部生検索ダイアログ (IEK060X)

`外部生 [検索]` ボタンで `subwin/IEK060X.wpp?p=&kubun=gaibu` がモーダル iframe で開く。**入れ子の iframe** （外側 dialog → 内側 iframe → さらに検索結果 iframe）構造になっている。

選択フロー:
1. 検索結果の `<tr>` 行 `onclick="trsel2(this, '<code>', '<name>', '<kubun>', '<grade>')"` をクリック → `parent.document.localparam.param/param1..` に値がセットされ、行の class が `sel9`（選択状態）になる
2. ダイアログ下部の **「選択」ボタン**（`onclick="dopost(opener.formmain, 1)"`）をクリック → 親フォーム（IEB010 main）に gaibuseicd 等が注入され、ダイアログが閉じる
3. **「終了」ボタン**は `opener.closeDialog()` でキャンセル。ダイアログ右上の「×（close）」も同様にキャンセルなので、選択を確定させたい場合は **必ず「選択」ボタンを押す**

### 「追加／修正」押下時の確認ダイアログ

新規登録（特に外部生→内部生 取り込み）で「追加／修正」を押すと、以下の確認ダイアログが連続で出る:

#### 1. 証憑データ確認ダイアログ（YSPC）

> プレミアムクラブ オンライン申込に該当生徒にひもづくデータが見つかりませんでした。
> 申込が完了していない理由を選択して下さい。
> ⦿ 未登録：証憑未取得（既定で選択済み）
> ◯ 未登録：デジタルデバイスなし
> [ＯＫ] [キャンセル]

- ラジオの値は内部的に `formmain.premshubetsu=4`（未登録）, `premreason=2`（証憑未取得）/ `3`（デジタルデバイスなし）に対応
- ＯＫで確定すると POST が実行される
- プログラマブル運用ではこのダイアログは出ないので、`premshubetsu=4 / premreason=2` を直接 POST に含めて肩代わりする

#### 2. 「登録しました」ダイアログ

> 生徒コード：<新seitocd> 契約者No.：<新keiyakushano>で登録しました。
> [OK]

- 採番された生徒コードが表示される独自モーダル（ネイティブ alert ではなく `#_overRideModalOK` の DOM ダイアログ）
- 押し忘れると次の操作に進めないので、自動化時は必ず OK ボタン（`button[focusable focused]` で取れる）をクリックして閉じる
- 採番済みの seitocd は POST レスポンスの `formmain.seitocd` 値からも取得できるので、ダイアログのテキストを読まなくても識別は可能

### MCP プログラマブル等価フロー

GUI 操作は `sks_convert_gaibu_to_internal()` MCP ツールで完全再現できる。
- ロード: `POST /service/IEB010.wpp` `mode=gaibu&kyoshitsucd=<教室>&seitocd=<外部生>`
- 登録: 通常の `cmd=regist` POST に `gaibuseicd` を含める。`premshubetsu=4 / premreason=2` で YSPC ダイアログを肩代わり
- 詳細は `sks-endpoints.md` の「外部生 → 内部生 取り込み」セクション参照

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
