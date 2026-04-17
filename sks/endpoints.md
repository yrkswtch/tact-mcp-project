# SKS (WEB-SKS 生徒管理システム) エンドポイント一覧

## 接続構造

```
[外部PC] → https://{SKS_BASE_URL}/ (SKS-proxy)
    → [hatogaya PC] → L2-connect-remote-access (VPNプロキシ)
        → {SKS_BASE_URL}/ (SKSサーバー)
```

- SKS-proxy: `https://{SKS_BASE_URL}/`
- 直接接続: `{SKS_BASE_URL}/`（L2-connect-remote-access導入済みPCのみ）
- **MCP設計**: 環境変数 `SKS_BASE_URL` でベースURLを切り替え
  - プロキシ: `SKS_BASE_URL=https://{SKS_BASE_URL}`
  - 直接: `SKS_BASE_URL={SKS_BASE_URL}`
  - パス（`/sks.wpp`, `/service/`, `/service/IEB030.wpp`等）は共通
- Copyright 2005-2007 Tact Corporation

## 認証

### ログイン前の必須JS（新規ウィンドウ阻止）
```javascript
win = null;                  // 既存セッション参照クリア
window.name = "SKSMAIN";     // 現在タブへのナビゲート強制
```
※SKSはログイン後に window.open() で別ウィンドウを開こうとする。
window.name が "SKSMAIN" の場合は同タブ遷移に切り替わる仕様。

### ログインAPI（Ajax JSON-RPC方式）

全てのAPIは `/cgi-bin/s2login.pl` に `POST cmd=jsx&param={JSON}` で送信。レスポンスはJSON。

**Step 1: 暗号化キー取得**
- POST `/cgi-bin/s2login.pl` data: `cmd=jsx&param={"cmd":"com"}`
- Response: `{"result":"OK", "com":"<AES暗号化キー>"}`

**Step 2: 認証**
- パスワードをAES暗号化: `CryptoJS.AES.encrypt(password, com)`
- POST `/cgi-bin/s2login.pl` data: `cmd=jsx&param={"cmd":"auth","id":"<loginid>","pw":"<encrypted>","iv":"<iv>"}`
- Response: `{"result":"OK", "tantoshacd":"...", "usersm":"...", "accesslv":"...", ...}`
- エラー: `NG3`=退職者、その他=ID/PW不正

**Step 3: 教室選択（複数教室の場合）**
- POST `/cgi-bin/s2login.pl` data: `cmd=jsx&param={"cmd":"login","kcd":"5558","loginid":"...","loginpw":"<encrypted>","tantoshacd":"...","usersm":"...","accesslv":"..."}`
- 成功 → `{"result":"OK", "SCRIPT":"openmain(...)"}`

**暗号化**: CryptoJS.AES.encrypt(plaintext, passphrase)
- CryptoJSデフォルト: OpenSSL互換、PBKDF2でキー導出、CBC、PKCS7パディング
- Python実装: `pycryptodome` でCryptoJS互換のAES暗号化を再現可能

### ログイン（ブラウザ直接）
- GET `/sks.wpp` — ログイン画面
  - トップページ `/` から `onclick="return checkie10('/sks.wpp');"` で遷移
  - フィールド: ログインID (type=tel), パスワード (type=password)
  - ID: 55580002 / PW: Hato1234
- POST `/cgi-bin/s2login.pl` — ログイン処理
  - 成功 → GET `/sks.wpp?cmd=op` → `/service/`（メインメニュー）にリダイレクト
- **⚠ ログイン失敗を繰り返すとロックされる可能性。リトライ禁止。**

## メインメニュー (`/service/`)

教室: (5558) 鳩ヶ谷校
業務検索プルダウン: 生徒登録(IEB010), 生徒名簿一覧(IEB030), 等
その他リンク: 通知一覧(`IEW010.wpp`), WebETS実施者一覧, WebETS未実施者一覧

### 生徒管理メニュー
| 画面名 | URL |
|---|---|
| 生徒登録 | `IEB010.wpp` |
| 外部生登録 | `IEB040.wpp` |
| 生徒名簿一覧 | `IEB030.wpp` |
| 受講履歴登録 | `IEB020.wpp` |
| PCS・夢SEED | `pcs.wpp` |
| 中学受験版PCS・夢SEED | `jpcs.wpp` |
| 夢SEED発注管理 | `#`（リンク先なし） |
| 夢SEED単元別確認テスト | `#`（リンク先なし） |
| 生徒集計一覧 | `IEB410.wpp` |
| 成績管理メニュー | `nssk.wpp` |
| PCS実施状況一覧 | `IEB420.wpp` |
| BCS視聴状況一覧 | `IEB440.wpp` |
| 生徒カード利用更新ステータス | `IEC110.wpp` |
| BCS-DVD一括注文 | `DVD020.wpp` |
| BCS-DVD注文状況 | `DVD010.wpp` |

### 受講料金管理メニュー

#### カード・ワイドネット者メニュー
| 画面名 | URL |
|---|---|
| カード・ワイドネット引落不能CVS振替 | `IEV110.wpp` |
| カード・ワイドネット引落結果確認 | `IEB210.wpp` |
| カード・ワイドネット再引落指定 | `IEB080.wpp` |
| カード・ワイドネット者用請求データ作成 | `IEB110.wpp` |
| カード・ワイドネット者用料金入力 | `IEB061.wpp` |
| カード・ワイドネット請求締処理 | `IEB320.wpp` |
| ★カード・ワイドネット請求締戻し処理 | `IEB325.wpp` |

#### 振込者メニュー
| 画面名 | URL |
|---|---|
| 振込者用料金入力 | `IEB070.wpp` |
| コンビニ振込用紙発行依頼 | `IEV120.wpp` |
| コンビニ振込用紙【再発行】 | `IEV130.wpp` |

#### 調整メニュー
| 画面名 | URL |
|---|---|
| 調整入力 | `IEB240.wpp` |
| 返金一覧 | `IEB270.wpp` |
| 調整一覧 | `IEB280.wpp` |

#### 受講料金関係帳票メニュー
| 画面名 | URL |
|---|---|
| 代金内訳明細書印刷 | `IEB550.wpp` |
| 各種ご案内書印刷 | `IEE570.wpp` |

#### 入金管理メニュー
| 画面名 | URL |
|---|---|
| 入金入力 | `IEB290.wpp` |
| 生徒未入金一覧表印刷 | `IEB530.wpp` |
| 入金内訳一覧 | `IEB260.wpp` |
| 未収金内訳明細 | `IEB220.wpp` |
| 月謝台帳 | `IEB190.wpp` |
| 月謝台帳出力 | `IEB250.wpp` |
| 請求取消処理 | `IEB300.wpp` |
| 滞留債権処理 | `IEB310.wpp` |
| コンビニ未入金一覧 | `IEV140.wpp` |
| コンビニ入金済一覧 | `IEV250.wpp` |

### やる気度診断メニュー
| 画面名 | URL |
|---|---|
| やる気度回答入力 | `IEC010.wpp` |
| やる気度診断照会 | `IEC510.wpp` |
| やる気度回答入力＆照会【小学生版】 | `IEC020.wpp` |
| ETS実施状況一覧 | `IEB430.wpp` |

### プレミアムクラブ管理メニュー
| 画面名 | URL |
|---|---|
| 保護者会員サイト | `#`（リンク先なし） |
| 管理画面ログイン | `#`（リンク先なし） |

### マスタ管理メニュー
| 画面名 | URL |
|---|---|
| 振込先登録 | `IEM030.wpp` |
| 担当者登録 | `IEM041.wpp` |
| 学校登録 | `IEM050.wpp` |

### 映像管理メニュー
| 画面名 | URL |
|---|---|
| 映像システム管理 | `#`（SSO経由。詳細は映像システム管理セクション参照） |

### その他リンク
| 画面名 | URL |
|---|---|
| 通知一覧 | `IEW010.wpp` |
| FC Q&A | `/fc/fcqa/` |
| FCマニュアル | `/fc/manual/` |
| ログアウト | `/cgi-bin/logout.pl` |

## 生徒名簿一覧 (`/service/IEB030.wpp`)

### ページ構造
- **外フレーム**: 検索フォーム + 列選択チェックボックス + アクションボタン
- **内iframe**: データテーブル（生徒行）。`frames[0]` でアクセス

### 検索フィルタ
| フィールド | 説明 |
|---|---|
| 氏名フリガナ | テキスト入力 + 50音ボタン（ア〜ワ行） |
| 氏名漢字 | テキスト入力（name: `seitosm`） |
| 学年 | プルダウン（00:0歳 〜 19:成人, 99:その他） |
| 生徒区分 | ラジオ: **内部生**(default) / 外部生 / YSPC非会員とメール無効者 |
| 退塾除く | チェックボックス（default: ON） |
| 表示件数 | readonly テキスト（検索結果件数を表示） |

### アクションボタン
| ボタン | 説明 |
|---|---|
| 表示 | 検索実行 |
| 全選択 | 列チェックボックスを全選択 |
| 出力全選択 | データ行のチェックボックスを全選択（列選択とは別） |
| Excel出力 | 表示中データをExcel(HTML形式.xls)としてダウンロード |
| ハガキ印刷 | 選択行のハガキ印刷 |
| タック紙印刷 | 選択行のタック紙印刷 |
| 生徒登録 | 選択行の生徒詳細（IEB010.wpp）へ遷移 |
| クリア | フィルタリセット |
| 終了 | 画面を閉じる |
| 赤字表示のHELP | 赤字表示される場合の説明 |

### 表示列（右側チェックボックスで選択、全58列）

| # | 列名 | # | 列名 |
|---|---|---|---|
| 0 | 教室コード | 1 | 教室名 |
| 2 | 学年 | 3 | 生徒コード |
| 4 | 生徒氏名 | 5 | 氏名フリガナ |
| 6 | 英字氏名 | 7 | 性別 |
| 8 | 生年月日 | 9 | 入会年度 |
| 10 | 入会時学年 | 11 | 入会時組 |
| 12 | 小学校 | 13 | 中学校 |
| 14 | 高校 | 15 | 郵便番号 |
| 16 | 住所1 | 17 | 住所2 |
| 18 | 住所3 | 19 | 電話番号 |
| 20 | 緊急連絡先電話番号 | 21 | 緊急連絡先宛 |
| 22 | 保護者氏名 | 23 | 備考 |
| 24 | 引落口座 | 25 | 契約者No |
| 26 | 引落開始予定年月 | 27 | 全銀コード |
| 28 | 全銀支店コード・通帳記号 | 29 | 銀行名 |
| 30 | 銀行カナ名 | 31 | 支店名 |
| 32 | 支店カナ名 | 33 | 口座種別 |
| 34 | 口座番号・通帳番号 | 35 | 名義人名 |
| 36 | 入塾日 | 37 | 退塾日 |
| 38 | 再塾日 | 39 | 授業開始日 |
| 40 | ETS実施回数 | 41 | ETS実施日 |
| 42 | 前受残 | 43 | コース名① |
| 44 | 科目① | 45 | 回数① |
| 46 | 金額① | 47 | コース名② |
| 48 | 科目② | 49 | 回数② |
| 50 | 金額② | 51 | コース名③ |
| 52 | 科目③ | 53 | 回数③ |
| 54 | 金額③ | 55 | 兄弟1 |
| 56 | 兄弟2 | 57 | 兄弟3 |
| 59 | YSポイント状況 | 60 | クラブ会員種別 |
| 61 | クラブ登録日 | | |

※ 列番号58は欠番（内部生colsで58を含めるとエラー）

