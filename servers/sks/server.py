"""
MCP Server for SKS (WEB-SKS 生徒管理システム)
スクールIE SKSの操作をMCPツールとして提供する

接続: 環境変数 SKS_BASE_URL で指定
環境変数 SKS_BASE_URL でベースURLを切り替え

【重要】ログイン試行に繰り返し失敗するとアカウントロックされる可能性。
ログイン失敗時はリトライせず即座にエラーを返すこと。
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# --- FastMCP ---
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("mcp package not found. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("SKS")

# --- Configuration ---
BASE_URL = os.environ.get("SKS_BASE_URL", "http://sks.example.internal")
ACCOUNT = os.environ.get("SKS_ACCOUNT", "")
PASSWORD = os.environ.get("SKS_PASSWORD", "")
CLASSROOM = os.environ.get("SKS_CLASSROOM", "{教室コード}")

# --- Session management ---
_session: requests.Session | None = None
_login_failed: bool = False


def _cryptojs_aes_encrypt(plaintext: str, passphrase: str) -> tuple[str, str]:
    """CryptoJS.AES.encrypt(plaintext, passphrase) 互換のAES暗号化"""
    salt = os.urandom(8)
    # OpenSSL EVP_BytesToKey: MD5ベースのキー導出
    data = passphrase.encode("utf-8") + salt
    key_iv = b""
    prev = b""
    while len(key_iv) < 48:  # 32 bytes key + 16 bytes IV
        prev = hashlib.md5(prev + data).digest()
        key_iv += prev
    key = key_iv[:32]
    iv = key_iv[32:48]

    cipher = AES.new(key, AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))

    # OpenSSL format: 'Salted__' + salt + ciphertext → Base64
    encrypted = base64.b64encode(b"Salted__" + salt + ct).decode("utf-8")
    iv_hex = iv.hex()
    return encrypted, iv_hex


def _sks_api(session: requests.Session, param: dict) -> dict:
    """SKS JSON-RPC API呼び出し"""
    r = session.post(
        f"{BASE_URL}/cgi-bin/s2login.pl",
        data={"cmd": "jsx", "param": json.dumps(param)},
    )
    return r.json()


def _get_session() -> requests.Session:
    """ログイン済みセッションを取得"""
    global _session, _login_failed
    if _session is not None:
        return _session
    if _login_failed:
        raise Exception(
            "Login previously failed. NOT retrying to avoid account lockout. "
            "Check SKS_ACCOUNT/SKS_PASSWORD and restart the MCP server."
        )

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    # Step 1: Get encryption key
    j1 = _sks_api(s, {"cmd": "com"})
    com = j1.get("com", "")
    if j1.get("result") != "OK" or not com:
        _login_failed = True
        raise Exception("Failed to get encryption key from SKS")

    # Step 2: Auth
    enc_pass, iv_hex = _cryptojs_aes_encrypt(PASSWORD, com)
    j2 = _sks_api(s, {
        "cmd": "auth",
        "id": ACCOUNT,
        "pw": enc_pass,
        "iv": iv_hex,
    })
    if j2.get("result") != "OK":
        _login_failed = True
        raise Exception(f"SKS auth failed: {j2.get('result')}")

    # Step 3: 単一教室アカウントはStep2で `openmain('/service/')` 指示が返るため
    # cmd=login の追加リクエストは不要。/service/ への直接アクセスでセッション確立する。
    # 複数教室アカウントの場合は別途 cmd=login で kcd 指定が必要かもしれないが現状未対応。
    r4 = s.get(f"{BASE_URL}/service/")
    if "生徒管理" not in r4.text and "メインメニュー" not in r4.text:
        _login_failed = True
        raise Exception("Failed to access SKS menu after login")

    _session = s
    return s


def _parse_student_table(html: str) -> list[dict]:
    """生徒名簿一覧のHTMLテーブルをパースする"""
    soup = BeautifulSoup(html, "html.parser")

    # フレーム内のテーブルを探す
    tables = soup.find_all("table")
    if not tables:
        return []

    # ヘッダー行を持つテーブルを探す
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        first_row = rows[0]
        headers = [td.get_text(strip=True) for td in first_row.find_all(["th", "td"])]
        if "生徒氏名" in headers or "生徒コード" in headers:
            students = []
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) >= len(headers) // 2:
                    record = {}
                    for i, h in enumerate(headers):
                        if i < len(cells) and h:
                            record[h] = cells[i]
                    if record:
                        students.append(record)
            return students

    return []


# =====================
# MCP Tools
# =====================


# cols定義: 内部生と外部生でテーブル列数が異なる
_COLS_NAIBU = ",".join(str(i) for i in list(range(58)) + [59, 60, 61])  # 58抜き
_COLS_GAIBU = ",".join(str(i) for i in range(29))  # 外部生は29列


@mcp.tool()
def sks_student_list(
    grade: str = "",
    name: str = "",
    kubun: str = "naibu",
    include_taijuku: bool = False,
) -> str:
    """生徒名簿一覧を取得する。

    Args:
        grade: 学年で絞り込み（例: "中3", "小6"）。空なら全学年
        name: 氏名で絞り込み（部分一致）。空なら全生徒
        kubun: 生徒区分（"naibu"=内部生, "gaibu"=外部生）
        include_taijuku: True=退塾者も含む（内部生のみ有効）
    """
    s = _get_session()

    # まずGETでフォーム状態を取得
    s.get(f"{BASE_URL}/service/IEB030.wpp")

    # cols: 内部生と外部生で異なる
    cols = _COLS_GAIBU if kubun == "gaibu" else _COLS_NAIBU

    data = {
        "mode": "if",
        "cols": cols,
        "selseitolist": "",
        "seitokm": "",
        "seitosm": "",
        "seitograde": "",
        "listcount": "",
        "seitokb": kubun,
    }
    # 退塾除く（チェックボックス: 送らない=退塾含む）
    if not include_taijuku:
        data["taijuku"] = "1"

    # POSTでiframe内のデータを取得
    r = s.post(f"{BASE_URL}/service/IEB030.wpp", data=data)
    html = r.content.decode("utf-8", errors="replace")
    students = _parse_student_table(html)

    if grade:
        students = [st for st in students if grade in st.get("学年", "")]
    if name:
        students = [
            st for st in students
            if name in st.get("生徒氏名", "") or name in st.get("氏名フリガナ", "")
        ]

    return json.dumps(
        {"count": len(students), "students": students},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def sks_student_export() -> str:
    """生徒名簿一覧のHTMLデータを取得する。Excel出力と同等のデータ。"""
    s = _get_session()
    r = s.get(f"{BASE_URL}/service/IEB030.wpp")
    html = r.content.decode("utf-8", errors="replace")
    students = _parse_student_table(html)

    return json.dumps(
        {"count": len(students), "students": students},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def sks_relogin() -> str:
    """SKSセッションをリセットして再ログインする。"""
    global _session, _login_failed
    _session = None
    _login_failed = False
    s = _get_session()
    return json.dumps({"result": "OK", "message": "Re-login successful"})


@mcp.tool()
def sks_menu() -> str:
    """SKSメインメニューの項目一覧を取得する。"""
    s = _get_session()
    r = s.get(f"{BASE_URL}/service/")
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if text and ".wpp" in href:
            items.append({"name": text, "url": href})

    return json.dumps(items, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_page(page: str) -> str:
    """SKSの任意のページを取得する。

    Args:
        page: ページパス（例: "IEB030.wpp", "IEB010.wpp"）
    """
    s = _get_session()
    url = f"{BASE_URL}/service/{page}" if not page.startswith("/") else f"{BASE_URL}{page}"
    r = s.get(url)
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    # テーブルデータがあればパース
    tables = soup.find_all("table")
    data = []
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) >= 2:
            headers = [td.get_text(strip=True) for td in rows[0].find_all(["th", "td"])]
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if cells:
                    record = {}
                    for i, h in enumerate(headers):
                        if i < len(cells) and h:
                            record[h] = cells[i]
                    if record:
                        data.append(record)

    # リンク一覧
    links = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if text:
            links.append({"text": text, "href": a["href"]})

    return json.dumps({
        "url": url,
        "title": soup.title.string if soup.title else "",
        "data_rows": len(data),
        "data": data[:100],
        "links": links[:50],
    }, ensure_ascii=False, indent=2)


# --- 郵便番号逆引き ---
_zip_data: list[tuple[str, str, str, str]] | None = None  # (zip, pref, city, town)


def _load_zip_data():
    """utf_ken_all.csv を読み込む"""
    global _zip_data
    if _zip_data is not None:
        return
    import csv
    _zip_data = []
    csv_path = os.path.join(os.path.dirname(__file__), "utf_ken_all.csv")
    if not os.path.exists(csv_path):
        return
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 9:
                _zip_data.append((row[2], row[6], row[7], row[8]))


def _lookup_zip(address: str) -> str:
    """住所文字列から郵便番号を逆引き"""
    _load_zip_data()
    if not _zip_data:
        return ""
    best = ""
    best_len = 0
    for zipcode, pref, city, town in _zip_data:
        full = pref + city + town
        # 「以下に掲載がない場合」はスキップ
        if "以下に掲載" in town:
            candidate = pref + city
        else:
            candidate = full
        if address.startswith(candidate) and len(candidate) > best_len:
            best = zipcode
            best_len = len(candidate)
    # ハイフン付きに整形
    if best and len(best) == 7:
        return f"{best[:3]}-{best[3:]}"
    return best


def _format_phone(phone: str) -> str:
    """電話番号をハイフン区切りにする"""
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) == 11 and digits.startswith("0"):
        # 携帯: 090-1234-5678
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    elif len(digits) == 10 and digits.startswith("0"):
        # 固定: 048-123-4567
        if digits.startswith("03") or digits.startswith("06"):
            return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
        else:
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return phone


# --- 年齢帯の推定 ---
_GRADE_TO_AGE = {
    "小学１年": "2", "小学２年": "2", "小学３年": "2",
    "小学４年": "2", "小学５年": "2", "小学６年": "2",
    "中学１年": "3", "中学２年": "3", "中学３年": "3",
    "高校１年": "4", "高校２年": "4", "高校３年": "4",
}

_GRADE_TO_SCHOOLKB = {
    "小学１年": ("2", "1"), "小学２年": ("2", "2"), "小学３年": ("2", "3"),
    "小学４年": ("2", "4"), "小学５年": ("2", "5"), "小学６年": ("2", "6"),
    "中学１年": ("3", "1"), "中学２年": ("3", "2"), "中学３年": ("3", "3"),
    "高校１年": ("4", "1"), "高校２年": ("4", "2"), "高校３年": ("4", "3"),
}

# --- 外部生登録(IEB040)の学年コード ---
# 00-06:0歳〜6歳, 07-12:小1〜小6, 13-15:中1〜中3, 16-18:高1〜高3, 19:成人, 99:その他
_GRADE_TO_GAIBUSEI_CODE = {
    "0歳": "00", "1歳": "01", "2歳": "02", "3歳": "03", "4歳": "04", "5歳": "05", "6歳": "06",
    "小学１年": "07", "小学２年": "08", "小学３年": "09",
    "小学４年": "10", "小学５年": "11", "小学６年": "12",
    "中学１年": "13", "中学２年": "14", "中学３年": "15",
    "高校１年": "16", "高校２年": "17", "高校３年": "18",
    "成人": "19", "その他": "99",
}


def _kana_to_halfwidth(text: str) -> str:
    """全角カタカナ・ひらがなを半角カタカナに変換（IEB040 seitokmに必須）"""
    if not text:
        return ""
    # ひらがな→全角カタカナ
    zenkaku = "".join(
        chr(ord(c) + 0x60) if "\u3041" <= c <= "\u3096" else c
        for c in text
    )
    # 全角カタカナ → 半角カタカナ
    z2h = {
        "ア": "ｱ", "イ": "ｲ", "ウ": "ｳ", "エ": "ｴ", "オ": "ｵ",
        "カ": "ｶ", "キ": "ｷ", "ク": "ｸ", "ケ": "ｹ", "コ": "ｺ",
        "サ": "ｻ", "シ": "ｼ", "ス": "ｽ", "セ": "ｾ", "ソ": "ｿ",
        "タ": "ﾀ", "チ": "ﾁ", "ツ": "ﾂ", "テ": "ﾃ", "ト": "ﾄ",
        "ナ": "ﾅ", "ニ": "ﾆ", "ヌ": "ﾇ", "ネ": "ﾈ", "ノ": "ﾉ",
        "ハ": "ﾊ", "ヒ": "ﾋ", "フ": "ﾌ", "ヘ": "ﾍ", "ホ": "ﾎ",
        "マ": "ﾏ", "ミ": "ﾐ", "ム": "ﾑ", "メ": "ﾒ", "モ": "ﾓ",
        "ヤ": "ﾔ", "ユ": "ﾕ", "ヨ": "ﾖ",
        "ラ": "ﾗ", "リ": "ﾘ", "ル": "ﾙ", "レ": "ﾚ", "ロ": "ﾛ",
        "ワ": "ﾜ", "ヲ": "ｦ", "ン": "ﾝ",
        "ガ": "ｶﾞ", "ギ": "ｷﾞ", "グ": "ｸﾞ", "ゲ": "ｹﾞ", "ゴ": "ｺﾞ",
        "ザ": "ｻﾞ", "ジ": "ｼﾞ", "ズ": "ｽﾞ", "ゼ": "ｾﾞ", "ゾ": "ｿﾞ",
        "ダ": "ﾀﾞ", "ヂ": "ﾁﾞ", "ヅ": "ﾂﾞ", "デ": "ﾃﾞ", "ド": "ﾄﾞ",
        "バ": "ﾊﾞ", "ビ": "ﾋﾞ", "ブ": "ﾌﾞ", "ベ": "ﾍﾞ", "ボ": "ﾎﾞ",
        "パ": "ﾊﾟ", "ピ": "ﾋﾟ", "プ": "ﾌﾟ", "ペ": "ﾍﾟ", "ポ": "ﾎﾟ",
        "ァ": "ｧ", "ィ": "ｨ", "ゥ": "ｩ", "ェ": "ｪ", "ォ": "ｫ",
        "ッ": "ｯ", "ャ": "ｬ", "ュ": "ｭ", "ョ": "ｮ",
        "ー": "ｰ", "・": "･", "　": " ",
    }
    return "".join(z2h.get(c, c) for c in zenkaku)


def _normalize_grade_key(grade: str) -> str:
    """学年表記を正規化（半角数字→全角数字）"""
    if not grade:
        return ""
    _han2zen = str.maketrans("0123456789", "０１２３４５６７８９")
    return grade.translate(_han2zen)


@mcp.tool()
def sks_inquiry_search(
    name: str = "",
    date_from: str = "",
    date_to: str = "",
) -> str:
    """SKS問い合わせ管理の一覧を検索する。

    Args:
        name: 生徒氏名で絞り込み（部分一致）
        date_from: 開始日 YYYY/MM/DD（空なら3ヶ月前）
        date_to: 終了日 YYYY/MM/DD（空なら今日）
    """
    s = _get_session()

    if not date_from:
        now = datetime.now()
        m = now.month - 3
        y = now.year
        if m < 1:
            m += 12
            y -= 1
        date_from = f"{y}/{m:02d}/01"
    if not date_to:
        date_to = datetime.now().strftime("%Y/%m/%d")

    r = s.post(f"{BASE_URL}/service/tryers/listup.wpp", data={
        "cmd": "search",
        "imtoiawasedtf": date_from,
        "imtoiawasedtt": date_to,
        "toiawasedtf": date_from.replace("/", ""),
        "toiawasedtt": date_to.replace("/", ""),
        "seitosm": name,
        "ad1": "",
        "nyukaikb": "0",
    })
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for tr in soup.find_all("tr", onclick=True):
        tds = tr.find_all("td")
        if len(tds) >= 8:
            results.append({
                "教室": tds[0].get_text(strip=True),
                "No": tds[1].get_text(strip=True),
                "種別": tds[2].get_text(strip=True),
                "問合せ日": tds[3].get_text(strip=True),
                "生徒氏名": tds[4].get_text(strip=True),
                "問合せ者": tds[5].get_text(strip=True),
                "郵便番号": tds[6].get_text(strip=True),
                "住所": tds[7].get_text(strip=True),
                "電話": tds[8].get_text(strip=True) if len(tds) > 8 else "",
                "対象年齢": tds[9].get_text(strip=True) if len(tds) > 9 else "",
                "媒体": tds[10].get_text(strip=True) if len(tds) > 10 else "",
            })

    return json.dumps(
        {"count": len(results), "results": results},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def sks_inquiry_register(
    student_name: str,
    guardian_name: str = "",
    inquiry_date: str = "",
    phone: str = "",
    postal_code: str = "",
    address_city: str = "",
    address_detail: str = "",
    address_building: str = "",
    grade: str = "",
    inquirer: str = "",
    memo: str = "",
) -> str:
    """SKSの問い合わせ管理に新規登録する。

    Args:
        student_name: 生徒氏名（例: "小森 湊斗"）
        guardian_name: 保護者氏名（例: "小森"）
        inquiry_date: 問合せ日 YYYY/MM/DD（空なら今日）
        phone: 電話番号（ハイフンなしでもOK、自動整形）
        postal_code: 郵便番号（空なら住所から自動逆引き）
        address_city: 住所1 都道府県市区町村（例: "埼玉県{市区名}"）
        address_detail: 住所2 番地（例: "1-1-1"）
        address_building: 住所3 建物名（例: "{建物名}{教室名}210"）
        grade: 学年（例: "中学２年", "小学５年"）。空なら不詳
        inquirer: 問合せ者（"父"/"母"/"本人"/"その他"）。空なら未設定
        memo: 備考欄に入れるテキスト
    """
    s = _get_session()

    # 問合せ日
    if not inquiry_date:
        inquiry_date = datetime.now().strftime("%Y/%m/%d")

    # 電話番号整形
    if phone:
        phone = _format_phone(phone)

    # 郵便番号逆引き
    if not postal_code and address_city:
        full_addr = address_city + address_detail
        postal_code = _lookup_zip(full_addr)

    # 学校区分・学年（半角数字→全角数字に正規化してからマッチ）
    _han2zen = str.maketrans("0123456789", "０１２３４５６７８９")
    grade_norm = grade.translate(_han2zen) if grade else ""
    schoolkb = ""
    grade_val = ""
    if grade_norm and grade_norm in _GRADE_TO_SCHOOLKB:
        schoolkb, grade_val = _GRADE_TO_SCHOOLKB[grade_norm]

    # 対象年齢
    age_val = _GRADE_TO_AGE.get(grade_norm, "10")  # デフォルト: 不詳

    # 問合せ者
    inquirer_map = {"父": "1", "母": "2", "本人": "3", "その他": "4"}
    elem2_val = inquirer_map.get(inquirer, "")

    # まずGETでフォームを取得（セッション確立＋hidden初期値取得）
    s.get(f"{BASE_URL}/service/tryers/regist.wpp")

    # POSTデータ: toiawasedt/postalcdのhiddenにも変換済みの値を入れる
    # （regist()のJS前処理を再現）
    # ※ numberフィールドは自動採番（指定しても無視される）
    data = {
        "cmd": "post",
        "kyoshitsucd": CLASSROOM,
        "kyoshitsusm": "{教室名}",
        "number": "",
        "toiawasedt": inquiry_date.replace("/", ""),
        "imtoiawasedt": inquiry_date,
        "seitosm": student_name,
        "hogoshasm": guardian_name,
        "postalcd": postal_code.replace("-", ""),
        "impostalcd": postal_code,
        "ad1": address_city,
        "ad2": address_detail,
        "ad3": address_building,
        "telno": phone,
        "schoolsm": "",
        "schoolkb": schoolkb,
        "grade": grade_val,
        "biko": memo,
        "elem1": "1",   # スクールIE
        "elem2": elem2_val,
        "elem3": age_val,
        "elem4": "7",   # 媒体: その他
        "elem5": "1",   # 内容: 料金（必須なのでデフォルト）
        "elem11": "1",  # 結果: 資料請求（デフォルト）
        "nyukaidt": "",
        "imnyukaidt": "",
    }

    r = s.post(f"{BASE_URL}/service/tryers/regist.wpp", data=data)

    # 成功判定: 登録後に検索して確認
    import time
    time.sleep(1)
    r_check = s.post(f"{BASE_URL}/service/tryers/listup.wpp", data={
        "cmd": "search",
        "imtoiawasedtf": inquiry_date[:7].replace("/", "/") + "/01",
        "imtoiawasedtt": inquiry_date,
        "toiawasedtf": inquiry_date.replace("/", "")[:6] + "01",
        "toiawasedtt": inquiry_date.replace("/", ""),
        "seitosm": student_name.split()[0] if " " in student_name else student_name,
        "ad1": "",
        "nyukaikb": "0",
    })
    check_html = r_check.content.decode("utf-8", errors="replace")
    found = student_name.replace(" ", "") in check_html.replace("　", "").replace(" ", "")

    return json.dumps({
        "result": "OK" if found else "UNCERTAIN",
        "student_name": student_name,
        "inquiry_date": inquiry_date,
        "postal_code": postal_code,
        "phone": phone,
        "verified": found,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_gaibusei_register(
    student_name: str,
    guardian_name: str,
    kana: str,
    grade: str,
    birth: str,
    sex: str = "",
    postal_code: str = "",
    address_city: str = "",
    address_detail: str = "",
    address_building: str = "",
    phone: str = "",
    emergency_phone: str = "",
    emergency_dest: str = "",
    memo: str = "",
    entry_year: str = "",
    kubun: str = "講習会生",
    gaibusei_code: str = "",
) -> str:
    """SKS外部生登録(IEB040)に新規登録する(講習会生/ETS体験生)。

    必須フィールド: student_name, guardian_name, kana, grade, birth。
    特に kana(フリガナ)は **サーバー側で必須バリデーションあり**。空だと登録失敗する。

    Args:
        student_name: 生徒氏名(例: "小川 夢禾")。必須。
        guardian_name: 保護者氏名(例: "小川 亮子")。フルネーム必須。
        kana: 生徒フリガナ(全角/半角カタカナ/ひらがな可、自動で半角カナへ変換)。**必須**。
        grade: 学年(例: "中学3年", "高校2年", "小学4年", "成人")。必須。
        birth: 生年月日(YYYY/MM/DD 例: "2010/04/15")。必須。
        sex: 性別("女"/"男"/"1"/"0"。1=女, 0=男。空なら男)
        postal_code: 郵便番号(空なら住所から自動逆引き)
        address_city: 住所1 都道府県市区町村(例: "埼玉県{市区名}")
        address_detail: 住所2 番地(例: "{町名}2-7-19")
        address_building: 住所3 建物名
        phone: 電話番号(ハイフンなしでもOK)
        emergency_phone: 緊急連絡先TEL
        emergency_dest: 緊急連絡先宛先
        memo: 備考
        entry_year: 登録年度 YYYY(空なら今年)
        kubun: 外部生区分("講習会生" or "ETS体験生")
        gaibusei_code: 既存コード(更新時のみ、新規は空)
    """
    s = _get_session()

    # フリガナ必須バリデーション（IEB040 サーバー側必須）
    if not kana or not kana.strip():
        return json.dumps({
            "result": "NG",
            "error": "kana(フリガナ) は IEB040 の必須フィールドです。空のまま登録できません。",
            "student_name": student_name,
        }, ensure_ascii=False)

    # 年度
    if not entry_year:
        entry_year = datetime.now().strftime("%Y")

    # 性別コード
    sex_map = {"男": "0", "女": "1", "0": "0", "1": "1", "": "0"}
    sex_code = sex_map.get(sex, "0")

    # 学年コード
    grade_key = _normalize_grade_key(grade)
    if grade_key not in _GRADE_TO_GAIBUSEI_CODE:
        return json.dumps({
            "result": "NG", "error": f"unknown grade: {grade}",
            "hint": "'中学3年', '高校2年', '小学4年', '成人', 'その他' etc."
        }, ensure_ascii=False)
    grade_code = _GRADE_TO_GAIBUSEI_CODE[grade_key]

    # 生年月日
    try:
        datetime.strptime(birth, "%Y/%m/%d")
    except ValueError:
        return json.dumps({
            "result": "NG", "error": f"invalid birth format: {birth}",
            "hint": "YYYY/MM/DD"
        }, ensure_ascii=False)
    datebirth = birth.replace("/", "")

    # 電話番号整形
    phone_fmt = _format_phone(phone) if phone else ""
    emtelno_fmt = _format_phone(emergency_phone) if emergency_phone else ""

    # 郵便番号逆引き
    if not postal_code and address_city:
        postal_code = _lookup_zip(address_city + address_detail)
    postal_no_hyphen = postal_code.replace("-", "") if postal_code else ""
    postal_with_hyphen = (
        f"{postal_no_hyphen[:3]}-{postal_no_hyphen[3:]}"
        if len(postal_no_hyphen) == 7 else postal_code
    )

    # フリガナ半角カナ変換
    kana_half = _kana_to_halfwidth(kana) if kana else ""

    # 外部生区分
    kubun_code = "1" if kubun == "ETS体験生" else "0"

    # フォーム状態を確立
    s.get(f"{BASE_URL}/service/IEB040.wpp")

    data = {
        "cmd": "regist",
        "ToiawaseNO": "",
        "ToiawaseFLG": "",
        "gaibuseikb": kubun_code,
        "gaibuseicd": gaibusei_code,  # 空=新規, コード指定=更新
        "entryyear": entry_year,
        "seitosm": student_name,
        "hogosha": guardian_name,
        "seitokm": kana_half,
        "seitoem": "",
        "seitosex": sex_code,
        "datebirth": datebirth,
        "imdatebirth": birth,
        "seitograde": grade_code,
        "kumi": "",
        "n9labo": "", "ac_n9labo": "",
        "hshogaku": "", "shogaku": "",
        "hchugaku": "", "chugaku": "",
        "hkoukou": "", "koukou": "",
        "postalcd": postal_no_hyphen,
        "impostalcd": postal_with_hyphen,
        "ad1": address_city,
        "ad2": address_detail,
        "ad3": address_building,
        "telno": phone_fmt,
        "emtelno": emtelno_fmt,
        "emdest": emergency_dest,
        "biko": memo,
    }

    r = s.post(f"{BASE_URL}/service/IEB040.wpp", data=data)
    r.encoding = "utf-8"
    html = r.text

    m = re.search(r"生徒コード：([0-9A-Z]+)\s*を(登録|更新)しました", html)
    if m:
        return json.dumps({
            "result": "OK",
            "gaibusei_code": m.group(1),
            "operation": m.group(2),
            "student_name": student_name,
            "kana": kana_half,
            "grade_code": grade_code,
            "postal_code": postal_with_hyphen,
        }, ensure_ascii=False, indent=2)

    m_err = re.search(r'await alert\("([^"]+)"\)', html)
    if m_err:
        return json.dumps({
            "result": "NG",
            "error": m_err.group(1),
            "student_name": student_name,
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "result": "UNKNOWN",
        "hint": "No success/error pattern matched in response",
        "student_name": student_name,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_gaibusei_register_from_inquiry(
    inquiry_no: str,
    kana: str,
    birth: str,
    sex: str = "",
    memo: str = "",
    emergency_phone: str = "",
    emergency_dest: str = "",
    kubun: str = "講習会生",
) -> str:
    """SKS問合せデータから外部生登録(IEB040)にコピーして登録する。

    問合せ管理から「検索→選択」で遷移するのと同じ動作。
    生徒氏名・保護者氏名・学年・住所・電話番号は問合せから自動転記される。
    フリガナ・性別・生年月日・備考だけ補完する。

    必須: kana(フリガナ) と birth(生年月日)。kana が空だとサーバー側
    バリデーションで弾かれて UNCERTAIN になる。

    Args:
        inquiry_no: SKS問合せNO(例: "1184")
        kana: 生徒フリガナ(全角/半角カナ/ひらがな可)。**必須**。
        birth: 生年月日(YYYY/MM/DD)。必須。
        sex: 性別("女"/"男"/"1"/"0"。空なら男)
        memo: 備考(転記先に残る)
        emergency_phone: 緊急連絡先TEL
        emergency_dest: 緊急連絡先宛先
        kubun: 外部生区分("講習会生" or "ETS体験生")
    """
    s = _get_session()

    # フリガナ必須バリデーション（IEB040 サーバー側必須）
    if not kana or not kana.strip():
        return json.dumps({
            "result": "NG",
            "error": "kana(フリガナ) は IEB040 の必須フィールドです。空のまま登録できません。",
            "inquiry_no": inquiry_no,
        }, ensure_ascii=False)

    kubun_code = "1" if kubun == "ETS体験生" else "0"
    sex_map = {"男": "0", "女": "1", "0": "0", "1": "1", "": "0"}
    sex_code = sex_map.get(sex, "0")
    kana_half = _kana_to_halfwidth(kana) if kana else ""

    # 生年月日バリデーション
    try:
        datetime.strptime(birth, "%Y/%m/%d")
    except ValueError:
        return json.dumps({"result": "NG", "error": f"invalid birth: {birth}"}, ensure_ascii=False)

    # 問合せから外部生登録画面へ転送: 問合せ管理→選択と同じGET
    # GET /service/IEB040.wpp?mode=toiawase&kyoshitsucd={CLASSROOM}&gaibuseicd=&gaibuseikb=&seitocd={CLASSROOM}:{inquiry_no}
    r_load = s.get(
        f"{BASE_URL}/service/IEB040.wpp",
        params={
            "mode": "toiawase",
            "kyoshitsucd": CLASSROOM,
            "gaibuseicd": "",
            "gaibuseikb": "",
            "seitocd": f"{CLASSROOM}:{inquiry_no}",
        },
    )
    r_load.encoding = "utf-8"
    html = r_load.text

    # レスポンスHTMLから pre-filled 値を抽出
    def _extract_value(name: str) -> str:
        pat = rf'name="{re.escape(name)}"[^>]*value="([^"]*)"'
        m = re.search(pat, html)
        if m:
            return m.group(1)
        # 逆順パターン(value が先)
        pat2 = rf'value="([^"]*)"[^>]*name="{re.escape(name)}"'
        m2 = re.search(pat2, html)
        return m2.group(1) if m2 else ""

    seitosm = _extract_value("seitosm")
    hogosha = _extract_value("hogosha")
    seitograde = _extract_value("seitograde")
    postalcd = _extract_value("postalcd")
    impostalcd = _extract_value("impostalcd")
    ad1 = _extract_value("ad1")
    ad2 = _extract_value("ad2")
    ad3 = _extract_value("ad3")
    telno = _extract_value("telno")
    entryyear = _extract_value("entryyear") or datetime.now().strftime("%Y")

    if not seitosm:
        return json.dumps({
            "result": "NG",
            "error": "Failed to load inquiry data — is the inquiry_no correct?",
            "inquiry_no": inquiry_no,
        }, ensure_ascii=False)

    data = {
        "cmd": "regist",
        "ToiawaseNO": "",
        "ToiawaseFLG": "",
        "gaibuseikb": kubun_code,
        "gaibuseicd": "",
        "entryyear": entryyear,
        "seitosm": seitosm,
        "hogosha": hogosha,
        "seitokm": kana_half,
        "seitoem": "",
        "seitosex": sex_code,
        "datebirth": birth.replace("/", ""),
        "imdatebirth": birth,
        "seitograde": seitograde,
        "kumi": "",
        "n9labo": "", "ac_n9labo": "",
        "hshogaku": "", "shogaku": "",
        "hchugaku": "", "chugaku": "",
        "hkoukou": "", "koukou": "",
        "postalcd": postalcd,
        "impostalcd": impostalcd,
        "ad1": ad1, "ad2": ad2, "ad3": ad3,
        "telno": telno,
        "emtelno": _format_phone(emergency_phone) if emergency_phone else "",
        "emdest": emergency_dest,
        "biko": memo,
    }

    r = s.post(f"{BASE_URL}/service/IEB040.wpp", data=data)
    r.encoding = "utf-8"
    r_html = r.text
    m = re.search(r"生徒コード：([0-9A-Z]+)\s*を(登録|更新)しました", r_html)
    if m:
        return json.dumps({
            "result": "OK",
            "gaibusei_code": m.group(1),
            "operation": m.group(2),
            "student_name": seitosm,
            "grade_code": seitograde,
            "from_inquiry_no": inquiry_no,
        }, ensure_ascii=False, indent=2)

    m_err = re.search(r'await alert\("([^"]+)"\)', r_html)
    if m_err:
        return json.dumps({"result": "NG", "error": m_err.group(1)}, ensure_ascii=False)

    return json.dumps({"result": "UNKNOWN"}, ensure_ascii=False)


# --- 分類コード (IEB070 ifbsel) ---
_BUNRUI_CODE = {
    "授業料値引": "19",
    "別途教材費": "50",
    "テスト費": "60",
    "ﾃｽﾄ費": "60",
    "その他": "70",
    "講習会費（テキスト代）": "91",
    "講習会費（ﾃｷｽﾄ代）": "91",
    "講習会費オプション（テスト費）": "92",
    "講習会費ｵﾌﾟｼｮﾝ（ﾃｽﾄ費）": "92",
    "講習会費オプション（ファイル代）": "93",
    "講習会費ｵﾌﾟｼｮﾝ（ﾌｧｲﾙ代）": "93",
}

_SEITOSHUBETSU_CODE = {
    "内部生": "0", "内部生（振込者）": "0",
    "WN": "1", "内部生（WN請求者）": "1",
    "外部生": "2",
}


@mcp.tool()
def sks_bill_register(
    student_code: str,
    bill_date: str,
    due_date: str,
    category: str,
    ryokin_code: str,
    quantity: int,
    unit_price: int,
    student_type: str = "外部生",
    shoriym: str = "",
    chushutsu: str = "10000",
    ctlno: str = "",
    comment: str = "",
    additional_items: list = None,
) -> str:
    """SKS振込者用料金入力(IEB070)に請求行を登録する。

    Args:
        student_code: 生徒コード(例: "26G035")
        bill_date: 振込票発行日(YYYY/MM/DD)
        due_date: 支払期日(YYYY/MM/DD)
        category: 分類("テスト費" / "別途教材費" / "授業料値引" / "その他" /
                  "講習会費（テキスト代）" / "講習会費オプション（テスト費）" /
                  "講習会費オプション（ファイル代）")
        ryokin_code: 料金コード(例: "61480/99" = 英検団体検定料2級)
        quantity: 数量
        unit_price: 単価(円)
        student_type: 生徒種別("内部生" / "WN" / "外部生")。外部生の場合 chushutsu 必須
        shoriym: 処理年月(YYYYMM)。空なら今月
        chushutsu: 抽出期間(外部生時のみ、単位=日、デフォルト10000)
        ctlno: 教室管理番号(空なら自動採番)
        comment: 請求明細のコメント(全半角60文字以内)
        additional_items: 追加の諸経費行(最大9行)。
          形式: [{"category": "...", "ryokin_code": "...",
                  "quantity": 1, "unit_price": 0}, ...]
    """
    s = _get_session()

    # 分類コード解決
    bsel = _BUNRUI_CODE.get(category)
    if not bsel:
        return json.dumps({
            "result": "NG", "error": f"unknown category: {category}",
            "valid_categories": list(_BUNRUI_CODE.keys()),
        }, ensure_ascii=False)

    # 生徒種別コード解決
    ssb = _SEITOSHUBETSU_CODE.get(student_type)
    if ssb is None:
        return json.dumps({
            "result": "NG", "error": f"unknown student_type: {student_type}",
        }, ensure_ascii=False)

    # 処理年月
    if not shoriym:
        shoriym = datetime.now().strftime("%Y%m")

    # 日付変換
    furikomidt = bill_date.replace("/", "").replace("-", "")
    shiharaidt = due_date.replace("/", "").replace("-", "")

    # フォーム状態を確立
    s.get(f"{BASE_URL}/service/IEB070.wpp")

    kingaku = quantity * unit_price
    data = {
        "mode": "regist",
        "seitocd": student_code,
        "scrolltop": "0",
        "shoriym": shoriym,
        "ctlno": ctlno,
        "chushutsu": chushutsu if ssb == "2" else "",
        "bcomment": comment,
        "seitoshubetsu": ssb,
        "furikomidt": furikomidt,
        "shiharaidt": shiharaidt,
        "nocvsfee": "",
        # 諸経費 1行目 (ifbsel21 ...)
        "ifcb21": "",
        "ifminus21": "0",
        "ifbsel21": bsel,
        "ifrsel21": ryokin_code,
        "ifkomasu21": str(quantity),
        "iftanka21": str(unit_price),
        "ifkingaku21": str(kingaku),
        "iftext21": "",
    }

    # 追加行
    total_kingaku = kingaku
    if additional_items:
        for i, item in enumerate(additional_items, start=1):
            row = 21 + i
            bsel_i = _BUNRUI_CODE.get(item["category"])
            if not bsel_i:
                return json.dumps({
                    "result": "NG",
                    "error": f"unknown category in row {row}: {item['category']}",
                }, ensure_ascii=False)
            qty_i = int(item["quantity"])
            unit_i = int(item["unit_price"])
            kin_i = qty_i * unit_i
            total_kingaku += kin_i
            data[f"ifcb{row}"] = ""
            data[f"ifminus{row}"] = "0"
            data[f"ifbsel{row}"] = bsel_i
            data[f"ifrsel{row}"] = item["ryokin_code"]
            data[f"ifkomasu{row}"] = str(qty_i)
            data[f"iftanka{row}"] = str(unit_i)
            data[f"ifkingaku{row}"] = str(kin_i)
            data[f"iftext{row}"] = ""

    # 講習会費削除チェックボックス(ブラウザ互換)
    for row in range(41, 46):
        data[f"ifcb{row}"] = ""

    r = s.post(f"{BASE_URL}/service/IEB070.wpp", data=data)
    r.encoding = "utf-8"
    html = r.text

    # 成功判定: TR_{seitocd}_{furikomidt} が追加されている
    if re.search(rf"TR_{re.escape(student_code)}_{re.escape(furikomidt)}", html):
        # 教室管理番号抽出
        m_ctl = re.search(
            rf"TR_{re.escape(student_code)}_{re.escape(furikomidt)}.*?"
            rf"{re.escape(furikomidt[:4])}/{re.escape(furikomidt[4:6])}/{re.escape(furikomidt[6:])}.*?"
            r"([0-9]{4})",
            html, re.DOTALL)
        return json.dumps({
            "result": "OK",
            "student_code": student_code,
            "ctlno": m_ctl.group(1) if m_ctl else "auto-assigned",
            "bill_date": bill_date,
            "due_date": due_date,
            "total": total_kingaku,
        }, ensure_ascii=False, indent=2)

    m_err = re.search(r'alert\("([^"]+)"\)', html)
    if m_err:
        return json.dumps({
            "result": "NG", "error": m_err.group(1),
        }, ensure_ascii=False)

    return json.dumps({
        "result": "UNKNOWN",
        "hint": "No TR row matched in response",
    }, ensure_ascii=False)


@mcp.tool()
def sks_ryokin_search(
    student_code: str,
    category: str,
    keyword: str = "",
    shoriym: str = "",
) -> str:
    """IEB071(料金検索)から料金コードを検索する。

    振込者用料金入力(IEB070)で料金名を選ぶ前に、
    コード("61480/99"のような)と単価を確認するのに使う。

    Args:
        student_code: 生徒コード(例: "26G035")
        category: 分類("テスト費" など。_BUNRUI_CODE のキー)
        keyword: 検索キーワード(例: "英検", "漢検")
        shoriym: 処理年月(YYYYMM、空なら今月)
    """
    s = _get_session()
    bsel = _BUNRUI_CODE.get(category)
    if not bsel:
        return json.dumps({
            "result": "NG", "error": f"unknown category: {category}",
        }, ensure_ascii=False)

    if not shoriym:
        shoriym = datetime.now().strftime("%Y%m")

    # IEB071 料金検索エンドポイント (ax モード)
    # param = {shoriym}|{shubetsu=2}|{seitocd}|{bunrui}|{pcName}||{keyword}
    param = f"{shoriym}|2|{student_code}|{bsel}|if2rsel1||{keyword}"
    r = s.get(f"{BASE_URL}/service/subwin/IEB071.wpp",
              params={"mode": "ax", "param": param})
    r.encoding = "utf-8"

    # tr 単位でブロック分割し、trsel_modal(code,name) と同じ tr 内の数値td から
    # 単価を拾う。料金コード自体は数値だが先頭/末尾以外の数値td を残し、
    # 100以上の値を unit_price 候補として返す。
    items = []
    for blk in re.split(r"<tr[^>]*>", r.text):
        m = re.search(r'trsel_modal\(this,"([^"]+)","([^"]+)"\)', blk)
        if not m:
            continue
        code, name = m.group(1), m.group(2)
        nums = re.findall(r"<td[^>]*>\s*([\d,]+)\s*</td>", blk)
        candidates = [int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()]
        # 料金コード自体(5桁番号)を除外する手はあるが、
        # 単価らしい値=最後尾の3桁以上の数値という運用で十分
        price_candidates = [v for v in candidates if v >= 100]
        items.append({
            "code": code,
            "name": name,
            "unit_price": price_candidates[-1] if price_candidates else None,
            "price_candidates": price_candidates,
        })
    return json.dumps({
        "result": "OK", "count": len(items),
        "items": items[:50],
    }, ensure_ascii=False, indent=2)


# =====================
# PCS Tools
# =====================

SSK2_URL = os.environ.get("SKS_SSK2_URL", "http://sks-ssk2.example.internal")
_pcs_session_ready: bool = False


_KYOUZAIKB_MAP = {"2": "0", "3": "B"}  # 数学=0, 英語=B

_COLOR_NAME_TO_CLASS = {
    "黄": "clYellow", "紺": "clNavy", "白": "", "青": "clBlue",
    "灰": "clGray", "赤": "clRed", "薄紺": "clLNavy", "緑": "clGreen",
}


def _pcs_establish_session(s: requests.Session, student_code: str, kyoukakb: str = "2"):
    """PCS系統図（別ドメインssk2）のセッションを確立する。

    pcs_start.wpp → Pcs.do の2段階POSTが必要。
    戻り値: PcsMenu.doのレスポンス(requests.Response)
    """
    global _pcs_session_ready

    kyouzaikb = _KYOUZAIKB_MAP.get(kyoukakb, "0")

    # pcs.wppにアクセスして生徒情報をセット
    s.get(f"{BASE_URL}/service/pcs.wpp")
    s.get(f"{BASE_URL}/service/pcs.wpp?cmd=ax&param={student_code}")

    # Step 1: POST pcs_start.wpp
    r1 = s.post(f"{BASE_URL}/service/pcs_start.wpp", data={
        "scd": student_code,
        "kyoukakb": kyoukakb,
        "kyouzaikb": kyouzaikb,
        "pflag": "1",
        "omtflag": "1",
    })

    # Step 2: 中間ページのfmpost2フォームをPOST → ssk2ドメインへ
    soup = BeautifulSoup(r1.text, "html.parser")
    form = soup.find("form", {"name": "fmpost2"})
    if not form:
        raise Exception("PCS session: fmpost2 form not found in pcs_start.wpp response")

    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name", "")
        if name:
            data[name] = inp.get("value", "")

    action = form.get("action", "")
    r2 = s.post(action, data=data, allow_redirects=True)

    if "系統図" not in r2.text and "PcsMenu" not in r2.url:
        raise Exception(f"PCS session: failed to reach PcsMenu.do (url={r2.url})")

    _pcs_session_ready = True
    return r2


@mcp.tool()
def pcs_print_mondai(student_code: str, kyoukakb: str = "2") -> str:
    """PCSの問題PDFをダウンロードしてローカルに保存する。

    Args:
        student_code: 生徒番号（例: "250015"）
        kyoukakb: 教科コード（2=算数・数学、他は要調査）
    """
    s = _get_session()
    _pcs_establish_session(s, student_code, kyoukakb)

    r = s.get(f"{SSK2_URL}/pcs/PcsPrintMondai.do?cmd=print&opt1=1&bgFlag=1")
    if r.content[:4] != b"%PDF":
        return json.dumps({"result": "FAILED", "error": "Response is not PDF",
                           "content_type": r.headers.get("Content-Type", ""),
                           "size": len(r.content)}, ensure_ascii=False)

    pdf_dir = os.path.join(os.path.expanduser("~"), "Documents", "pcs_pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    filename = f"mondai_{student_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(pdf_dir, filename)
    with open(pdf_path, "wb") as f:
        f.write(r.content)

    # ページ数取得
    pages = 0
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        pages = len(reader.pages)
    except Exception:
        pass

    return json.dumps({
        "result": "OK",
        "path": pdf_path,
        "size": len(r.content),
        "pages": pages,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def pcs_print_kaitou(student_code: str, kyoukakb: str = "2") -> str:
    """PCSの解答PDFをダウンロードしてローカルに保存する。

    Args:
        student_code: 生徒番号（例: "250015"）
        kyoukakb: 教科コード（2=算数・数学、他は要調査）
    """
    s = _get_session()
    if not _pcs_session_ready:
        _pcs_establish_session(s, student_code, kyoukakb)

    r = s.get(f"{SSK2_URL}/pcs/PcsPrintMondai.do?cmd=print&opt1=2&bgFlag=1")
    if r.content[:4] != b"%PDF":
        return json.dumps({"result": "FAILED", "error": "Response is not PDF",
                           "content_type": r.headers.get("Content-Type", ""),
                           "size": len(r.content)}, ensure_ascii=False)

    pdf_dir = os.path.join(os.path.expanduser("~"), "Documents", "pcs_pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    filename = f"kaitou_{student_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(pdf_dir, filename)
    with open(pdf_path, "wb") as f:
        f.write(r.content)

    pages = 0
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        pages = len(reader.pages)
    except Exception:
        pass

    return json.dumps({
        "result": "OK",
        "path": pdf_path,
        "size": len(r.content),
        "pages": pages,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def pcs_print_pdf(pdf_path: str, paper: str = "A3", nup: str = "") -> str:
    """PDFをプリンタ（iR-ADV C3720）で印刷する。

    Args:
        pdf_path: 印刷するPDFファイルのパス
        paper: 用紙サイズ（"A3" or "A4"）
        nup: Nin1設定（"2x1"=2in1, "2x2"=4in1, "3x2"=6in1）。空なら通常印刷
    """
    sumatra = os.path.join(os.path.expanduser("~"),
                           "AppData", "Local", "SumatraPDF", "SumatraPDF.exe")
    if not os.path.exists(sumatra):
        return json.dumps({"result": "FAILED", "error": "SumatraPDF not found"})

    if not os.path.exists(pdf_path):
        return json.dumps({"result": "FAILED", "error": f"PDF not found: {pdf_path}"})

    printer = "iR-ADV C3720"
    settings = f"paper={paper},color"
    if nup:
        settings += f",{nup}"
    else:
        settings += ",noscale"

    import subprocess
    cmd = [sumatra, "-print-to", printer, "-print-settings", settings, pdf_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    return json.dumps({
        "result": "OK" if result.returncode == 0 else "FAILED",
        "returncode": result.returncode,
        "printer": printer,
        "settings": settings,
        "pdf": pdf_path,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def pcs_create_problem(
    student_code: str,
    selected_units: str,
    kyoukakb: str = "2",
    auto_complete_cycle: bool = True,
) -> str:
    """PCSの問題作成を行う。指定した単元で問題を作成する。

    Args:
        student_code: 生徒番号（例: "250015"）
        selected_units: 選択する単元コードのカンマ区切り（例: "1701_01,1701_02,1801_01"）
        kyoukakb: 教科コード（2=算数・数学, 3=英語）
        auto_complete_cycle: Trueなら前回サイクル未完了時に自動的に採点(0点)→カリキュラム(4回)→更新してから問題作成する
    """
    s = _get_session()
    kyouzaikb = _KYOUZAIKB_MAP.get(kyoukakb, "0")
    picks = set(u.strip() for u in selected_units.split(",") if u.strip())

    if not picks:
        return json.dumps({"result": "FAILED", "error": "No units selected"})

    # PCSセッション確立
    r2 = _pcs_establish_session(s, student_code, kyoukakb)
    html = r2.text
    soup = BeautifulSoup(html, "html.parser")

    # 前回サイクル未完了判定
    # delm(問題削除)にdisabled属性がなければ問題作成済み → サイクル未完了
    delm = soup.find("input", {"name": "delm"})
    has_problem = delm and not delm.has_attr("disabled") if delm else False

    if auto_complete_cycle and has_problem:
        # 採点(0点)
        r_s = s.get(f"{SSK2_URL}/pcs/PcsSaiten.do")
        soup_s = BeautifulSoup(r_s.text, "html.parser")
        saiten_data = {}
        for inp in soup_s.find_all("input"):
            n, t = inp.get("name", ""), inp.get("type", "")
            if not n or t in ("checkbox", "button"):
                continue
            saiten_data[n] = inp.get("value", "")
        if any("correctcnt" in k for k in saiten_data):
            for k in list(saiten_data.keys()):
                if "correctcnt" in k:
                    saiten_data[k] = "0"
            saiten_data["cmd"] = "regist"
            s.post(f"{SSK2_URL}/pcs/PcsSaiten.do", data=saiten_data)

        # カリキュラム(tukikaisu=4)
        r_c = s.get(f"{SSK2_URL}/pcs/PcsCurriculum.do")
        soup_c = BeautifulSoup(r_c.text, "html.parser")
        cur_data = {}
        for inp in soup_c.find_all("input"):
            n, t = inp.get("name", ""), inp.get("type", "")
            if not n or t == "button":
                continue
            if t == "radio" and inp.get("checked") is None:
                continue
            cur_data[n] = inp.get("value", "")
        cur_data["cmd"] = "regist"
        cur_data["tukikaisu"] = "4"
        s.post(f"{SSK2_URL}/pcs/PcsCurriculum.do", data=cur_data)

        # 更新(reload)
        r_reload = s.get(f"{SSK2_URL}/pcs/PcsMenu.do", params={
            "mode": "", "kaisu": "", "seitoCd": student_code,
            "kyoukakb": kyoukakb, "kyouzaikb": kyouzaikb,
        })
        html = r_reload.text
        soup = BeautifulSoup(html, "html.parser")

    # JS初期化から色情報を抽出
    color_map = {}
    for m in re.finditer(
        r'doCheckbox\("(\d+)",\s*"([^"]+)",\s*"color\|([^"]+)"\)', html
    ):
        _, key, color = m.groups()
        color_map[key] = _COLOR_NAME_TO_CLASS.get(color, "")

    # testflg自動判定
    testflg = "1" if "shubetsu[1].checked = true" in html else "0"

    # form1フィールド取得
    form1 = soup.find("form", {"name": "form1"})
    if not form1:
        return json.dumps({"result": "FAILED", "error": "form1 not found"})
    f1 = {
        inp.get("name", ""): inp.get("value", "")
        for inp in form1.find_all("input")
        if inp.get("name")
    }
    fm = {
        inp.get("name", ""): inp.get("value", "")
        for inp in soup.find("form", {"name": "formmain"}).find_all("input")
        if inp.get("name")
    }

    # 全単元リスト
    all_tg = []
    for inp in soup.find_all("input", {"type": "checkbox"}):
        n = inp.get("name", "")
        if n.startswith("tg"):
            k = n[2:]
            if k not in all_tg:
                all_tg.append(k)

    # checks構築: CRLF, 色情報保持, 選択単元はclYellow
    lines = []
    for k in all_tg:
        if k in picks:
            lines.append(f"{k}|1|clYellow||")
        else:
            cname = color_map.get(k, "")
            lines.append(f"{k}|0|{cname}||")
    checks = "\r\n".join(lines) + "\r\n"

    # POST
    f1["mode"] = "updm"
    f1["checks"] = checks
    f1["jisshikaisu"] = fm.get("kaisu", "1")
    f1["pattern"] = fm.get("pattern", "1")
    f1["testflg"] = testflg

    r3 = s.post(f"{SSK2_URL}/pcs/PcsMenu.do", data=f1, allow_redirects=True)
    soup3 = BeautifulSoup(r3.text, "html.parser")
    fm3 = {
        inp.get("name", ""): inp.get("value", "")
        for inp in soup3.find_all("input")
        if inp.get("name")
    }

    smsg = fm3.get("SMSG", "")
    success = "問題が印刷" in smsg

    return json.dumps({
        "result": "OK" if success else "FAILED",
        "student_code": student_code,
        "kyoukakb": kyoukakb,
        "kaisu": fm3.get("kaisu", ""),
        "selected_units": len(picks),
        "testflg": testflg,
        "message": smsg.strip(),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def pcs_saiten(student_code: str, scores: str = "", kyoukakb: str = "2") -> str:
    """PCSの採点登録を行う。全問題に指定した正解数を登録する。

    Args:
        student_code: 生徒番号（例: "250015"）
        scores: 全問題の正解数をカンマ区切りで指定（例: "0,1,0,1,1,0"）。空なら全て0
        kyoukakb: 教科コード（2=算数・数学）
    """
    s = _get_session()
    if not _pcs_session_ready:
        _pcs_establish_session(s, student_code, kyoukakb)

    # 採点画面を取得してフィールドを特定
    r = s.get(f"{SSK2_URL}/pcs/PcsSaiten.do")
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    # correctcnt フィールドを収集
    fields = []
    for inp in soup.find_all("input", id=lambda x: x and x.startswith("POINT_")):
        fields.append({
            "id": inp["id"],
            "name": inp.get("name", ""),
        })

    if not fields:
        return json.dumps({"result": "FAILED", "error": "No POINT fields found"})

    # スコア設定
    score_list = []
    if scores:
        score_list = scores.split(",")
    # 足りない分は0で埋める
    while len(score_list) < len(fields):
        score_list.append("0")

    # POSTデータ構築（フォームの全hiddenフィールド + 正解数）
    data = {}
    for inp in soup.find_all("input", {"type": "hidden"}):
        name = inp.get("name", "")
        if name:
            data[name] = inp.get("value", "")

    for i, field in enumerate(fields):
        data[field["name"]] = score_list[i]

    # 登録POST
    r2 = s.post(f"{SSK2_URL}/pcs/PcsSaiten.do", data=data)

    return json.dumps({
        "result": "OK" if r2.status_code == 200 else "FAILED",
        "student_code": student_code,
        "fields": len(fields),
        "scores": score_list[:len(fields)],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def pcs_curriculum(student_code: str, kyoukakb: str = "2") -> str:
    """PCSのカリキュラム登録を行う。

    Args:
        student_code: 生徒番号（例: "250015"）
        kyoukakb: 教科コード（2=算数・数学）
    """
    s = _get_session()
    if not _pcs_session_ready:
        _pcs_establish_session(s, student_code, kyoukakb)

    # カリキュラム画面を取得
    r = s.get(f"{SSK2_URL}/pcs/PcsCurriculum.do")
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    # フォームデータ収集
    data = {}
    for inp in soup.find_all("input"):
        name = inp.get("name", "")
        if name:
            data[name] = inp.get("value", "")
    for sel in soup.find_all("select"):
        name = sel.get("name", "")
        if name:
            opt = sel.find("option", selected=True)
            data[name] = opt["value"] if opt else ""

    # 登録POST
    r2 = s.post(f"{SSK2_URL}/pcs/PcsCurriculum.do", data=data)

    return json.dumps({
        "result": "OK" if r2.status_code == 200 else "FAILED",
        "student_code": student_code,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_zip_lookup(address: str) -> str:
    """住所から郵便番号を逆引きする。

    Args:
        address: 住所文字列（例: "埼玉県{市区名}○○町1-2-3"）
    """
    zipcode = _lookup_zip(address)
    return json.dumps({"address": address, "postal_code": zipcode}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# IEB010 (内部生情報) 編集ヘルパー
# ---------------------------------------------------------------------------
#
# IEB010 のフォームは「HTML の value 属性」と「JS で後から注入される値」の
# 二段構成になっている。たとえば 入会日(契約日) は HTML 上は空で、
# ページ下部の <script> に
#     document.getElementById('imnyujukudt').value = '2022/03/07';
# のようにリテラル代入される。さらに登録ボタン押下時、JS が
#     formmain.nyujukudt.value = formmain.imnyujukudt.value.replace(/\//g,'')
# のように imXXX → XXX (スラッシュ除去) でコピーする。
#
# requests + BeautifulSoup だけでは JS が走らないので、これら JS 注入値も
# 正規表現で拾ってフォームデータに詰める必要がある。
# ---------------------------------------------------------------------------

# document.getElementById('imdatebirth').value= '2012/05/05';  などを抽出
_IEB010_JS_VALUE_RE = re.compile(
    r"""document\.getElementById\(\s*['"]([A-Za-z0-9_]+)['"]\s*\)\.value\s*=\s*['"]([^'"]*)['"]\s*;"""
)


def _ieb010_parse_form(html: str):
    """IEB010 のフォームを解析して (form_data, formmain_or_None) を返す。
    JS 注入値・imXXX→XXX のスラッシュ除去コピーまで補完する。
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"name": "formmain"}) \
        or soup.find("form", attrs={"id": "formmain"}) \
        or soup.find("form", attrs={"action": re.compile(r"IEB010")})
    if not form:
        return None, None

    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        v = inp.get("value", "") or ""
        if itype == "checkbox":
            # GUI の挙動: checked のものは value(または "on")、未チェックは送らない。
            # ただし IEB010 では実用上ほぼ問題にならない（hidden 多用のため）。
            if inp.has_attr("checked"):
                data[name] = v or "on"
        elif itype == "radio":
            if inp.has_attr("checked"):
                data[name] = v
        else:
            data[name] = v

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True)
        if opt is None:
            opt = sel.find("option")
        data[name] = opt.get("value", "") if opt else ""

    for ta in form.find_all("textarea"):
        name = ta.get("name")
        if name:
            data[name] = ta.get_text() or ""

    # JS で注入される初期値（日付など）を補完
    for m in _IEB010_JS_VALUE_RE.finditer(html):
        fld, val = m.group(1), m.group(2)
        data[fld] = val
        # imXXX (YYYY/MM/DD) → XXX (YYYYMMDD) も自動補完
        if fld.startswith("im") and re.match(r"\d{4}/\d{2}/\d{2}$", val):
            data[fld[2:]] = val.replace("/", "")

    return data, form


def _ieb010_load(seitocd: str):
    """IEB010 を生徒コード指定でロードし、(data, raw_html) を返す。
    ロード失敗時は (None, raw_html) を返す。
    """
    s = _get_session()
    r = s.post(
        f"{BASE_URL}/service/IEB010.wpp",
        data={"seitocd": seitocd, "kyoshitsucd": ""},
    )
    r.encoding = "utf-8"
    html = r.text
    data, _form = _ieb010_parse_form(html)
    return data, html


def _ieb010_extract_error(html: str) -> str | None:
    """登録レスポンスからサーバ側エラーメッセージを抽出する。なければ None。"""
    # E00003: <契約日>を入力してください。 のようなテーブル組み込みエラー
    m = re.search(r"E\d{5}[:：][^<\n]+", html)
    if m:
        return m.group(0).strip()
    # await alert("...") 形のエラー
    m2 = re.search(r'await\s+alert\(\s*["\']([^"\']+)["\']', html)
    if m2 and any(kw in m2.group(1) for kw in ("失敗", "エラー", "正しく", "違反")):
        return m2.group(1)
    return None


@mcp.tool()
def sks_internal_update_memo(seitocd: str, memo: str) -> str:
    """SKS内部生(IEB010)の備考欄(biko)を更新する。

    既存のフォーム値を維持したまま備考だけを書き換える。退塾済み生徒も対象。

    Args:
        seitocd: 生徒コード（例: "210027"）
        memo: 備考に設定する文字列（メールアドレス等）
    """
    return sks_internal_update_fields(seitocd, {"biko": memo})


@mcp.tool()
def sks_internal_update_fields(seitocd: str, fields: dict) -> str:
    """SKS内部生(IEB010)の任意フィールドを更新する汎用ツール。

    既存のフォーム値を維持したまま、指定したフィールドだけを上書きして登録する。
    日付フィールドのような JS 注入値も自動的に補完するので、
    フィールド名と値だけ与えれば良い。

    よく使うフィールド名（IEB010 のフォーム要素名）:
      - biko          : 備考（input[type=text]、最大50字）
      - seitosm       : 生徒氏名
      - seitokm       : 生徒カナ
      - seitoem       : 生徒英字氏名
      - postalcd / impostalcd : 郵便番号（数字7桁 / ハイフン付き表示）
      - ad1 / ad2 / ad3 : 住所
      - telno         : 電話番号
      - emtelno       : 緊急連絡先電話番号
      - emdest        : 緊急連絡先続柄
      - hogoshamail   : 保護者メール
      - r1name / r1zokugara / r1old / r1work : 保護者1の氏名/続柄/年齢/職業
      - r2name / r2zokugara / r2old / r2work : 保護者2
      - imdatebirth (YYYY/MM/DD) : 生年月日
      - imnyujukudt (YYYY/MM/DD) : 入会日（契約日）

    Args:
        seitocd: 生徒コード（例: "210027"）
        fields: 上書きするフィールド名→値の辞書（例: {"biko": "...", "telno": "090-..."}）
    """
    if not isinstance(fields, dict) or not fields:
        return json.dumps({
            "result": "NG",
            "error": "fields must be a non-empty dict",
        }, ensure_ascii=False, indent=2)

    # 1. ロード
    data, html = _ieb010_load(seitocd)
    if data is None:
        # ログイン画面に飛ばされた等
        if "passwd" in (html or "") and "kyoshitsucd" in (html or ""):
            return json.dumps({
                "result": "NG",
                "error": "session expired (login redirect)",
                "seitocd": seitocd,
            }, ensure_ascii=False, indent=2)
        return json.dumps({
            "result": "NG",
            "error": "formmain not found",
            "seitocd": seitocd,
            "html_preview": (html or "")[:300],
        }, ensure_ascii=False, indent=2)

    before = {k: data.get(k, "") for k in fields.keys()}

    # 2. 上書き
    for k, v in fields.items():
        data[k] = "" if v is None else str(v)

    # 3. 送信前正規化: GUI の registEntry() 内で行われる im → 裏 hidden コピーを再現する。
    #    JS:
    #      formmain.postalcd.value = formmain.impostalcd.value.replace(/-/g, '');
    #      formmain.nyujukudt.value = formmain.imnyujukudt.value.replace(/\//g, '');
    #      formmain.datebirth.value = formmain.imdatebirth.value.replace(/\//g, '');
    #
    #    これをやらないと、サーバが返してくる impostalcd "334-0074" を
    #    そのまま postalcd にも入れて送り返す → サーバ側で再フォーマットされて
    #    "334--0074" のように2重ハイフンが累積する事故が起きる。
    if data.get("impostalcd"):
        data["postalcd"] = data["impostalcd"].replace("-", "")
    if data.get("imnyujukudt"):
        data["nyujukudt"] = data["imnyujukudt"].replace("/", "")
    if data.get("imdatebirth"):
        data["datebirth"] = data["imdatebirth"].replace("/", "")

    data["cmd"] = "regist"
    data.setdefault("TORIKOMIFLG", "1")

    # 3. POST
    s = _get_session()
    r = s.post(f"{BASE_URL}/service/IEB010.wpp", data=data)
    r.encoding = "utf-8"
    html2 = r.text

    err = _ieb010_extract_error(html2)
    if err:
        return json.dumps({
            "result": "NG",
            "error": err,
            "seitocd": seitocd,
            "fields_attempted": list(fields.keys()),
            "before": before,
        }, ensure_ascii=False, indent=2)

    # 4. 反映確認
    after_data, _ = _ieb010_parse_form(html2)
    after = {}
    if after_data is not None:
        after = {k: after_data.get(k, "") for k in fields.keys()}

    all_ok = all(
        after.get(k, "") == ("" if v is None else str(v))
        for k, v in fields.items()
    )
    return json.dumps({
        "result": "OK" if all_ok else "UNKNOWN",
        "seitocd": seitocd,
        "before": before,
        "after": after,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_internal_get_fields(seitocd: str, fields: list | None = None) -> str:
    """SKS内部生(IEB010)のフォーム値を取得する。

    更新前の値確認や、利用可能なフィールド名を調べる用途。

    Args:
        seitocd: 生徒コード
        fields: 取得したいフィールド名のリスト。None または空なら全フィールドを返す。
    """
    data, html = _ieb010_load(seitocd)
    if data is None:
        return json.dumps({
            "result": "NG",
            "error": "formmain not found (session expired?)",
            "seitocd": seitocd,
        }, ensure_ascii=False, indent=2)

    if fields:
        sub = {k: data.get(k, "") for k in fields}
        return json.dumps({
            "result": "OK",
            "seitocd": seitocd,
            "fields": sub,
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "result": "OK",
        "seitocd": seitocd,
        "fields": data,
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 受講登録 (授業登録 / IEB020) — GUIなし
# ---------------------------------------------------------------------------
#
# GUIフロー: IEB010「授業登録」ボタン → モーダルiframe(IEB020) で
#   1. 受講履歴(日付+02:通常授業開始 等)を「追加」
#      AJAX GET  IEB020.wpp?cmd=ax&param=IF1regist|{生徒}||{入会日}|{日付}|{受講履歴}|
#   2. コース表が出る(IF2SHOW)。各コードの単価/割引が hidden で埋め込まれる:
#        TANKA{code}     = 単価(週1回ぶんの基準額)
#        WGK{n}_{code}   = n回受講時の割引額(累積)
#        ⇒ 金額 = 単価 × 回数 − WGK{回数}
#   3. コース名/回数/科目(国数英理社)を入れて「追加／修正」
#      AJAX GET  IEB020.wpp?cmd=ax&param=IF2ADD|{生徒}|01|{入会日}|{行}
#        行 = {idx}:{code}:{回数}:{国}:{数}:{英}:{理}:{社}:{金額}
#
# 受講(月謝)登録の専用APIは無く上記AJAXを順に叩く。IF2SHOWは常に空フォームを返す
# (保存済みコースは出ない)ため、二重登録防止に名簿の 科目① を事前チェックする。
# param中の "01" は対象の受講履歴行番号。新規生徒の最初の1件なら "01"。

_SUBJECT_FLAGS = [  # IF2ADD 行内の順序: 国・数・英・理・社
    "国", "数", "英", "理", "社",
]
_IF_RIREKI_LABEL = {
    "02": "通常授業開始", "03": "退塾または休塾",
    "04": "再塾", "05": "コース変更", "06": "選択科目変更",
}


def _student_record(seitocd: str) -> dict | None:
    """名簿一覧(IEB030)から生徒コード一致のレコードを返す。退塾者も含む。"""
    s = _get_session()
    s.get(f"{BASE_URL}/service/IEB030.wpp")
    r = s.post(f"{BASE_URL}/service/IEB030.wpp", data={
        "mode": "if", "cols": _COLS_NAIBU, "selseitolist": "",
        "seitokm": "", "seitosm": "", "seitograde": "",
        "listcount": "", "seitokb": "naibu",
    })
    html = r.content.decode("utf-8", errors="replace")
    for st in _parse_student_table(html):
        code = (st.get("生徒ｺｰﾄﾞ") or st.get("生徒コード") or "").strip()
        if code == str(seitocd):
            return st
    return None


def _ieb020_fetch_show(s: requests.Session, seitocd: str, nyujukudt: str, tsuban: str = "01") -> str:
    """IF2SHOW(コース入力フォーム+料金hidden)を取得する。読み取り専用。

    tsuban: 対象の受講履歴通番。コース表の学年は「その通番の履歴行の適用日時点の学年」
            で決まる。既定 "01" は入会時履歴=入会時学年のコース表になる。
            学年が進級した既存生のコース変更では、新規履歴行を登録して発番された
            新通番を渡さないと現学年のコース表・単価が取れない（sks-failures.md 失敗1）。
    """
    param = f"IF2SHOW|{seitocd}|{tsuban}|{nyujukudt}"
    r = s.get(f"{BASE_URL}/service/IEB020.wpp", params={"cmd": "ax", "param": param})
    return r.content.decode("utf-8", errors="replace")


def _ieb020_last_tsuban(s: requests.Session, seitocd: str) -> str:
    """IF1SHOW(受講履歴一覧)から最新の通番(lasttsuban, 2桁文字列)を返す。履歴なしは ''。"""
    r = s.get(f"{BASE_URL}/service/IEB020.wpp", params={"cmd": "ax", "param": f"IF1SHOW|{seitocd}"})
    html = r.content.decode("utf-8", errors="replace")
    m = re.findall(r"lasttsuban='(\d+)'", html)
    return m[-1] if m else ""


def _ieb020_add_history(s: requests.Session, seitocd: str, lasttsuban: str,
                        nyujukudt: str, sd_compact: str, rireki: str) -> str:
    """受講履歴行を登録(IF1regist)し、発番された新通番(2桁文字列)を返す。⚠サーバ永続化。

    新通番は IF1regist レスポンス末尾の lasttsuban='NN' から取得する
    （既存最大通番 +1 で発番される）。
    """
    param = f"IF1regist|{seitocd}|{lasttsuban}|{nyujukudt}|{sd_compact}|{rireki}|"
    r = s.get(f"{BASE_URL}/service/IEB020.wpp", params={"cmd": "ax", "param": param})
    html = r.content.decode("utf-8", errors="replace")
    m = re.findall(r"lasttsuban='(\d+)'", html)
    return m[-1] if m else ""


def _course_grade_suffix(courses: list) -> str:
    """コース表の代表コース名から学年サフィックス(小6/中3/高2等)を推定する。"""
    for c in courses:
        mm = re.search(r"(小[1-6]|中[1-3]|高[1-3])$", c["name"].rstrip())
        if mm:
            return mm.group(1)
    return ""


def _ieb020_parse_pricing(html: str):
    """IF2SHOW HTMLから (courses, tanka, wgk) を解析する。
      courses: [{"code": "12100", "name": "PS2･中3"}, ...]
      tanka:   {code: 単価(int)}
      wgk:     {code: {回数(int): 割引額(int)}}
    """
    soup = BeautifulSoup(html, "html.parser")
    courses = []
    sel = soup.find("select", attrs={"name": "if2CS_0"})
    if sel:
        for opt in sel.find_all("option"):
            v = (opt.get("value") or "").strip()
            if not v:
                continue
            txt = opt.get_text(strip=True)
            if "：" in txt:
                name = txt.split("：", 1)[1]
            elif ":" in txt:
                name = txt.split(":", 1)[1]
            else:
                name = txt
            courses.append({"code": v, "name": name})
    tanka = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"name='TANKA(\d+)'[^>]*?value='(\d+)'", html)
    }
    wgk: dict[str, dict[int, int]] = {}
    for m in re.finditer(r"name='WGK(\d+)_(\d+)'[^>]*?value='(\d+)'", html):
        n, code, val = int(m.group(1)), m.group(2), int(m.group(3))
        wgk.setdefault(code, {})[n] = val
    return courses, tanka, wgk


def _compute_kingaku(code: str, kaisu: int, tanka: dict, wgk: dict) -> int | None:
    """金額 = 単価×回数 − WGK{回数}"""
    if code not in tanka:
        return None
    return tanka[code] * kaisu - wgk.get(code, {}).get(kaisu, 0)


def _auto_ps2_course(courses: list, grade: str) -> str | None:
    """学年に対応するPS2標準コースのコードを自動特定する。"""
    cands = [
        c for c in courses
        if c["name"].startswith("PS2")
        and c["name"].rstrip().endswith(grade)
        and not any(x in c["name"] for x in ("追加", "同時", "ﾒｲﾄ", ">"))
    ]
    return cands[0]["code"] if cands else None


@mcp.tool()
def sks_jugyo_show(seitocd: str) -> str:
    """SKS内部生の受講(コース)情報と、選択可能なコース・料金を取得する（読み取り専用）。

    現在の受講状況(名簿の コース①②③/科目/回数/金額)と、その生徒の学年で選べる
    コース一覧(コード:名称・単価)を返す。受講登録の事前確認に使う。

    Args:
        seitocd: 生徒コード（例: "260007"）
    """
    s = _get_session()
    rec = _student_record(seitocd)
    if rec is None:
        return json.dumps({"result": "NG", "error": f"生徒コード {seitocd} が名簿に見つかりません"},
                          ensure_ascii=False, indent=2)
    data, _ = _ieb010_load(seitocd)
    nyujukudt = (data or {}).get("nyujukudt", "")

    current = {}
    for i in ("①", "②", "③"):
        if (rec.get(f"科目{i}", "") or "").strip():
            current[i] = {
                "コース名": rec.get(f"ｺｰｽ名{i}", ""),
                "科目": rec.get(f"科目{i}", ""),
                "回数": rec.get(f"回数{i}", ""),
                "金額": rec.get(f"金額{i}", ""),
            }

    course_list = []
    if re.match(r"^\d{8}$", nyujukudt):
        courses, tanka, _wgk = _ieb020_parse_pricing(_ieb020_fetch_show(s, seitocd, nyujukudt))
        course_list = [
            {"code": c["code"], "name": c["name"], "単価": tanka.get(c["code"])}
            for c in courses
        ]

    return json.dumps({
        "result": "OK",
        "seitocd": seitocd,
        "氏名": rec.get("生徒氏名", ""),
        "学年": rec.get("学年", ""),
        "入会日": nyujukudt,
        "現在の受講": current or "(未登録)",
        "選択可能コース": course_list,
        "_金額式": "金額 = 単価×回数 − 割引(WGK)",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_jugyo_register(
    seitocd: str,
    subjects: str,
    kaisu: int | None = None,
    course_code: str = "",
    start_date: str = "",
    rireki: str = "02",
    dry_run: bool = True,
    allow_existing: bool = False,
) -> str:
    """SKS内部生の受講(月謝コース)を登録する。GUIの「授業登録」相当。

    受講履歴(通常授業開始 等)を追加し、コース情報(コース名/回数/科目)を登録する。
    金額は 単価×回数−割引 で自動算出する。
    学年が進級した既存生のコース変更(rireki=05)は、履歴行を先に登録して新通番を発番させ、
    その新通番のコース表から現学年の単価を取得して算出する(詳細: docs/sks-failures.md 失敗1)。

    ⚠ 月謝が発生する不可逆操作。デフォルト dry_run=True では登録内容を返すだけで
       書き込まない。実際に登録するには dry_run=False を明示すること。

    Args:
        seitocd: 生徒コード（例: "260007"）
        subjects: 受講科目。国数英理社の文字列（例: "数", "数英", "国数英"）
        kaisu: 週回数。省略時は科目数（数英なら2）
        course_code: コースコード（例: "12100"）。空なら現学年からPS2標準コースを自動選択。
                     dry_run時は入会時学年のコース表しか読めず、進級済み生は自動特定できない
                     ことがある（その場合は course_code を指定するか dry_run=False で実行）
        start_date: 授業開始日 YYYY/MM/DD。空なら今日
        rireki: 受講履歴コード（02:通常授業開始 / 04:再塾 / 05:コース変更 / 06:選択科目変更）
        dry_run: True(既定)=登録内容を返すだけ。False=実際に登録
        allow_existing: True=既に科目①が登録済みでも続行（再登録/変更時）
    """
    s = _get_session()
    rec = _student_record(seitocd)
    if rec is None:
        return json.dumps({"result": "NG", "error": f"生徒コード {seitocd} が名簿に見つかりません"},
                          ensure_ascii=False, indent=2)
    grade = (rec.get("学年", "") or "").strip()
    existing = (rec.get("科目①", "") or "").strip()
    if existing and not allow_existing:
        return json.dumps({
            "result": "NG",
            "error": (f"既に受講登録済み（コース①={rec.get('ｺｰｽ名①','')} 科目①={existing} "
                      f"回数①={rec.get('回数①','')} 金額①={rec.get('金額①','')}）。"
                      "再登録/変更する場合は allow_existing=True を指定してください。"),
            "seitocd": seitocd,
        }, ensure_ascii=False, indent=2)

    # 入会日(契約日)
    data, _ = _ieb010_load(seitocd)
    nyujukudt = (data or {}).get("nyujukudt", "")
    if not re.match(r"^\d{8}$", nyujukudt):
        return json.dumps({"result": "NG", "error": f"入会日(nyujukudt)を取得できません: {nyujukudt!r}"},
                          ensure_ascii=False, indent=2)

    # 科目フラグ（国数英理社の順）
    sel_names = [ch for ch in _SUBJECT_FLAGS if ch in subjects]
    if not sel_names:
        return json.dumps({"result": "NG", "error": f"科目が未指定です（国数英理社のいずれか）: {subjects!r}"},
                          ensure_ascii=False, indent=2)
    flags = ["1" if ch in subjects else "0" for ch in _SUBJECT_FLAGS]
    if kaisu is None:
        kaisu = len(sel_names)

    # 日付
    sd = (start_date or "").strip() or datetime.now().strftime("%Y/%m/%d")
    sd_compact = sd.replace("/", "").replace("-", "")
    if not re.match(r"^\d{8}$", sd_compact):
        return json.dumps({"result": "NG", "error": f"start_date 形式が不正: {start_date!r}（YYYY/MM/DD）"},
                          ensure_ascii=False, indent=2)

    # コース表(IF2SHOW)の学年は「対象通番の適用日時点の学年」で決まる。通番01(入会時)を
    # 読むと入会時学年になるため、進級済み生のコース変更では新通番が必要(sks-failures.md 失敗1)。
    # 新通番は履歴行を登録(IF1regist)しないと発番されない＝永続化が伴うため、
    #   dry_run: 入会時通番のコース表で参考値を出し、現学年と異なれば警告(正確値は実行時確定)
    #   実登録 : 履歴行を登録→新通番→新通番のIF2SHOW(現学年)で単価取得→IF2ADD
    last_tsuban = _ieb020_last_tsuban(s, seitocd)

    if dry_run:
        courses, tanka, wgk = _ieb020_parse_pricing(_ieb020_fetch_show(s, seitocd, nyujukudt, "01"))
        if not courses:
            return json.dumps({"result": "NG", "error": "コース選択肢を取得できません（IF2SHOW解析失敗）"},
                              ensure_ascii=False, indent=2)
        ref_grade = _course_grade_suffix(courses)
        cc = course_code or (_auto_ps2_course(courses, grade) or "")
        cname = next((c["name"] for c in courses if c["code"] == cc), cc)
        kingaku = _compute_kingaku(cc, kaisu, tanka, wgk) if cc else None
        next_tsuban = f"{int(last_tsuban)+1:02d}" if last_tsuban.isdigit() else "01"
        plan = {
            "result": "DRY_RUN",
            "seitocd": seitocd,
            "氏名": rec.get("生徒氏名", ""),
            "学年": grade,
            "受講履歴": f"{rireki}:{_IF_RIREKI_LABEL.get(rireki, '')}",
            "授業開始日": sd,
            "コース": f"{cc}：{cname}" if cc else "(実行時に現学年で特定)",
            "回数": kaisu,
            "科目": "".join(sel_names),
            "見込み新通番": next_tsuban,
            "note": "dry_run=True のため未登録。実行するには dry_run=False を指定してください。",
        }
        if ref_grade and ref_grade != grade:
            # 進級済み生: 入会時通番では現学年の単価が取れない
            plan["金額"] = None
            msg = (f"現学年は{grade}だが、入会時通番(01)のコース表は{ref_grade}基準のため正確な単価を"
                   f"取得できない。dry_run=Falseで実行すると、履歴行を登録→新通番→現学年({grade})の"
                   f"コース表から単価を取得して金額を算出する。")
            msg += (f" 参考(入会時{ref_grade}基準): {kingaku:,}円" if kingaku is not None
                    else " ※course_code未指定かつ入会時学年で自動特定不可。course_codeを指定するか実行時に現学年で特定。")
            plan["warning"] = msg
        else:
            if not cc:
                return json.dumps({
                    "result": "NG",
                    "error": f"PS2標準コースを自動特定できません（学年={grade}）。course_code を指定してください。",
                    "available_courses": courses,
                }, ensure_ascii=False, indent=2)
            if kingaku is None:
                return json.dumps({
                    "result": "NG", "error": f"コース {cc} の単価が見つかりません",
                    "available_courses": courses,
                }, ensure_ascii=False, indent=2)
            plan["金額"] = kingaku
        return json.dumps(plan, ensure_ascii=False, indent=2)

    # --- 実登録（新通番フロー）---
    # 1) 受講履歴行を登録 → 新通番を発番（⚠永続化）
    new_tsuban = _ieb020_add_history(s, seitocd, last_tsuban, nyujukudt, sd_compact, rireki)
    if not new_tsuban or new_tsuban == last_tsuban:
        return json.dumps({
            "result": "NG",
            "error": f"受講履歴の登録に失敗（新通番が発番されませんでした）。last={last_tsuban!r} new={new_tsuban!r}",
            "seitocd": seitocd,
        }, ensure_ascii=False, indent=2)

    # 2) 新通番でコース表(現学年)を取得
    courses, tanka, wgk = _ieb020_parse_pricing(_ieb020_fetch_show(s, seitocd, nyujukudt, new_tsuban))
    if not courses:
        return json.dumps({
            "result": "NG",
            "error": (f"新通番{new_tsuban}でコース表を取得できません（IF2SHOW解析失敗）。"
                      "履歴行は登録済みのためGUIで確認してください。"),
            "new_tsuban": new_tsuban,
        }, ensure_ascii=False, indent=2)
    if not course_code:
        course_code = _auto_ps2_course(courses, grade) or ""
        if not course_code:
            return json.dumps({
                "result": "NG",
                "error": (f"PS2標準コースを自動特定できません（学年={grade}）。course_code を指定して"
                          "再実行してください（履歴行は登録済みのため、再実行時は同一通番にコースが入ります）。"),
                "available_courses": courses,
                "new_tsuban": new_tsuban,
            }, ensure_ascii=False, indent=2)
    course_name = next((c["name"] for c in courses if c["code"] == course_code), course_code)
    kingaku = _compute_kingaku(course_code, kaisu, tanka, wgk)
    if kingaku is None:
        return json.dumps({
            "result": "NG", "error": f"コース {course_code} の単価が見つかりません",
            "available_courses": courses, "new_tsuban": new_tsuban,
        }, ensure_ascii=False, indent=2)

    # 3) コース確定
    row = f"0:{course_code}:{kaisu}:{':'.join(flags)}:{kingaku}"
    if2_param = f"IF2ADD|{seitocd}|{new_tsuban}|{nyujukudt}|{row}"
    r2 = s.get(f"{BASE_URL}/service/IEB020.wpp", params={"cmd": "ax", "param": if2_param})

    # 検証: 名簿で 科目①/金額① が反映されたか
    rec2 = _student_record(seitocd) or {}
    saved = {
        "ｺｰｽ名①": rec2.get("ｺｰｽ名①", ""),
        "科目①": rec2.get("科目①", ""),
        "回数①": rec2.get("回数①", ""),
        "金額①": rec2.get("金額①", ""),
        "授業開始日": rec2.get("授業開始日", ""),
    }
    ok = bool(saved["科目①"].strip()) and bool(saved["金額①"].strip())
    plan = {
        "result": "OK" if ok else "UNCERTAIN",
        "seitocd": seitocd,
        "氏名": rec.get("生徒氏名", ""),
        "学年": grade,
        "受講履歴": f"{rireki}:{_IF_RIREKI_LABEL.get(rireki, '')}",
        "授業開始日": sd,
        "コース": f"{course_code}：{course_name}",
        "回数": kaisu,
        "科目": "".join(sel_names),
        "金額": kingaku,
        "new_tsuban": new_tsuban,
        "saved": saved,
        "if2_status": r2.status_code,
    }
    if not ok:
        plan["warning"] = "登録後の名簿に科目①/金額①が反映されていません。手動で確認してください。"
    return json.dumps(plan, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 外部生 → 内部生 取り込み
# ---------------------------------------------------------------------------
#
# GUI フロー:
#   1. 生徒登録(IEB010) を開く
#   2. 「外部生 検索」ダイアログで対象を選択 → 外部生コード等が IEB010 にロードされ、
#      氏名・住所・電話・保護者氏名等が自動転記される
#   3. 必須フィールド（生年月日・入会日・保護者続柄・キャンペーン種別/理由 等）を補完
#   4. 「追加／修正」を押すと、gaibuseicd 付きで cmd=regist が POST され、
#      サーバ側で内部生コード採番 + 外部生レコードからの取り込みが完了
#
# プログラマブル:
#   ロードリクエスト:
#     POST /service/IEB010.wpp
#       mode=gaibu&kyoshitsucd=<教室コード>&seitocd=<外部生コード>
#   登録リクエスト:
#     通常の cmd=regist POST に gaibuseicd を含めるだけ
#
# 続柄コード（r1zokugara 等）:
#   01=父 / 02=母 / 03=祖父 / 04=祖母 / 05=兄 / 06=姉 / 07=弟 / 08=妹
#   09=叔父 / 10=叔母 / 11=夫 / 12=妻 / 13=本人 / 14=その他
#
# キャンペーン種別 (campaign) の数値コードはマスタによって変わるので、
# ロード後の form HTML から <select name="campaign"> の option を解析して
# 日本語名 → コード のマップを動的に作る。

_ZOKUGARA_TO_CODE = {
    "父": "01", "母": "02", "祖父": "03", "祖母": "04",
    "兄": "05", "姉": "06", "弟": "07", "妹": "08",
    "叔父": "09", "叔母": "10",
    "夫": "11", "妻": "12",
    "本人": "13", "その他": "14",
}

# YSPC「証憑データ確認」ダイアログ既定値
# premshubetsu=4 (未登録)、premreason=2 (証憑未取得) / 3 (デジタルデバイスなし)
_YSPC_DEFAULT_PREMSHUBETSU = "4"
_YSPC_DEFAULT_PREMREASON = "2"


def _ieb010_load_from_gaibu(gaibuseicd: str, kyoshitsucd: str | None = None):
    """外部生コードを指定して IEB010 のフォームをロード。
    外部生情報（氏名・住所・電話・保護者氏名等）が自動転記された状態の form を返す。

    Returns: (data: dict, html: str)
    """
    s = _get_session()
    kyo = kyoshitsucd or CLASSROOM
    r = s.post(
        f"{BASE_URL}/service/IEB010.wpp",
        data={"mode": "gaibu", "kyoshitsucd": kyo, "seitocd": gaibuseicd},
    )
    r.encoding = "utf-8"
    html = r.text
    data, _form = _ieb010_parse_form(html)
    return data, html


def _campaign_code_map(html: str) -> dict[str, str]:
    """<select name="campaign"> から 表示名 → value のマップを作る."""
    soup = BeautifulSoup(html, "html.parser")
    sel = soup.find("select", attrs={"name": "campaign"})
    if not sel:
        return {}
    out: dict[str, str] = {}
    for opt in sel.find_all("option"):
        text = (opt.get_text() or "").strip()
        val = opt.get("value", "")
        if text and val:
            out[text] = val
    return out


@mcp.tool()
def sks_convert_gaibu_to_internal(
    gaibuseicd: str,
    nyujukudt: str,
    datebirth: str,
    parent1_zokugara: str = "父",
    parent1_is_primary: bool = True,
    campaign: str = "",
    campaign_reason: str = "",
    premshubetsu: str = _YSPC_DEFAULT_PREMSHUBETSU,
    premreason: str = _YSPC_DEFAULT_PREMREASON,
    additional_fields: dict | None = None,
    kyoshitsucd: str | None = None,
    dry_run: bool = False,
) -> str:
    """外部生(IEB040) を内部生(IEB010) として取り込む。GUI の「外部生検索→選択→追加/修正」と等価。

    Args:
        gaibuseicd: 外部生コード（例: "26G033"）
        nyujukudt: 入会日（契約日） YYYY/MM/DD（例: "2026/04/27"）
        datebirth: 生年月日 YYYY/MM/DD（例: "2012/04/02"）
        parent1_zokugara: 保護者1の続柄（"父"/"母"/"祖父"等）。
            外部生に保護者氏名が登録されていれば自動転記される。
        parent1_is_primary: 保護者1を筆頭保護者（請求宛先）にするか。
            False にすると rgfamily=2 (保護者2が筆頭) になる。
        campaign: キャンペーン種別の表示名（"チラシ割引"/"ペア入塾"/"兄弟"/
            "講習"/"再入塾"/"GLEC単独入会" のいずれか。空で未選択）
        campaign_reason: 理由テキスト（例: "春期チラシ"）。txtriyu1 に入る。
        premshubetsu: YSPC会員種別。"4"=未登録（既定）。
        premreason: YSPC理由。"2"=証憑未取得（既定）/"3"=デジタルデバイスなし。
        additional_fields: その他の上書きフィールド（dict）。
        kyoshitsucd: 教室コード。空なら環境変数 SKS_CLASSROOM。
        dry_run: True なら POST せずに送信予定データだけ返す（デバッグ用）。
    """
    # 1. 外部生情報をロード
    data, html = _ieb010_load_from_gaibu(gaibuseicd, kyoshitsucd)
    if data is None or not data.get("seitosm"):
        # 外部生が見つからない / セッション切れ
        return json.dumps({
            "result": "NG",
            "error": "外部生がロードできなかった（コード違い or セッション切れ？）",
            "gaibuseicd": gaibuseicd,
            "html_preview": (html or "")[:300],
        }, ensure_ascii=False, indent=2)

    if data.get("gaibuseicd", "") != gaibuseicd:
        return json.dumps({
            "result": "NG",
            "error": f"ロード後 gaibuseicd 不一致: expected={gaibuseicd}, got={data.get('gaibuseicd')}",
        }, ensure_ascii=False, indent=2)

    # 2. ユーザ指定フィールドを上書き
    data["imdatebirth"] = datebirth
    data["imnyujukudt"] = nyujukudt
    data["entryyear"] = nyujukudt[:4] if len(nyujukudt) >= 4 else data.get("entryyear", "")

    z_code = _ZOKUGARA_TO_CODE.get(parent1_zokugara, "")
    if not z_code:
        return json.dumps({
            "result": "NG",
            "error": f"不明な続柄: {parent1_zokugara}（許容: {list(_ZOKUGARA_TO_CODE)}）",
        }, ensure_ascii=False, indent=2)
    data["r1zokugara"] = z_code
    data["rgfamily"] = "1" if parent1_is_primary else "2"

    if campaign:
        camp_map = _campaign_code_map(html)
        if campaign not in camp_map:
            return json.dumps({
                "result": "NG",
                "error": f"不明なキャンペーン種別: {campaign}（許容: {list(camp_map)}）",
            }, ensure_ascii=False, indent=2)
        data["campaign"] = camp_map[campaign]
    if campaign_reason:
        data["txtriyu1"] = campaign_reason

    # YSPC ダイアログ相当の値（GUI で OK を押した結果）
    data["premshubetsu"] = premshubetsu
    data["premreason"] = premreason

    if additional_fields:
        for k, v in additional_fields.items():
            data[k] = "" if v is None else str(v)

    # 3. 送信前正規化（im → 裏 hidden コピー、postalcd ハイフン除去）
    if data.get("impostalcd"):
        data["postalcd"] = data["impostalcd"].replace("-", "")
    if data.get("imnyujukudt"):
        data["nyujukudt"] = data["imnyujukudt"].replace("/", "")
    if data.get("imdatebirth"):
        data["datebirth"] = data["imdatebirth"].replace("/", "")

    data["cmd"] = "regist"
    data.setdefault("TORIKOMIFLG", "1")

    if dry_run:
        return json.dumps({
            "result": "DRYRUN",
            "gaibuseicd": gaibuseicd,
            "preview": {
                "seitosm": data.get("seitosm"),
                "seitokm": data.get("seitokm"),
                "seitosex": data.get("seitosex"),
                "seitograde": data.get("seitograde"),
                "imnyujukudt": data.get("imnyujukudt"),
                "imdatebirth": data.get("imdatebirth"),
                "ad1": data.get("ad1"),
                "ad2": data.get("ad2"),
                "telno": data.get("telno"),
                "r1zokugara": data.get("r1zokugara"),
                "r1name": data.get("r1name"),
                "rgfamily": data.get("rgfamily"),
                "campaign": data.get("campaign"),
                "txtriyu1": data.get("txtriyu1"),
                "premshubetsu": data.get("premshubetsu"),
                "premreason": data.get("premreason"),
            },
        }, ensure_ascii=False, indent=2)

    # 4. POST cmd=regist
    s = _get_session()
    r = s.post(f"{BASE_URL}/service/IEB010.wpp", data=data)
    r.encoding = "utf-8"
    html2 = r.text

    err = _ieb010_extract_error(html2)
    if err:
        return json.dumps({
            "result": "NG",
            "error": err,
            "gaibuseicd": gaibuseicd,
        }, ensure_ascii=False, indent=2)

    # 5. 反映確認: 新しい seitocd / keiyakushano が採番されているか
    after_data, _ = _ieb010_parse_form(html2)
    new_seitocd = (after_data or {}).get("seitocd", "")
    keiyakushano = (after_data or {}).get("keiyakushano", "")

    if not new_seitocd:
        return json.dumps({
            "result": "UNKNOWN",
            "gaibuseicd": gaibuseicd,
            "msg": "登録応答に seitocd がない。GUI で確認推奨",
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "result": "OK",
        "gaibuseicd": gaibuseicd,
        "new_seitocd": new_seitocd,
        "keiyakushano": keiyakushano,
        "seitosm": (after_data or {}).get("seitosm", ""),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_gaibu_preview(gaibuseicd: str, kyoshitsucd: str | None = None) -> str:
    """外部生コードで IEB010 をロードした結果（自動転記される情報）を確認する。

    sks_convert_gaibu_to_internal を呼ぶ前のプレビュー用。POST せず読み取りだけ。

    Args:
        gaibuseicd: 外部生コード
        kyoshitsucd: 教室コード（空なら環境変数）
    """
    data, html = _ieb010_load_from_gaibu(gaibuseicd, kyoshitsucd)
    if data is None or not data.get("seitosm"):
        return json.dumps({
            "result": "NG",
            "error": "外部生がロードできなかった",
            "gaibuseicd": gaibuseicd,
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "result": "OK",
        "gaibuseicd": data.get("gaibuseicd"),
        "seitosm": data.get("seitosm"),
        "seitokm": data.get("seitokm"),
        "seitosex": data.get("seitosex"),
        "seitograde": data.get("seitograde"),
        "ad1": data.get("ad1"),
        "ad2": data.get("ad2"),
        "ad3": data.get("ad3"),
        "impostalcd": data.get("impostalcd"),
        "telno": data.get("telno"),
        "biko": data.get("biko"),
        "r1name": data.get("r1name"),
        "r2name": data.get("r2name"),
        "campaign_options": list(_campaign_code_map(html).keys()),
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# IEV120 コンビニ振込用紙発行依頼
# ---------------------------------------------------------------------------
#
# GUI フロー:
#   1. 「振込者用料金入力 (IEB070)」で請求行を登録（sks_bill_register）
#   2. メインメニュー → コンビニ振込用紙発行依頼 (IEV120)
#   3. 発行待ち行が出る → チェック（or 全て選択）→ 確定
#   4. 「コンビニ伝票発行依頼を実行します。」OK → 状態が「CVS確定済」に
#   5. 後日、本人宛にコンビニ払込用紙が郵送される
#
# プログラマブル:
#   POST /service/IEV120.wpp
#     cmd=regist
#     kyoshitsucd=<教室コード>
#     kyoshitsusm=<教室名>
#     period=<振込票発行日 YYYY/MM/DD>
#     CVS_<n>=<行ID 文字列、ハイフン区切り>
#     AMT_<n>=<手数料込合計>
#     AMT1_<n>=<請求額(税抜)>
#     CB_<n>=1            ← 発行処理 ON
#     SADDR_<n>=Y         ← 請求先へ送る
#     dtcount=<行数>
#     showcount=<表示行数>
#     hidecount=0
#     seikyusum=<合計表示文字列>
#
# CVS_<n> の値の例: "26G036-202604-1-0-20260427--20260427-330"
#   <生徒コード>-<処理年月>-<種別>-<?>-<振込票発行日>-(空)-(発行済発行日)-<手数料>
#
# 確定後（再ロード時）の判定:
#   - 状態列に「CVS確定済」と表示
#   - CB_<n> checkbox が checked のまま
#   - CVS_<n> 値の途中の空フィールドに発行日が埋まる

_IEV120_NUM_RE = re.compile(r"^(CVS|AMT|AMT1|CB|BADDR|SADDR)_(\d+)$")


def _iev120_parse_meta(html: str) -> dict | None:
    """IEV120 の formmain（枠だけ）から共通フィールドを抽出。"""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", attrs={"name": "formmain"}) or soup.find("form", attrs={"id": "formmain"})
    if not form:
        return None
    meta: dict[str, str] = {}
    for k in ("kyoshitsucd", "kyoshitsusm", "period",
              "dtcount", "showcount", "hidecount", "seikyusum", "nocvs"):
        el = form.find(["input"], attrs={"name": k})
        if el is not None:
            meta[k] = el.get("value", "") or ""
    return meta


def _iev120_parse_rows(html_fragment: str) -> list[dict]:
    """IEV120 ajax VIEW のレスポンスHTML断片から行データを解析する。"""
    soup = BeautifulSoup(html_fragment, "html.parser")
    rows_by_idx: dict[int, dict] = {}
    for inp in soup.find_all("input"):
        name = inp.get("name", "")
        m = _IEV120_NUM_RE.match(name)
        if not m:
            continue
        kind, idx_s = m.group(1), m.group(2)
        idx = int(idx_s)
        row = rows_by_idx.setdefault(idx, {"idx": idx})
        v = inp.get("value", "") or ""
        if kind == "CB":
            row["cb"] = inp.has_attr("checked")
            row["cb_disabled"] = inp.has_attr("disabled")
        elif kind == "BADDR":
            row["baddr"] = v if inp.has_attr("checked") else ""
            row["baddr_disabled"] = inp.has_attr("disabled")
        else:
            row[kind.lower()] = v

    rows: list[dict] = []
    for idx in sorted(rows_by_idx):
        r = rows_by_idx[idx]
        cvs_val = r.get("cvs", "")
        seitocd = cvs_val.split("-", 1)[0] if cvs_val else ""
        # 確定済判定: CVS_<n> 値の発行済発行日フィールドが埋まっているか
        # 未確定: ...-20260427---330  / 確定済: ...-20260427--20260427-330
        parts = cvs_val.split("-")
        confirmed = bool(len(parts) >= 8 and parts[-2])
        r["seitocd"] = seitocd
        r["confirmed"] = confirmed
        rows.append(r)
    return rows


def _iev120_load(s: requests.Session) -> tuple[dict | None, list[dict]]:
    """IEV120 を初期GET + ajax VIEW で完全ロードして (meta, rows) を返す。

    メイン画面 GET ではフォームの枠だけ返り、行データは ajax view で取得する仕様。
    """
    r1 = s.get(f"{BASE_URL}/service/IEV120.wpp")
    r1.encoding = "utf-8"
    meta = _iev120_parse_meta(r1.text)
    if meta is None:
        return None, []

    r2 = s.get(
        f"{BASE_URL}/service/IEV120.wpp",
        params={"cmd": "ax", "param": "VIEW|N"},
    )
    r2.encoding = "utf-8"
    rows = _iev120_parse_rows(r2.text)
    # ajax レスポンスに dtcount が含まれていれば反映
    if "dtcount" not in meta or not meta["dtcount"]:
        m_dt = re.search(r"name='dtcount'\s+value='(\d+)'", r2.text)
        if m_dt:
            meta["dtcount"] = m_dt.group(1)
    return meta, rows


@mcp.tool()
def sks_cvs_issue_pending() -> str:
    """IEV120 コンビニ振込用紙発行依頼の発行待ち（および確定済み）一覧を取得。

    確定したい行は seitocd（生徒コード）を sks_cvs_issue_confirm に渡す。
    """
    s = _get_session()
    meta, rows = _iev120_load(s)
    if meta is None:
        return json.dumps({
            "result": "NG",
            "error": "formmain not found (session expired?)",
        }, ensure_ascii=False, indent=2)
    pending = [r for r in rows if not r.get("confirmed")]
    return json.dumps({
        "result": "OK",
        "meta": meta,
        "total": len(rows),
        "pending_count": len(pending),
        "rows": [
            {
                "idx": r["idx"],
                "seitocd": r["seitocd"],
                "amt": r.get("amt"),
                "amt1": r.get("amt1"),
                "cvs": r.get("cvs"),
                "saddr": r.get("saddr"),
                "checked": r.get("cb"),
                "confirmed": r.get("confirmed"),
            }
            for r in rows
        ],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_cvs_issue_confirm(student_codes: list | None = None, dry_run: bool = False) -> str:
    """IEV120 コンビニ振込用紙発行依頼を確定（CVS発行依頼）する。
    GUI の「全て選択 → 確定 → OK」と等価。

    Args:
        student_codes: 確定したい生徒コードのリスト（例: ["26G036"]）。
                       None または空なら、未確定の全行を確定する（GUI「全て選択」相当）。
        dry_run: True なら POST せず送信予定データだけ返す。

    確定済みの行は対象外（重複処理防止）。
    """
    s = _get_session()
    meta, rows = _iev120_load(s)
    if meta is None:
        return json.dumps({
            "result": "NG",
            "error": "formmain not found (session expired?)",
        }, ensure_ascii=False, indent=2)

    # 確定済み除外、対象抽出
    pending = [r for r in rows if not r.get("confirmed")]
    if student_codes:
        wanted = set(student_codes)
        target = [r for r in pending if r["seitocd"] in wanted]
        not_found = wanted - {r["seitocd"] for r in pending}
    else:
        target = pending
        not_found = set()

    if not target:
        return json.dumps({
            "result": "NG",
            "error": "確定対象の行がありません（既に確定済み or 該当生徒コードなし）",
            "not_found": list(not_found),
            "pending_count": len(pending),
        }, ensure_ascii=False, indent=2)

    # POST データ組み立て
    data: dict[str, str] = {
        "cmd": "regist",
        "kyoshitsucd": meta.get("kyoshitsucd", ""),
        "kyoshitsusm": meta.get("kyoshitsusm", ""),
        "period": meta.get("period", ""),
        "dtcount": meta.get("dtcount", str(len(rows))),
        "showcount": meta.get("showcount", str(len(rows))),
        "hidecount": meta.get("hidecount", "0"),
        "seikyusum": meta.get("seikyusum", ""),
    }
    target_idx = {r["idx"] for r in target}
    for r in rows:
        idx = r["idx"]
        data[f"CVS_{idx}"] = r.get("cvs", "")
        data[f"AMT_{idx}"] = r.get("amt", "")
        data[f"AMT1_{idx}"] = r.get("amt1", "")
        if r.get("saddr"):
            data[f"SADDR_{idx}"] = r["saddr"]
        if idx in target_idx:
            data[f"CB_{idx}"] = "1"

    if dry_run:
        return json.dumps({
            "result": "DRYRUN",
            "target_count": len(target),
            "targets": [{"seitocd": r["seitocd"], "amt": r.get("amt")} for r in target],
            "post_keys": sorted(data.keys()),
            "not_found": list(not_found),
        }, ensure_ascii=False, indent=2)

    r2 = s.post(f"{BASE_URL}/service/IEV120.wpp", data=data)
    r2.encoding = "utf-8"

    # 結果確認: 対象行が確定済みになっているか（再ロードで状態取得）
    _, rows_after = _iev120_load(s)
    after_state = {}
    if rows_after:
        for r in rows_after:
            if r["seitocd"] in {tr["seitocd"] for tr in target}:
                after_state[r["seitocd"]] = r.get("confirmed", False)

    all_ok = all(after_state.get(tr["seitocd"], False) for tr in target)
    return json.dumps({
        "result": "OK" if all_ok else "UNKNOWN",
        "confirmed_count": sum(1 for v in after_state.values() if v),
        "target_count": len(target),
        "targets": [{"seitocd": tr["seitocd"], "amt": tr.get("amt"),
                     "confirmed": after_state.get(tr["seitocd"], False)} for tr in target],
        "not_found": list(not_found),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_student_summary(asofdate: str = "") -> str:
    """SKS生徒集計一覧(IEB410)を取得する。基準日時点の学年別生徒数を返す。

    画面の「Excel出力」ボタン相当の動作。レスポンスはHTMLテーブル形式の
    Excel(application/vnd.ms-excel)なので、サーバ側でパースしてJSONで返す。

    Args:
        asofdate: 基準日 YYYY/MM/DD または YYYYMMDD（空なら今日）

    Returns:
        {
          "asofdate": "YYYY/MM/DD",
          "rows": [
            {"label": "当月月初生徒数", "by_grade": {"小1": 0, ..., "成人": 0}, "total": 45},
            {"label": "当月入塾数", ...},
            {"label": "当月在籍者数", ...},
            {"label": "当月退塾数", ...},
            {"label": "翌月月初生徒数", ...},
          ]
        }
    """
    import datetime as _dt

    if not asofdate:
        asofdate = _dt.date.today().strftime("%Y/%m/%d")
    digits = re.sub(r"[^\d]", "", asofdate)
    if len(digits) != 8:
        return json.dumps({"error": "asofdate must be YYYY/MM/DD or YYYYMMDD",
                           "got": asofdate}, ensure_ascii=False)
    asof_iso = f"{digits[0:4]}/{digits[4:6]}/{digits[6:8]}"

    s = _get_session()
    # 初期GETでセッション準備
    s.get(f"{BASE_URL}/service/IEB410.wpp")

    body = {
        "mode": "excel",
        "kyoshitsucd": "",
        "seitocd": "",
        "asofdate": digits,
        "gakunen": "",
        "shubetsu": "",
    }
    r = s.post(f"{BASE_URL}/service/IEB410.wpp", data=body)
    if r.status_code != 200:
        return json.dumps({"error": f"HTTP {r.status_code}", "asofdate": asof_iso},
                          ensure_ascii=False)

    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table")
    if not table:
        return json.dumps({"error": "table not found", "asofdate": asof_iso,
                           "preview": html[:200]}, ensure_ascii=False)

    trs = table.find_all("tr")
    if len(trs) < 2:
        return json.dumps({"error": "insufficient rows", "asofdate": asof_iso},
                          ensure_ascii=False)

    header = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]
    grade_labels = header[1:-1]
    out_rows = []
    for tr in trs[1:]:
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 2:
            continue
        label = cells[0]
        last = cells[-1]
        total = int(last) if last.lstrip("-").isdigit() else last
        by_grade = {}
        for i, g in enumerate(grade_labels, start=1):
            v = cells[i] if i < len(cells) else ""
            by_grade[g] = int(v) if v.lstrip("-").isdigit() else v
        out_rows.append({"label": label, "by_grade": by_grade, "total": total})

    return json.dumps({"asofdate": asof_iso, "rows": out_rows},
                      ensure_ascii=False, indent=2)


# --- Entry point ---
if __name__ == "__main__":
    mcp.run()
