"""
MCP Server for WebSupport (tactgroup.net)
スクールIE WEB SUPPORT の操作をMCPツールとして提供する

【重要】ログイン試行に繰り返し失敗するとアカウントがロックされる。
ログイン失敗時はリトライせず即座にエラーを返すこと。
"""
import csv
import io
import json
import os
import re
import sys
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

# --- FastMCP ---
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("mcp package not found. Install with: pip install mcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("WebSupport")

# --- Configuration ---
BASE_URL = os.environ.get("WEBSUPPORT_URL", "https://www.tactgroup.net")
ACCOUNT = os.environ.get("WEBSUPPORT_ACCOUNT", "")
PASSWORD = os.environ.get("WEBSUPPORT_PASSWORD", "")

# --- Session management ---
_session: requests.Session | None = None
_login_failed: bool = False  # ログイン失敗フラグ（リトライ防止）


def _get_session() -> requests.Session:
    """ログイン済みセッションを取得（未ログインなら自動ログイン）

    【重要】ログイン失敗時はリトライしない。繰り返し失敗するとアカウントロックされる。
    セッションタイムアウト検知: 既存セッションでtop.phpをGETし、
    「ログインタイムアウト」が含まれていたらセッション破棄→再ログイン。
    """
    global _session, _login_failed
    if _session is not None:
        # セッションが生きているか確認
        r = _session.get(f"{BASE_URL}/contents/class/top/top.php")
        try:
            text = r.content.decode("euc-jp", errors="ignore")
        except Exception:
            text = r.text
        if "ログインタイムアウト" not in text:
            return _session
        # タイムアウト → セッション破棄して再ログイン
        _session = None
    if _login_failed:
        raise Exception(
            "Login previously failed. NOT retrying to avoid account lockout. "
            "Check WEBSUPPORT_ACCOUNT/WEBSUPPORT_PASSWORD and restart the MCP server."
        )

    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/contents/class/login/login.php",
        data={
            "classAccount": ACCOUNT,
            "classPassword": PASSWORD,
            "btnLogin.x": "42",
            "btnLogin.y": "11",
        },
    )
    if "top.php" not in r.url and "PICK UP" not in r.text:
        _login_failed = True
        raise Exception(
            "Login failed. NOT retrying to avoid account lockout. "
            "Check WEBSUPPORT_ACCOUNT/WEBSUPPORT_PASSWORD."
        )

    # 一覧前処理（セッション確立に必要）
    s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantListPre.php")
    _session = s
    return s


def _parse_csv(content: bytes) -> list[dict]:
    """CP932エンコードされたCSVをパースしてdictのリストで返す"""
    text = content.decode("cp932")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _parse_detail_html(html: str) -> dict:
    """詳細ページのHTMLからフィールドを抽出"""
    soup = BeautifulSoup(html, "html.parser")
    data = {}
    # テーブルのth/tdペアを抽出
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        for i in range(0, len(cells) - 1, 2):
            key = cells[i].get_text(strip=True).replace("※", "").strip()
            val = cells[i + 1].get_text(strip=True)
            if key:
                data[key] = val
    return data


# --- MCP Tools ---


@mcp.tool()
def applicant_list(
    limit: int = 50,
    status: str = "",
    since: str = "",
) -> str:
    """生徒受付管理の一覧をCSVダウンロード経由で取得する。

    Args:
        limit: 取得する最大件数（デフォルト50、最新順）
        status: ステータスで絞込み（例: "受付", "教室対応中", "入会成約"）
        since: この日時以降のレコードのみ（YYYY-MM-DD形式）
    """
    s = _get_session()
    r = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/download.php",
        data={"btn_download": ""},
    )
    if r.headers.get("Content-Disposition") is None:
        return json.dumps({"error": "CSV download failed. Session may have expired."})

    rows = _parse_csv(r.content)

    # フィルタ
    if status:
        rows = [r for r in rows if status in r.get("ステータス", "")]
    if since:
        rows = [
            r
            for r in rows
            if r.get("受付日時", "") >= since
        ]

    # 最新順（受付日時降順）でlimit件
    rows.sort(key=lambda x: x.get("受付日時", ""), reverse=True)
    rows = rows[:limit]

    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool()
def applicant_detail(applicant_id: str) -> str:
    """生徒受付の詳細情報を取得する。

    Args:
        applicant_id: 問合せNO（例: "749101"）
    """
    s = _get_session()
    r = s.get(
        f"{BASE_URL}/contents/boshu/class/applicant/applicantDetail.php",
        params={"rei": applicant_id, "num": "1"},
    )
    html = r.content.decode("euc-jp", errors="replace")
    data = _parse_detail_html(html)
    data["問合せNO"] = applicant_id
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def applicant_search(
    name: str = "",
    phone: str = "",
    email: str = "",
    limit: int = 20,
) -> str:
    """生徒受付データを名前・電話番号・メールアドレスで検索する。

    Args:
        name: 保護者名または生徒名で検索（部分一致）
        phone: 電話番号で検索（部分一致）
        email: メールアドレスで検索（部分一致）
        limit: 最大件数
    """
    s = _get_session()
    r = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/download.php",
        data={"btn_download": ""},
    )
    if r.headers.get("Content-Disposition") is None:
        return json.dumps({"error": "CSV download failed."})

    rows = _parse_csv(r.content)

    results = []
    for row in rows:
        match = True
        if name:
            names = (
                row.get("保護者氏名（漢字）", "")
                + row.get("保護者氏名（カナ）", "")
                + row.get("生徒氏名（漢字）", "")
                + row.get("生徒氏名（カナ）", "")
            )
            if name not in names:
                match = False
        if phone and phone not in row.get("電話番号", ""):
            match = False
        if email and email not in row.get("メールアドレス", ""):
            match = False
        if match:
            results.append(row)

    results.sort(key=lambda x: x.get("受付日時", ""), reverse=True)
    return json.dumps(results[:limit], ensure_ascii=False, indent=2)


@mcp.tool()
def applicant_new_count() -> str:
    """未開封の新着問い合わせ件数を取得する。"""
    s = _get_session()
    r = s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantList.php")
    html = r.content.decode("euc-jp", errors="replace")

    # 「教室未開封数： N件」を抽出
    match = re.search(r"教室未開封数[：:]\s*(\d+)\s*件", html)
    count = int(match.group(1)) if match else 0

    # 最新の受付日時も取得
    match2 = re.search(r"現在までの受付数[：:]\s*(\d+)\s*件", html)
    total = int(match2.group(1)) if match2 else 0

    return json.dumps(
        {"未開封数": count, "受付総数": total},
        ensure_ascii=False,
    )