### Python/MCPからの取得方法（GUIなし）
`POST /service/IEB030.wpp` に以下のパラメータ:
```
mode=if
cols=...               ← 表示列番号。**空だと0バイトが返る**
                         内部生: 0,1,...,57,59,60,61（58抜き）
                         外部生: 0,1,...,28（29列。内部生のcolsを使うとDBエラー）
selseitolist=
seitokm=
seitosm=
seitograde=
taijuku=1              ← 退塾除く（チェック時=1、含む場合は送らない）
listcount=
seitokb=naibu          ← 内部生。gaibu=外部生
```
レスポンス: iframe用HTML。`tr[ondblclick]`が生徒データ行。
- 内部生（退塾除く）: 46件程度
- 内部生（退塾含む）: 683件程度
- 外部生: 358件程度

### 出力機能
- 「Excel出力」ボタン — 表示中のデータをExcel(HTML形式.xls)としてダウンロード
- 「ハガキ印刷」「タック紙印刷」
- 「生徒登録」ボタン — 生徒詳細（IEB010.wpp）へ遷移

### 生徒詳細への遷移
行をクリック → bn010クリック → `IEB010.wpp`（生徒登録画面）にPOST遷移
- `window.open('', 'IEB030', ...)` で新ウィンドウを開こうとする
- 事前に `window.name = "IEB030"` を設定しておくと同タブ遷移になる
- beforeunloadダイアログは `Event.prototype.returnValue` setter上書きで無効化

### 生徒詳細遷移前の必須JS
```javascript
// beforeunload無効化
const origDesc = Object.getOwnPropertyDescriptor(Event.prototype, 'returnValue');
if (origDesc) {
  Object.defineProperty(Event.prototype, 'returnValue', {
    get: origDesc.get,
    set: function(val) {
      if (this.type === 'beforeunload') return;
      if (origDesc.set) origDesc.set.call(this, val);
    },
    configurable: true
  });
}
window.onbeforeunload = null;

// window.openを新規タブで開くように上書き（同タブだと閉じたとき戻れない）
window.name = "IEB030";
const _origOpen = window.open.bind(window);
window.open = function(url, name, features) {
  if (!url || url === '' || url === 'about:blank') return window;
  return _origOpen(url, '_blank');
};
```

## 外部生登録 (`/service/IEB040.wpp`)

### ページ構造
- タブなし、単一フラットページ構成（iframe/frameなし）
- IEB010（内部生）と比べて大幅に簡素。金融・請求関連セクションが一切ない

### IEB010との関係
- 外部生＝見込み客（講習会参加者やETS体験者）。請求が発生しないため口座・ワイドネット・クレジット等は不要
- IEB010の「外部生取込」機能で外部生→内部生に変換可能（取込時に入会年度/入会日等が設定される）
- IEB030で `seitokb=gaibu` 検索時にcols=0〜28（29列）しか使えないのは、外部生テーブルに口座・請求・家族フィールドが存在しないため

### 外部生区分（ラジオ）
- **講習会生**（デフォルト）
- **ETS体験生**

### フォームフィールド

#### 基本情報
| フィールド | 型 | 備考 |
|---|---|---|
| 生徒番号 | テキスト | + 表示/検索ボタン |
| 登録年度 | readonly | IEB010の「入会年度」に相当 |
| 非表示区分 | チェックボックス | 「非表示にする」— 一覧から隠す |
| 生徒氏名 | テキスト | |
| 保護者氏名 | テキスト | トップレベルフィールド（IEB010では家族構成テーブル内） |
| フリガナ(半角カナ) | テキスト | 全角→半角自動変換 |
| フリガナ(英字) | テキスト | |
| 性別 | プルダウン | 男/女 |
| 生年月日 | テキスト | YYYY/MM/DD |
| 登録時学年 | プルダウン | 00:0歳〜19:成人, 99:その他。IEB010の「入会時学年」に相当 |
| 組 | テキスト | |

#### 学校情報
IEB010と同じ構成: 小学校/中学校/高校 × (コード(readonly) + 名前(readonly) + 検索ボタン)

#### 住所・連絡先
IEB010と同じ構成: 郵便番号(+検索) / 住所1〜3 / 電話番号 / 緊急連絡先TEL / 緊急連絡先宛先 / 備考

### IEB010にあってIEB040にないセクション
- ❌ 家族構成（4行: ラジオ+続柄+氏名+年齢+勤務先）
- ❌ 入会金（金額/キャンペーン種別/ちらし名）
- ❌ ワイドネット請求情報
- ❌ クレジットカード決済
- ❌ YSPC会員種別
- ❌ 口座情報（銀行）
- ❌ 教室間移籍・他ブランド在籍
- ❌ 授業登録ボタン
- ❌ 住所以外への請求書送付先登録ボタン

### IEB040固有の要素
| ボタン/フィールド | 説明 |
|---|---|
| 外部生区分ラジオ | 講習会生 / ETS体験生 |
| 非表示区分チェック | レコードを一覧から隠す |
| ETSID出力ボタン | ETS用のID出力 |
| 問合せ管理起動ボタン | 最初からenabled（IEB010ではdisabled） |

### アクションボタン
| ボタン | 状態 | 説明 |
|---|---|---|
| 問合せ管理起動 | enabled | `/service/tryers/` を開く |
| 検索（問合せ） | enabled | |
| 表示 / 検索（生徒番号） | enabled | |
| 検索（小学校/中学校/高校/郵便番号） | enabled | |
| ETSID出力 | enabled | |
| 追加／修正 | **disabled** | 生徒表示後にenableになると推測 |
| 削除 | **disabled** | 同上 |
| クリア | enabled | フォームリセット |
| 終了 | enabled | 画面を閉じる |

## 生徒登録 (`/service/IEB010.wpp`)

### ページ構造
- タブなし、単一フラットページ構成（iframe/frameなし）
- IEB030.wppの「生徒登録」ボタンからPOST遷移で開く
- `fminvoke` フォーム: `seitocd={生徒番号}`, `action="IEB010.wpp"`, `target` を `_blank` に変更して新規タブで開く

### IEB030からの遷移方法（DevTools）
```javascript
// invokeSeito()内部の動作を再現
// fminvoke.target = "IEB030" だと同タブ遷移になるので _blank に変更
const fm = document.fminvoke;
fm.kyoshitsucd.value = "";
fm.seitocd.value = "{生徒番号}";
fm.action = "IEB010.wpp";
fm.target = "_blank";
fm.submit();
```
※ `invokeSeito()` は `CSAction(new Array('27829040'))` → `CSCallFunction('invokeSeito','')` で呼ばれる
※ `selectedseito` hidden要素に、iframe内で行クリックした生徒番号がセットされる

### セクション構成

#### 検索・取込エリア
| フィールド | 説明 |
|---|---|
| 生徒番号 | テキスト入力 + 表示ボタン + 検索ボタン |
| 問合せ管理起動 | ボタン（`id="bnToiawaseKidou"`）→ `/service/tryers/` をwindow.openで開く |
| 外部生番号 | テキスト入力 + 取込ボタン + 検索ボタン（disabled） |
| スクエア生 | 教室プルダウン + 番号入力 + 取込ボタン（disabled） |

#### 基本情報
| フィールド | 型 | 備考 |
|---|---|---|
| 入会年度 | readonly | |
| 入会日【契約日】 | テキスト | YYYY/MM/DD |
| 氏名 | テキスト | |
| フリガナ | テキスト | 半角カナ |
| 英字氏名 | テキスト | |
| 性別 | プルダウン | 1:女, 2:男 等 |
| 生年月日 | テキスト | YYYY/MM/DD |
| 入会時学年 | プルダウン | 00:0歳〜19:成人, 99:その他 |
| 組 | テキスト | |
| 教室間移籍・他ブランド在籍 | プルダウン | 無し / 移籍 / 他ブランド在籍 |

#### 学校情報
| フィールド | 型 | 備考 |
|---|---|---|
| 小学校コード | readonly(10桁) | + 検索ボタン |
| 小学校名 | readonly | 検索で自動セット |
| 中学校コード | readonly | + 検索ボタン |
| 中学校名 | readonly | |
| 高校コード | readonly | + 検索ボタン |
| 高校名 | readonly | |

#### 住所・連絡先
| フィールド | 型 | 備考 |
|---|---|---|
| 郵便番号 | テキスト | + 検索ボタン（住所自動入力） |
| 住所1 | テキスト | 都道府県市区町村 |
| 住所2 | テキスト | 番地 |
| 住所3 | テキスト | 建物名等 |
| 電話番号 | テキスト | |
| 緊急連絡先TEL | テキスト | |
| 緊急連絡先 宛先 | テキスト | |
| 備考 | テキスト | メールアドレス等が入ることがある |

#### 家族構成（4行）
各行: ラジオ（保護者=請求宛先選択） + 続柄プルダウン + 氏名 + 年齢 + 勤務先
- 続柄選択肢: 父/母/祖父/祖母/兄/姉/弟/妹/叔父/叔母/夫/妻/本人/その他
- ラジオがチェックされた行が請求宛先になる

#### 入会金
| フィールド | 型 | 備考 |
|---|---|---|
| 入会金 | readonly | |
| キャンペーン種別 | プルダウン(disabled) | ペア入塾/兄弟/講習/再入塾/チラシ割引/GLEC単独入会 |
| ちらし名 | テキスト(disabled) | |

#### ワイドネット請求情報
| フィールド | 型 | 備考 |
|---|---|---|
| 引落し口座 | プルダウン(disabled) | 銀行 等 |
| 契約者No. | テキスト(disabled) | 形式: `0{教室コード}0000{生徒番号}` |
| 引落開始年月 | テキスト(disabled) | YYYY/MM |

#### クレジットカード決済
- ステータス表示（例: `（請求不可）`）
- 送付依頼ボタン

#### YSPC会員種別
- ステータス表示（例: `プレミアム会員（有料）`）
- YSPC管理画面リンク（`href=#`, JS遷移）

#### 口座情報（銀行）
| フィールド | 型 | 備考 |
|---|---|---|
| 全銀コード | テキスト(disabled) × 2 | 銀行コード + 支店コード |
| 銀行名・支店名 | テキスト(disabled) × 2 | + 検索ボタン |
| 口座種別 | プルダウン(disabled) | 普通 等 |
| 口座番号 | テキスト(disabled) | |
| 名義人 | テキスト(disabled) | 半角カナ |

### アクションボタン
| ボタン | 説明 |
|---|---|
| 授業登録 | 受講登録画面へ遷移 |
| 住所以外への請求書送付先登録 | 別送付先の設定 |
| 追加／修正 | 生徒情報の保存 |
| 削除 | 生徒データ削除 |
| クリア | フォームリセット |
| 終了 | 画面を閉じる |

## 受講履歴登録 (`/service/IEB020.wpp`)

### 表示方法
IEB010.wppの「授業登録」ボタンから**モーダルiframe**で開く（window.openではない）。
- `cmdJugyoTorokuClick()` → `id='modal'` を表示 → `id='modalif'`(iframe)に `IEB020.wpp` をPOST
- POSTパラメータ: `seitocd={生徒番号}`, `mode=seito`, `sub=1`（hidden要素を動的生成してsubmit）
- `forminvoke.target = 'modalif'`

### ページ構造
モーダルiframe内の単一ページ。ヘッダー（教室・生徒番号・氏名【学年】・入会日）+ 受講履歴セクション + コース情報テーブル。

### 受講履歴セクション
| フィールド | 型 | 備考 |
|---|---|---|
| 日付 | テキスト | YYYY/MM/DD |
| 受講履歴 | プルダウン | 02:通常授業開始 / 03:退塾または休塾 / 04:再塾 / 05:コース変更 / 06:選択科目変更 |
| 備考 | テキスト | |

ボタン: 追加 / 削除 / クリア（履歴行の操作）

既存の履歴行は上部にreadonly表示される。

