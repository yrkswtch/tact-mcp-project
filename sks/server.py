"""
MCP Server for SKS (WEB-SKS 生徒管理システム)
スクールIE SKSの操作をMCPツールとして提供する

接続: SKS-proxy (https://schoolie-tacs.mirrei.dev/) または直接 (http://tacs.tacsvpn/)
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
BASE_URL = os.environ.get("SKS_BASE_URL", "https://schoolie-tacs.mirrei.dev")
ACCOUNT = os.environ.get("SKS_ACCOUNT", "")
PASSWORD = os.environ.get("SKS_PASSWORD", "")
CLASSROOM = os.environ.get("SKS_CLASSROOM", "5558")

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

    # Step 3: Login with classroom
    enc_pass2, _ = _cryptojs_aes_encrypt(PASSWORD, com)
    j3 = _sks_api(s, {
        "cmd": "login",
        "kcd": CLASSROOM,
        "loginid": ACCOUNT,
        "loginpw": enc_pass2,
        "tantoshacd": j2.get("tantoshacd", ""),
        "usersm": j2.get("usersm", ""),
        "accesslv": j2.get("accesslv", ""),
    })
    if j3.get("result") != "OK":
        _login_failed = True
        raise Exception(f"SKS login failed: {j3.get('result')}")

    # Step 4: Access service menu to establish session
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
        address_city: 住所1 都道府県市区町村（例: "埼玉県川口市"）
        address_detail: 住所2 番地（例: "坂下町3-26-12"）
        address_building: 住所3 建物名（例: "ウィルローズ鳩ヶ谷210"）
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

    # 学校区分・学年
    schoolkb = ""
    grade_val = ""
    if grade and grade in _GRADE_TO_SCHOOLKB:
        schoolkb, grade_val = _GRADE_TO_SCHOOLKB[grade]

    # 対象年齢
    age_val = _GRADE_TO_AGE.get(grade, "10")  # デフォルト: 不詳

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
        "kyoshitsusm": "鳩ヶ谷校",
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


# =====================
# PCS Tools
# =====================

SSK2_URL = "https://schoolie-tacs-ssk2.mirrei.dev"
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
        address: 住所文字列（例: "埼玉県川口市赤井4-25-7"）
    """
    zipcode = _lookup_zip(address)
    return json.dumps({"address": address, "postal_code": zipcode}, ensure_ascii=False)


# --- Entry point ---
if __name__ == "__main__":
    mcp.run()