@mcp.tool()
def applicant_download_csv(output_path: str = "") -> str:
    """生徒受付データをCSVファイルとしてダウンロードする。

    Args:
        output_path: 保存先パス（省略時はカレントディレクトリに保存）
    """
    s = _get_session()
    r = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/download.php",
        data={"btn_download": ""},
    )
    if r.headers.get("Content-Disposition") is None:
        return json.dumps({"error": "CSV download failed."})

    # CP932 → UTF-8変換して保存
    text = r.content.decode("cp932")
    if not output_path:
        output_path = f"websupport_applicants_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)

    rows = _parse_csv(r.content)
    return json.dumps(
        {"saved_to": output_path, "record_count": len(rows)},
        ensure_ascii=False,
    )


@mcp.tool()
def message_list(page: int = 1, limit: int = 20) -> str:
    """メッセージボックスの一覧を取得する。

    Args:
        page: ページ番号（1始まり、1ページ20件）
        limit: 取得する最大件数
    """
    s = _get_session()
    url = f"{BASE_URL}/contents/web_message/class/receive-box/list.php"
    if page > 1:
        url += f"?page={page}"
    r = s.get(url)
    html = r.content.decode("euc-jp", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    # 総件数を抽出
    total_match = re.search(r"全(\d+)件", html)
    total = int(total_match.group(1)) if total_match else 0

    # detail.phpリンクからメッセージを抽出
    messages = []
    for a in soup.find_all("a", href=lambda h: h and "detail.php" in h):
        title = a.get_text(strip=True)
        href = a["href"]
        mid = href.split("mid=")[-1] if "mid=" in href else ""
        # 日付はリンクの近くのtd
        parent_tr = a.find_parent("tr")
        date = ""
        if parent_tr:
            for td in parent_tr.find_all("td"):
                text = td.get_text(strip=True)
                if re.match(r"\d{4}/\d{2}/\d{2}", text):
                    date = text
                    break
        messages.append({"mid": mid, "title": title, "date": date})

    return json.dumps(
        {"total": total, "page": page, "messages": messages[:limit]},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def message_detail(mid: str) -> str:
    """メッセージの詳細を取得する。

    Args:
        mid: メッセージID（message_listで取得可能）
    """
    s = _get_session()
    r = s.get(
        f"{BASE_URL}/contents/web_message/class/receive-box/detail.php",
        params={"mid": mid},
    )
    html = r.content.decode("euc-jp", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    data = {"mid": mid}

    # 受信日時・件名を抽出（th/tdペア）
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            key = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if key and val:
                data[key] = val

    # 本文を抽出（最も長いテキストブロック）
    longest = ""
    for td in soup.find_all("td"):
        text = td.get_text(strip=True)
        if len(text) > len(longest) and "Copyright" not in text and "MBOX" not in text:
            longest = text
    if longest and "本文" not in data:
        data["本文"] = longest

    # 添付ファイルのリンク
    attachments = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "download" in href.lower() or href.endswith((".pdf", ".xlsx", ".xls", ".doc", ".docx", ".zip")):
            attachments.append({"name": a.get_text(strip=True), "url": href})
    if attachments:
        data["添付ファイル"] = attachments

    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def message_search(keyword: str, max_pages: int = 5) -> str:
    """メッセージボックスをキーワードで検索する（件名の部分一致）。

    Args:
        keyword: 検索キーワード（件名に含まれるテキスト）
        max_pages: 検索する最大ページ数（1ページ20件）
    """
    s = _get_session()
    results = []

    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}/contents/web_message/class/receive-box/list.php"
        if page > 1:
            url += f"?page={page}"
        r = s.get(url)
        html = r.content.decode("euc-jp", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        found_on_page = False
        for a in soup.find_all("a", href=lambda h: h and "detail.php" in h):
            title = a.get_text(strip=True)
            if keyword in title:
                href = a["href"]
                mid = href.split("mid=")[-1] if "mid=" in href else ""
                parent_tr = a.find_parent("tr")
                date = ""
                if parent_tr:
                    for td in parent_tr.find_all("td"):
                        text = td.get_text(strip=True)
                        if re.match(r"\d{4}/\d{2}/\d{2}", text):
                            date = text
                            break
                results.append({"mid": mid, "title": title, "date": date})
                found_on_page = True

        if not found_on_page and page > 1:
            break

    return json.dumps(results, ensure_ascii=False, indent=2)


# =====================
# SafetyMail (SFM) Tools
# =====================

SFM_URL = "https://sfm.tactgroup.net"


def _get_sfm_session() -> requests.Session:
    """SFMにSSO経由でログイン済みセッションを取得"""
    s = _get_session()
    # SSO遷移
    s.get(f"{BASE_URL}/contents/sfm/sso/ie_class.php")
    return s


@mcp.tool()
def sfm_attendance_list() -> str:
    """出席簿（在席・不在一覧）を取得する。現在の入退室状況がわかる。"""
    s = _get_sfm_session()
    s.get(f"{SFM_URL}/sfm/ie-class/attendance/student/listPre.php")
    r = s.get(f"{SFM_URL}/sfm/ie-class/attendance/student/list.php")
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    # 在席/不在カウント
    text = soup.get_text()
    present = re.search(r"在席\s*(\d+)人", text)
    absent = re.search(r"不在\s*(\d+)人", text)
    total_match = re.search(r"検索結果[：:]\s*(\d+)件", text)

    students = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 6:
            sid_text = tds[0].get_text(strip=True)
            if sid_text.isdigit():
                student = {
                    "生徒ID": sid_text,
                    "生徒名": tds[1].get_text(strip=True),
                    "生徒名カナ": tds[2].get_text(strip=True),
                    "学年": tds[3].get_text(strip=True),
                    "生徒区分": tds[4].get_text(strip=True),
                    "入室": tds[5].get_text(strip=True) if len(tds) > 5 else "",
                    "退室": tds[6].get_text(strip=True) if len(tds) > 6 else "",
                }
                students.append(student)

    return json.dumps({
        "在席": int(present.group(1)) if present else 0,
        "不在": int(absent.group(1)) if absent else 0,
        "total": int(total_match.group(1)) if total_match else len(students),
        "students": students,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def sfm_student_list(page: int = 1) -> str:
    """生徒基本情報の一覧を取得する。

    Args:
        page: ページ番号（1始まり）
    """
    s = _get_sfm_session()
    s.get(f"{SFM_URL}/sfm/ie-class/management/student/listPre.php")
    url = f"{SFM_URL}/sfm/ie-class/management/student/list.php"
    if page > 1:
        url += f"?page={page}"
    r = s.get(url)
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    students = []
    for a in soup.find_all("a", href=lambda h: h and "detail.php?sid=" in h):
        sid = a["href"].split("sid=")[-1]
        name = a.get_text(strip=True)
        tr = a.find_parent("tr")
        if tr:
            tds = tr.find_all("td")
            students.append({
                "sid": sid,
                "生徒ID": tds[0].get_text(strip=True) if len(tds) > 0 else "",
                "生徒名": name,
                "生徒名カナ": tds[2].get_text(strip=True) if len(tds) > 2 else "",
                "学年": tds[3].get_text(strip=True) if len(tds) > 3 else "",
                "生徒区分": tds[4].get_text(strip=True) if len(tds) > 4 else "",
            })

    return json.dumps({"page": page, "students": students}, ensure_ascii=False, indent=2)


@mcp.tool()
def sfm_student_detail(sid: str) -> str:
    """生徒基本情報の詳細を取得する。

    Args:
        sid: 生徒ID（sfm_student_listで取得可能）
    """
    s = _get_sfm_session()
    r = s.get(
        f"{SFM_URL}/sfm/ie-class/management/student/detail.php",
        params={"sid": sid},
    )
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    data = {"sid": sid}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            key = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if key:
                data[key] = val

    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def sfm_inbox(page: int = 1) -> str:
    """連絡帳の受信箱一覧を取得する。

    Args:
        page: ページ番号
    """
    s = _get_sfm_session()
    s.get(f"{SFM_URL}/sfm/ie-class/message/receive-box/listPre.php")
    url = f"{SFM_URL}/sfm/ie-class/message/receive-box/list.php"
    if page > 1:
        url += f"?page={page}"
    r = s.get(url)
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    messages = []
    for a in soup.find_all("a", href=lambda h: h and "detail.php?mid=" in h):
        title = a.get_text(strip=True)
        mid = a["href"].split("mid=")[-1]
        parent_tr = a.find_parent("tr")
        date = ""
        sender = ""
        if parent_tr:
            tds = parent_tr.find_all("td")
            for td in tds:
                text = td.get_text(strip=True)
                if re.match(r"\d{4}/\d{2}/\d{2}", text):
                    date = text
                elif text and text != title and not text.isdigit():
                    sender = text
        messages.append({"mid": mid, "title": title, "date": date, "from": sender})

    return json.dumps({"page": page, "messages": messages}, ensure_ascii=False, indent=2)


@mcp.tool()
def sfm_inbox_detail(mid: str) -> str:
    """連絡帳の受信メッセージ詳細を取得する。

    Args:
        mid: メッセージID
    """
    s = _get_sfm_session()
    r = s.get(
        f"{SFM_URL}/sfm/ie-class/message/receive-box/detail.php",
        params={"mid": mid},
    )
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    data = {"mid": mid}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            key = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if key and val:
                data[key] = val

    return json.dumps(data, ensure_ascii=False, indent=2)


# =====================
# Top Page Tools
# =====================


@mcp.tool()
def top_page(limit: int = 20) -> str:
    """WebSupportトップページの最新記事を取得する。

    Args:
        limit: 取得する最大件数（デフォルト20）
    """
    s = _get_session()
    r = s.get(f"{BASE_URL}/contents/class/menu/top.php")
    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    div = soup.find("div", class_="topicsText")
    if not div:
        return json.dumps({"error": "topicsText div not found"})

    text = div.get_text(separator="\n")

    # 日付パターンで記事を抽出: 【X月X日】①〜 タイトル
    entries = re.findall(r"【(\d+月\d+日)】[①-⑳\d]*\s*(.+)", text)
    items = []
    for date, title in entries[:limit]:
        title = title.strip()
        if title:
            items.append({"date": date, "title": title})

    return json.dumps(items, ensure_ascii=False, indent=2)


# =====================
# OKS Ordering System Tools
# =====================


_oks_agreed: bool = False


def _oks_ensure_agreed(s: requests.Session):
    """OKS利用規約同意済みにする（セッション内で1回だけ）"""
    global _oks_agreed
    if _oks_agreed:
        return
    s.post(f"{BASE_URL}/contents/class/menu/clauseAgree.php")
    _oks_agreed = True


@mcp.tool()
def oks_bihin_list(page: int = 1, keyword: str = "") -> str:
    """OKS備品の一覧を取得する。

    Args:
        page: ページ番号（1始まり）
        keyword: キーワード検索（商品名の部分一致）
    """
    s = _get_session()
    _oks_ensure_agreed(s)

    url = f"{BASE_URL}/contents/oks/class/bihin/item/list.php"
    if keyword:
        r = s.post(url, data={"keyword": keyword, "search": "1"})
    else:
        r = s.get(url + (f"?page={page}" if page > 1 else ""))

    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    items = []
    for a in soup.find_all("a", href=lambda h: h and "detail.php?iid=" in h):
        iid = a["href"].split("iid=")[-1]
        name = a.get_text(strip=True)
        tr = a.find_parent("tr")
        stock = "あり"
        price = ""
        unit = ""
        if tr:
            tds = tr.find_all("td")
            texts = [td.get_text(strip=True) for td in tds]
            if "在庫なし" in texts:
                stock = "在庫なし"
            for t in texts:
                if "円" in t:
                    price = t
                    break
            if len(texts) > 3:
                unit = texts[3]
        items.append({"iid": iid, "name": name, "price": price, "unit": unit, "stock": stock})

    # Total pages
    pages = set()
    for a in soup.find_all("a", href=lambda h: h and "page=" in h):
        m = re.search(r"page=(\d+)", a["href"])
        if m:
            pages.add(int(m.group(1)))
    max_page = max(pages) if pages else 1

    return json.dumps(
        {"page": page, "total_pages": max_page, "items": items},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def oks_bihin_detail(iid: str) -> str:
    """OKS備品の詳細情報を取得する。

    Args:
        iid: 商品ID（oks_bihin_listで取得可能）
    """
    s = _get_session()
    _oks_ensure_agreed(s)
    r = s.get(f"{BASE_URL}/contents/oks/class/bihin/item/detail.php?iid={iid}")
    html = r.content.decode("euc-jisx0213", errors="replace")
    return json.dumps(_parse_detail_html(html), ensure_ascii=False, indent=2)


@mcp.tool()
def oks_kyouzai_list(page: int = 1, keyword: str = "") -> str:
    """OKS教材の一覧を取得する。

    Args:
        page: ページ番号（1始まり、全122ページ）
        keyword: キーワード検索
    """
    s = _get_session()
    _oks_ensure_agreed(s)
    s.get(f"{BASE_URL}/contents/oks/class/kyouzai/item/listPre.php")

    url = f"{BASE_URL}/contents/oks/class/kyouzai/item/list.php"
    if keyword:
        r = s.post(url, data={"keyword": keyword, "search": "1"})
    else:
        r = s.get(url + (f"?page={page}" if page > 1 else ""))

    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    items = []
    for a in soup.find_all("a", href=lambda h: h and "detail.php?iid=" in h):
        iid = a["href"].split("iid=")[-1]
        name = a.get_text(strip=True)
        items.append({"iid": iid, "name": name})

    pages = set()
    for a in soup.find_all("a", href=lambda h: h and "page=" in h):
        m = re.search(r"page=(\d+)", a["href"])
        if m:
            pages.add(int(m.group(1)))
    max_page = max(pages) if pages else 1

    return json.dumps(
        {"page": page, "total_pages": max_page, "items": items},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def oks_kyouzai_detail(iid: str) -> str:
    """OKS教材の詳細情報を取得する。

    Args:
        iid: 商品ID
    """
    s = _get_session()
    _oks_ensure_agreed(s)
    r = s.get(f"{BASE_URL}/contents/oks/class/kyouzai/item/detail.php?iid={iid}")
    html = r.content.decode("euc-jisx0213", errors="replace")
    return json.dumps(_parse_detail_html(html), ensure_ascii=False, indent=2)


@mcp.tool()
def oks_cart_add(iid: str, quantity: int, category: str = "bihin") -> str:
    """OKSカートに商品を追加する。注文確定はしない。

    Args:
        iid: 商品ID（oks_bihin_list等で取得可能）
        quantity: 数量
        category: "bihin"（備品）/ "kyouzai"（教材）
    """
    s = _get_session()
    _oks_ensure_agreed(s)

    # Ajax在庫チェック（JS経由で呼ばれる処理を再現）
    s.post(
        f"{BASE_URL}/contents/oks/class/{category}/item/itemCheckOfAjax.php",
        data={"iid": iid},
    )

    # カートに追加
    r = s.post(
        f"{BASE_URL}/contents/oks/class/{category}/item/detailItemAdd.php",
        data={"iid": iid, "unit": str(quantity)},
    )

    # カート内容を確認
    r2 = s.get(f"{BASE_URL}/contents/oks/class/{category}/cart/list.php")
    html = r2.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    cart_items = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 6:
            no = tds[0].get_text(strip=True)
            if no.isdigit():
                # 数量はinputフィールドの場合がある
                qty_td = tds[4]
                qty_input = qty_td.find("input")
                qty = qty_input.get("value", "") if qty_input else qty_td.get_text(strip=True)
                cart_items.append({
                    "No": no,
                    "商品CD": tds[1].get_text(strip=True),
                    "商品名": tds[2].get_text(strip=True),
                    "価格": tds[3].get_text(strip=True),
                    "数量": qty,
                    "単位": tds[5].get_text(strip=True),
                    "小計": tds[7].get_text(strip=True) if len(tds) > 7 else "",
                })

    return json.dumps({"added": {"iid": iid, "quantity": quantity}, "cart": cart_items}, ensure_ascii=False, indent=2)


@mcp.tool()
def oks_cart_view(category: str = "bihin") -> str:
    """OKSカートの中身を確認する。

    Args:
        category: "bihin"（備品）/ "kyouzai"（教材）
    """
    s = _get_session()
    _oks_ensure_agreed(s)
    r = s.get(f"{BASE_URL}/contents/oks/class/{category}/cart/list.php")
    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    cart_items = []
    total = ""
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 6:
            no = tds[0].get_text(strip=True)
            if no.isdigit():
                # 数量はinputフィールドの場合がある
                qty_td = tds[4]
                qty_input = qty_td.find("input")
                qty = qty_input.get("value", "") if qty_input else qty_td.get_text(strip=True)
                cart_items.append({
                    "No": no,
                    "商品CD": tds[1].get_text(strip=True),
                    "商品名": tds[2].get_text(strip=True),
                    "価格": tds[3].get_text(strip=True),
                    "数量": qty,
                    "単位": tds[5].get_text(strip=True),
                    "小計": tds[7].get_text(strip=True) if len(tds) > 7 else "",
                })
        # 合計行
        text = tr.get_text(strip=True)
        if "合計" in text:
            m = re.search(r"合計([\d,]+円)", text)
            if m:
                total = m.group(1)

    return json.dumps({"items": cart_items, "total": total}, ensure_ascii=False, indent=2)


@mcp.tool()
def oks_order_list(category: str = "bihin") -> str:
    """OKS発注履歴を取得する。

    Args:
        category: "bihin"（備品）/ "kyouzai"（教材）/ "estimate"（見積）
    """
    s = _get_session()
    _oks_ensure_agreed(s)
    r = s.get(f"{BASE_URL}/contents/oks/class/{category}/order/list.php")
    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    orders = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 3:
            vals = [td.get_text(strip=True) for td in tds]
            if any(v for v in vals) and vals[0] != "No":
                orders.append(vals)

    return json.dumps(
        {"category": category, "orders": orders},
        ensure_ascii=False, indent=2,
    )


# =====================
# Manual/Resource Tools
# =====================


@mcp.tool()
def manual_categories() -> str:
    """マニュアル・資料のカテゴリ一覧を取得する。"""
    s = _get_session()
    r = s.get(f"{BASE_URL}/contents/ie-online/class/manual/top.php")
    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    categories = []
    for a in soup.find_all("a", href=lambda h: h and "search.php" in h):
        name = a.get_text(strip=True)
        href = a["href"]
        cid = ""
        if "categoryID=" in href:
            cid = href.split("categoryID=")[-1].strip()
        if name and name not in [c["name"] for c in categories]:
            categories.append({"categoryID": cid, "name": name})

    return json.dumps(categories, ensure_ascii=False, indent=2)


@mcp.tool()
def manual_search(category_id: str = "", keyword: str = "", page: int = 1) -> str:
    """マニュアル・資料をカテゴリIDまたはキーワードで検索する。

    Args:
        category_id: カテゴリID（manual_categoriesで取得可能）
        keyword: フリーテキスト検索
        page: ページ番号
    """
    s = _get_session()
    url = f"{BASE_URL}/contents/ie-online/class/manual/search.php"
    params = {}
    if category_id:
        params["categoryID"] = category_id
    if page > 1:
        params["page"] = str(page)

    if keyword:
        r = s.post(url, data={"keyword": keyword, "search": "1", **params})
    else:
        r = s.get(url, params=params)

    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if ("DOWNLOAD_FILES" in href or href.endswith((".pdf", ".xlsx", ".zip", ".doc", ".docx"))) and text:
            if href.startswith("/"):
                href = BASE_URL + href
            elif not href.startswith("http"):
                href = BASE_URL + "/contents/ie-online/class/manual/" + href
            results.append({"title": text, "url": href})

    return json.dumps(results, ensure_ascii=False, indent=2)


# =====================
# SFM Additional Tools
# =====================


@mcp.tool()
def sfm_sendbox(page: int = 1) -> str:
    """連絡帳の送信箱一覧を取得する。

    Args:
        page: ページ番号（全47ページ）
    """
    s = _get_sfm_session()
    s.get(f"{SFM_URL}/sfm/ie-class/message/send-box/listPre.php")
    url = f"{SFM_URL}/sfm/ie-class/message/send-box/list.php"
    if page > 1:
        url += f"?page={page}"
    r = s.get(url)
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    messages = []
    for a in soup.find_all("a", href=lambda h: h and "detail.php?mid=" in h):
        title = a.get_text(strip=True)
        mid = a["href"].split("mid=")[-1]
        parent_tr = a.find_parent("tr")
        date = ""
        if parent_tr:
            for td in parent_tr.find_all("td"):
                text = td.get_text(strip=True)
                if re.match(r"\d{4}/\d{2}/\d{2}", text):
                    date = text
                    break
        messages.append({"mid": mid, "title": title, "date": date})

    return json.dumps({"page": page, "messages": messages}, ensure_ascii=False, indent=2)


@mcp.tool()
def sfm_sendbox_detail(mid: str) -> str:
    """連絡帳の送信メッセージ詳細を取得する。

    Args:
        mid: メッセージID
    """
    s = _get_sfm_session()
    r = s.get(
        f"{SFM_URL}/sfm/ie-class/message/send-box/detail.php",
        params={"mid": mid},
    )
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    data = {"mid": mid}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            key = cells[0].get_text(strip=True)
            val = cells[1].get_text(strip=True)
            if key and val:
                data[key] = val

    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def sfm_ganbaru_list(page: int = 1, sort: str = "num", order: str = "asc") -> str:
    """がんばるポイントの生徒一覧を取得する。

    Args:
        page: ページ番号
        sort: ソートキー（num=ID, snm=名前, snk=カナ, grd=学年, rlp=ラリーポイント）
        order: ソート順（asc/desc）
    """
    s = _get_sfm_session()
    s.get(f"{SFM_URL}/ganbaru/ie-class/student/listPre.php")
    url = f"{SFM_URL}/ganbaru/ie-class/student/list.php?page={page}&sort={sort}&order={order}"
    r = s.get(url)
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    students = []
    for a in soup.find_all("a", href=lambda h: h and "detail.php?sid=" in h):
        sid = a["href"].split("sid=")[-1]
        name = a.get_text(strip=True)
        tr = a.find_parent("tr")
        if tr:
            tds = tr.find_all("td")
            students.append({
                "sid": sid,
                "生徒名": name,
                "生徒名カナ": tds[2].get_text(strip=True) if len(tds) > 2 else "",
                "学年": tds[3].get_text(strip=True) if len(tds) > 3 else "",
                "ラリーポイント": tds[4].get_text(strip=True) if len(tds) > 4 else "",
            })

    return json.dumps({"page": page, "students": students}, ensure_ascii=False, indent=2)


@mcp.tool()
def sfm_ganbaru_detail(sid: str) -> str:
    """がんばるポイントの生徒詳細（月別ポイント履歴）を取得する。

    Args:
        sid: 生徒ID
    """
    s = _get_sfm_session()
    r = s.get(f"{SFM_URL}/ganbaru/ie-class/student/detail.php?sid={sid}")
    html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    data = {"sid": sid}
    points = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) == 2:
            k = cells[0].get_text(strip=True)
            v = cells[1].get_text(strip=True)
            if re.match(r"\d{4}/\d{2}", k):
                points.append({"month": k, "points": v})
            elif k:
                data[k] = v

    data["ポイント履歴"] = points
    return json.dumps(data, ensure_ascii=False, indent=2)


# =====================
# Movie/Video Tools
# =====================

BRIGHTCOVE_ACCOUNT = "4887491978001"
BRIGHTCOVE_PLAYER = "sJekpxuKD_default"


def _get_brightcove_policy_key() -> str:
    """BrightcoveのPolicy Keyを取得する"""
    r = requests.get(
        f"https://players.brightcove.net/{BRIGHTCOVE_ACCOUNT}/{BRIGHTCOVE_PLAYER}/index.min.js"
    )
    m = re.search(r"(BCpk[A-Za-z0-9\-_]+)", r.text)
    if not m:
        raise Exception("Brightcove policy key not found")
    return m.group(1)


@mcp.tool()
def movie_list() -> str:
    """成功事例・ノウハウ動画の一覧をカテゴリ別に取得する。"""
    s = _get_session()
    r = s.get(f"{BASE_URL}/contents/success_case/class/movie/list.php")
    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    categories = []
    for details in soup.find_all("details", class_="accordion"):
        cat_div = details.find("div", class_="accordion-Category")
        cat = cat_div.get_text(strip=True) if cat_div else "?"
        is_new = bool(details.find("div", class_="accordion-Icon", string=re.compile("NEW")))

        videos = []
        for a in details.find_all("a", href=lambda h: h and "detail.php" in h):
            title_div = a.find("div", class_="movieTitle")
            title = title_div.get_text(strip=True) if title_div else a.get_text(strip=True)
            href = a["href"]
            bcmid = href.split("bcmid=")[-1] if "bcmid=" in href else ""
            view_div = a.find("div", class_="movieViewHistory")
            viewed = "視聴済" if view_div and "視聴済" in view_div.get_text() else "未視聴"
            videos.append({"bcmid": bcmid, "title": title, "viewed": viewed})

        categories.append({"category": cat, "new": is_new, "videos": videos})

    return json.dumps(categories, ensure_ascii=False, indent=2)


@mcp.tool()
def movie_detail(bcmid: str) -> str:
    """動画の詳細情報（タイトル、説明、タグ、Brightcove video ID）を取得する。

    Args:
        bcmid: 動画ID（movie_listで取得可能）
    """
    s = _get_session()
    r = s.get(f"{BASE_URL}/contents/success_case/class/movie/detail.php?bcmid={bcmid}")
    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    data = {"bcmid": bcmid}

    # Brightcove video ID
    vid_el = soup.find(attrs={"data-video-id": True})
    if vid_el:
        data["brightcove_video_id"] = vid_el["data-video-id"]

    # Tags and metadata
    text = soup.get_text()
    tag_match = re.search(r"関連タグ[：:]\s*(.+)", text)
    if tag_match:
        data["tags"] = tag_match.group(1).strip()
    dur_match = re.search(r"動画時間[：:]\s*(.+)", text)
    if dur_match:
        data["duration"] = dur_match.group(1).strip()

    # Download links (PDF etc)
    downloads = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href or "DOWNLOAD" in href:
            downloads.append({"name": a.get_text(strip=True), "url": href})
    if downloads:
        data["downloads"] = downloads

    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def movie_download_url(bcmid: str) -> str:
    """動画のダウンロード用HLS URLを取得する。yt-dlp + ffmpegでダウンロード可能。

    Args:
        bcmid: 動画ID
    """
    s = _get_session()
    r = s.get(f"{BASE_URL}/contents/success_case/class/movie/detail.php?bcmid={bcmid}")
    html = r.content.decode("euc-jisx0213", errors="replace")

    vid_match = re.search(r'data-video-id=["\'](\d+)', html)
    if not vid_match:
        return json.dumps({"error": "Brightcove video ID not found"})

    video_id = vid_match.group(1)
    policy_key = _get_brightcove_policy_key()

    headers = {"Accept": f"application/json;pk={policy_key}"}
    r2 = requests.get(
        f"https://edge.api.brightcove.com/playback/v1/accounts/{BRIGHTCOVE_ACCOUNT}/videos/{video_id}",
        headers=headers,
    )
    if r2.status_code != 200:
        return json.dumps({"error": f"Brightcove API error: {r2.status_code}"})

    data = r2.json()
    hls_url = ""
    for src in data.get("sources", []):
        url = src.get("src", "")
        if "https" in url and "hls" in url and "v4" in url:
            hls_url = url
            break

    return json.dumps({
        "bcmid": bcmid,
        "title": data.get("name", ""),
        "video_id": video_id,
        "duration_sec": data.get("duration", 0) // 1000,
        "hls_url": hls_url,
        "download_command": f'yt-dlp --ffmpeg-location <ffmpeg_path> --merge-output-format mp4 -o "<output>.mp4" "{hls_url}"',
    }, ensure_ascii=False, indent=2)


# =====================
# Applicant Registration Tool
# =====================


def _euc_encode(v: str) -> str:
    """文字列をEUC-JPでURLエンコードする"""
    from urllib.parse import quote as urlquote
    return urlquote(str(v).encode("euc-jisx0213", errors="replace"), safe="")


@mcp.tool()
def applicant_register(
    parent_name_sei: str,
    parent_name_mei: str,
    tel: str,
    email: str,
    grade: str,
    address: str,
    parent_kana_sei: str = "",
    student_name_sei: str = "",
    student_name_mei: str = "",
    relationship: str = "2",
    sex: str = "1",
    memo: str = "",
    contact_channel: str = "1",
    inquiry: str = "0",
    reception_time: str = "",
) -> str:
    """生徒受付管理に新規登録する。

    Args:
        parent_name_sei: 保護者姓（漢字）
        parent_name_mei: 保護者名（漢字）
        parent_kana_sei: 保護者姓カナ（必須。全角カタカナ。漢字から推定して入れる。どうしても推定不能なら「ア」）。※保護者名カナ(parent_kana_mei)・生徒カナは必須ではないので「ア」を入れないこと
        student_name_sei: 生徒姓（漢字、不明なら空）
        student_name_mei: 生徒名（漢字、不明なら空）
        tel: 電話番号（ハイフンなし）
        email: メールアドレス
        grade: 学年（数値ID: 小1=1...小6=6, 中1=7...中3=9, 高1=10...高3=12）
        address: 住所（川口市〜）
        relationship: 関係（父=1, 母=2, 祖父母=3, 本人=4, その他=0）
        sex: 性別（男性=1, 女性=2）
        memo: メモ
        contact_channel: 問合せ経路（HP=1, CC=2, 教室電話=3, 塾ナビ=5, その他=0）
        inquiry: 問合せ内容（その他=0, 体験授業=4, 資料請求=6）
        reception_time: 受付日時（YYYY-MM-DD HH:MM形式、空なら現在時刻）
    """
    from urllib.parse import quote as urlquote

    if not reception_time:
        reception_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    s = _get_session()
    euc = _euc_encode

    # Step 0: GET form page to collect hidden fields
    r = s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantNew.php")
    html = r.content.decode("euc-jisx0213", errors="replace")
    if "新規登録中：入力" not in html:
        return json.dumps({"error": "Could not load registration form"})

    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"action": "./applicantNew.php"})

    # Fields we'll set explicitly - skip these from hidden defaults
    explicit_fields = {
        "btnToConfirm", "reception_time", "contact_channel", "inquiry", "contact_staff",
        "parent_name_sei", "parent_name_mei", "parent_kana_sei", "parent_kana_mei",
        "relationship", "student_name_sei", "student_name_mei", "student_kana_sei", "student_kana_mei",
        "tel", "email", "sex", "grade", "school_name", "zip_code", "prefecture",
        "address1", "address2", "note", "purpose", "inspire_other", "memo",
        "inspire[4]", "motive[4]",
    }

    parts = []
    for inp in form.find_all("input", {"type": "hidden"}):
        name = inp.get("name", "")
        val = inp.get("value", "")
        if name and name not in explicit_fields:
            parts.append(f"{name}={euc(val)}")

    # Form fields
    rt_encoded = reception_time.replace(":", "%3A").replace(" ", "+")
    email_encoded = email.replace("@", "%40")

    parts.extend([
        f"btnToConfirm={euc('登録内容を確認する')}",
        f"reception_time={rt_encoded}",
        f"contact_channel={contact_channel}",
        f"inquiry={inquiry}",
        "contact_staff=1",
        f"parent_name_sei={euc(parent_name_sei)}",
        f"parent_name_mei={euc(parent_name_mei)}",
        f"parent_kana_sei={euc(parent_kana_sei)}",
        "parent_kana_mei=",
        f"relationship={relationship}",
        f"student_name_sei={euc(student_name_sei)}",
        f"student_name_mei={euc(student_name_mei)}",
        "student_kana_sei=",
        "student_kana_mei=",
        f"tel={tel}",
        f"email={email_encoded}",
        f"sex={sex}",
        f"grade={grade}",
        "school_name=",
        "zip_code=",
        "prefecture=11",
        f"address1={euc(address)}",
        "address2=",
        "note=",
        "purpose=",
        "inspire_other=",
        f"memo={euc(memo)}",
        "inspire%5B4%5D=1",
        "motive%5B4%5D=1",
    ])

    body = "&".join(parts).encode("ascii")

    # Step 1: POST to confirm
    r2 = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/applicantNew.php",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    html2 = r2.content.decode("euc-jisx0213", errors="replace")

    if "入力確認" not in html2:
        return json.dumps({"error": "Failed to reach confirm page", "html_preview": html2[:200]})

    # Step 2: POST to register
    body2 = f"btnRegister={euc('登録する')}".encode("ascii")
    r3 = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/applicantNew.php",
        data=body2,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if "Detail" in r3.url:
        import re
        m = re.search(r"rei=(\d+)", r3.url)
        no = m.group(1) if m else "unknown"
        return json.dumps({
            "success": True,
            "問合せNO": no,
            "生徒名": f"{student_name_sei} {student_name_mei}",
            "url": r3.url,
        }, ensure_ascii=False)

    html3 = r3.content.decode("euc-jisx0213", errors="replace")
    if "完了" in html3:
        return json.dumps({"success": True, "生徒名": f"{student_name_sei} {student_name_mei}"}, ensure_ascii=False)

    return json.dumps({"error": "Registration may have failed", "url": r3.url})


def _applicant_edit_post(applicant_id: str, overrides: dict) -> str:
    """applicantEdit.phpへの2ステップPOSTで問い合わせを更新する。

    settle_batch.py の settle_one() と同じロジック。
    overrides: 上書きするフィールド名→値のdict。指定しないフィールドは既存値を保持。
    """
    from urllib.parse import quote as urlquote

    s = _get_session()
    euc = _euc_encode

    # Step 0: GET edit form
    r = s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantEdit.php?rei={applicant_id}&num=1")
    html = r.content.decode("euc-jisx0213", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", action=lambda a: a and "applicantEdit" in a)
    if not form:
        return json.dumps({"error": "Edit form not found", "html_preview": html[:200]})

    # Collect all existing form fields
    explicit = set(overrides.keys()) | {"btnToConfirm"}
    parts = []

    for inp in form.find_all("input", type="hidden"):
        n = inp.get("name", "")
        v = inp.get("value", "")
        if n and n not in explicit:
            parts.append(f"{euc(n)}={euc(v)}")

    for inp in form.find_all("input"):
        n = inp.get("name", "")
        t = inp.get("type", "")
        v = inp.get("value", "")
        if not n or t in ("hidden", "submit", "checkbox"):
            continue
        if n not in explicit:
            parts.append(f"{euc(n)}={euc(v)}")

    for sel in form.find_all("select"):
        n = sel.get("name", "")
        if n and n not in explicit:
            opt = sel.find("option", selected=True)
            val = opt["value"] if opt else ""
            if n == "inquiry" and (not val or val == "0"):
                val = "4"
            elif n == "relationship" and not val:
                val = "2"
            elif n == "contact_staff" and not val:
                val = "1"
            elif n == "sex" and not val:
                val = "1"
            elif n == "grade" and not val:
                val = "1"
            elif n == "prefecture" and not val:
                val = "11"
            parts.append(f"{euc(n)}={val}")

    for ta in form.find_all("textarea"):
        n = ta.get("name", "")
        if n and n not in explicit:
            parts.append(f"{euc(n)}={euc(ta.get_text())}")

    for inp in form.find_all("input", type="checkbox"):
        n = inp.get("name", "")
        if n and inp.get("checked") is not None:
            parts.append(f"{euc(n)}={inp.get('value', '1')}")

    # Apply overrides
    for k, v in overrides.items():
        parts.append(f"{euc(k)}={euc(v)}")

    # Required field补完: 保護者姓カナ(parent_kana_sei)のみ必須。空なら「ア」を入れる。
    # parent_kana_mei / student_kana_* はフォーム上必須ではないので空のままでOK（「ア」を入れない）
    ef = euc("parent_kana_sei")
    has_val = any(p.startswith(f"{ef}=") and len(p) > len(f"{ef}=") for p in parts)
    if not has_val:
        parts = [p for p in parts if not p.startswith(f"{ef}=")]
        parts.append(f"{ef}={euc('ア')}")

    # inspire/motive: at least one must be =1
    if not any("inspire" in p and p.endswith("=1") for p in parts):
        new_parts = []
        replaced = False
        for p in parts:
            if p == "inspire%5B4%5D=0" and not replaced:
                new_parts.append("inspire%5B4%5D=1")
                replaced = True
            else:
                new_parts.append(p)
        parts = new_parts
        if not replaced:
            parts.append("inspire%5B4%5D=1")

    if not any("motive" in p and p.endswith("=1") for p in parts):
        new_parts = []
        replaced = False
        for p in parts:
            if p == "motive%5B4%5D=0" and not replaced:
                new_parts.append("motive%5B4%5D=1")
                replaced = True
            else:
                new_parts.append(p)
        parts = new_parts
        if not replaced:
            parts.append("motive%5B4%5D=1")

    parts.append(f"btnToConfirm={euc('更新内容を確認する')}")
    body = "&".join(parts).encode("ascii")

    # Step 1: POST to confirm
    r2 = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/applicantEdit.php?rei={applicant_id}&num=1",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    html2 = r2.content.decode("euc-jisx0213", errors="replace")

    if "エラー" in html2:
        soup2 = BeautifulSoup(html2, "html.parser")
        errors = [td.get_text(strip=True)[:60] for td in soup2.find_all(["td", "div", "span"])
                  if "エラー" in td.get_text() or "必須" in td.get_text()]
        return json.dumps({"error": "; ".join(errors[:3])}, ensure_ascii=False)

    if "btnRegister" not in html2:
        return json.dumps({"error": "Confirm page not reached", "html_preview": html2[:500]})

    # Step 2: POST to register
    body2 = f"btnRegister={euc('登録する')}".encode("ascii")
    r3 = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/applicantEdit.php?rei={applicant_id}&num=1",
        data=body2,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if "Detail" in r3.url or "applicantDetail" in r3.url:
        return json.dumps({"success": True, "applicant_id": applicant_id}, ensure_ascii=False)

    return json.dumps({"error": "Update may have failed", "url": r3.url})


@mcp.tool()
def applicant_update_memo(
    applicant_id: str,
    memo: str,
    append: bool = True,
) -> str:
    """問い合わせのメモ欄を更新する。

    Args:
        applicant_id: 問合せNO（例: "752167"）
        memo: メモ内容
        append: Trueなら既存メモに追記、Falseなら上書き
    """
    s = _get_session()

    if append:
        # 既存メモを取得
        r = s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantEdit.php?rei={applicant_id}&num=1")
        html = r.content.decode("euc-jisx0213", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        ta = soup.find("textarea", {"name": "memo"})
        current = ta.get_text().strip() if ta else ""
        if current:
            memo = current + "\n" + memo
        # Reset session state (the GET above consumed the form token)
        # Re-do via _applicant_edit_post which does its own GET

    return _applicant_edit_post(applicant_id, {"memo": memo})


@mcp.tool()
def applicant_update(
    applicant_id: str,
    memo: str = "",
    result: str = "",
    issue_date: str = "",
    contact_staff: str = "",
    student_name_sei: str = "",
    student_name_mei: str = "",
    student_kana_sei: str = "",
    student_kana_mei: str = "",
    parent_kana_sei: str = "",
    parent_kana_mei: str = "",
    zip_code: str = "",
    address1: str = "",
    address2: str = "",
    tel: str = "",
    email: str = "",
) -> str:
    """問い合わせの各フィールドを更新する。指定したフィールドのみ更新。

    注意: 必須フィールドは保護者姓カナ(parent_kana_sei)のみ。名(parent_kana_mei)・生徒カナは任意（空欄OK、「ア」を入れない）。

    Args:
        applicant_id: 問合せNO（例: "752167"）
        memo: メモ欄（空なら変更しない）
        result: 結果（1=入会, 2=入会せず, 空なら変更しない）
        issue_date: 入会成約日 YYYY-MM-DD（空なら変更しない）
        contact_staff: 初回問合せ対応者（1=室長, 空なら変更しない）
        student_name_sei: 生徒姓（漢字、空なら変更しない）
        student_name_mei: 生徒名（漢字、空なら変更しない）
        student_kana_sei: 生徒姓カナ（空なら変更しない）
        student_kana_mei: 生徒名カナ（空なら変更しない）
        parent_kana_sei: 保護者姓カナ（空なら変更しない）
        parent_kana_mei: 保護者名カナ（空なら変更しない）
        zip_code: 郵便番号（ハイフン自動除去、空なら変更しない）
        address1: 住所（空なら変更しない）
        address2: 建物名（空なら変更しない）
        tel: 電話番号（ハイフン自動除去、空なら変更しない）
        email: メールアドレス（空なら変更しない）
    """
    # 電話番号・郵便番号のハイフン自動除去
    if tel:
        tel = re.sub(r"[^\d]", "", tel)
    if zip_code:
        zip_code = re.sub(r"[^\d]", "", zip_code)

    overrides = {}
    field_map = {
        "memo": memo, "result": result, "issue_date": issue_date,
        "contact_staff": contact_staff,
        "student_name_sei": student_name_sei, "student_name_mei": student_name_mei,
        "student_kana_sei": student_kana_sei, "student_kana_mei": student_kana_mei,
        "parent_kana_sei": parent_kana_sei, "parent_kana_mei": parent_kana_mei,
        "zip_code": zip_code, "address1": address1, "address2": address2,
        "tel": tel, "email": email,
    }
    for k, v in field_map.items():
        if v:
            overrides[k] = v

    if not overrides:
        return json.dumps({"error": "No fields to update"})

    return _applicant_edit_post(applicant_id, overrides)


@mcp.tool()
def applicant_delete(applicant_id: str) -> str:
    """問い合わせを削除する。

    一覧画面のcheckbox経由で削除する（内部IDとreiの対応をHTML解析で取得）。

    Args:
        applicant_id: 問合せNO（例: "752171"）
    """
    s = _get_session()
    euc = _euc_encode

    # Step 1: 一覧前処理
    s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantListPre.php")

    # Step 2: 一覧を検索なしで取得（最新50件）
    r = s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantList.php")
    html = r.content.decode("euc-jisx0213", errors="replace")

    # Step 3: HTMLからrce valueとreiの対応を取得
    soup = BeautifulSoup(html, "html.parser")
    rce_value = None

    rows = soup.find_all("tr")
    for row in rows:
        link = row.find("a", href=lambda h: h and f"rei={applicant_id}" in h)
        if link:
            cb = row.find("input", {"type": "checkbox", "name": re.compile(r"^rce\[")})
            if cb:
                rce_value = cb.get("value", "")
                break

    # 見つからなければ検索で探す
    if not rce_value:
        # applicant_idで詳細を取得してから名前で検索
        r_detail = s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantDetail.php?rei={applicant_id}&num=1")
        detail_html = r_detail.content.decode("euc-jisx0213", errors="replace")
        if "ログインタイムアウト" in detail_html:
            return json.dumps({"error": "Session expired"})
        if "applicantList" in r_detail.url:
            return json.dumps({"error": f"Applicant {applicant_id} not found (redirected to list)"})

        # 全件CSVからも探す
        r_csv = s.post(f"{BASE_URL}/contents/boshu/class/applicant/download.php", data={"btn_download": ""})
        csv_text = r_csv.content.decode("cp932", errors="replace")
        reader = csv.DictReader(io.StringIO(csv_text))
        target_name = None
        for row_data in reader:
            if row_data.get("問合せNO", "").strip() == applicant_id:
                target_name = row_data.get("保護者氏名（漢字）", "").split()[0]
                break

        if target_name:
            # 名前で検索して一覧を取得
            search_body = f"name={euc(target_name)}&search=1".encode("ascii")
            r_search = s.post(
                f"{BASE_URL}/contents/boshu/class/applicant/applicantList.php",
                data=search_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            search_html = r_search.content.decode("euc-jisx0213", errors="replace")
            soup2 = BeautifulSoup(search_html, "html.parser")
            for row in soup2.find_all("tr"):
                link = row.find("a", href=lambda h: h and f"rei={applicant_id}" in h)
                if link:
                    cb = row.find("input", {"type": "checkbox", "name": re.compile(r"^rce\[")})
                    if cb:
                        rce_value = cb.get("value", "")
                        break

    if not rce_value:
        return json.dumps({"error": f"Could not find internal ID for applicant {applicant_id}"})

    # Step 4: 削除POST
    delete_body = f"btn_delete={euc('削除する')}&rce%5B0%5D={rce_value}".encode("ascii")
    r_del = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/applicantDelete.php",
        data=delete_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if "applicantList" in r_del.url or r_del.status_code == 200:
        # 削除確認: 詳細ページにアクセスしてリダイレクトされるか確認
        r_check = s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantDetail.php?rei={applicant_id}&num=1")
        if "applicantList" in r_check.url:
            return json.dumps({"success": True, "applicant_id": applicant_id, "deleted": True})
        else:
            return json.dumps({"error": "Delete may have failed - record still accessible"})

    return json.dumps({"error": "Delete request failed", "status": r_del.status_code})


# --- Entry point ---
if __name__ == "__main__":
    mcp.run()