### コース情報テーブル（7行）
各行の構成:
| フィールド | 型 | 備考 |
|---|---|---|
| 削除 | チェックボックス | |
| コース名 | プルダウン | コードと名称（例: `12100：PS2･小4`） |
| 回数 | テキスト | 週あたり回数 |
| 国 | チェックボックス | 国語 |
| 数 | チェックボックス | 数学・算数 |
| 英 | チェックボックス | 英語 |
| 理 | チェックボックス | 理科 |
| 社 | チェックボックス | 社会 |
| 金額 | readonly | コース＋回数から自動算出 |

- 最大7コース同時登録可能（通常は1〜2行使用）
- コース名プルダウンは学年に応じた選択肢が表示される

### アクションボタン
| ボタン | 説明 |
|---|---|
| 追加／修正 | コース情報を保存 |
| 元に戻す | 変更を取り消し |
| クリア | コース情報をリセット |
| 終了 | モーダルを閉じる |

## PCS・オーダーメイドテキスト (`/service/pcs.wpp`)

### 概要
PCS（パーソナルカリキュラムシステム）はテスト作成・採点・カリキュラム管理システム。
系統図画面は別ドメイン `{SKS_SSK2_URL}` で動くJava Servlet（.do）。

### 画面遷移
```
メインメニュー → PCS・夢SEED (pcs.wpp, 同タブhref遷移)
  → 生徒番号入力 → 検索（Ajax: pcs.wpp?cmd=ax&param={生徒番号}）
  → 生徒検索ポップアップ（iframe内モーダル、subwin/IKK011.wpp）
  → 教科選択（画像ボタン） → 系統図（PcsMenu.do, 同タブ遷移）
```

### PCS選択画面 (`/service/pcs.wpp`)
- 生徒番号入力 → 検索ボタン（画像）
- Ajax: `GET pcs.wpp?cmd=ax&param={生徒番号}` で生徒情報取得。`xmlseito()` 関数
- 教科ボタン（画像）→ `dopost2(kyoukakb, kyouzaikb)` → `fmpost2` フォームPOST → `pcs_start.wpp`

#### 教科パラメータマッピング（dopost2の引数）
| 教科 | kyoukakb | kyouzaikb | 備考 |
|---|---|---|---|
| 国語(小学) | 4 | 1 | |
| 算数・数学 | 2 | 0 | |
| 英語 | 3 | B | |
| 国語(中学) | 4 | 2 | |
| 理科(中学) | 5 | 2 | |
| 社会(中学) | 9 | 2 | |

#### fmpost2フォーム
- action: `pcs_start.wpp`
- hidden: `scd`(生徒番号), `kyoukakb`, `kyouzaikb`, `pflag=1`, `omtflag=1`
- pcs_start.wpp → ssk2の`Pcs.do`へ2段階POSTで系統図に遷移

### 系統図 (`PcsMenu.do`) — 別ドメイン ssk2
- URL: `https://{SKS_SSK2_URL}/pcs/PcsMenu.do`
- 同タブ遷移（pcs.wppから直接）

#### JS変数
| 変数 | 説明 | 例 |
|---|---|---|
| `STAGES` | 表示可能な学年ステージ | `['1','2','3']` (小/中/高) |
| `GMODE` | 現在表示中のステージ | `2` (中学生版) |
| `KK` | 教科コード | `'2'` (算数・数学) |
| `LOAD_FLAG` | データ読み込み完了フラグ | `true` |
| `TangenList` | 全単元データ（ステージ別） | stage1:109, stage2:101, stage3:121単元 |
| `SMSG` | ステータスメッセージ | 次の操作を示すガイド文 |

#### 単元の色（状態）
| cname | 色 | 意味 | 遷移タイミング |
|---|---|---|---|
| (empty) | 白 | 未選択・初期状態 | |
| `clYellow` | **黄** | **習得済選択**（問題作成対象に選んだ） | ①問題作成で付与 |
| `clGreen` | **緑** | **採点結果 ○**（正解） | ③採点で付与 |
| `clBlue` | **青** | **採点結果 △**（一部正解） | ③採点で付与 |
| `clNavy` | **紺** | **採点結果 ×**（不正解） | ③採点で付与 |
| `clLNavy` | 水色 | **目標選択**（今後の学習目標） | 目標選択で付与 |
| `clRed` | **赤/ピンク** | **予定登録済** | ⑤予定登録で付与 |
| `clGray` | 灰 | **カリキュラム作成後**（完了） | ⑥カリキュラム作成で付与 |

※公式ヘルプ(IKKhelp.html)のフローチャートに基づく。△×の単元のうち、青単元（目標単元）に関連する単元のみ赤く表示される。

#### PCSワークフローの状態遷移（公式フロー + 実測）
```
[empty] → 習得済選択 → [clYellow]（黄）
  → ①問題作成
  SMSG: 「問題が印刷できます。採点を押して下さい。」
  有効ボタン: ①②③ / 無効: ④⑤⑥

→ ②問題印刷（PDFダウンロード、状態変化なし）

→ ③採点 → [clGreen=○ / clBlue=△ / clNavy=×]
  SMSG: 「結果帳票が印刷できます。予定登録を押して下さい。」
  有効ボタン: ④⑤

→ ④結果帳票印刷（PDF、状態変化なし）

→ 目標選択 → [clLNavy]（水色）

→ ⑤予定登録 → [clRed]（赤/ピンク）
  SMSG: 「カリキュラム作成を押して下さい。」
  有効ボタン: ⑥

→ ⑥カリキュラム作成 → [clGray]（灰）
  月回数(tukikaisu=4)を入力して登録
  SMSG: 「単元を選択し問題作成を押して下さい。」
  → 次のテストサイクルへ（kaisu+1）
```

#### ボタン有効/無効の状態マトリクス
| 状態 | ① | ② | ③ | ④ | ⑤予定 | ⑤次テスト | ⑥ | 問題削除 |
|---|---|---|---|---|---|---|---|---|
| 初期（単元選択前） | ○ | × | × | × | × | × | × | × |
| 問題作成後 | ○ | ○ | ○ | × | × | × | × | × |
| 採点後 | ○ | ○ | ○ | ○ | ○ | × | × | × |
| 予定登録後 | ○ | ○ | ○ | ○ | ○ | ○ | ○ | × |
| カリキュラム作成後 | ○ | × | × | × | × | × | × | × |

#### 3つのフォーム
| フォーム | action | method | 用途 |
|---|---|---|---|
| `formmain` | PcsMenu.do | post | ①問題作成/⑤予定登録/問題削除（checksを含むPOST） |
| `form1` | PcsMenu.do | post | ①問題作成(cmd_updm)/⑤予定登録(cmd_updy)のchecks送信 |
| `fmpost` | PcsMenu.do | get | 更新(__reload) |

#### form1のhiddenフィールド
| name | 説明 |
|---|---|
| `jisshikaisu` | 実施回数 |
| `seitoCd` | 生徒番号 |
| `kyoukakb` | 教科コード |
| `kyouzaikb` | 教材区分 |
| `gtype` | ステージタイプ（1=小, 2=中, 3=高） |
| `version` | バージョン（固定: 6） |
| `mode` | 処理モード（`updm`=問題作成, `updy`=予定登録, `nextm`=次のテスト, `delete`=問題削除） |
| `testflg` | テストフラグ（0=通常, 1=BIテスト） |
| `pattern` | パターン |
| `checks` | 全単元のチェック状態（`doCheckboxes()`で生成） |

#### ボタン一覧
| ボタン | 関数 | 動作 |
|---|---|---|
| 選択削除 | `cmd_alloff()` | 全チェックボックスを解除 |
| 問題削除 | `cmd_delete()` | confirm後 `formmain.mode='delete'` でsubmit |
| ① 問題作成 | `cmd_updm(false)` | checksを生成→form1でPOST（mode=updm） |
| ① デジタル版問題作成 | `cmd_updm(true)` | タブレット用問題作成 |
| ② 問題印刷 | `cmd_printm()` | `window.open('PcsPrintMondai.do?flg=...')` |
| ③ 採点 | `cmd_upds()` | `window.open('PcsSaiten.do')` ← **cmd_saitenではなくcmd_upds** |
| ④ 結果帳票印刷 | `cmd_printr()` | `window.open('PcsPrintResult2.do?flg=...')` |
| ⑤ 予定登録 | `cmd_updy()` | checksを生成→form1でPOST（mode=updy） |
| ⑤ 次のテストへ | `cmd_nextm()` | form1でPOST（mode=nextm） |
| ⑥ カリキュラム作成 | `cmd_updk()` | `window.open('PcsCurriculum.do')` |
| 履歴 | `showhist()` | `window.open('PcsHistory.do?p=5558_250022_'+KK)` |
| 色の説明 | `showHelp()` | `window.open('IKKhelp.html')` |
| 更新 | `__reload()` | fmpostでGET（PcsMenu.do） |
| 小学生版/中学生版/高校生版 | `changeStage('1'/'2'/'3')` | ステージ切替（DOM表示切替のみ） |
| 夢SEED発注 | `omt(0)` / `omt(1)` | `window.open('Omt.do?flag=0&ktzFlag=...')` |
| 拡大/リセット | `zoomPcs(0.5)` / `zoomPcs(0)` | CSS zoom変更 |
| 終了 | `closeOmt(); CSAction(...)` | 夢SEEDウィンドウを閉じて終了 |

### 夢SEED発注 (`Omt.do`) — ポップアップ/新規タブ（2026-04-17分析）

- URL: `https://{SKS_SSK2_URL}/pcs/Omt.do?flag=0&ktzFlag={0|1}`
  - `ktzFlag=0`: テーブル形式の問題数入力画面（`omt(0)`）
  - `ktzFlag=1`: ビジュアル系統図で単元選択する画面（`omt(1)`）
  - ktzFlag=1の「問題数入力画面」ボタンでktzFlag=0に遷移可能
- **window.open経由**で開くため、事前にwindow.open上書きが必要

#### 画面構成
- 教室・生徒情報ヘッダー
- 概算問題ページ数（※30～120ページに収める）、概算総ページ数（※400ページに収める）、合計出題数
- 製本オプション: 公式集出力（中1/中2/中3 各checkbox）、中学準備編出力、高校準備編出力

#### ボタン

| ボタン | 機能 | 備考 |
|--------|------|------|
| 発注確認画面 | 確認画面へ遷移 | 出題数0のときdisabled。**押しても発注完了ではない** |
| 全出題数クリア | 全出題数リセット | |
| 途中保存 | 作成途中の状態を保存 | |
| キャンセル | 中止 | |
| 一括設定/一括解除 | まとめ解説の一括ON/OFF | |
| すべて表示/非表示 | 問題形式の表示切替 | |
| PCS・ETS連動 | PCS/ETS結果と連動して出題 | |
| 出題する | 一括出題（難易度select + 問数text） | |

#### 単元テーブル（約200+小単元 × 3難易度 = 約600テキストボックス）

各行の構造:

| まとめ解説(checkbox) | 大単元(checkbox) | 小単元(checkbox) | 学年 | レベル変更 | 結果 | 発展(出題数text/N問) | 標準(出題数text/N問) | 基礎(出題数text/N問) |

- 結果ステータス: `未`(未実施) / `○`(合格) / `△`(一部合格) / `×`(不合格)
- 利用可能問題数が0の難易度はテキストボックスがdisabled
- 単元ジャンプ: `[小]`→`#tg1`, `[中]`→`#tg2`, `[高]`→`#tg3`

#### 一括出題コントロール
- 難易度select: `発展` / `標準` / `基礎`
- 問数textbox + 「出題する」ボタン → チェック済み単元に一括出題

#### 単元範囲（ktzFlag=0 テーブル形式共通）
- **小学校**（小2～小6）: 整数、四則演算、分数、小数、図形、面積、体積、割合、比例反比例、場合の数
- **中学校**（中1～中3）: 正負の数、文字と式、方程式、関数、図形、証明、確率、データ分析 + 入試対策
- **高校**: 数I/A/II/B/C/III の全範囲

