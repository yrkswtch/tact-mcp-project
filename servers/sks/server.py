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


# --- 外部ENVファイルから秘匿設定を読み込む（リポジトリに実値を置かない） ---
def _load_env_file() -> None:
    """環境変数 SKS_ENV_FILE が指す .env から設定を補完する。
    教室コード等の教室固有値・認証情報は、このリポジトリ外のファイルに置く。
    既存の環境変数は上書きしない（呼び出し側の指定を優先）。"""
    path = os.environ.get("SKS_ENV_FILE", "")
    if not path or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env_file()

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
        "kyoshitsusm": os.environ.get("SKS_CLASSROOM_NAME", ""),
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

    # 診断用: 登録POSTのサーバー応答テキスト(タグ除去・先頭300字)
    try:
        resp_text = BeautifulSoup(r.content.decode("utf-8", errors="replace"), "html.parser").get_text(" ", strip=True)[:300]
    except Exception:
        resp_text = ""
    return json.dumps({
        "result": "OK" if found else "UNCERTAIN",
        "student_name": student_name,
        "inquiry_date": inquiry_date,
        "postal_code": postal_code,
        "phone": phone,
        "verified": found,
        "sent_grade": grade_val,
        "sent_schoolkb": schoolkb,
        "post_status": r.status_code,
        "server_response": resp_text,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_gaibusei_register(
    student_name: str,
    guardian_name: str,
    kana: str,
    grade: str,
    birth: str = "",
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

    必須フィールド: student_name, guardian_name, kana, grade。
    特に kana(フリガナ)は **サーバー側で必須バリデーションあり**。空だと登録失敗する。
    性別(sex)も実質必須だが空なら男(0)で送る。生年月日(birth)は IEB040 側では
    必須ではなく**任意**(空ならそのまま空で登録)。

    Args:
        student_name: 生徒氏名(例: "小川 夢禾")。必須。
        guardian_name: 保護者氏名(例: "小川 亮子")。フルネーム必須。
        kana: 生徒フリガナ(全角/半角カタカナ/ひらがな可、自動で半角カナへ変換)。**必須**。
        grade: 学年(例: "中学3年", "高校2年", "小学4年", "成人")。必須。
        birth: 生年月日(YYYY/MM/DD 例: "2010/04/15")。任意(空可)。
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

    # 生年月日(任意。IEB040 はフリガナのみ必須で生年月日は必須ではない。空ならスキップ)
    birth = (birth or "").strip()
    if birth:
        try:
            datetime.strptime(birth, "%Y/%m/%d")
        except ValueError:
            return json.dumps({
                "result": "NG", "error": f"invalid birth format: {birth}",
                "hint": "YYYY/MM/DD"
            }, ensure_ascii=False)
    datebirth = birth.replace("/", "") if birth else ""

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
    birth: str = "",
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

    必須: kana(フリガナ)。kana が空だとサーバー側バリデーションで弾かれて
    UNCERTAIN になる。生年月日(birth)は IEB040 側では必須ではなく**任意**(空可)。

    Args:
        inquiry_no: SKS問合せNO(例: "1184")
        kana: 生徒フリガナ(全角/半角カナ/ひらがな可)。**必須**。
        birth: 生年月日(YYYY/MM/DD)。任意(空可)。
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

    # 生年月日(任意。IEB040 はフリガナのみ必須。空ならスキップ)
    birth = (birth or "").strip()
    if birth:
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
if "example.internal" in SSK2_URL:
    # 未設定の場合は起動時警告のみ、実際のツール呼び出し時に明示エラー化する
    print(
        f"[SKS MCP WARN] SKS_SSK2_URL is the placeholder ({SSK2_URL!r}). "
        f"PCS tools will fail. Add to your env file (SKS_ENV_FILE) e.g. 'SKS_SSK2_URL=http://ssk2.tacsvpn'.",
        file=sys.stderr,
    )
_pcs_session_ready: bool = False


def _guard_ssk2_url() -> None:
    """PCS系ツール開始時に呼ぶ。SSK2_URLが未設定placeholderなら人間に読める例外を上げる。"""
    if "example.internal" in SSK2_URL:
        raise RuntimeError(
            "SKS_SSK2_URL is not configured (currently the default placeholder "
            f"{SSK2_URL!r}). PCS tools require a real SSK2 domain URL. "
            "Add e.g. 'SKS_SSK2_URL=http://ssk2.tacsvpn' to your env file "
            "(pointed to by SKS_ENV_FILE) and restart the MCP process."
        )


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

    _guard_ssk2_url()

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
def pcs_layout_info() -> str:
    """PCS系統図（PcsMenu.do）の 5列レイアウト・Y軸階層・「過去2年遡り」帯の
    意味・_NN小節の使い分け・選定チェックリスト を返す。

    **pcs_create_problem を呼ぶ前に必ず読むこと**。読まずに単元を選定すると
    ・Col3(応用系:文章問題/割合/比/平均/速さ) が丸ごと落ちる
    ・Col4(変化と関係:グラフ/変わり方) が抜ける
    ・小4を機械的に各章1個ずつ選んで下方向に広がりすぎる
    ・_31 ばかり選んで章丸ごと復習の意図が薄れる
    等の頻発事故が起きる（2026-07-19 布野兄弟のPCS作成で3回連続の指摘・修正）。

    内容は `~/.tact-mcp/sks_data/pcs_layout_notes.md` から読む。ファイルが
    無ければエラーメッセージを返す。
    """
    p = os.path.join(os.path.expanduser("~"), ".tact-mcp", "sks_data", "pcs_layout_notes.md")
    if not os.path.exists(p):
        return json.dumps({
            "result": "FAILED",
            "error": f"Layout notes file not found: {p}",
            "hint": "Ask the classroom operator or check the sks_pcs.html knowledge file48/file49",
        }, ensure_ascii=False)
    with open(p, encoding="utf-8") as f:
        return f.read()


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
def pcs_print_result(student_code: str, kyoukakb: str = "2",
                     score_flag: str = "1", bg_flag: str = "1") -> str:
    """PCSの結果帳票PDF（系統図形式 ○△×）をダウンロードして保存する。

    採点済みでないと結果帳票は出力できない（GET/POST・状態変化なし）。
    フロー: PcsPrintResult2.do?flg=1（選択画面・実施回数取得）
          → PcsPrintResult2.do?cmd=print&jisshikaisu=..（dlForm取得）
          → PcsPrintDownload.do に dlForm を POST（PDF本体）

    Args:
        student_code: 生徒番号（例: "260007"）
        kyoukakb: 教科コード（2=算数・数学, 3=英語）
        score_flag: 得点表示 1=あり / 0=なし（初回Iテストは表示されない）
        bg_flag: 1=A3白紙にグラフィック印刷 / 0=文字のみ（専用用紙向け）
    """
    s = _get_session()
    _pcs_establish_session(s, student_code, kyoukakb)

    # ① 選択画面（印刷コンテキスト確立＋実施回数の取得）
    sel = s.get(f"{SSK2_URL}/pcs/PcsPrintResult2.do?flg=1")
    soup = BeautifulSoup(sel.text, "html.parser")
    fm = soup.find("form", {"name": "formmain"})
    if not fm or "結果帳票" not in sel.text:
        return json.dumps({"result": "NO_FORM", "snippet": sel.text[:300]},
                          ensure_ascii=False)
    js_inp = fm.find("input", {"name": "jisshikaisu"})
    jisshikaisu = (js_inp.get("value") if js_inp else "") or "1"

    # ② cmd=print → ダウンロードフォーム(dlForm)を受け取る
    q = (f"cmd=print&jisshikaisu={jisshikaisu}&compTestFlag=0"
         f"&scoreFlag={score_flag}&kaisu=&bgFlag={bg_flag}")
    r = s.get(f"{SSK2_URL}/pcs/PcsPrintResult2.do?{q}")
    dl = BeautifulSoup(r.text, "html.parser").find("form", {"name": "dlForm"})
    if not dl:
        reason = "出力できませんでした（採点未済の可能性）" \
            if "出力できませんでした" in r.text else r.text[:200]
        return json.dumps({"result": "NOT_AVAILABLE", "reason": reason,
                           "jisshikaisu": jisshikaisu}, ensure_ascii=False)

    # ③ PcsPrintDownload.do に POST → PDF本体
    action = dl.get("action")
    data = {i.get("name"): i.get("value", "") for i in dl.find_all("input") if i.get("name")}
    pdf = s.post(f"{SSK2_URL}/pcs/{action}", data=data)
    if pdf.content[:4] != b"%PDF":
        return json.dumps({"result": "NOT_PDF",
                           "content_type": pdf.headers.get("Content-Type", ""),
                           "size": len(pdf.content)}, ensure_ascii=False)

    pdf_dir = os.path.join(os.path.expanduser("~"), "Documents", "pcs_pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    filename = f"kekka_{student_code}_kk{kyoukakb}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path = os.path.join(pdf_dir, filename)
    with open(pdf_path, "wb") as f:
        f.write(pdf.content)

    pages = 0
    try:
        from PyPDF2 import PdfReader
        pages = len(PdfReader(pdf_path).pages)
    except Exception:
        pass

    return json.dumps({
        "result": "OK", "path": pdf_path, "size": len(pdf.content),
        "pages": pages, "jisshikaisu": jisshikaisu,
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

    ⚠️ 単元選定前に `pcs_layout_info()` を呼ぶこと。系統図の 5列レイアウトと
    「過去2年遡り」帯の意味を理解しないと機械的な章羅列で偏る（Col3応用系や
    Col4変化と関係が丸ごと落ちる／小4を機械的に均等網羅して下方向に広がる
    等の頻発事故）。詳細は ~/.tact-mcp/sks_data/pcs_layout_notes.md 参照。

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

# 一部のフィールドは静的HTMLの value 属性に JS 関数呼び出しのプレースホルダが
# 直書きされている（例: <input name="entprice" value="getEntprice()">）。
# ブラウザは登録ボタン押下時に JS が実値へ置換して送るが、requests 経由では
# プレースホルダ文字列がそのまま POST され、サーバが値を解釈できず
# "E00002:データベースにてエラーが発生しました。" になる（どのフィールド更新でも失敗）。
# POST 前にこの種の値を空へ正規化する。
_IEB010_JS_CALL_RE = re.compile(r"^[A-Za-z_]\w*\s*\([^()]*\)$")


#: IEB010 で disabled でも POST payload に含める必要があるフィールド。
#: GUI の dopost_confirmed() が submit 直前に disabled=false にして送るので
#: サーバー側は実値を期待している。これらを payload から落とすと
#: wnkozakb 等の必須項目欠落 で E00002 になる。
_IEB010_DISABLED_INCLUDE = {
    "wnkozakb", "imwnstym", "wnstym", "keiyakushano",
    "ZenginCD", "zscdtsucho", "GinkoSM", "ShitenSM",
    "KozaSyubetsuKB", "KozaNO", "MeigininGinko",
    "YBTsuchoKigo", "YBTsuchoNO", "MeigininYubin",
    "ybdefkigo1", "ybdefkigo2", "ybdeftsuchono",
    "YubinCD", "yubincd",
    "seitograde", "premshubetsu", "premreason",
}


# ---------------------------------------------------------------------------
# IEB010 履歴系フィールドの構造的破壊防止 (2026-07-23 確立)
# ---------------------------------------------------------------------------
# サーバーの regist ハンドラは、payload の PRECAMV (現DB値) と campaign (新値)
# を単純比較して差があれば TBFNYUKAIKINJOHO の KYANSHUBETSU / RIYU / PRECAMV
# を UPDATE で SET する。パーサは form の HTML value 属性を素で拾うだけで、
# JS 実行後の値は知らない。新規入塾直後の生徒は PRECAMV が JS で挿入される
# 前の状態 (value="") で form が生成されるため、パーサが送る payload は
# {PRECAMV="", campaign="10"} または {PRECAMV="10", campaign=""} と対称性が
# 崩れ、サーバーが「変更あり」と判定して既存 DB 値を空に UPDATE=破壊する。
#
# 実証 (2026-07-23、岡田空優260015):
#   {"PRECAMV":"10","campaign":"","txtriyu1":""} → 履歴データ全消え
#   {"PRECAMV":"","campaign":"10","txtriyu1":"夏期チラシ"} → 完全復元
#
# 対策: ユーザーが明示的にキャンペーン変更を意図しない限り、以下の
# 「pair 対称化」で payload の (現値, 新値) を常に一致させ、no-op を保証する。
# これで通常の biko/氏名/住所等の更新では履歴フィールドを構造的に壊せない。

#: サーバーの差分判定に使われる (現DB値フィールド, 新値フィールド) のペア。
#: ユーザーが新値側を明示指定しなければ、現値をコピーして常に等しくする。
_IEB010_HISTORY_MIRROR_PAIRS = [
    ("PRECAMV", "campaign"),
]


def _protect_ieb010_history(data: dict, user_fields: dict) -> None:
    """IEB010 履歴系フィールドの構造的破壊防止 (対称化)。

    `data` は form から parse した payload。`user_fields` は sks_internal_
    update_fields 等の呼び出し側でユーザーが明示指定した field dict。

    ユーザーが pair のどちら (現値または新値) も明示指定していない場合は、
    サーバーが「変更なし」判定するよう新値 = 現値 に強制する。片方だけでも
    ユーザーが指定していれば意図的操作なのでそのまま通す (エスケープハッチ)。
    """
    for cur_key, new_key in _IEB010_HISTORY_MIRROR_PAIRS:
        if cur_key in user_fields or new_key in user_fields:
            # ユーザーが履歴変更を明示 → そのまま通す
            continue
        data[new_key] = data.get(cur_key, "")


def _ieb010_parse_form(html: str):
    """IEB010 のフォームを解析して (form_data, formmain_or_None) を返す。
    JS 注入値・imXXX→XXX のスラッシュ除去コピーまで補完する。

    ⚠️ disabled input/select は、GUI が JS で明示的に enable にして送る
    ホワイトリスト (`_IEB010_DISABLED_INCLUDE`) 以外は payload から除外する。
    それらを空で送るとサーバーが既存値を空更新して破壊するため。
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
        if inp.has_attr("disabled") and name not in _IEB010_DISABLED_INCLUDE:
            continue
        itype = (inp.get("type") or "text").lower()
        # button/submit/reset/image は GUI が form.submit() で送らない (クリックされて
        # いない限り送信対象外)。これを含めるとサーバー Perl が submitButtonName='検索'
        # を見て「これは検索リクエスト」と誤判定し、cmd=regist を silent にスキップして
        # DB を更新しない。レスポンスには送信値を echo するので反映されたように見える。
        # 2026-07-23 岡田空優260015 の学校/姉更新で発覚。
        if itype in ("button", "submit", "reset", "image"):
            continue
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
        if sel.has_attr("disabled") and name not in _IEB010_DISABLED_INCLUDE:
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

    # JS 関数呼び出しプレースホルダ（例: entprice="getEntprice()"）を空へ正規化。
    # requests は JS を実行できないため、実値化されないまま送ると E00002 になる。
    for _k, _v in list(data.items()):
        if isinstance(_v, str) and _IEB010_JS_CALL_RE.match(_v.strip()):
            data[_k] = ""

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

    # entprice (入会金) が空だと Oracle UPDATE TBFNYUKAIKINJOHO SET NYUKAIKIN=, ...
    # で ORA-00936 (式がありません) → E00002。JS プレースホルダ getEntprice() が
    # _IEB010_JS_CALL_RE で "" に正規化されるため、新規入塾直後の生徒で発生する。
    # 数値列なので 0 に補完する。
    if not data.get("entprice"):
        data["entprice"] = "0"

    # 履歴系フィールド (PRECAMV/campaign) の対称化 = 構造的破壊防止
    _protect_ieb010_history(data, fields)

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

    # 4. 反映確認: POST レスポンスの form value は「送信値の echo」を含むため、
    # サーバーが silent に SET をスキップしても "OK" と誤判定される。真の反映は
    # 別 GET で fresh reload して DB 値を取得する必要がある (2026-07-23 岡田君で発覚)。
    after_data, _ = _ieb010_load(seitocd)
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


def _student_record(seitocd: str, include_taijuku: bool = False) -> dict | None:
    """名簿一覧(IEB030)から生徒コード一致のレコードを返す。

    Args:
        seitocd: 生徒コード
        include_taijuku: True で退塾者も検索対象に含める(再塾処理 rireki=04 用)。
            デフォルトは False(在籍者のみ) — sks_jugyo_register で退塾生に誤って
            新規コースを入れる事故を防ぐガード。
    """
    s = _get_session()
    s.get(f"{BASE_URL}/service/IEB030.wpp")
    data = {
        "mode": "if", "cols": _COLS_NAIBU, "selseitolist": "",
        "seitokm": "", "seitosm": "", "seitograde": "",
        "listcount": "", "seitokb": "naibu",
    }
    if not include_taijuku:
        data["taijuku"] = "1"  # 退塾者除外(既定)
    r = s.post(f"{BASE_URL}/service/IEB030.wpp", data=data)
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

    受講履歴(通常授業開始 / 再塾 / コース変更 / 選択科目変更)を追加し、コース情報
    (コース名/回数/科目)を登録する。金額は 単価×回数−割引 で自動算出する。

    ・rireki='02' 通常授業開始: 新規入会生の初コース登録(在籍者のみ)
    ・rireki='04' 再塾: 退塾済み生徒の復塾処理(退塾者名簿から自動検索。退塾日は履歴として残り、
                    再塾日が新たに設定される)
    ・rireki='05' コース変更: 学年進級・週回数変更等(履歴行→新通番→現学年コース表)
    ・rireki='06' 選択科目変更

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
    # rireki=04(再塾) の時は 退塾者名簿も検索対象に含める(復塾処理)。
    # 他の rireki は 在籍者のみ(誤って退塾生に新規コースを入れる事故を防ぐ)。
    rec = _student_record(seitocd, include_taijuku=(rireki == "04"))
    if rec is None:
        hint = ""
        if rireki != "04":
            hint = "（退塾済み生徒の場合は rireki='04'(再塾)を指定してください）"
        return json.dumps({"result": "NG",
                           "error": f"生徒コード {seitocd} が名簿に見つかりません{hint}"},
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

    # entprice (入会金) が空だと Oracle UPDATE TBFNYUKAIKINJOHO SET NYUKAIKIN=, ...
    # で ORA-00936 (式がありません) → E00002。JS プレースホルダ getEntprice() が
    # _IEB010_JS_CALL_RE で "" に正規化されるため、新規入塾直後の生徒で発生する。
    # 数値列なので 0 に補完する。
    if not data.get("entprice"):
        data["entprice"] = "0"

    # 履歴系フィールド (PRECAMV/campaign) の対称化 = 構造的破壊防止
    _protect_ieb010_history(data, additional_fields or {})

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


def _ieb010_load_from_inquiry(inquiry_no: str, kyoshitsucd: str | None = None):
    """問合せNoを指定して IEB010 をロード（mode=toiawase＝「問合せ管理起動→呼び出し」相当）。
    問合せの氏名・住所・電話・保護者氏名が自動転記された state の form を返す。
    Returns: (data: dict, html: str)
    """
    s = _get_session()
    kyo = kyoshitsucd or CLASSROOM
    r = s.get(
        f"{BASE_URL}/service/IEB010.wpp",
        params={"mode": "toiawase", "kyoshitsucd": kyo, "seitocd": f"{kyo}:{inquiry_no}"},
    )
    r.encoding = "utf-8"
    html = r.text
    data, _form = _ieb010_parse_form(html)
    return data, html


_SEX_TO_CODE = {"女": "1", "男": "0", "1": "1", "0": "0"}

# テンプレ生徒（兄弟等）から「同じ」でコピーする 家族・引落・連絡系フィールド
_TEMPLATE_COPY_FIELDS = (
    "rgfamily",
    "r1name", "r1old", "r1work", "r1zokugara",
    "r2name", "r2old", "r2work", "r2zokugara",
    "r3name", "r3old", "r3work", "r3zokugara",
    "r4name", "r4old", "r4work", "r4zokugara",
    "wnkozakb", "ZenginCD", "zscdtsucho", "KozaNO", "MeigininGinko", "KozaSyubetsuKB",
    "YubinCD", "YBTsuchoKigo", "YBTsuchoNO", "MeigininYubin", "ybdefkigo1", "ybdefkigo2", "ybdeftsuchono",
    "hogoshamail", "emtelno", "emdest", "biko",
)


@mcp.tool()
def sks_naibusei_register_from_inquiry(
    inquiry_no: str,
    kana: str,
    sex: str,
    birth: str,
    grade: str,
    nyujukudt: str,
    school_code: str = "",
    school_name: str = "",
    template_seitocd: str = "",
    campaign: str = "",
    campaign_reason: str = "",
    premshubetsu: str = _YSPC_DEFAULT_PREMSHUBETSU,
    premreason: str = _YSPC_DEFAULT_PREMREASON,
    additional_fields: dict | None = None,
    kyoshitsucd: str | None = None,
    dry_run: bool = False,
) -> str:
    """SKS問合せデータから内部生(IEB010)として直接登録する。GUI「問合せ管理起動→呼び出し→追加/修正」と等価。

    外部生登録(IEB040)を経ていない問合せ生の入会登録ルート（外部生→内部生 変換とは別）。
    問合せ管理起動(mode=toiawase)で IEB010 に問合せをロード → 氏名/住所/電話/保護者氏名は自動転記 →
    フリガナ・性別・生年月日・学年・学校・入会日 を補完 → cmd=regist。
    失敗時は E00002（新規レコードは作られない）。成功時は新 seitocd を返す。

    Args:
        inquiry_no: SKS問合せNo（例 "1216"。sks_inquiry_search で得られる No）。
        kana: 生徒フリガナ（半角カナ推奨、例 "ﾐﾔｶﾞﾜ ﾏｲ"）。
        sex: 性別（"女"/"男" または "1"/"0"）。
        birth: 生年月日 YYYY/MM/DD。
        grade: 入会時学年コード（07=小1…12=小6, 13=中1…15=中3, 16=高1…18=高3, 19=成人, 99=その他）。
        nyujukudt: 入会日（契約日）YYYY/MM/DD。
        school_code: 出身校コード10桁（IEM050 学校一覧。7桁目 1=小/2=中/3=高。例 星美学園小=0000001203）。任意。
        school_name: 出身校名（school_code とペア）。任意。
        template_seitocd: 兄弟等「同じ」でコピーする既存内部生の生徒コード。
            指定するとその生徒の 家族(r1-4)・引落口座・保護者メール等をコピーする（兄弟入会向け）。
        campaign: キャンペーン種別の表示名（"兄弟"/"ペア入塾"/"チラシ割引"等。空で未選択）。
        campaign_reason: 理由テキスト（txtriyu1）。
        premshubetsu/premreason: YSPC証憑ダイアログ肩代わり既定（4=未登録/2=証憑未取得）。
        additional_fields: その他の上書き（dict）。組(nyukaikumi)・引落開始年月(imwnstym)等はここで。
        kyoshitsucd: 教室コード。空なら環境変数。
        dry_run: True なら POST せず送信予定データを返す。

    ※ 授業登録(コース/科目/回数)・引落開始年月(imwnstym)・YSPC本登録 は本ツールの対象外（別途）。
    """
    # 1. 問合せをロード
    data, html = _ieb010_load_from_inquiry(inquiry_no, kyoshitsucd)
    if data is None or not data.get("seitosm"):
        return json.dumps({
            "result": "NG",
            "error": "問合せがロードできなかった（No違い or セッション切れ？）",
            "inquiry_no": inquiry_no,
            "html_preview": (html or "")[:300],
        }, ensure_ascii=False, indent=2)

    # 2. テンプレ生徒（兄弟等）の家族・引落をコピー
    if template_seitocd:
        tdata, _ = _ieb010_load(template_seitocd)
        if not tdata:
            return json.dumps({
                "result": "NG",
                "error": f"template_seitocd={template_seitocd} がロードできなかった",
            }, ensure_ascii=False, indent=2)
        for k in _TEMPLATE_COPY_FIELDS:
            if tdata.get(k) not in (None, ""):
                data[k] = tdata[k]

    # 3. 固有フィールドを補完
    data["seitokm"] = kana
    data["seitosex"] = _SEX_TO_CODE.get(str(sex), "0")
    data["imdatebirth"] = birth
    data["seitograde"] = grade
    data["imnyujukudt"] = nyujukudt
    data["entryyear"] = nyujukudt[:4] if len(nyujukudt) >= 4 else data.get("entryyear", "")
    if school_code:
        data["hshogaku"] = school_code
    if school_name:
        data["shogaku"] = school_name

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

    data["premshubetsu"] = premshubetsu
    data["premreason"] = premreason

    if additional_fields:
        for k, v in additional_fields.items():
            data[k] = "" if v is None else str(v)

    # 4. 送信前正規化（im → 裏 hidden、postalcd ハイフン除去）
    if data.get("impostalcd"):
        data["postalcd"] = data["impostalcd"].replace("-", "")
    if data.get("imnyujukudt"):
        data["nyujukudt"] = data["imnyujukudt"].replace("/", "")
    if data.get("imdatebirth"):
        data["datebirth"] = data["imdatebirth"].replace("/", "")

    # entprice (入会金) が空だと Oracle UPDATE TBFNYUKAIKINJOHO SET NYUKAIKIN=, ...
    # で ORA-00936 (式がありません) → E00002。JS プレースホルダ getEntprice() が
    # _IEB010_JS_CALL_RE で "" に正規化されるため、新規入塾直後の生徒で発生する。
    # 数値列なので 0 に補完する。
    if not data.get("entprice"):
        data["entprice"] = "0"

    # 履歴系フィールド (PRECAMV/campaign) の対称化 = 構造的破壊防止
    _protect_ieb010_history(data, additional_fields or {})

    data["cmd"] = "regist"
    data.setdefault("TORIKOMIFLG", "1")
    data.setdefault("ToiawaseNO", str(inquiry_no))
    data.setdefault("ToiawaseFLG", "1")

    if dry_run:
        return json.dumps({
            "result": "DRYRUN",
            "inquiry_no": inquiry_no,
            "preview": {k: data.get(k) for k in (
                "seitosm", "seitokm", "seitosex", "seitograde", "imdatebirth", "imnyujukudt",
                "hshogaku", "shogaku", "ad1", "ad2", "telno",
                "rgfamily", "r1name", "r1zokugara", "r2name", "r3name",
                "wnkozakb", "KozaNO", "campaign", "premshubetsu", "premreason",
            )},
        }, ensure_ascii=False, indent=2)

    # 5. POST cmd=regist
    s = _get_session()
    r = s.post(f"{BASE_URL}/service/IEB010.wpp", data=data)
    r.encoding = "utf-8"
    html2 = r.text

    err = _ieb010_extract_error(html2)
    if err:
        return json.dumps({
            "result": "NG", "error": err, "inquiry_no": inquiry_no,
        }, ensure_ascii=False, indent=2)

    after_data, _ = _ieb010_parse_form(html2)
    new_seitocd = (after_data or {}).get("seitocd", "")
    keiyakushano = (after_data or {}).get("keiyakushano", "")
    if not new_seitocd:
        return json.dumps({
            "result": "UNKNOWN", "inquiry_no": inquiry_no,
            "msg": "登録応答に seitocd がない。GUI で確認推奨",
        }, ensure_ascii=False, indent=2)

    return json.dumps({
        "result": "OK",
        "inquiry_no": inquiry_no,
        "new_seitocd": new_seitocd,
        "keiyakushano": keiyakushano,
        "seitosm": (after_data or {}).get("seitosm", ""),
        "note": "授業登録・引落開始年月・YSPC本登録は別途",
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


# ===========================================================================
# IEB240 調整入力（返金・追加徴収）
# ===========================================================================
# GUIの doreload / dopost_confirm / dodelete を HTTP で再現する。
#
# 重要な挙動（2026-06 実機調査）:
#  - 調整区分 choseikb: "0"=次回カード/WN反映(処理年月=wnym=翌WN月) /
#    "1"=振込(処理年月=frym=当月)。
#  - 生徒ロードは formmain を POST（doreload 相当）。formmain.choseikb の値で
#    処理年月が切り替わる。choseikb="1"(振込) で再ロードすると、サーバが該当
#    処理年月(frym)の既存振込調整を fmpost に展開し、削除モードになる。
#  - 登録は fmpost に mode="regist"、削除は mode="delete" を入れて fmpost を POST。
#  - 振込返金の口座情報は生徒の登録口座が自動展開される。
#  - 返金理由日(henkinriyudt)は返金時必須。POST 時は日付のスラッシュを除去
#    (YYYY/MM/DD→YYYYMMDD, YYYY/MM→YYYYMM)。
#  - 注意: ログインパスワードの AES 暗号化やセッションは _get_session() を共用。
#    GUIブラウザのセッションを注入しない（別セッションで独立動作させる）。

_CHOSEI_KINGAKU = {
    "10": "授業料", "20": "入会金", "30": "維持管理費", "40": "基礎教材費",
    "50": "別途教材費", "60": "テスト費", "70": "その他",
    "81": "春期講習会費", "82": "夏期講習会費", "83": "冬期講習会費",
    "91": "講習会費(テキスト代)", "92": "講習会費OP(テスト費)", "93": "講習会費OP(ファイル代)",
}
_CHOSEI_NAME_TO_CODE = {
    "授業料": "10", "入会金": "20", "維持管理費": "30", "基礎教材費": "40",
    "別途教材費": "50", "テスト費": "60", "その他": "70",
    "春期講習会費": "81", "夏期講習会費": "82", "冬期講習会費": "83",
}


def _ieb240_parse_form(html: str, formname: str):
    """IEB240 の指定フォームを name->value で返す（HTML値＋getElementById注入値）。
    JS注入は _IEB010_JS_VALUE_RE（getElementById限定）で拾うため、関数本体の
    `fmpost.mode.value="delete"` 等は混入しない。"""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"name": formname})
    if not form:
        return None
    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("radio", "checkbox"):
            if inp.has_attr("checked"):
                data[name] = inp.get("value", "")
        elif itype in ("button", "submit", "image", "reset"):
            continue
        else:
            data[name] = inp.get("value", "") or ""
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        data[name] = opt.get("value", "") if opt else ""
    for ta in form.find_all("textarea"):
        if ta.get("name"):
            data[ta["name"]] = ta.get_text() or ""
    for m in _IEB010_JS_VALUE_RE.finditer(html):
        data[m.group(1)] = m.group(2)
    return data


def _ieb240_frym_wnym(html: str):
    """ロード済みHTMLから frym(振込の処理年月) / wnym(WNの処理年月) を抽出。"""
    fr = re.search(r"\bfrym\s*=\s*'(\d{6})'", html)
    wn = re.search(r"\bwnym\s*=\s*'(\d{6})'", html)
    return (fr.group(1) if fr else None), (wn.group(1) if wn else None)


def _ieb240_load(seitocd: str, seitoshubetsu: str = "0", choseikb: str = "0",
                 shoriym: str | None = None):
    """IEB240 に生徒をロード（doreload 相当）。戻り値=ロード後HTML。
    choseikb="1"(振込)+shoriym(YYYY/MM) で既存の振込調整を展開できる。"""
    s = _get_session()
    r0 = s.get(f"{BASE_URL}/service/IEB240.wpp")
    r0.encoding = "utf-8"
    fm = _ieb240_parse_form(r0.text, "formmain")
    if fm is None:
        raise Exception("IEB240: formmain が見つかりません（セッション切れ?）")
    fm["seitocd"] = seitocd
    fm["seitoshubetsu"] = seitoshubetsu
    fm["choseikb"] = choseikb
    fm["mode"] = ""
    if shoriym:
        fm["shoriym"] = shoriym
    r = s.post(f"{BASE_URL}/service/IEB240.wpp", data=fm)
    r.encoding = "utf-8"
    return r.text


def _ieb240_existing(html: str) -> dict:
    """ロード済みHTMLの既存調整情報（stat_prev・金額・氏名・口座）。"""
    sp = re.search(r'var\s+stat_prev\s*=\s*"([^"]*)"', html)
    fp = _ieb240_parse_form(html, "fmpost") or {}
    fmain = _ieb240_parse_form(html, "formmain") or {}
    kingaku = {c: fp.get(f"KINGAKU_{c}", "") for c in _CHOSEI_KINGAKU
               if fp.get(f"KINGAKU_{c}")}
    return {
        "seitocd": fmain.get("seitocd"), "seitosm": fmain.get("seitosm"),
        "seitograde": fmain.get("seitograde"), "wnstatus": fmain.get("wnstatus"),
        "stat_prev": sp.group(1) if sp else "",
        "stat_prev_label": {"0": "返金", "1": "追加徴収"}.get(sp.group(1) if sp else "", "なし"),
        "has_adjustment": bool(kingaku),
        "kingaku": {f"{c}:{_CHOSEI_KINGAKU[c]}": v for c, v in kingaku.items()},
        "henkinriyu": fp.get("henkinriyu"),
        "henkinriyudt": fp.get("imhenkinriyudt"),
        "shoriym": fp.get("shoriym"),
        "koza": {"ginko": fp.get("ginkosm"), "shiten": fp.get("shitensm"),
                 "kozano": fp.get("kozano"), "meigi": fp.get("meiginin")},
    }


def _ieb240_result_msg(html: str):
    """登録/削除レスポンスから実メッセージ（ソースコード中のalertは除外）を抽出。"""
    # 完了表示は本文テーブルに現れる。関数定義中の alert("...") は除外したいので
    # 「登録しました」「削除しました」等の固定句のみを本文出現で判定する。
    if "登録しました" in html:
        return True, "登録しました"
    if "削除しました" in html:
        return True, "削除しました"
    m = re.search(r"E\d{5}[:：][^<\n]+", html)
    if m:
        return False, m.group(0).strip()
    return None, None


def _choseikb_code(v: str) -> str:
    return {"furikomi": "1", "振込": "1", "1": "1",
            "wn": "0", "ワイドネット": "0", "カード": "0", "0": "0"}.get(str(v), "1")


@mcp.tool()
def sks_chousei_get(seitocd: str, choseikb: str = "furikomi",
                    seitoshubetsu: str = "naibu") -> str:
    """SKS 調整入力(IEB240)で生徒をロードし、既存の調整（返金/追加徴収）を確認する。

    Args:
        seitocd: 生徒コード（例 "240006"）
        choseikb: 調整区分 "furikomi"(振込・当月) / "wn"(次回カード/WN反映・翌月)。
                  既存の振込調整を見るには "furikomi"。
        seitoshubetsu: "naibu"(内部生) / "gaibu"(外部生)
    """
    ss = "1" if seitoshubetsu == "gaibu" else "0"
    kb = _choseikb_code(choseikb)
    html = _ieb240_load(seitocd, ss, kb)
    frym, wnym = _ieb240_frym_wnym(html)
    shoriym = frym if kb == "1" else wnym
    if shoriym:
        html = _ieb240_load(seitocd, ss, kb, f"{shoriym[:4]}/{shoriym[4:]}")
    ex = _ieb240_existing(html)
    ex["frym"] = frym
    ex["wnym"] = wnym
    return json.dumps(ex, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_chousei_register(seitocd: str, kingaku: dict, henkin_riyu: str = "退塾",
                         henkin_riyudt: str = "", choseikb: str = "furikomi",
                         seitoshubetsu: str = "naibu", tesuryo_futan: str = "先方",
                         confirm: bool = False) -> str:
    """SKS 調整入力(IEB240)に【返金】を登録する（dopost_confirm 相当, mode=regist）。

    金銭処理。confirm=True が必須（未指定なら登録せず内容プレビューを返す）。

    Args:
        seitocd: 生徒コード
        kingaku: 科目別の返金額。科目コードまたは科目名→金額の dict。
                 例 {"30": 7800, "40": 6240} / {"維持管理費": 7800, "基礎教材費": 6240}
                 （コード: 10授業料/20入会金/30維持管理費/40基礎教材費/50別途教材費/
                  60テスト費/70その他/81春期/82夏期/83冬期/91テキスト代/92OPテスト/93OPファイル）
        henkin_riyu: 返金理由（退塾/休塾/コース変更/週回数変更/請求間違い）
        henkin_riyudt: 返金理由日 "YYYY/MM/DD"（返金は必須。実質退塾日でよい）
        choseikb: "furikomi"(振込) / "wn"(次回カード/WN反映)。退塾者は振込が基本
        seitoshubetsu: "naibu" / "gaibu"
        tesuryo_futan: 振込手数料負担 "先方" / "当方"
        confirm: True で実際に登録。False は登録せずプレビュー
    """
    ss = "1" if seitoshubetsu == "gaibu" else "0"
    kb = _choseikb_code(choseikb)
    tesu = "1" if tesuryo_futan == "先方" else "0"

    # 科目コードに正規化
    norm: dict[str, str] = {}
    for k, v in (kingaku or {}).items():
        code = str(k) if str(k) in _CHOSEI_KINGAKU else _CHOSEI_NAME_TO_CODE.get(str(k))
        if not code:
            return json.dumps({"ok": False, "error": f"未知の科目: {k}"}, ensure_ascii=False)
        norm[code] = str(int(v))
    if not norm:
        return json.dumps({"ok": False, "error": "kingaku が空です"}, ensure_ascii=False)
    if henkin_riyu and not henkin_riyudt:
        return json.dumps({"ok": False, "error": "返金は henkin_riyudt(返金理由日) が必須"},
                          ensure_ascii=False)

    # 処理年月を確定するため一度ロード → frym/wnym
    pre = _ieb240_load(seitocd, ss, kb)
    frym, wnym = _ieb240_frym_wnym(pre)
    shoriym = frym if kb == "1" else wnym
    if not shoriym:
        return json.dumps({"ok": False, "error": "処理年月(frym/wnym)を特定できません"},
                          ensure_ascii=False)
    html = _ieb240_load(seitocd, ss, kb, f"{shoriym[:4]}/{shoriym[4:]}")
    fp = _ieb240_parse_form(html, "fmpost")
    if fp is None:
        return json.dumps({"ok": False, "error": "fmpost が見つかりません"}, ensure_ascii=False)

    total = 0
    for c in _CHOSEI_KINGAKU:
        fp[f"KINGAKU_{c}"] = norm.get(c, "")
        if norm.get(c):
            total += int(norm[c])
    fp["choseisum"] = str(total)
    fp["shohizei"] = "0"
    fp["gokei"] = str(total)
    fp["mode"] = "regist"
    fp["choseikeitai"] = "0"      # 返金
    fp["choseikb"] = kb
    fp["seitoshubetsu"] = ss
    fp["seitocd"] = seitocd
    fp["henkinriyu"] = henkin_riyu
    fp["imhenkinriyudt"] = henkin_riyudt
    fp["henkinriyudt"] = henkin_riyudt.replace("/", "")
    fp["tesuryokb"] = tesu
    fp["shoriym"] = shoriym
    fp["hikiotoshiym"] = (fp.get("hikiotoshiym", "") or "").replace("/", "")
    for src, dst in (("imfurikomidt", "furikomidt"), ("imhenkindt", "henkindt"),
                     ("imshiharaidt", "shiharaidt")):
        fp[dst] = (fp.get(src, "") or "").replace("/", "")

    preview = {
        "seitocd": seitocd, "choseikeitai": "返金", "choseikb": "振込" if kb == "1" else "WN/カード反映",
        "shoriym": shoriym, "kingaku": {f"{c}:{_CHOSEI_KINGAKU[c]}": norm[c] for c in norm},
        "gokei": total, "henkin_riyu": henkin_riyu, "henkin_riyudt": henkin_riyudt,
        "tesuryo_futan": tesuryo_futan,
        "koza": {"ginko": fp.get("ginkosm"), "kozano": fp.get("kozano"), "meigi": fp.get("meiginin")},
    }
    if not confirm:
        return json.dumps({"ok": None, "preview": preview,
                           "note": "confirm=True で登録します"}, ensure_ascii=False, indent=2)

    s = _get_session()
    r = s.post(f"{BASE_URL}/service/IEB240.wpp", data=fp)
    r.encoding = "utf-8"
    ok, msg = _ieb240_result_msg(r.text)
    return json.dumps({"ok": bool(ok), "message": msg, "registered": preview},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def sks_chousei_delete(seitocd: str, choseikb: str = "furikomi",
                       seitoshubetsu: str = "naibu", confirm: bool = False) -> str:
    """SKS 調整入力(IEB240)の既存調整を【削除】する（dodelete 相当, mode=delete）。

    金銭処理。confirm=True が必須。choseikb は既存調整の登録区分に合わせる
    （振込で登録した調整は "furikomi"）。

    Args:
        seitocd: 生徒コード
        choseikb: "furikomi"(振込) / "wn"(次回カード/WN反映)
        seitoshubetsu: "naibu" / "gaibu"
        confirm: True で実際に削除。False は削除対象の確認のみ
    """
    ss = "1" if seitoshubetsu == "gaibu" else "0"
    kb = _choseikb_code(choseikb)
    pre = _ieb240_load(seitocd, ss, kb)
    frym, wnym = _ieb240_frym_wnym(pre)
    shoriym = frym if kb == "1" else wnym
    if not shoriym:
        return json.dumps({"ok": False, "error": "処理年月(frym/wnym)を特定できません"},
                          ensure_ascii=False)
    html = _ieb240_load(seitocd, ss, kb, f"{shoriym[:4]}/{shoriym[4:]}")
    ex = _ieb240_existing(html)
    if not (ex["stat_prev"] in ("0", "1") and ex["has_adjustment"]):
        return json.dumps({"ok": False, "error": "削除対象の既存調整が見つかりません",
                           "loaded": ex}, ensure_ascii=False, indent=2)
    if not confirm:
        return json.dumps({"ok": None, "target": ex, "note": "confirm=True で削除します"},
                          ensure_ascii=False, indent=2)

    fp = _ieb240_parse_form(html, "fmpost")
    fmain = _ieb240_parse_form(html, "formmain") or {}
    fp["mode"] = "delete"
    fp["choseikeitai"] = ex["stat_prev"]       # 0=返金 / 1=追加徴収
    fp["choseikb"] = kb
    fp["seitoshubetsu"] = ss
    fp["seitocd"] = seitocd
    fp["shoriym"] = shoriym
    fp["hikiotoshiym"] = (fp.get("hikiotoshiym", "") or "").replace("/", "")
    for src, dst in (("imfurikomidt", "furikomidt"), ("imhenkindt", "henkindt"),
                     ("imshiharaidt", "shiharaidt"), ("imhenkinriyudt", "henkinriyudt")):
        fp[dst] = (fp.get(src, "") or "").replace("/", "")

    s = _get_session()
    r = s.post(f"{BASE_URL}/service/IEB240.wpp", data=fp)
    r.encoding = "utf-8"
    # 削除確認: 再ロードして既存調整が消えたか
    after = _ieb240_existing(_ieb240_load(seitocd, ss, kb, f"{shoriym[:4]}/{shoriym[4:]}"))
    deleted = not after["has_adjustment"]
    return json.dumps({"ok": deleted, "deleted_target": ex,
                       "after": {"has_adjustment": after["has_adjustment"],
                                 "stat_prev": after["stat_prev"]}},
                      ensure_ascii=False, indent=2)


# =====================
# IEB250 月謝台帳出力（Excel自動DL + パース）
# =====================

def _ieb250_parse(html: str, target_seitocd: str | None = None) -> dict:
    """IEB250 Excel(=HTML)レスポンスをパースして月謝台帳を構造化。

    SKSが返すのは Content-Type: application/vnd.ms-excel の HTML(Excel互換)。
    各生徒ブロックの構成は、まず科目ラベルが縦に21行並び、続いて月ヘッダ行
    （YYYY年MM月×12 + 合計）、その後同順で21科目分の数値行が並ぶ。

    戻り値: {"students":[{"seitocd","name","grade","months","rows","total"}, ...]}
    rows[科目] = 月別配列 (None=空表示, 整数=金額)。
    target_seitocd 指定でその生徒のみ返す。
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    markers: list[tuple[int, str, str, str]] = []
    for i, tbl in enumerate(tables):
        cells = [td.get_text(strip=True) for td in tbl.find_all(["td", "th"])]
        if len(cells) >= 3 and cells[0] == "生徒番号" and cells[1].startswith("'"):
            code = cells[1][1:]
            m = re.match(r"^(.*?)\s*【(.+?)】$", cells[2])
            name = m.group(1) if m else cells[2]
            grade = m.group(2) if m else ""
            markers.append((i, code, name, grade))

    def _num(s: str):
        if s is None:
            return None
        s = s.strip()
        if not s or s == "・":
            return None
        try:
            return int(s.replace(",", ""))
        except ValueError:
            return None

    students = []
    for j, (mi, code, name, grade) in enumerate(markers):
        if target_seitocd and code != target_seitocd:
            continue
        end_i = markers[j + 1][0] if j + 1 < len(markers) else len(tables)
        # ブロック内の全 tr を、入れ子テーブルが原因で重複しないよう浅く拾う
        all_trs = []
        for tbl in tables[mi:end_i]:
            for tr in tbl.find_all("tr", recursive=True):
                all_trs.append(tr)
        # tr ごとに直下の td/th だけ取得（入れ子tableのtrは別途処理されるので除外）
        trs_cells: list[list[str]] = []
        for tr in all_trs:
            # tr の直下 td/th のみ（深い tr は別途登場するので excluded）
            tds = tr.find_all(["td", "th"], recursive=False)
            cells = [td.get_text(strip=True) for td in tds]
            if any(c.strip() for c in cells):
                trs_cells.append(cells)

        # 月ヘッダ行を探す
        header_idx = -1
        months: list[str] = []
        for ti, cs in enumerate(trs_cells):
            cs_strip = [c.strip() for c in cs]
            ms = [c for c in cs_strip if re.match(r"^\d{4}年\d{2}月$", c)]
            if len(ms) >= 6 and any(c in ("合　計", "合計") for c in cs_strip):
                header_idx = ti
                months = ms
                break
        if header_idx < 0:
            continue

        # ラベル列: 月ヘッダ直前までの単独セル行
        labels: list[str] = []
        for cs in trs_cells[:header_idx]:
            non_empty = [c.strip() for c in cs if c.strip()]
            if len(non_empty) != 1:
                continue
            lab = non_empty[0].replace("　", "").replace(" ", "")
            if lab == "科目":
                continue
            labels.append(lab)

        # 数値行: header_idx+1 以降を順に、labels に対応させる
        # 列数(月+合計=13) の行を数値行扱い。値が全て None でも空表示の行として残す
        rows: dict[str, list[int | None]] = {}
        totals: dict[str, int] = {}
        n_cols = len(months) + 1
        idx = 0
        for cs in trs_cells[header_idx + 1:]:
            if idx >= len(labels):
                break
            if len(cs) != n_cols:
                continue
            vals = [_num(cs[k]) for k in range(len(months))]
            tot = _num(cs[len(months)])
            label = labels[idx]
            rows[label] = vals
            if tot is not None:
                totals[label] = tot
            idx += 1

        students.append({
            "seitocd": code, "name": name, "grade": grade,
            "months": months, "rows": rows, "total": totals,
        })
    return {"students": students}


@mcp.tool()
def sks_gessyadaicho_export(
    taishoym: str,
    seitocds: str = "",
    kubun: str = "naibu",
    include_taijuku: bool = False,
    save_path: str = "",
    parse: bool = True,
) -> str:
    """SKS IEB250「月謝台帳出力」をExcel自動DLし、構造化して返す。

    SKSメニューの IEB250 をブラウザGUIで操作する代わりに、内部のAjax/POST APIを
    直接叩いて Excel（HTML-Excel）を取得する。

    Args:
        taishoym: 対象年月 YYYY/MM または YYYYMM。**12ヶ月表示窓の開始月**。
                  入会時(過去)の請求を見たければ入会月を指定する。
        seitocds: カンマ区切りの生徒コード列。空なら全件。例 "260006,260002"
        kubun: "naibu"=内部生 / "gaibu"=外部生
        include_taijuku: True=退塾者も含める
        save_path: 保存先パス（空なら保存せず構造化結果のみ返す）
        parse: True=HTMLをパースしてJSONで返す / False=保存パスのみ返す

    Returns:
        JSON: {"ok": True, "taishoym": "...", "students": [...], "saved": "<path>"}
        - 各 student: {"seitocd","name","grade","months","rows","total"}
        - rows[科目] = 月別配列 (None=非表示, 0=ゼロ, 整数=金額)
    """
    ym_slash = taishoym if "/" in taishoym else f"{taishoym[:4]}/{taishoym[4:]}"
    ym_str = ym_slash.replace("/", "")
    if not re.match(r"^\d{6}$", ym_str):
        return json.dumps({"ok": False, "error": f"invalid taishoym: {taishoym}"},
                          ensure_ascii=False)

    sess = _get_session()
    # 1. ページ初期化（セッション確立）
    sess.get(f"{BASE_URL}/service/IEB250.wpp")

    # 2. Ajax で生徒一覧取得
    # param = VIEW|YYYYMM|区分(0:内/1:外)|grade|notaijuku|sortv1|sortv2
    kubun_code = "0" if kubun == "naibu" else "1"
    notaijuku = "0" if include_taijuku else "1"
    ax_param = f"VIEW|{ym_str}|{kubun_code}||{notaijuku}|0|0"
    r_ax = sess.get(f"{BASE_URL}/service/IEB250.wpp",
                    params={"cmd": "ax", "param": ax_param})
    codes_all = re.findall(r'sel0\(this,&quot;(\d+)&quot;', r_ax.text) \
        or re.findall(r'sel0\(this,"(\d+)"', r_ax.text)
    codes_all = list(dict.fromkeys(codes_all))

    # 3. 出力対象を決定
    if seitocds.strip():
        wanted = [c.strip() for c in seitocds.split(",") if c.strip()]
        # 一覧に無い生徒（退塾済等）も指定可能。SKSは個別にも応答する
        codes_post = wanted
    else:
        codes_post = codes_all

    if not codes_post:
        return json.dumps({"ok": False, "error": "no students to export",
                           "ax_count": len(codes_all)},
                          ensure_ascii=False)

    # 4. fmp フォームで Excel POST
    r_xls = sess.post(f"{BASE_URL}/service/IEB250.wpp?excel",
                      data={"mode": "print", "taishoym": ym_str,
                            "s": ",".join(codes_post)})
    if not r_xls.content:
        return json.dumps({"ok": False, "error": "empty response"},
                          ensure_ascii=False)

    saved = ""
    if save_path:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(r_xls.content)
        saved = save_path

    out: dict = {"ok": True, "taishoym": ym_slash,
                 "students_requested": codes_post,
                 "ax_students_count": len(codes_all)}
    if saved:
        out["saved"] = saved
    if parse:
        try:
            text = r_xls.content.decode("utf-8")
        except UnicodeDecodeError:
            text = r_xls.content.decode("utf-8", errors="replace")
        target = codes_post[0] if len(codes_post) == 1 else None
        parsed = _ieb250_parse(text, target_seitocd=target)
        out["students"] = parsed["students"]
    else:
        out["bytes"] = len(r_xls.content)
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_widenet_data_create(shoriym: str = "", confirm: bool = False) -> str:
    """IEB110「カード・ワイドネット請求データ作成」を実行する。

    月次WN〆フローの最初のステップ。処理年月の翌月分の請求データを作成する。
    IEB320「WN／カード請求締め処理」の前工程。

    Args:
        shoriym: 処理年月（YYYY/MM 形式）。空ならSKSのIEB110 GETが返す既定値を使う。
        confirm: True なら実行。False（既定）は dry-run（画面上の shoriym/billym を返すのみ）。

    Returns:
        JSON: {ok, shoriym, billym, created, deleted, message, url}
        - created: データ作成件数
        - deleted: 請求額「０」による削除件数
    """
    s = _get_session()

    r0 = s.get(f"{BASE_URL}/service/IEB110.wpp")
    html0 = r0.content.decode("utf-8", errors="replace")
    soup0 = BeautifulSoup(html0, "html.parser")

    def _find_hidden(name: str) -> str:
        el = soup0.find("input", {"name": name})
        return (el.get("value", "") if el else "").strip()

    default_shori = _find_hidden("shoriym")
    default_bill = _find_hidden("billym")

    if not default_shori or not default_bill:
        return json.dumps({
            "ok": False,
            "error": "IEB110 GET でshoriym/billymが取得できませんでした（ログインまたは画面構造の変化）",
        }, ensure_ascii=False, indent=2)

    if shoriym and shoriym != default_shori:
        return json.dumps({
            "ok": False,
            "error": f"指定 shoriym={shoriym} は画面既定 {default_shori} と一致しません。SKS運用上、当月分以外の遡及/先送り実行はサポートしていません。",
            "screen_shoriym": default_shori,
            "screen_billym": default_bill,
        }, ensure_ascii=False, indent=2)

    used_shori = default_shori
    used_bill = default_bill

    def _parse_result(html: str) -> dict:
        text = BeautifulSoup(html, "html.parser").get_text("\n")
        done = "作成が終了しました" in text
        m_created = re.search(r"データ作成件数[：:]\s*(\d+)", text)
        m_deleted = re.search(r"削除件数[：:]\s*(\d+)", text)
        return {
            "ok": done,
            "created": int(m_created.group(1)) if m_created else None,
            "deleted": int(m_deleted.group(1)) if m_deleted else None,
            "raw_snippet": text.strip()[:400],
        }

    if not confirm:
        return json.dumps({
            "ok": True,
            "dry_run": True,
            "shoriym": used_shori,
            "billym": used_bill,
            "note": "confirm=True で実行。JS dopost() 相当の GET /service/IEB110.wpp?mode=if&shoriym=...&billym=... を叩く。",
        }, ensure_ascii=False, indent=2)

    params = {"mode": "if", "shoriym": used_shori, "billym": used_bill}
    r = s.get(f"{BASE_URL}/service/IEB110.wpp", params=params)
    html = r.content.decode("utf-8", errors="replace")
    parsed = _parse_result(html)

    return json.dumps({
        "ok": parsed["ok"],
        "shoriym": used_shori,
        "billym": used_bill,
        "created": parsed["created"],
        "deleted": parsed["deleted"],
        "message": "カード・ワイドネット請求データの作成が終了しました。" if parsed["ok"] else "作成完了を検出できませんでした",
        "url": r.url,
        "raw_snippet": parsed["raw_snippet"],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sks_daikin_meisai_export(
    taishoym: str,
    repkb: str = "seitobetsu",
    shoriym: str = "",
    save_path: str = "",
) -> str:
    """IEB550「代金内訳明細書印刷」の PDF を取得する。

    出力区分（repkb）の3種いずれかで、任意の対象年月の代金内訳明細書を PDF で返す。
    「生徒別合計」は月次〆前後の残高・請求状況把握に使う（前月請求済みかの確認等）。

    HTTPチェーン: POST /service/IEB550.wpp → 中継HTML(input params) → GET /cgi-bin/bizreport.pl → PDF

    Args:
        taishoym: 対象年月（YYYY/MM または YYYYMM）。例 "2026/06"
        repkb: 出力区分。"wn"|"card"|"0"=カード/WN請求のみ、"furikomi"|"1"=振込のみ、"seitobetsu"|"2"=生徒別合計（既定）
        shoriym: 処理年月（YYYY/MM）。空なら IEB550 GET 既定値（当月）を使う
        save_path: PDF保存先。空ならバイトサイズのみ返す（PDF内容は返さない）

    Returns:
        JSON: {ok, taishoym, repkb, shoriym, saved, bytes, is_empty}
        - is_empty: PDF実体があるが対象データなしのテンプレ（~987B）の可能性がTrue
    """
    ym_str = taishoym.replace("/", "")
    if not re.match(r"^\d{6}$", ym_str):
        return json.dumps({"ok": False, "error": f"invalid taishoym: {taishoym}"},
                          ensure_ascii=False)

    _repkb_map = {
        "wn": "0", "card": "0", "wn_card": "0", "0": "0",
        "furikomi": "1", "furi": "1", "1": "1",
        "seitobetsu": "2", "sougou": "2", "goukei": "2", "2": "2",
    }
    kb = _repkb_map.get(repkb.lower(), None)
    if kb is None:
        return json.dumps({"ok": False, "error": f"invalid repkb: {repkb}",
                           "hint": "use one of: wn/card/0, furikomi/1, seitobetsu/2"},
                          ensure_ascii=False)

    s = _get_session()

    r0 = s.get(f"{BASE_URL}/service/IEB550.wpp")
    if not shoriym:
        soup0 = BeautifulSoup(r0.content.decode("utf-8", errors="replace"), "html.parser")
        el = soup0.find("input", {"name": "shoriym"})
        shoriym = (el.get("value", "") if el else "").strip()
        if not shoriym:
            return json.dumps({"ok": False, "error": "shoriym既定値が取れませんでした"},
                              ensure_ascii=False)

    data = {
        "kyoshitsucd": CLASSROOM,
        "kyoshitsusm": os.environ.get("SKS_CLASSROOM_NAME", ""),
        "cmd": "print",
        "shoriym": shoriym,
        "repkb": kb,
        "taishoym": ym_str,
    }
    r1 = s.post(f"{BASE_URL}/service/IEB550.wpp", data=data)

    pdf_bytes: bytes | None = None
    if r1.content.startswith(b"%PDF"):
        pdf_bytes = r1.content
    else:
        soup = BeautifulSoup(r1.text, "html.parser")
        params = {}
        for inp in soup.find_all("input"):
            nm = inp.get("name")
            val = inp.get("value", "") or ""
            if nm and nm not in ("submitButtonName",):
                params[nm] = val
        if not params or params.get("ParamSEIKYUYM") == "000001":
            return json.dumps({"ok": True, "taishoym": ym_str, "repkb": kb,
                               "shoriym": shoriym, "is_empty": True,
                               "message": "対象データなし（中継HTML判定）"},
                              ensure_ascii=False)
        r2 = s.get(f"{BASE_URL}/cgi-bin/bizreport.pl", params=params)
        if r2.content.startswith(b"%PDF"):
            pdf_bytes = r2.content
        else:
            return json.dumps({"ok": False, "taishoym": ym_str, "repkb": kb,
                               "shoriym": shoriym,
                               "error": "PDFではないレスポンス",
                               "content_type": r2.headers.get("content-type", ""),
                               "bytes": len(r2.content)},
                              ensure_ascii=False)

    is_empty = len(pdf_bytes) < 1500
    saved = ""
    if save_path:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(pdf_bytes)
        saved = save_path

    return json.dumps({
        "ok": True,
        "taishoym": ym_str,
        "repkb": kb,
        "shoriym": shoriym,
        "bytes": len(pdf_bytes),
        "is_empty": is_empty,
        "saved": saved,
        "note": "is_empty=True は 987B前後の対象データなしテンプレの可能性" if is_empty else "",
    }, ensure_ascii=False, indent=2)


_IEB061_KOSHU_SHUBETSU = {"春期": "81", "夏期": "82", "冬期": "83"}
_IEB061_GRADE_CODE = {
    "小1": "07", "小2": "08", "小3": "09", "小4": "10", "小5": "11", "小6": "12",
    "中1": "13", "中2": "14", "中3": "15",
    "高1": "16", "高2": "17", "高3": "18",
    "その他": "20",
}


def _ieb061_load_student(sess, seitocd: str, grade_label: str, shoriym: str, billym: str) -> dict:
    """IEB061 の cmd=if で生徒詳細を取得し、courseop/Course3 と既存if1/if2/if3行を辞書化して返す。"""
    sess.get(f"{BASE_URL}/service/IEB061.wpp")
    gcode = _IEB061_GRADE_CODE.get(grade_label)
    if gcode is None:
        raise ValueError(f"unknown grade: {grade_label!r} (expected 小4/中3/高2 etc)")
    param = f"A|{seitocd}|{gcode}:{grade_label}|{shoriym}|{billym}"
    r = sess.get(f"{BASE_URL}/service/IEB061.wpp",
                 params={"cmd": "if", "param": param})
    text = r.content.decode("utf-8", errors="replace")
    if "生徒" not in text and "courseop" not in text:
        raise RuntimeError(f"IEB061 load returned unexpected content ({len(text)}B)")

    courseop: dict = {}
    for m in re.finditer(r"courseop\['([^']+)'\]\s*=\s*'([^']+)'", text):
        code, val = m.group(1), m.group(2)
        parts = val.split("/")
        try:
            tanka = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        except Exception:
            tanka = None
        courseop[code] = {"tanka": tanka, "stage": parts[0], "raw": val}

    course3: dict = {}
    for m in re.finditer(r"Course3\['(\d+)'\]\['([^']+)'\]\s*=\s*'([^｜']+)｜", text):
        bsel = m.group(1)
        ryokin = m.group(2)
        label = m.group(3)
        course3.setdefault(bsel, {})[ryokin] = label

    def _extract_rows(prefix: str, max_i: int) -> dict:
        rows: dict = {}
        for i in range(1, max_i + 1):
            rsel_m = re.search(rf"finditem\('{prefix}rsel{i}',\s*'([^']*)'\)", text)
            if not rsel_m or not rsel_m.group(1):
                continue
            row = {"rsel": rsel_m.group(1)}
            bsel_m = re.search(rf"finditem\('{prefix}bsel{i}',\s*'([^']*)'\)", text)
            if bsel_m:
                row["bsel"] = bsel_m.group(1)
            ksel_m = re.search(rf"finditem\('{prefix}ksel{i}',\s*(\d+)\)", text)
            if ksel_m:
                row["ksel"] = ksel_m.group(1)
            koma_m = re.search(rf"getElementById\('{prefix}koma{i}'\)\.value\s*=\s*'([^']*)'", text)
            if koma_m:
                row["koma"] = koma_m.group(1)
            tanka_m = re.search(rf"getElementById\('{prefix}tanka{i}'\)\.value\s*=\s*'([^']*)'", text)
            if tanka_m:
                row["tanka"] = tanka_m.group(1)
            kin_m = re.search(rf"getElementById\('{prefix}kingaku{i}'\)\.value\s*=\s*'([^']*)'", text)
            if kin_m:
                row["kingaku"] = kin_m.group(1)
            taisho_m = re.search(rf"finditem\(\"{prefix}taisho{i}\",\s*\"([^\"]*)\"\)", text)
            if taisho_m:
                row["taisho"] = taisho_m.group(1)
            rows[i] = row
        return rows

    return {"seitocd": seitocd, "grade": grade_label, "shoriym": shoriym,
            "billym": billym, "courseop": courseop, "course3": course3,
            "existing": {
                "if1": _extract_rows("if1", 10),
                "if2": _extract_rows("if2", 20),
                "if3": _extract_rows("if3", 5),
            }}


@mcp.tool()
def sks_ieb061_load(seitocd: str, grade: str) -> str:
    """IEB061「カード・ワイドネット者用料金入力」で指定生徒の詳細をロードし、選択可能な講習会費コース一覧＋単価を返す。

    夏期／春期／冬期の料金コード（ifrsel）を確定するのに使う。

    Args:
        seitocd: 生徒コード（例 "240035"）
        grade: 学年ラベル（例 "中3"/"高2"/"小5"）

    Returns:
        JSON: {seitocd, grade, shoriym, billym,
               koshu_options: [{shubetsu, bsel, ryokin_code, name, tanka}, ...]}
    """
    sess = _get_session()
    r0 = sess.get(f"{BASE_URL}/service/IEB061.wpp")
    soup = BeautifulSoup(r0.content.decode("utf-8", errors="replace"), "html.parser")
    def val(name):
        el = soup.find(id=name) or soup.find("input", {"name": name})
        return (el.get("value", "") if el else "").strip()
    shoriym = val("_shoriym") or val("shoriym")
    billym = val("billym")
    shoriym_yyyymm = shoriym.replace("/", "") if shoriym else ""
    if not shoriym or not billym:
        return json.dumps({"ok": False, "error": "IEB061 GET で shoriym/billym 取れず"},
                          ensure_ascii=False)

    try:
        info = _ieb061_load_student(sess, seitocd, grade, shoriym, billym)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    options: list = []
    for bsel, ryokin_map in info["course3"].items():
        shubetsu = {"81": "春期", "82": "夏期", "83": "冬期"}.get(bsel, bsel)
        for code, name in ryokin_map.items():
            tanka = (info["courseop"].get(code) or {}).get("tanka")
            options.append({"shubetsu": shubetsu, "bsel": bsel,
                            "ryokin_code": code, "name": name, "tanka": tanka})
    return json.dumps({"ok": True, "seitocd": seitocd, "grade": grade,
                       "shoriym": shoriym_yyyymm or shoriym, "billym": billym,
                       "koshu_options_count": len(options),
                       "koshu_options": options}, ensure_ascii=False, indent=2)


_IEB061_MISC_BSEL = {
    "授業料値引": "19", "入会金": "20", "入会金値引": "29",
    "維持管理費": "30", "基礎教材費": "40", "別途教材費": "50",
    "テスト費": "60", "ﾃｽﾄ費": "60", "その他": "70",
    "講習会費テキスト代": "91", "講習会費（テキスト代）": "91", "講習会費（ﾃｷｽﾄ代）": "91",
    "講習会費オプションテスト費": "92", "講習会費オプション（テスト費）": "92",
    "講習会費ｵﾌﾟｼｮﾝ（ﾃｽﾄ費）": "92",
    "講習会費オプションファイル代": "93", "講習会費オプション（ファイル代）": "93",
    "講習会費ｵﾌﾟｼｮﾝ（ﾌｧｲﾙ代）": "93",
}


@mcp.tool()
def sks_wn_koshu_register(
    seitocd: str,
    grade: str,
    lines: list,
    misc_lines: list | None = None,
    bcomment: str = "",
    confirm: bool = False,
) -> str:
    """IEB061 経由でワイドネット対象生徒に講習会費行（最大5行）と諸経費行（追加分）を登録する。

    Args:
        seitocd: 生徒コード
        grade: 学年ラベル
        lines: 講習会費行。[{"shubetsu":"夏期", "ryokin_code":"16200/15", "koma": 36}, ...]
        misc_lines: 諸経費追加行（テキスト代等）。
                    [{"bunrui":"講習会費テキスト代", "ryokin_code":"15700/99", "quantity":1}, ...]
                    tankaは courseop から自動、kingaku = quantity × tanka
        bcomment: YSPC請求明細コメント
        confirm: False(既定)=dry-run
    """
    if not lines or len(lines) > 5:
        return json.dumps({"ok": False, "error": "lines は1〜5件"}, ensure_ascii=False)

    sess = _get_session()
    r0 = sess.get(f"{BASE_URL}/service/IEB061.wpp")
    soup = BeautifulSoup(r0.content.decode("utf-8", errors="replace"), "html.parser")
    def val(name):
        el = soup.find(id=name) or soup.find("input", {"name": name})
        return (el.get("value", "") if el else "").strip()
    shoriym_slash = val("_shoriym") or val("shoriym")
    billym_slash = val("billym")
    if not shoriym_slash or not billym_slash:
        return json.dumps({"ok": False, "error": "shoriym/billym を IEB061 GET から取れず"},
                          ensure_ascii=False)
    shoriym_yyyymm = shoriym_slash.replace("/", "")
    billym_yyyymm = billym_slash.replace("/", "")

    try:
        info = _ieb061_load_student(sess, seitocd, grade, shoriym_slash, billym_slash)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"load失敗: {e}"}, ensure_ascii=False)

    resolved = []
    for ln in lines:
        shubetsu = ln.get("shubetsu", "夏期")
        bsel = _IEB061_KOSHU_SHUBETSU.get(shubetsu)
        if bsel is None:
            return json.dumps({"ok": False,
                               "error": f"shubetsu 不正: {shubetsu}"},
                              ensure_ascii=False)
        ryokin = ln["ryokin_code"]
        if ryokin not in info["course3"].get(bsel, {}):
            return json.dumps({"ok": False,
                               "error": f"料金コード {ryokin} は bsel={bsel} に存在せず",
                               "hint": "sks_ieb061_load で確認",
                               "available_sample": list(info["course3"].get(bsel, {}).items())[:5]},
                              ensure_ascii=False)
        koma = int(ln["koma"])
        if koma <= 0:
            return json.dumps({"ok": False, "error": "コマ数は正の整数"}, ensure_ascii=False)
        tanka = (info["courseop"].get(ryokin) or {}).get("tanka")
        if tanka is None:
            return json.dumps({"ok": False,
                               "error": f"courseop に単価がない: {ryokin}"},
                              ensure_ascii=False)
        resolved.append({"shubetsu": shubetsu, "bsel": bsel, "ryokin": ryokin,
                         "name": info["course3"][bsel][ryokin],
                         "koma": koma, "tanka": tanka, "kingaku": koma * tanka})

    misc_resolved = []
    for ln in (misc_lines or []):
        bunrui = ln.get("bunrui", "")
        bsel = _IEB061_MISC_BSEL.get(bunrui)
        if bsel is None:
            return json.dumps({"ok": False,
                               "error": f"bunrui 不正: {bunrui}",
                               "hint": f"valid: {list(_IEB061_MISC_BSEL.keys())}"},
                              ensure_ascii=False)
        ryokin = ln["ryokin_code"]
        qty = int(ln.get("quantity", 1))
        tanka_override = ln.get("tanka")
        if tanka_override is not None:
            tanka = int(tanka_override)
        else:
            tanka = (info["courseop"].get(ryokin) or {}).get("tanka")
            if tanka is None:
                return json.dumps({"ok": False,
                                   "error": f"courseop に単価がない: {ryokin}"},
                                  ensure_ascii=False)
        misc_resolved.append({"bunrui": bunrui, "bsel": bsel, "ryokin": ryokin,
                              "quantity": qty, "tanka": tanka, "kingaku": qty * tanka})

    total_all = sum(r["kingaku"] for r in resolved) + sum(r["kingaku"] for r in misc_resolved)

    if not confirm:
        return json.dumps({"ok": True, "dry_run": True, "seitocd": seitocd,
                           "grade": grade, "shoriym": shoriym_yyyymm,
                           "billym": billym_yyyymm,
                           "resolved": resolved, "misc_resolved": misc_resolved,
                           "bcomment": bcomment,
                           "total": total_all,
                           "note": "confirm=True で実POSTします"},
                          ensure_ascii=False, indent=2)

    fmpost = {
        "mode": "regist",
        "seitocd": seitocd,
        "seikyuym": billym_slash,
        "shoriym": shoriym_yyyymm,
        "bcomment": bcomment,
        "scrolltop": "0",
    }
    def _kingaku0(row: dict) -> str:
        k = row.get("kingaku")
        if k:
            return str(k)
        try:
            return str(int(row.get("tanka") or 0) * int(row.get("koma") or 0))
        except Exception:
            return ""
    for i, row in (info["existing"]["if1"] or {}).items():
        idx = 10 + int(i)
        fmpost[f"ifcb{idx}"] = ""
        fmpost[f"ifminus{idx}"] = ""
        fmpost[f"ifbsel{idx}"] = "10"
        fmpost[f"ifrsel{idx}"] = row.get("rsel", "")
        fmpost[f"ifksel{idx}"] = row.get("ksel", "")
        fmpost[f"ifkomasu{idx}"] = row.get("koma", "")
        fmpost[f"iftanka{idx}"] = row.get("tanka", "")
        fmpost[f"ifkingaku{idx}"] = _kingaku0(row)
        fmpost[f"iftaisho{idx}"] = row.get("taisho", "")
    def _kingaku(row: dict) -> str:
        k = row.get("kingaku")
        if k:
            return str(k)
        try:
            return str(int(row.get("tanka") or 0) * int(row.get("koma") or 0))
        except Exception:
            return ""
    existing_if2 = info["existing"]["if2"] or {}
    used_if2_indexes: set = set(existing_if2.keys())
    for i, row in existing_if2.items():
        idx = 20 + int(i)
        fmpost[f"ifcb{idx}"] = ""
        fmpost[f"ifminus{idx}"] = "0"
        fmpost[f"ifbsel{idx}"] = row.get("bsel", "")
        fmpost[f"ifrsel{idx}"] = row.get("rsel", "")
        fmpost[f"ifkomasu{idx}"] = row.get("koma", "")
        fmpost[f"iftanka{idx}"] = row.get("tanka", "")
        fmpost[f"ifkingaku{idx}"] = _kingaku(row)
        fmpost[f"iftext{idx}"] = ""
    misc_slot = 1
    for mr in misc_resolved:
        while misc_slot in used_if2_indexes:
            misc_slot += 1
        if misc_slot > 20:
            return json.dumps({"ok": False,
                               "error": "諸経費行が20行を超えました（既存分含む）"},
                              ensure_ascii=False)
        idx = 20 + misc_slot
        fmpost[f"ifcb{idx}"] = ""
        fmpost[f"ifminus{idx}"] = "0"
        fmpost[f"ifbsel{idx}"] = mr["bsel"]
        fmpost[f"ifrsel{idx}"] = mr["ryokin"]
        fmpost[f"ifkomasu{idx}"] = str(mr["quantity"])
        fmpost[f"iftanka{idx}"] = str(mr["tanka"])
        fmpost[f"ifkingaku{idx}"] = str(mr["kingaku"])
        fmpost[f"iftext{idx}"] = ""
        used_if2_indexes.add(misc_slot)
        misc_slot += 1
    existing_if3 = info["existing"]["if3"] or {}
    used_if3_indexes: set = set(existing_if3.keys())
    for i, row in existing_if3.items():
        idx = 40 + int(i)
        fmpost[f"ifcb{idx}"] = ""
        fmpost[f"ifminus{idx}"] = "0"
        fmpost[f"iftext{idx}"] = "0"
        fmpost[f"ifbsel{idx}"] = row.get("bsel", "")
        fmpost[f"ifrsel{idx}"] = row.get("rsel", "")
        fmpost[f"ifkomasu{idx}"] = row.get("koma", "")
        fmpost[f"iftanka{idx}"] = row.get("tanka", "")
        fmpost[f"ifkingaku{idx}"] = _kingaku(row)
    slot = 1
    for r in resolved:
        while slot in used_if3_indexes:
            slot += 1
        if slot > 5:
            return json.dumps({"ok": False,
                               "error": "講習会費行が5行を超えました（既存分含む）"},
                              ensure_ascii=False)
        idx = 40 + slot
        fmpost[f"ifcb{idx}"] = ""
        fmpost[f"ifminus{idx}"] = "0"
        fmpost[f"iftext{idx}"] = "0"
        fmpost[f"ifbsel{idx}"] = r["bsel"]
        fmpost[f"ifrsel{idx}"] = r["ryokin"]
        fmpost[f"ifkomasu{idx}"] = str(r["koma"])
        fmpost[f"iftanka{idx}"] = str(r["tanka"])
        fmpost[f"ifkingaku{idx}"] = str(r["kingaku"])
        used_if3_indexes.add(slot)
        slot += 1

    resp = sess.post(f"{BASE_URL}/service/IEB061.wpp", data=fmpost)
    html = resp.content.decode("utf-8", errors="replace")
    err_m = re.search(r'alert\("([^"]+)"\)', html)
    saved = "SHIME" + seitocd in html or "生徒が登録されました" in html or "確定しました" in html

    return json.dumps({"ok": bool(saved) and not err_m,
                       "seitocd": seitocd, "grade": grade,
                       "shoriym": shoriym_yyyymm, "billym": billym_yyyymm,
                       "resolved": resolved, "bcomment": bcomment,
                       "total": sum(r["kingaku"] for r in resolved),
                       "response_status": resp.status_code,
                       "response_bytes": len(resp.content),
                       "error": err_m.group(1) if err_m else None,
                       "url": resp.url},
                      ensure_ascii=False, indent=2)


_IEB250_GRADE_CODE = {
    "小1": "07", "小2": "08", "小3": "09", "小4": "10", "小5": "11", "小6": "12",
    "中1": "13", "中2": "14", "中3": "15",
    "高1": "16", "高2": "17", "高3": "18", "成人": "19",
}


@mcp.tool()
def sks_gaibu_hidden_list(
    taishoym: str = "",
    name: str = "",
    grade: str = "",
    include_visible: bool = True,
) -> str:
    """IEB250「月謝台帳出力」の裏で、外部生名簿（非表示扱いも含む）を取得する。

    通常の <code>sks_student_list(kubun="gaibu")</code>（IEB040 経由）では
    退塾中の講習会生など「非表示」扱いの外部生は取れない。IEB250 の Ajax
    エンドポイントで <code>notaijuku=1</code>（外部生モードでは「非表示も含む」）
    を叩くと隠された外部生コードを含む名簿が取れる。

    再塾生の「隠れ外部生コード」（例：三原歩夢 22G060）を機械的に発見する用途。

    Args:
        taishoym: 対象年月 YYYY/MM or YYYYMM。省略時は当月。
        name: 氏名の部分一致フィルタ（例 "三原"）。空なら全件。
        grade: 学年ラベル（"中3"/"高2" 等）。空なら全学年。
        include_visible: True（既定）=可視の外部生も含める / False=非表示のみ差分抽出

    Returns:
        JSON: {ok, taishoym, count_all, count_hidden_only, students:[{seitocd, grade, name, hidden}]}
    """
    if not taishoym:
        taishoym = datetime.now().strftime("%Y/%m")
    ym_str = taishoym.replace("/", "")
    if not re.match(r"^\d{6}$", ym_str):
        return json.dumps({"ok": False, "error": f"invalid taishoym: {taishoym}"},
                          ensure_ascii=False)

    grade_code = ""
    if grade:
        g = _IEB250_GRADE_CODE.get(grade)
        if g is None:
            return json.dumps({"ok": False, "error": f"unknown grade: {grade}",
                               "hint": f"valid: {list(_IEB250_GRADE_CODE.keys())}"},
                              ensure_ascii=False)
        grade_code = g

    sess = _get_session()
    sess.get(f"{BASE_URL}/service/IEB250.wpp")

    def _fetch(notaijuku: str) -> list[dict]:
        p = f"VIEW|{ym_str}|1|{grade_code}|{notaijuku}|0|0"
        r = sess.get(f"{BASE_URL}/service/IEB250.wpp",
                     params={"cmd": "ax", "param": p})
        text = r.content.decode("utf-8", errors="replace")
        rows = []
        pattern = re.compile(
            r'<TR[^>]*onclick=[\'"]sel0\(this,"([^"]+)","[^"]*"\)[^>]*>\s*'
            r'<TD>([^<]+)</TD>\s*<TD>[^<]+</TD>\s*<TD>([^<]+)</TD>',
            re.DOTALL,
        )
        for m in pattern.finditer(text):
            rows.append({"seitocd": m.group(1), "grade": m.group(2).strip(),
                         "name": m.group(3).strip()})
        return rows

    all_rows = _fetch("1")
    visible_only = _fetch("0")
    visible_codes = {r["seitocd"] for r in visible_only}

    students = []
    for row in all_rows:
        is_hidden = row["seitocd"] not in visible_codes
        if not include_visible and not is_hidden:
            continue
        if name and name not in row["name"]:
            continue
        row["hidden"] = is_hidden
        students.append(row)

    return json.dumps({
        "ok": True, "taishoym": taishoym,
        "count_all": len(all_rows),
        "count_hidden_only": len(all_rows) - len(visible_only),
        "students": students,
    }, ensure_ascii=False, indent=2)


# --- Entry point ---
if __name__ == "__main__":
    mcp.run()