#### ktzFlag=1 系統図版の構成
- ビジュアル系統図（画像上にチェックボックスを絶対配置）
- チェックボックス全331個（ktzFlag=0と同じ `tg{単元コード}` name属性）
- テキストボックスなし（問数入力はktzFlag=0側で行う）
- `changeStage(1/2/3)` でDOM表示切替（小学/中学/高校）

#### ktzFlag=1 フォーム（`omtForm`）
- action: `Omt.do` (POST)
- hidden fields: `kyoshitsuCd`, `seitoCd`, `seitoGakunen`, `kyoukakb`, `kyouzaikb`, `gtype`, `version`, `tempSeqNo`, `tcd`, `tnm`, `uid`, `listflag`, `gmflag`, `ktzFlag=true`, `kselTangen`, `flag`, `s_pcsets`, `s_pcsets2Lv`, `ktzHistLinkFlag`

#### ktzFlag=1 ボタン動作

| ボタン | 関数 | 動作 |
|--------|------|------|
| 問題数入力画面 | `submitInput()` | チェック済み単元コードを `kselTangen` にセット、`flag=2` で omtForm submit → ktzFlag=0 に遷移（選択単元のみ表示） |
| PCS・ETS連動 | `openPcsetsPanel()` | 連動パネル表示 |
| 出題する | `setPcsetsLevel()` | PCS・ETS連動パネル内で難易度一括設定 |
| 閉じる | `closePcsetsPanel()` | パネル閉じる |
| 小学生版/中学生版/高校生版 | `changeStage(1/2/3)` | DOM表示切替 |
| キャンセル | `self.close()` | タブ閉じ |
| 拡大/リセット | `zoomPcs(0.5/0)` | CSS zoom |

#### ktzFlag=1 → ktzFlag=0 遷移フロー
1. 系統図上でチェックボックスを選択
2. 「問題数入力画面」クリック → `submitInput()` 発火
3. チェック済み単元コードが `kselTangen` hidden に格納（カンマ区切り）
4. `flag=2` で POST → ktzFlag=0 テーブル形式に遷移
5. 選択した単元のみフィルタ表示される

#### 第2フォーム（履歴読み込み）
- action: `OmtPcsHistoryLoad.do` (POST, target=`wWork`)
- hidden: `kyoshitsuCd`, `seitoCd`, `kyoukakb`, `kyouzaikb`, `version`, `tcd`, `tnm`, `akey`（認証トークン）

#### ktzFlag=1 チェックボックス内訳（全331個）

| ステージ | name範囲 | 数 |
|---------|----------|-----|
| 小学 | `tg12xx`〜`tg16xx` | 67 |
| 中学 | `tg17xx`〜`tg20xx` + `tg87xx`〜`tg99xx` | 112 |
| 高校 | `tg10xx` + `tg22xx`〜`tg60xx` | 152 |
| **合計** | | **331** |

#### 単元チェックボックスのname規則
`tg{単元コード}` 形式。例: `tg1201_31`, `tg1001_02`
- 単元コードは `tg{大分類}{小分類}_{連番}` 構造
- 算数・数学で全331単元

#### 単元コード先頭2桁→学年マッピング（数学）

| 先頭2桁 | 学年 | 内容 | 単元数 |
|---------|------|------|--------|
| 12 | 小2 | 整数の基礎、たし算ひき算 | 8 |
| 22 | 小2 | 時刻と時間、表とグラフ | 3 |
| 32 | 小2 | 長さ・かさ、三角形四角形、箱の形 | 6 |
| 13 | 小3 | たし算ひき算、かけ算わり算、分数 | 12 |
| 23 | 小3 | 時刻と時間、表とグラフ | 2 |
| 33 | 小3 | 長さ重さ、三角形、円と球 | 4 |
| 14 | 小4 | 整数、四則、分数、小数 | 17 |
| 24 | 小4 | 表とグラフ、折れ線グラフ | 3 |
| 34 | 小4 | 角度、面積、立方体直方体 | 8 |
| 15 | 小5 | 整数、小数、分数、割合 | 19 |
| 25 | 小5 | 表とグラフ | 2 |
| 35 | 小5 | 図形、面積、体積 | 8 |
| 16 | 小6 | 分数計算、比、比例反比例、場合の数 | 11 |
| 26 | 小6 | 表とグラフ | 2 |
| 36 | 小6 | 図形、面積、体積 | 4 |
| 17 | 中1 | 正負の数、文字と式、方程式 | 19 |
| 27 | 中1 | 比例反比例 | 5 |
| 37 | 中1 | 平面図形、空間図形 | 7 |
| 87 | 中1 | まとめ（正負、文字式、方程式、比例反比例、平面図形、空間図形） | 6 |
| 18 | 中2 | 式の計算、連立方程式、1次関数 | 5+α |
| 28 | 中2 | 平行と合同、図形の性質と証明 | 5 |
| 38 | 中2 | 確率、データの分析 | 7 |
| 88 | 中2 | まとめ | 5 |
| 19 | 中3 | 式の展開と因数分解、平方根、2次方程式 | 10 |
| 29 | 中3 | 2乗に比例する関数 | 4 |
| 39 | 中3 | 相似、円、三平方の定理 | 7+α |
| 89 | 中3 | まとめ | 7 |
| 99 | 入試対策 | 計算、数の性質、文章題、関数、作図、図形、確率、融合 | 9 |
| 10 | 数I | 集合、論理、整式と実数、1次不等式、2次方程式、2次関数、三角比、図形と計量、データの分析 | 22 |
| 40 | 数A | 場合の数、確率、図形の性質、数学と人間の活動 | 17 |
| 20 | 数II | 式と証明、複素数と方程式、図形と方程式、指数対数関数、三角関数、微分積分の考え | 24 |
| 50 | 数B | 数列、確率分布と統計的な推測 | 10 |
| 60 | 数C | 平面ベクトル、空間ベクトル、平面上の曲線と複素数平面 | 12 |
| 30 | 数III | 極限、微分法、積分法 | 13 |
| 90 | 高校まとめ | 数I/A/II/B/C/IIIの各まとめ | 19 |

#### `shubetsu` ラジオ（テスト種別）
- `shubetsu[0]` = 通常テスト（checked=0）
- `shubetsu[1]` = BIテスト（checked=1）
- 外部生はBIテスト不可
- **window.open上書きが必要**（ページ遷移でリセットされるため毎回設定）

### 採点登録 (`PcsSaiten.do`) — ポップアップ/新規タブ
- URL: `https://{SKS_SSK2_URL}/pcs/PcsSaiten.do`
- フォームフィールド:
  - `correctcnt({問題コード})` — 正解数入力（例: `correctcnt(1002-0201-0009)`）
  - ID: `POINT_{番号}` （例: `POINT_1` 〜 `POINT_16`）
  - 各問題 `/1` 形式（1問中何問正解か）
- ボタン: 採点印刷 / 取消削除 / **登録/修正** / 終了
- 登録: POST `PcsSaiten.do` → 確認ダイアログ（SKS独自モーダル）→ OK → 完了
- 終了: `doClose()` — ウィンドウを閉じる（新規タブなら普通にタブを閉じればOK）

### カリキュラム登録 (`PcsCurriculum.do`) — ポップアップ/新規タブ
- URL: `https://{SKS_SSK2_URL}/pcs/PcsCurriculum.do`
- **window.open上書きは⑥カリキュラム作成ボタンの直前にのみかける**（予定登録等の他のボタンでは上書き不要。上書きしたままだと予定登録等がフォームsubmitで白画面になる）

#### ヘッダー情報
| フィールド | name | 型 |
|---|---|---|
| 教室コード | `kyoshitsucd` | text |
| 教室名 | `kyoshitsusm` | text |
| 生徒番号 | `seitocd` | text |
| 生徒氏名 | `seitosm` | text |
| 学年 | `gakunen` | text |
| 実施回数 | `jisshikaisu` | text |
| 実施日 | `jisshidt` | text (YYYYMMDD) |

#### 表示設定
| フィールド | name | 型 | 備考 |
|---|---|---|---|
| 表示対象 | `disp` | ラジオ | 0:講師用 / 1:講師用(青赤のみ) / 2:面談用 |
| 月回数 | `tukikaisu` | テキスト | **登録時1以上必須**。0だとエラー |
| セルの移動 | `cell` | ラジオ | 0:下のセル / 1:右のセル |
| ランク | `rank` | テキスト | |

#### セクション1: 既定単元（テスト結果から自動生成）
テスト結果で弱点として判定された単元が自動的にリストされる。
各行のフィールド:
| name | 型 | 説明 |
|---|---|---|
| `code1_{N}` | hidden | 単元コード（例: `1302-31`） |
| `jikant1_{N}` | text | 必要時間合計 |
| `jikanie1_{N}` | text | 必要時間IE |
| `jikanka1_{N}` | text | 必要時間家庭 |
| `jikanko1_{N}` | text | 必要時間講習会 |

行数は `jikan1count` hidden で管理。

#### テーブル列
大単元 / 単元 / 学習目標【学校】 / テスト結果(▲/×等) / 学習目標【IE】 / 必要時間合計 / 必要時間IE / 必要時間家庭 / 必要時間講習会

#### セクション2: 追加分（手動、最大10行）
各行: `hdtan_{N}`(大単元名) + `htan_{N}`(単元名) + `jikant2_{N}` + `jikanie2_{N}` + `jikanka2_{N}` + `jikanko2_{N}`

#### アクションボタン
| ボタン | name | 関数 | cmd値 |
|---|---|---|---|
| カリキュラム印刷 | `cmdPrint` | `doPrint()` | `print`（target=_blankでPDF出力） |
| 取消削除 | `cmdDel` | `doDelete()` | `delete` |
| 登録/修正 | `cmdRegist` | `doRegist()` | `regist`（tukikaisu>=1チェック後submit） |
| 終了 | `cmdPrint` | `doClose()` | self.close() |

#### POST先
`PcsCurriculum.do` に自身へPOST。登録成功後は同画面がリロードされる。
登録後、系統図に戻って「更新」を押すと次のテストサイクルに進む。

### 問題・解答印刷 (`PcsPrintMondai.do`)
- 印刷選択画面: `GET PcsPrintMondai.do?flg=1`（ポップアップ/新規タブ）
- ラジオボタン: 問題／解答を印刷 / 問題のみ(A3) / 解答のみ(A4)
- PDF直接取得（GUIなしで可能）:
  - 問題のみ: `GET PcsPrintMondai.do?cmd=print&opt1=1&bgFlag=1` → PDF (A3 297x420mm)
  - 解答のみ: `GET PcsPrintMondai.do?cmd=print&opt1=2&bgFlag=1` → PDF (A4 210x297mm)
  - 問題+解答: `GET PcsPrintMondai.do?cmd=print&opt1=0&bgFlag=1` → PDF（未検証）
- bgFlag: 1=背景あり, 0=背景なし（推測）

### PCSセッション確立手順（Python/MCP用）
GUIなしでPCS系統図（別ドメイン ssk2）にアクセスするには2段階のフォームPOSTが必要:
1. `POST /service/pcs_start.wpp` data: `scd={生徒番号}&kyoukakb={教科}&kyouzaikb=0&pflag=1&omtflag=1`
2. レスポンスHTML内の `fmpost2` フォーム（action=`https://{SKS_SSK2_URL}/pcs/Pcs.do`）の全hiddenフィールドをPOST
   - hiddenに `kcd`, `rmad`, `hk`(ハッシュ), `knm`, `tcd`, `tnm`, `uid`, `gmflag` 等が含まれる
   - このPOSTでssk2ドメインのJSESSIONIDが発行される
3. 以降 `PcsPrintMondai.do`, `PcsSaiten.do`, `PcsCurriculum.do` 等にアクセス可能

### 印刷（ローカル）
- プリンタ: `iR-ADV C3720`（Canon iR-ADV C3720F、IP: 192.168.1.100）
- SumatraPDF: `C:\Users\hatogaya\AppData\Local\SumatraPDF\SumatraPDF.exe`
- 問題PDF印刷: `SumatraPDF -print-to "iR-ADV C3720" -print-settings paper=A3,color,noscale {pdf}`
- 解答PDF印刷:
  - 1ページ: `paper=A3,color,fit`（A3拡大）
  - 2ページ: `paper=A3,color,fit,2x1`（2in1）
  - 3-4ページ: `paper=A3,color,fit,2x2`（4in1）
  - 5-6ページ: `paper=A3,color,fit,3x2`（6in1）

### checks文字列の形式（予定登録・問題作成で使用）
`doCheckboxes(GMODE, STAGES[i], "checked")` が全チェックボックスの状態をCRLF区切りで返す:
```
{単元コード}|{0or1}|{色クラス}||\r\n
```
- `0` = 未チェック、`1` = チェック済み
- 色クラス: 現在の状態（clYellow=選択済, clGreen=○, clBlue=△, clNavy=×, clRed=予定登録済, clGray=完了, 空=未選択）
- **全単元（未チェック含む）を送る必要がある**
- **改行は `\r\n`（CRLF）を使うこと**
- **非選択単元の色クラスも保持して送ること**（空にするとサーバーが拒否する場合がある）
- tag/ctagは空でOK（4番目・5番目のフィールド）
- 例（Chromeで実際に送信された値）:
  ```
  1701_03|0|clNavy||\r\n
  1703_01|0|||\r\n
  1902_01|1|clYellow||\r\n
  1001_01|1|clYellow||\r\n
  ```
- STAGESはページのJSで定義された学年ステージの配列

### PythonからのPOST時に必要な3つの条件
1. **CRLF**: `\r\n` で行区切り
2. **色情報の保持**: HTMLのJS初期化 `doCheckbox("stg", "key", "color|色名")` を正規表現で抽出し色クラスに変換
3. **testflg自動判定**: HTMLに `shubetsu[1].checked = true` があれば `testflg=1`、なければ `testflg=0`

色名→色クラス変換表:
| 色名 | 色クラス |
|------|---------|
| 黄 | clYellow |
| 紺 | clNavy |
| 白 | (空) |
| 青 | clBlue |
| 灰 | clGray |
| 赤 | clRed |
| 薄紺 | clLNavy |
| 緑 | clGreen |
- GMODEは表示中の学年モード（"3"=高校生版）
- form1にPOST: `mode=updy`（予定登録）、`mode=updm`（問題作成）
- form1の必須hidden: `seitoCd`, `kyoukakb`, `kyouzaikb`, `gtype=3`, `version=6`
- **解決済(2026-04-17)**: PythonからのPOSTは正しい処理段階でのみ有効。問題作成済み(未採点)の状態で再度問題作成POSTしても無視される。正しい順序: 採点→(予定登録)→カリキュラム→更新→問題作成。各ステップを飛ばさずに順に実行すれば全てPython POSTで動作する
- **ステップスキップ可能（2026-04-17実験済み）**: サーバー側のバリデーションはUI側より緩い
  - ③採点スキップ: 可能。問題作成済→採点せずに⑥カリキュラムで次サイクルに進める
  - ⑤予定登録スキップ: 可能。UIではdisabledだがPython POSTで⑥カリキュラムを直接呼べる
  - **最短パス: ①問題作成 → ⑥カリキュラム(tukikaisu=4) → 更新 → 次の①問題作成**
  - ただし不正常な繰り返し（カリキュラムだけ何度も呼ぶ等）で状態が壊れることがある。正常なサイクルで使うこと
- **checksのcname（重要）**: サーバーは色クラスで操作を判別する
  - 問題作成(mode=updm): 選択単元に **`clYellow`** が必須。空だとサーバーが無視する。`{key}|1|clYellow||`
  - 予定登録(mode=updy): cmd_updyはclBlueの数をカウントして0だと拒否する。ブラウザでは`tgSelectChange()`が新規チェック単元をclBlueに設定する。`{key}|1|clBlue||`
  - ※clBlueは本来「採点結果△」の色だが、予定登録のJS判定にも使われる（二重の意味）
  - 非選択単元は `{key}|0|||` でOK（cnameは空）
  - 既に色がついている単元はその色をそのまま送る。tag/ctagも保持（例: `{key}|1|clNavy|紺|`）
  - ブラウザでは `tgSelectChange()` が自動的にcnameを設定するが、PythonからPOSTする場合は明示的に設定が必要
  - **改行コードは `\r\n`（CRLF）を使うこと**。ブラウザのform submitはCRLFで送る。LFでも通る場合があるが、英語高校生など条件によっては通らない。統一してCRLFにすべき
  - **testflg**: HTMLのJS初期化で `shubetsu[1].checked = true` が設定されている場合は `testflg=1` にする。PythonではHTMLレスポンスから `shubetsu[1].checked = true` の有無をチェックして自動判定すること
  - **色情報の保持**: 非選択単元もcnameを保持して送る。HTMLのJS初期化から `doCheckbox("stg", "key", "color|色名")` を正規表現で抽出し、色名→clNameに変換。tag/ctagは空でOK

### ⚠ PCS自動化の重要ルール

**1. 処理段階の確認が必須**
新しいPCS作成に入る前に、現在の処理段階を必ず確認する。ボタンの有効/無効状態で判断:
- ①問題作成 enabled, ②③ disabled → 単元未選択（初期状態）
- ②問題印刷 enabled, ③採点 enabled, ⑤予定登録 disabled → 問題作成済・未採点
- ⑤予定登録 enabled, ⑥カリキュラム disabled → 採点済・予定未登録
- ⑥カリキュラム作成 enabled → 予定登録済・カリキュラム未作成
未完了のステップがあれば、先にそれを完了させてから次のサイクルに進む。

**2. 各ステップの必須操作**
- ③採点: 全問の正解数を入力（テスト未実施なら全問0点で登録可）
- ⑤予定登録: checksを生成してPOST（mode=updy）。**window.open上書きはしない**
- ⑥カリキュラム作成: PcsCurriculum.doで `tukikaisu`（月回数）に**必ず4を入力**。0だと登録失敗する
- ⑥の後は「更新」ボタンで系統図をリロードしてから次のサイクルへ

**3. checksフィールドの必須条件**
- 全331単元を含める（選択=1、非選択=0）
- 形式: `{単元コード}|{0or1}|{色クラス}||` 改行区切り
- 色クラスは現在の状態（clBlue, clRed等）。新規や未実施は空文字でOK
- **1つも選択されていない（全部0）だとサーバーが拒否する**

**4. PCSサイクル完了の順序**
```
③採点(0点) → ⑤予定登録(checks) → ⑥カリキュラム作成(tukikaisu=4) → 更新 → ①問題作成(checks)
```
この順序を飛ばすとボタンが有効化されず処理が通らない。

### PCS 1回分の手順（GUI操作）
1. PCS画面で生徒番号入力 → 検索 → 教科選択 → 系統図
2. 現在の処理段階を確認（ボタン有効/無効で判断）
3. 未完了ステップがあれば先に完了させる:
   - ③採点（未採点なら0点登録）→ ⑤予定登録 → ⑥カリキュラム(tukikaisu=4) → 更新
4. 単元チェックボックスを選択（学年に応じて約25個）
5. ①問題作成 — **直前にwindow.open上書き**（ポップアップの場合）
6. ②問題印刷 → PDF取得 → SumatraPDFでA3カラー片面印刷
7. 解答印刷 → PDF取得 → ページ数に応じてNin1でA3印刷
8. ③採点 → 別タブで正解数入力 → 登録/修正 → タブ閉じる — **直前にwindow.open上書き**
9. ⑤予定登録（単元チェック後）— **window.open上書きはしない**
10. ⑥カリキュラム作成 → 別タブでtukikaisu=4入力 → 登録 → タブ閉じる — **直前にwindow.open上書き**

### GUIなしPCS手順（Python/MCP）
1. SKSログイン（AES暗号化3ステップ）
2. `pcs_start.wpp` POST → `Pcs.do` POST（2段階）でssk2セッション確立
3. ボタン状態を確認して現在の処理段階を判定
4. 未完了ステップを順に実行:
   - `PcsSaiten.do` POST cmd=regist → 採点（0点）
   - `PcsMenu.do` POST mode=updy + checks → 予定登録
   - `PcsCurriculum.do` POST cmd=regist + tukikaisu=4 → カリキュラム
   - `PcsMenu.do` GET（更新）
5. 単元選択して `PcsMenu.do` POST mode=updm + checks → 問題作成
6. `PcsPrintMondai.do?cmd=print&opt1=1&bgFlag=1` GET → 問題PDF
7. `PcsPrintMondai.do?cmd=print&opt1=2&bgFlag=1` GET → 解答PDF
8. SumatraPDFでローカル印刷

### SKS独自モーダルダイアログ
- ネイティブalert/confirmではない独自実装
- `window.confirm` / `window.alert` の上書きでは対応不可
- OKボタン: `document.getElementById('_overRideModalOK').click()`
- キャンセルもある場合: `.ui-dialog-buttonset button` で取得可能

## 問い合わせ管理 (`/service/tryers/`)

IEB010.wpp（生徒登録画面）の「問合せ管理起動」ボタン（`id="bnToiawaseKidou"`）から遷移。
`_StartSub2('',event,'ToiawaseRoot')` → `window.open('/service/tryers/', 'SearchWin', ...)` で開く。
※ ボタンは通常disabled。DevToolsからは `disabled=false` にしてから呼ぶか、直接 `_StartSub2()` を実行

### メニュー (`/service/tryers/` = `index.wpp`)
| ボタン | 遷移先 | onclick |
|---|---|---|
| 登録 | `regist.wpp` | `window.location.href='regist.wpp'` |
| 変更・削除 | `listup.wpp` | `CSAction(...)` |
| 問い合わせ比率 | 統計画面 | `CSAction(...)` |
| 内容印刷 | `plist.wpp` | `CSAction(...)` |
| ラベル印刷 | | `CSAction(...)` |
| 終了 | 前画面に戻る | `CSAction(...)` |

### 登録 (`/service/tryers/regist.wpp`)

GET で空フォーム表示。POST `cmd=post` で登録実行。フレームなし単一ページ。

#### フォームフィールド
| name | 内容 | 型 | 備考 |
|---|---|---|---|
| `cmd` | コマンド | hidden | 固定: `post`（登録）/ `update`（編集） |
| `kyoshitsucd` | 教室コード | text(readonly) | 固定: `5558` |
| `kyoshitsusm` | 教室名 | text(readonly) | 固定: `鳩ヶ谷校` |
| `number` | No. | text(readonly) | 自動採番 |
| `toiawasedt` | 問合せ日 | hidden | 実際の送信値（YYYYMMDD形式） |
| `imtoiawasedt` | 問合せ日（表示） | text | `YYYY/MM/DD`。デフォルト: 当日 |
| `seitosm` | 生徒氏名 | text | |
| `hogoshasm` | 保護者氏名 | text | |
| `postalcd` | 郵便番号 | hidden | 実際の送信値（ハイフンなし） |
| `impostalcd` | 郵便番号（表示） | tel | |
| `ad1` | 住所1（都道府県市区町村） | text | 例: `埼玉県川口市` |
| `ad2` | 住所2（番地） | text | 例: `赤井4-25-7` |
| `ad3` | 住所3（建物名等） | text | 例: `ウィルローズ鳩ヶ谷210` |
| `telno` | 電話番号 | tel | |
| `schoolsm` | 学校名 | text | |
| `schoolkb` | 学校区分 | select | 幼児(1)/小学校(2)/中学校(3)/高校(4)/大学(5) |
| `grade` | 学年 | select | 動的（schoolkbに連動。小学校→1〜6、中学校→1〜3、高校→1〜3） |
| `elem1` | 種別 | listbox | スクールIE(1)/WinBe(2)/キッズDuo(3)/チャイルド・アイズ(4)/忍者ナイン(5) |
| `elem2` | 問合せ者 | listbox | 父(1)/母(2)/本人(3)/その他(4) |
| `elem3` | 対象年齢 | listbox | 0歳〜6歳(1)/7歳〜12歳(2)/13歳〜15歳(3)/16歳〜18歳(4)/19歳〜22歳(5)/23歳〜30歳(6)/31歳〜40歳(7)/41歳〜60歳(8)/61歳〜(9)/不詳(10) |
| `elem4` | 媒体（認知動機） | listbox | 折込チラシ(1)/封書DM(2)/葉書DM(3)/紹介(4)/ポスティング(5)/TELアポ(6)/その他(7) |

※ HTMLにad1のinputが2つ存在するが、`form.ad1` / `form.ad2` / `form.ad3` で設定すること

#### チェックボックス — 内容は？（最低1つ必須）
| name | ラベル | value |
|---|---|---|
| `elem5` | 料金 | 1 |
| `elem6` | 諸経費 | 1 |
| `elem7` | システム | 1 |
| `elem8` | カリキュラム | 1 |
| `elem9` | コース | 1 |
| `elem10` | 講習会 | 1 |

#### チェックボックス — 結果は？（任意）
| name | ラベル | value |
|---|---|---|
| `elem11` | 資料請求 | 1 |
| `elem12` | 体験希望 | 1 |
| `elem13` | 入会希望 | 1 |
| `elem14` | 【入会】 | 1 |
| `elem15` | 採用関連 | 1 |

#### その他フィールド
| name | 内容 | 型 |
|---|---|---|
| `biko` | 備考 | textarea |
| `imnyukaidt` | 入会日（表示） | text（YYYY/MM/DD） |

#### アクションボタン
| ボタン | 説明 |
|---|---|
| 登録 | `regist()` を呼んでPOST |
| 終了 | 前画面に戻る |

#### 登録処理 (`regist()`)
`regist()`はasync functionで以下の前処理を行う:
1. `formmain.toiawasedt.value` ← `imtoiawasedt` の値をYYYYMMDD形式に変換
2. `formmain.postalcd.value` ← `impostalcd` の値からハイフン除去
3. `formmain.nyukaidt.value` ← `imnyukaidt` の値をコピー
4. バリデーション（生徒氏名必須、内容チェックボックス最低1つ必須）
5. `formmain.submit()` でPOST

**重要**: PythonからのHTTP直接POSTでは`regist()`のJS前処理が走らないため、`toiawasedt`や`postalcd`のhiddenフィールドに正しい値が入らず、登録が効かない。Chrome経由で`regist()`を呼ぶか、Pythonでhiddenフィールドの値変換を正確に再現する必要がある。

成功パターン（Chrome JSから）:
```javascript
document.formmain.seitosm.value = '須山 一華';
// ... 各フィールド設定 ...
regist();  // → フォームリセット＝登録成功
```

#### バリデーション
- **内容は？（elem5〜10）のうち最低1つ必須**。未選択だと「問合せの内容を入力してください。」のalert
- 結果は？（elem11〜14）は必須ではない
- 登録成功後はフォームがリセットされて同じ画面に戻る

#### 備考
- `toiawasedt` と `imtoiawasedt` の2つがある。表示用（im〜）に入力→hidden（toiawasedt）にコピーされる仕組み
- `postalcd` と `impostalcd` も同様
- WebSupportの「認知動機」「問合せ動機」はSKSの選択肢にない場合がある → 備考欄に記載する運用

### 変更・削除 (`/service/tryers/listup.wpp`)

GET で検索フォーム表示。フレームなし単一ページ。3つのフォーム（fm1/fm2/fm3）を含む。

#### 検索フィルタ (fm1)
| フィールド | 型 | デフォルト | 備考 |
|---|---|---|---|
| 問合せ日FROM | text | 1ヶ月前 | YYYY/MM/DD |
| 問合せ日TO | text | 当日 | YYYY/MM/DD |
| 生徒氏名 | text | | 部分一致 |
| 住所1 | text | | 部分一致 |
| 種別 | combobox | (全て) | スクールIE/WinBe/キッズDuo/チャイルド・アイズ/忍者ナイン |
| 問合せ者 | combobox | (全て) | 父/母/本人/その他 |
| 対象年齢 | combobox | (全て) | 0歳〜6歳 〜 61歳〜/不詳 |
| 媒体 | combobox | (全て) | 折込チラシ/封書DM/葉書DM/紹介/ポスティング/TELアポ/その他 |
| 入会状態 | radio | 全て | 全て / 入会済のみ / 未入会のみ |

#### 検索結果テーブル列
教室 / No. / 種別 / 問合せ日 / 生徒氏名 / 問合せ者 / 郵便番号 / 住所 / 電話 / 対象年齢 / 媒体

#### アクションボタン
| ボタン | onclick | 説明 |
|---|---|---|
| 検索 | `dosearch()` | fm1でPOST検索 |
| 編集 | `edit()` | 選択行のデータをfm3に読み込み |
| 削除 | `remove()` | fm2に `cmd=remove`, `code={教室:No}` をセットしてsubmit |
| 終了 | | 前画面に戻る |

#### フォーム構造
- `fm1` — 検索フォーム。`cmd=search` でPOST
- `fm2` — 行操作用（`cmd` + `code`）。行選択後に編集/削除を発火
- `fm3` — データ編集フォーム。`edit()` でデータが読み込まれる

#### 編集フォーム (fm3)
行を選択→`edit()`を呼ぶと、検索結果の下に編集フォームが展開される。
フィールドは**登録フォーム(regist.wpp)とほぼ同一**:

| フィールド | 違い |
|---|---|
| 教室コード/教室名 | readonly（同じ） |
| No. | readonly、値あり（同じ） |
| 問合せ日〜入会日 | 全て同じフィールド構成 |
| 種別/問合せ者/対象年齢/媒体 | listbox（同じ選択肢） |
| 内容チェック(elem5〜10) | 同じ6項目 |
| 結果チェック(elem11〜15) | 同じ5項目 |
| 備考 | textarea（同じ） |

**regist.wppとの相違点:**
- コマンド: `cmd=update`（登録は `cmd=post`）
- ボタン: 登録 + 終了のみ（regist.wppは登録 + 終了。クリアボタンなし）
- `dopost()` でPOST → 確認ダイアログ → `#_overRideModalOK` click で確定

#### 行選択
行の `onclick="sel(this,'5558:{No}')"` で選択。codeは `5558:{No}` 形式。

#### Python/MCPからの削除
```
POST /service/tryers/listup.wpp
data: cmd=remove&code={教室コード}:{No}
```
- 削除は不可逆。確認ダイアログは出ない（Pythonからの場合）
- GUI上の`remove()`も確認ダイアログなしで即削除する

#### ⚠ cmd=update で空データを送ってはいけない
`cmd=update`で全フィールド空のPOSTを送ると、問い合わせ管理(listup.wpp)の検索からは消えるが、外部生検索(IEB012.wpp)にはレコード枠（空行）が残るゴミデータになる。削除は必ず`cmd=remove`を使うこと。

#### Python/MCPからの更新
```
POST /service/tryers/listup.wpp
data: cmd=update&number={No}&kyoshitsucd={教室コード}&seitosm=...（全フィールド必須）
```
- 事前にsearch→editの遷移は不要。直接`cmd=update`をPOSTすればサーバーは受け付ける
- **全フィールドを送る必要がある**。省略したフィールドは空で上書きされる

#### 学年フィールドの正規化
`_GRADE_TO_SCHOOLKB`辞書のキーは全角数字（`中学３年`）。入力が半角数字（`中学3年`）の場合マッチしないため、`sks_inquiry_register`内で半角→全角に正規化してからマッチする。

#### 編集の自動化手順
1. `listup.wpp` を開く
2. `fm1.seitosm` に名前を入れて検索ボタンclick
3. `tr[onclick="sel(this,'5558:XXXX')"]` をclick → 行選択
4. `edit()` を呼ぶ → fm3にデータが読み込まれる（`cmd=update`）
5. fm3のフィールドを変更
6. 登録ボタンclick → 確認ダイアログ → `#_overRideModalOK` click

#### ダイアログ
- SKSのダイアログはネイティブalertではなく独自モーダル
- 「選択されていません」: `#_overRideModalOK` click
- 「更新して宜しいですか？」: `#_overRideModalOK` click（キャンセルもあり）
- 統一的に `document.getElementById('_overRideModalOK').click()` で確定

## 成績管理メニュー (`/service/nssk.wpp`)

### 概要
nssk.wppにアクセスすると**別ドメイン `{SKS_SSK1_URL}`** にリダイレクトされる。
PCSがssk2なのに対し、成績管理はssk1。Java Servlet(.do)ベース。

### 遷移
`nssk.wpp` → `SpecifiedKyoshitsuUpdateWait.do` → `Top.do`（メニュー画面）

### メニュー
| 画面名 | URL | 備考 |
|---|---|---|
| 定期テスト | `TeikiTest.do` | |
| 通知表 | `Tuuchi.do` | |
| コンピュータテスト | `ComputerTest.do` | |
| 校内実力テスト | `KounaiTest.do` | |
| 成績カルテ | `Carte.do` | |
| レーダーチャート | `SpiderWebChart.do` | |
| 学校別各種設定 | `MasterMenu.do` | マスタ管理 |
| 操作マニュアル | `help/manual.pdf` | PDFリンク |
| WebSKSメニュー | メインメニューに戻る | `https://{SKS_BASE_URL}/service/` |

※ ベースURL: `https://{SKS_SSK1_URL}/nssk/`
※ Copyright 2009-2024 Tactgroup INC.

### 各画面の詳細

#### 定期テスト (`TeikiTest.do`)
- フィルタ: 年度(2022〜2026) / 試験(1学期中間・期末, 2学期中間・期末, 3学期期末) / 在学校(11校) / 退塾者も表示 / テスト無も表示
- テーブル列: 学年, 生徒CD, 生徒氏名, 不要, 履歴, 受講科目, コース/週回数, 国語/数学/英語/社会/理科/計, 音楽/美術/体育/技家/合計, 順位
- ボタン: 表示 / 比較表示(disabled) / Excel書き出し(disabled) / 入力値更新(disabled) / メニューに戻る

#### 通知表 (`Tuuchi.do`)
- 定期テストと同構造。試験→学期(1学期/2学期/3学期/学年評定)

#### コンピュータテスト (`ComputerTest.do`)
- フィルタ: 年度 / 試験(第1回〜第7回) / 退塾者・テスト無チェック。**在学校フィルタなし**（統一テストのため）
- テーブル列: 学年, 生徒CD, 生徒氏名, 得点(国/数/英/社/理/3科計/5科計), **偏差値**(国/数/英/社/理/3科/5科)

#### 校内実力テスト (`KounaiTest.do`)
- フィルタ: 年度 / 試験(第1回〜第6回) / 在学校(11校) / チェックボックス
- テーブル列: コンピュータテストと同様 + **順位**列

#### 成績カルテ (`Carte.do`)
- フィルタ: 年度 / 在学校(**21校** — 定期テストより多い。過去の学校含む) / 退塾者も表示
- **出力専用**（データ入力グリッドなし）
- ボタン: 表示 / メニューに戻る

#### レーダーチャート (`SpiderWebChart.do`)
- 成績カルテと同構造（21校、出力専用）

#### 学校別各種設定 (`MasterMenu.do`)
サブメニュー:
| 画面名 | URL |
|---|---|
| 定期テスト平均点設定 | `MasterHeikinTeiki.do` |
| 校内実力テスト平均点設定 | `MasterHeikinKounai.do` |
| 各種テスト名称設定 | `MasterSeisekiName.do` |

### 共通仕様
- データ入力画面(定期テスト/通知表/コンピュータテスト/校内実力テスト)には Excel書き出し / 比較表示 / 入力値更新ボタンがある（データ読み込み後に有効化）
- 学校リスト: 定期テスト/通知表/校内実力テスト=11校、成績カルテ/レーダーチャート=21校（過去の在籍校も含む）
- コンピュータテストのみ在学校フィルタなし（統一テスト）

## 担当者登録 (`/service/IEM041.wpp`)

### ページ構造
フレームなし単一ページ。教室のスタッフ（ログインユーザー）を管理する。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 教室コード | readonly | `5558` |
| 教室名 | readonly | `鳩ヶ谷校` |
| ログインID | テキスト | 8桁（教室コード5558 + 4桁）。検索/表示ボタンあり |
| 氏名 | テキスト | |
| フリガナ | テキスト | |
| 入社日 | テキスト | デフォルト: 当日 (YYYY/MM/DD) |
| パスワード | テキスト | チェックボックス「パスワードを変更する時はチェックを入れて下さい。」付き |
| アクセスレベル | プルダウン | 1 / 3 |
| 区分 | プルダウン | 0:利用中 / 1:削除 |

※ パスワードルール: 8文字、英数大文字小文字3種使用

### アクションボタン
| ボタン | 説明 |
|---|---|
| 表示 | ログインIDで検索・表示 |
| 検索 | ログインID検索 |
| 追加/修正 | データ保存 |
| クリア | フォームリセット |
| 終了 | 画面を閉じる |

## 学校登録 (`/service/IEM050.wpp`)

### ページ構造
フレームなし単一ページ。教室で使用する学校マスタの管理画面。
上部に登録済み学校一覧（テーブル）、下部に登録/編集フォーム。

### 学校コード体系
10桁: `0000001001` 等
- 先頭6桁: `000000`（固定?）
- 7桁目: 学校区分（1=小学校, 2=中学校, 3=高校）
- 8〜10桁目: 連番
- 公立: 001〜（小:001〜, 中:001〜, 高:001〜）
- 公立(他市区): 101〜, 901〜
- 私立: 201〜
- 国立: 301〜

### 登録済み学校（鳩ヶ谷校）
- 小学校: 24校（公立20 + 私立4）
- 中学校: 30校（公立16 + 私立13 + 国立1）
- 高校: 37校（公立17 + 私立20）

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 学校コード | テキスト | 10桁 |
| 学校区分 | プルダウン | 1:小学校 / 2:中学校 / 3:高校 / 4:大学 |
| 学校名 | テキスト | |
| フリガナ | テキスト | |
| 学校分類 | プルダウン | 0:公立 / 1:私立 / 2:国立 / 3:公立中高一貫 / 4:私立中高一貫 |
| 削除区分 | プルダウン | 0:通常 / 1:削除 |

### アクションボタン
追加/修正 / クリア / 終了

## 生徒集計一覧 (`/service/IEB410.wpp`)

### ページ構造
単一ページ + 2つのiframe（データ表示用）。iframeは表示ボタンクリック後にデータ読み込み。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 日付 | テキスト | YYYYMMDD形式（デフォルト: 当日） |

### アクションボタン
表示 / Excel出力 / クリア / 終了

### iframe内テーブル構造
学年別（小1〜高3）の行 × 以下の列:
- 当月月初生徒数
- 当月入塾数
- 当月在籍者数
- 当月退塾数
- 翌月月初生徒数
- 合計列

全数値はクリック可能なリンク（ドリルダウンで生徒一覧表示）。
2つ目のiframeはグラフ表示用（推測）。

### 備考
日付1パラメータのみのシンプルな集計画面。Excel出力機能あり。

## PCS実施状況一覧 (`/service/IEB420.wpp`)

### ページ構造
フレームなし単一ページ。PCSテストの実施状況を色分けで表示する管理画面。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 教室コード | readonly | `5558` |
| 教室名 | readonly | `鳩ヶ谷校` |
| 表示件数 | readonly | |
| 教科 | ラジオ | **数学**(default) / 英語 |
| 実施N回以上 | テキスト | デフォルト: `1` |

### 色分け閾値（日数ベース）
入塾日〜初回実施、前回実施日〜現在の経過日数で色分け表示:
| 閾値 | 下限(日) | 上限(日) |
|---|---|---|
| 閾値1（入塾~初回） | 21 | 240 |
| 閾値2（前回実施~） | 14 | 20 |
| 閾値3 | 150 | 239 |
| 閾値4 | 4 | 13 |
| 閾値5 | 90 | 149 |

### データテーブル列
学年 / 生徒CD / 生徒氏名 / テスト実施回数 / 入塾日 / 現在の状態 / テスト実施日（最大29回分）

### 表示データ例（数学）
36名表示。実施率: 小100% / 中100% / 高89%。

### Ajaxデータ取得（GUIなし）
ページのデータは`xmlloads`関数でAjax取得される。Pythonからは以下で直接取得可能:
```
GET /service/IEB420.wpp?cmd=ax&param=0|1  → 数学
GET /service/IEB420.wpp?cmd=ax&param=0|2  → 英語
```
- 事前に `GET /service/IEB420.wpp` でセッション確立が必要
- レスポンスは `HTML|JS` のパイプ区切り。HTMLパート（最初の`|`まで）をBeautifulSoupでパース
- `dopost(v)` は内部で `xmlloads(0, 'layer1', v+'|'+kk)` を呼ぶ（v=0:通常検索, kk=1:数学/2:英語）

### アクションボタン
表示 / 色の抽出を解除 / Excel出力 / クリア / 終了

## 通知一覧 (`/service/IEW010.wpp`)

### ページ構造
フレームなし単一ページ。システム通知の一覧表示。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 期間FROM | テキスト | |
| 期間TO | テキスト | |
| 表示件数 | readonly | |

### 結果テーブル列
登録日時 / 学年 / 生徒コード / 生徒氏名 / 備考

### アクションボタン
表示 / 終了

### 備考
メインメニューの「通知一覧 未確認がN件あります」からリンクされる画面。Excel出力なし。

## やる気度回答入力 (`/service/IEC010.wpp`)

### ページ構造
フレームなし単一ページ。やる気度診断アンケートの回答入力画面。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 生徒区分 | ラジオ | **内部生**(default) / 外部生 |
| 生徒番号 | テキスト | + 検索ボタン |
| 生徒氏名表示 | readonly | 検索後に表示 |
| 対象世代 | readonly | `0：中学生高校生用` |
| 問題バージョンCD | readonly | `0：A` |
| 実施年月日 | テキスト | |

### アクションボタン
| ボタン | 状態 | 説明 |
|---|---|---|
| 検索 | enabled | 生徒番号で検索 |
| 表示 | enabled | 回答フォーム表示 |
| やるき度診断 | **disabled** | データ読み込み後に有効化 |
| 確定 | **disabled** | 回答確定 |
| クリア | enabled | |
| 終了 | enabled | |

### 備考
2段階ワークフロー: 生徒検索 → 実施日入力+表示 → 回答入力 → 確定。
対象は中学生・高校生。バージョンAのアンケート。

## 中学受験版PCS (`/service/jpcs.wpp`)

### ページ構造
フレームなし単一ページ。通常PCS(pcs.wpp)の中学受験版。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 教室 | テキスト | `5558 : 鳩ヶ谷校` |
| 生徒番号 | テキスト | |
| 表示フィールド1 | readonly | 検索後に生徒情報表示 |
| 表示フィールド2 | readonly | |

### サンプルPDFリンク
| 教科 | URL |
|---|---|
| 国語問題サンプル | `pcsimg/jj-sample.pdf` |
| 算数 ステージI | `pcsimg/jm1-sample.pdf` |
| 算数 ステージII | `pcsimg/jm2-sample.pdf` |
| 算数 ステージIII | `pcsimg/jm3-sample.pdf` |
| 理科 ステージI | `pcsimg/jsc1-sample.pdf` |
| 理科 ステージII | `pcsimg/jsc2-sample.pdf` |
| 理科 ステージIII | `pcsimg/jsc3-sample.pdf` |
| 社会問題サンプル | `pcsimg/jso-sample.pdf` |

### 通常PCS(pcs.wpp)との違い
- 対象: 中学受験生（小学生）
- 教科: 国語/算数(3ステージ)/理科(3ステージ)/社会（通常PCSは国語/算数・数学/英語/理科/社会）
- 算数・理科がステージ制（I/II/III）

### アクションボタン
終了のみ

## 振込先登録 (`/service/IEM030.wpp`)

### ページ構造
フレームなし単一ページ。教室レベルの振込先設定（生徒単位ではない）。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 教室コード | テキスト | `5558` |
| 教室名 | readonly | `鳩ヶ谷校` |
| 振込先種別 | ラジオ | 銀行 / 郵便局 |

### アクションボタン
確定 / 終了

## カード・ワイドネット者用料金入力 (`/service/IEB061.wpp`)

### ページ構造
フレームなし単一ページ。WN（ワイドネット）自動引落対象生徒の料金入力。

### 前提条件
先にワイドネット請求データ作成処理を実行する必要がある。未実行だと「編集可能な請求データがありません。」のモーダルが出る。

### ヘッダー情報（readonly）
授業料計 / 諸経費計 / 講習費計 / 処理年月 / 請求年月 / 引落予定日 / 対象生徒数

### 生徒一覧テーブル列
退会日 / 請求 / 学年 / 生徒名 / 前月・当月・翌月 / 講習会費 / 消費税 / 合計

### 料金入力セクション（生徒別）
- **授業料**（10行）: 削除チェック / 授業料名 / 種別(単コマ/週回数/増コマ) / コマ数 / 単価(readonly) / 金額(readonly) / 対象年月(前月/当月/翌月)
- **諸経費**（15行）: 削除チェック / 分類(授業料値引/入会金/維持管理費/基礎教材費/別途教材費/テスト費/その他/講習会費テキスト代等) / 料金名 / 数量 / 単価(readonly) / 金額(readonly)
- **講習会費**（5行）: 削除チェック / 講習種別(春期/夏期/冬期) / コース名 / コマ数 / 単価・金額(readonly)
- **YSPC請求明細コメント**: textarea（最大60文字）

### アクションボタン
前生徒 / 次生徒 / 確定 / 削除 / 元に戻す / 終了

## 振込者用料金入力 (`/service/IEB070.wpp`)

### ページ構造
フレームなし単一ページ。銀行振込・コンビニ払い生徒の料金入力。

### フィルタ
| フィールド | 型 | 備考 |
|---|---|---|
| 生徒種別 | ラジオ | **内部生振込者**(default) / 内部生WN請求者 / 外部生 |
| 処理年月 | readonly | |
| WN締済年月 | readonly | |

### 生徒一覧テーブル列
退会日 / 学年 / 生徒名 / 振込票発行日 / 教室管理番号 / WN当月・翌月 / 振込当月・翌月 / 講習会費 / 消費税 / 合計

### 生徒別詳細
生徒名 / 学年 / 受講コース(readonly) / 振込票発行日 / 支払期日 / 教室管理番号 / 収納手数料教室負担チェック
授業料金額計 / 諸経費金額計 / 講習会費金額計(readonly) / YSPC請求明細コメント(textarea)

### アクションボタン
元に戻す / 前生徒 / 次生徒 / 確定 / 削除 / クリア / 終了

### 備考
WN金額は参照表示のみ。WN回収金を振込画面で入力しないこと。

## 入金入力 (`/service/IEB290.wpp`)

### ページ構造
単一ページ + iframe（入金内訳表示用）。

### フィルタ
| フィールド | 型 | 備考 |
|---|---|---|
| 処理年月 | テキスト | デフォルト: 当月 |
| 生徒種別 | ラジオ | **内部生**(default) / 外部生 |
| 入金状況 | ラジオ | **未入金**(default) / 入金済 |

### 生徒一覧テーブル列
学年 / 生徒名 / 請求年月 / 振込票発行日 / 教室管理番号 / 請求額 / 消費税 / 請求合計 / 入金額 / 残高 / 前受残

### 入金入力フィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 入金日 | テキスト | |
| 入金種別 | プルダウン | 振込 / 現金 / 振込手数料 |
| 入金額 | テキスト | |

### アクションボタン
行追加 / 行削除 / 行クリア / 元に戻す / 前生徒 / 次生徒 / 確定 / クリア / 終了

### 表示データ例（2026/04、内部生・未入金）
10件、合計398,685円の未入金。古い滞納（2020年〜、成人=退塾済み）から当月分まで混在。
同一生徒が複数月の未入金で複数行表示されることがある。

### 備考
複数行の入金明細を追加可能（日付+種別+金額）。前受残の追跡機能あり。

## 月謝台帳 (`/service/IEB190.wpp`)

### ページ構造
フレームなし単一ページ。月次の請求・入金サマリーを表示する帳簿。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 生徒番号 | テキスト | + 検索ボタン |
| 生徒名 | readonly | |
| 対象年月 | テキスト | YYYY/MM |

### 帳簿行ラベル（縦方向）
入会金 / 授業料 / 諸経費(維持管理費/基礎教材費/別途教材費/テスト費/その他) / 講習会(講習会費/テキスト代) / 消費税 / 請求計 / ワイドネット / カード / 振込 / 入金計 / 残高計 / 返金

### 表示データ例（郷田愛日 250022、2025/08〜）
- 列: 12ヶ月分（対象年月〜+11ヶ月）+ 合計列
- 授業料: 16,170〜29,480/月（コース変更で変動）
- 講習会費: 季節ごとにスパイク（12月46,480、1月83,700等）
- 請求計年間: 291,280円
- 入金計: 261,580円（残高29,700円）

### アクションボタン
検索 / 表示 / 前月 / 次月 / 前生徒CD / 次生徒CD / Excel出力 / クリア / 終了

## BCS視聴状況一覧 (`/service/IEB440.wpp`)

### ページ構造
フレームなし単一ページ。BCS（ビデオ学習システム）の視聴状況管理。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 表示件数 | readonly | |
| 実施回数 | テキスト | N回以上。デフォルト: `1` |
| 生徒フィルタ | ラジオ | **全生徒**(default) / 入塾1ヶ月経過 |

### 色分け閾値
入塾日〜初回、前回実施日〜現在の経過日数で色分け。IEB420(PCS実施状況)と同じ仕組み。

### テーブル列
生徒 / カードID / 学年区分(数学/英語/小/中/高)

### 表示データ例
41名表示。全員視聴0回（BCS未使用）。各行の形式: `0(0,0)` = 合計(数学,英語)。

### アクションボタン
表示 / 視聴履歴を追加 / 勉強法選択を更新 / 選択したビデオをDVD注文 / 色の抽出を解除 / Excel出力 / 更新 / 終了

### 備考
鳩ヶ谷校ではBCSは実質未使用。機能としてはDVD注文・視聴履歴追加が可能。

## やる気度診断照会 (`/service/IEC510.wpp`)

### ページ構造
フレームなし単一ページ。ETS（やる気度診断）の結果閲覧・印刷画面。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 生徒種別 | ラジオ | **内部生**(default) / 外部生 |
| 生徒番号 | テキスト | + 検索ボタン |
| 生徒名 | readonly | |
| 実施回数 | プルダウン | 動的（検索後に選択肢生成） |
| 実施年月日 | テキスト | |

### 結果表示セクション
- **あなたのすばらしいところ**: 5項目（チャート表示リンク `javascript:large(N)`）
- **あなたのやる気度**: チャートリンク
- **あなたのMUST**: 2項目(readonly)
- **あなたの良くない思い込み**: 2項目(readonly)
- **あなたに必要なもの**: 5項目(編集可能)
- **職業/分野/達成度テーブル**: 5行(編集可能)

### 印刷設定
| フィールド | 型 | 備考 |
|---|---|---|
| 出力対象 | ラジオ | **両方**(default) / グラフ帳票1枚目 / 文章帳票2枚目 |
| 背景も印刷する | チェックボックス | |
| 裏面を印刷 | リンク | `etsb.pdf` |

### アクションボタン
検索 / 表示 / 印刷設定 / 印刷(disabled) / 文章表示(disabled) / 終了

## ETS実施状況一覧 (`/service/IEB430.wpp`)

### ページ構造
フレームなし単一ページ。ETS（やる気度診断テスト）の実施状況を色分け表示。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 教室コード | readonly | `5558` |
| 教室名 | readonly | `鳩ヶ谷校` |
| 表示件数 | readonly | |
| 実施回数 | テキスト | N回以上。デフォルト: `1` |

### 色分け閾値（5段階、日数ベース）
IEB420(PCS)・IEB440(BCS)と同じ色分け方式。入塾日〜初回、前回実施日〜現在の経過日数で赤/黄/緑/青/白に色分け。

### データテーブル列
ETSID発行状態 / 学年 / 生徒CD / 生徒氏名 / テスト実施回数 / 入塾日 / 現在の状態 / テスト実施日（最大19回分） / 最新ETSID発行日 / ステータスメッセージ
- 各行にチェックボックスあり（ETSID一括操作用）
- ステータス例: 「ID発行から8日経過」「04/11 実施」

### 表示データ例
41名表示。実施率: 小33% / 中70% / 高100%。

### アクションボタン
表示 / 色の抽出を解除 / Excel出力 / クリア / ETSID出力 / 終了

## 代金内訳明細書印刷 (`/service/IEB550.wpp`)

### ページ構造
フレームなし単一ページ。請求内訳明細書のPDF印刷。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 処理年月 | readonly | |
| 出力区分 | ラジオ | **カード/WN請求のみ**(default) / 振込のみ / 生徒別合計 |
| 対象年月 | プルダウン | 過去2年分（24ヶ月選択可） |

### アクションボタン
印刷 / クリア / 終了

## 各種ご案内書印刷 (`/service/IEE570.wpp`)

### ページ構造
フレームなし単一ページ。請求・引落通知等のPDF印刷。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 出力区分 | ラジオ | **ワイドネット**(default) / カード / 振込のみ / WN引落不能銀行振込 |
| 処理年月 | テキスト | YYYY/MM |
| 発行区分 | ラジオ | **初回**(default) / 再発行 |
| YSPC | ラジオ | **全て**(default) / 非会員とメール無効者 |
| 生徒番号 | テキスト | フィルタ用 + 検索ボタン |

### アクションボタン
検索 / 印刷 / クリア / 終了

### 備考
WN請求締処理の後に実行する必要がある。

## カード・ワイドネット引落結果確認 (`/service/IEB210.wpp`)

### ページ構造
単一ページ + iframe（データテーブル表示用）。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 請求年月 | readonly | ▲▼ボタンで月移動 |
| 表示区分 | ラジオ | **引落不能分**(default) / 全件 |
| 表示件数 | readonly | |
| 請求合計 | readonly | |
| 入金合計 | readonly | |

### iframe内テーブル列
学年 / 区分(WN/カード) / 生徒 / 請求額 / 調整額 / 消費税 / 請求合計 / 入金額 / 引落結果

### 引落結果の値
- `資金不足` — WN引落失敗
- `カードエラー` — カード引落失敗

### 表示データ例（2026/04、引落不能分）
4件、請求合計267,120円。WN資金不足3件+カードエラー1件。

### アクションボタン
▲(前月) / ▼(次月) / 表示 / 印刷 / クリア / 終了

## カード・ワイドネット請求締処理 (`/service/IEB320.wpp`)

### ページ構造
フレームなし単一ページ。月次のWN/カード請求を確定する処理。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 処理年月 | readonly | |
| 請求年月 | readonly | |
| 請求金額 IE | テキスト | 代金内訳明細書の合計を手入力（検証用） |

### アクションボタン
実行(disabled — 締め完了済みの場合) / 終了

### 重要
- **締め処理は不可逆**。一度実行すると解除できない（FC Q&A参照）
- 実行ボタンは請求金額の入力が一致した場合のみ有効化

## 調整入力 (`/service/IEB240.wpp`)

### ページ構造
フレームなし単一ページ。返金・追加徴収の入力。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 生徒種別 | ラジオ | **内部生**(default) / 外部生 |
| 生徒番号 | テキスト | |
| 調整形態 | ラジオ | **返金**(default) / 追加徴収 |
| 処理年月 | readonly | |
| 次回引落年月 | readonly | |
| 調整区分 | ラジオ | **次回カード/WN反映**(default) / 振込 |
| 返金理由 | プルダウン | 退塾 / 休塾 / コース変更 / 週回数変更 / 請求間違い |

### 料金科目（13項目、全てテキスト入力）
IE授業料 / IE入会金 / IE維持管理費 / IE基礎教材費 / IE別途教材費 / IEテスト費 / IEその他 / IE春期講習会費 / IE夏期講習会費 / IE冬期講習会費 / IE講習会費(テキスト代) / IE講習会費OP(テスト費) / IE講習会費OP(ファイル代) / 合計(readonly)

### アクションボタン
確定(disabled) / 削除(disabled) / クリア / 終了

### 備考
カード返金は過去にカード請求実績が必要。

## 未収金内訳明細 (`/service/IEB220.wpp`)

### ページ構造
フレームなし単一ページ。未収金（滞納）の一覧レポート。

### フォームフィールド
| フィールド | 型 | 備考 |
|---|---|---|
| 並び順 | ラジオ | **処理年月,教室,生徒順**(default) / 教室,生徒,処理年月順 |
| 件数 | readonly | |

### テーブル列
教室コード / 部門 / 氏名 / 処理年月 / 滞納期間 / 請求区分 / 入会金 / 授業料当月 / 授業料翌月 / 維持管理費 / 基礎教材費 / 別途教材費 / テスト費 / 講習会費 / ファイル代 / 消費税額 / 未収合計

### 部門別集計
IE / WinBe / KD / CE / N9（マルチブランド対応）

### 表示データ例
18件、合計436,235円。13ヶ月超の古い滞納（退塾済み成人）から当月振込分まで混在。

### アクションボタン
表示 / Excel出力 / クリア / 終了

## FC Q&A (`/fc/fcqa/`)

静的HTMLページ。フォームなし。SKS運用のQ&A集。

### 主要トピック
- WN締め処理前の口座変更: 一度削除してロック解除
- **月次処理の締め解除は不可能**
- 退塾時のWN返金: 退塾処理後でも可能（月末前に退塾処理必須）
- コンビニ請求: 複数請求を1枚にまとめ不可
- PCS/ETS印刷設定: A3横(ETS), A3縦(PCS問題/結果H), A3横(PCS結果W), A4縦(PCS解答)

## FCマニュアル (`/fc/manual/`)

静的HTMLページ。各種マニュアルPDFのダウンロードリンク集。

### ダウンロード可能なマニュアル
| ファイル名 | サイズ | 内容 |
|---|---|---|
| `websks-sousamanual.pdf` | 33.0MB | Web-SKS操作マニュアル |
| `20230205CVSmanual.pdf` | 1.16MB | コンビニ決済ご利用マニュアル |
| `PCSmanual_ver27.pdf` | 3.48MB | PCS・夢SEEDご利用マニュアル |
| `print.xls` | 635KB | WebETS・PCS印刷時の設定 |

## 技術的特徴
- `.wpp` 拡張子 — 独自のWebアプリケーションフレームワーク
- フレーム構造: メインページ内にiframe/frameを使用
- 生徒名簿はframes[0]内にレンダリング
- AES暗号化ライブラリ (CryptoJS) 使用 — ログインパスワードの暗号化
- jQuery UI使用
- EUC-JPまたはShift_JISの可能性あり（要確認）

## JKS
- `/jks.wpp` — 別システム（受講管理？）
- 未調査
