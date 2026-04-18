"""
問合リストExcel書き込みスクリプト
新規応募をtactgroup.netから取得し、日報Excelの問合リストに追記する

毎朝5時にタスクスケジューラで実行
xlwingsでExcelアプリ経由で書き込むので数式やマクロを壊さない
"""
import csv
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
import xlwings as xw

# --- 設定（環境変数またはここを書き換え） ---
BASE_URL = os.environ.get("WEBSUPPORT_URL", "https://www.tactgroup.net")
ACCOUNT = os.environ.get("WEBSUPPORT_ACCOUNT", "")
PASSWORD = os.environ.get("WEBSUPPORT_PASSWORD", "")

EXCEL_DIR = Path(os.environ.get("NIPPOU_DIR", ""))
CLASSROOM_NAME = os.environ.get("NIPPOU_CLASSROOM_NAME", "")
SHEET_NAME = "問合リスト"
HEADER_ROW = 3  # ヘッダー行
DATA_START_ROW = 5  # データ開始行（Row4は例）


def get_excel_path(dt: datetime = None) -> str:
    """対象月の日報Excelパスを返す。"""
    if dt is None:
        dt = datetime.now()
    return str(EXCEL_DIR / f"日報{dt.year}年{dt.month}月【{CLASSROOM_NAME}】.xlsx")

STATE_FILE = Path(__file__).parent / "excel_last_dt.json"
LOG_FILE = Path(__file__).parent / "excel_writer.log"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_dt": ""}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def login() -> requests.Session:
    s = requests.Session()
    s.get(f"{BASE_URL}/contents/class/login/login.php")
    r = s.post(
        f"{BASE_URL}/contents/class/login/login.php",
        data={
            "classAccount": ACCOUNT,
            "classPassword": PASSWORD,
            "btnLogin.x": "42",
            "btnLogin.y": "11",
        },
    )
    if "top.php" not in r.url:
        log("ERROR: Login failed")
        sys.exit(1)
    return s


def get_new_applicants(s: requests.Session, last_dt: str) -> list[dict]:
    s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantListPre.php")
    r = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/download.php",
        data={"btn_download": ""},
    )
    if r.headers.get("Content-Disposition") is None:
        log("ERROR: CSV download failed")
        sys.exit(1)

    text = r.content.decode("cp932")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    new = []
    for row in rows:
        dt = row.get("受付日時", "")
        if not dt:
            continue
        if last_dt and dt <= last_dt:
            continue
        new.append(row)

    new.sort(key=lambda x: x.get("受付日時", ""))
    return new


def to_halfwidth(text: str) -> str:
    """全角数字・英字・記号を半角に変換"""
    result = []
    for ch in text:
        code = ord(ch)
        # 全角数字 ０-９ → 0-9
        if 0xFF10 <= code <= 0xFF19:
            result.append(chr(code - 0xFF10 + 0x30))
        # 全角英大文字 Ａ-Ｚ → A-Z
        elif 0xFF21 <= code <= 0xFF3A:
            result.append(chr(code - 0xFF21 + 0x41))
        # 全角英小文字 ａ-ｚ → a-z
        elif 0xFF41 <= code <= 0xFF5A:
            result.append(chr(code - 0xFF41 + 0x61))
        # 全角ハイフン・マイナス
        elif ch in ('－', '―', '‐'):
            result.append('-')
        else:
            result.append(ch)
    return "".join(result)


def convert_grade(grade: str) -> str:
    """CSVの学年形式をExcelの選択肢形式に変換。中学３年→中３、小学６年→小６、高校１年→高１"""
    import re
    if not grade:
        return ""
    m = re.match(r"(小学|中学|高校)(\d+|[０-９]+)年?", grade.replace("小学校", "小学"))
    if m:
        prefix = m.group(1)
        num = m.group(2)
        prefix_map = {"小学": "小", "中学": "中", "高校": "高"}
        short_prefix = prefix_map.get(prefix, prefix)
        return f"{short_prefix}{num}"
    # 既に短縮形（小６等）ならそのまま
    if re.match(r"(小|中|高)[０-９\d]", grade):
        return grade
    return grade


def route_to_method(route: str) -> str:
    """問合せ経路からExcelの問合せ手段(Y列)を判定。初期設定シートの選択肢に合わせる"""
    if not route:
        return ""
    if route in ("CC",):
        return "本部FD"
    if "電話" in route or "TEL" in route:
        return "教室TEL"
    if "来校" in route or "来訪" in route or "直接" in route:
        return "直来"
    return "HP経由"


def route_to_media(applicant: dict) -> str:
    """問合媒体(Z列)を判定。初期設定シートの選択肢に合わせる。
    選択肢: 折込チラシ, TVCM, ポスティング, 校門前・駅前配布, HP, 看板・外パンフレット, 紹介, DM, 不明
    """
    motive = applicant.get("問合せ動機", "").strip()
    inspire = applicant.get("認知動機", "").strip()
    source = motive if motive else inspire
    if not source:
        return "不明"

    # 選択肢へのマッピング
    if "チラシ" in source or "折込" in source:
        return "折込チラシ"
    if "CM" in source or "テレビ" in source or "TV" in source:
        return "TVCM"
    if "ポスティング" in source:
        return "ポスティング"
    if "校門" in source or "駅前" in source or "配布" in source:
        return "校門前・駅前配布"
    if "看板" in source or "パンフ" in source:
        return "看板・外パンフレット"
    if "紹介" in source:
        return "紹介"
    if "DM" in source:
        return "DM"
    # 塾ナビ, HP, コエテコ, テラコヤプラス, クチコミサイト, 外部サイト等 → HP
    if any(k in source for k in ("HP", "ナビ", "サイト", "コエテコ", "テラコヤ", "ネット", "Web", "web")):
        return "HP"
    return "不明"


def build_address(applicant: dict) -> str:
    """住所を整形。埼玉県・川口市を除去し、町域以下のみ"""
    address = applicant.get("ご住所", "").strip()
    building = applicant.get("建物名", "").strip()

    # 埼玉県・川口市を除去
    address = address.replace("埼玉県", "").replace("川口市", "").strip()

    if building:
        address = f"{address}{building}"
    return address


def build_name(applicant: dict) -> str:
    """氏名を整形。生徒名あり→生徒名、なし→保護者名（保護者）"""
    student = applicant.get("生徒氏名（漢字）", "").strip()
    guardian = applicant.get("保護者氏名（漢字）", "").strip()

    if student:
        return student
    elif guardian:
        return f"{guardian}（保護者）"
    else:
        # カナで代替
        student_kana = applicant.get("生徒氏名（カナ）", "").strip()
        guardian_kana = applicant.get("保護者氏名（カナ）", "").strip()
        if student_kana:
            return student_kana
        elif guardian_kana:
            return f"{guardian_kana}（保護者）"
    return ""


def extract_eiken_info(applicant: dict) -> str:
    """メモ欄から英検情報を抽出。「第1回英検2級」等の形式で返す"""
    import re
    memo = applicant.get("メモ", "").strip()
    if not memo:
        return ""
    # パターン: "英検第1回 3級申込" or "英検3級" or "第1回英検2級"
    grade_m = re.search(r"(\d+)\s*級", memo)
    round_m = re.search(r"第\s*(\d+)\s*回", memo)
    if grade_m and "英検" in memo:
        grade = grade_m.group(1)
        round_str = f"第{round_m.group(1)}回" if round_m else ""
        return f"{round_str}英検{grade}級".strip()
    return ""


def build_remarks(applicant: dict) -> str:
    """備考を整形。住所（川口市除去）, 英検情報, 質問・要望"""
    parts = []
    addr = build_address(applicant)
    if addr:
        parts.append(addr)
    eiken = extract_eiken_info(applicant)
    if eiken:
        parts.append(eiken)
    note = applicant.get("ご質問・ご要望", "").strip()
    if note:
        parts.append(note)
    return ", ".join(parts)


def write_to_excel(applicants: list[dict]):
    if not applicants:
        log("No new applicants to write")
        return

    log(f"Writing {len(applicants)} applicants to Excel...")

    app = xw.App(visible=False)
    try:
        wb = app.books.open(get_excel_path())
        ws = wb.sheets[SHEET_NAME]

        # B列（日付）でデータが入っている最終行を探す
        # A列はNo.が全行に入っているので使えない
        next_row = DATA_START_ROW
        for row_idx in range(DATA_START_ROW, 300):
            val = ws.range(f"B{row_idx}").value
            if val is not None:
                next_row = row_idx + 1
            else:
                next_row = row_idx
                break

        for applicant in applicants:
            dt_str = applicant.get("受付日時", "")

            # 日付と時刻を分離（日付は時刻なしで書き込む）
            date_val = None
            time_str = ""
            if dt_str:
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    date_val = dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    time_str = dt.strftime("%H:%M")
                except:
                    pass

            name = to_halfwidth(build_name(applicant))
            grade = convert_grade(applicant.get("お子さまの学年", ""))
            phone_raw = to_halfwidth(applicant.get("電話番号", "")).replace("-", "").replace(" ", "")
            # ハイフン付きフォーマットにして文字列として書き込む（数値化防止）
            if len(phone_raw) == 11:
                phone = f"{phone_raw[:3]}-{phone_raw[3:7]}-{phone_raw[7:]}"
            elif len(phone_raw) == 10:
                phone = f"{phone_raw[:3]}-{phone_raw[3:6]}-{phone_raw[6:]}"
            else:
                phone = phone_raw
            school = to_halfwidth(applicant.get("学校名", "").strip())
            route = applicant.get("問合せ経路", "")

            # 書き込み（数式列 A,E,X,AF,AI は触らない）
            if date_val:
                ws.range(f"B{next_row}").value = date_val      # Col2 日付
            if time_str:
                ws.range(f"C{next_row}").value = time_str      # Col3 時間
            if grade:
                ws.range(f"D{next_row}").value = grade         # Col4 学年（E列は数式で自動判定）
            if name:
                ws.range(f"F{next_row}").value = name          # Col6 生徒氏名
            if phone:
                ws.range(f"G{next_row}").value = phone         # Col7 TEL
            if school:
                ws.range(f"H{next_row}").value = school        # Col8 学校名
            ws.range(f"W{next_row}").value = "10%"                    # Col23 見込み（初期値）
            ws.range(f"Y{next_row}").value = route_to_method(route)  # Col25 問合せ手段
            media = route_to_media(applicant)
            if media:
                ws.range(f"Z{next_row}").value = media         # Col26 問合媒体
            remarks = to_halfwidth(build_remarks(applicant))
            if remarks:
                ws.range(f"AH{next_row}").value = remarks      # Col34 備考

            log(f"  Row {next_row}: {name} ({grade}) {route}")
            next_row += 1

        wb.save()
        log(f"Excel saved: {get_excel_path()}")
    finally:
        app.quit()


def write_merged(applicants: list[dict]):
    """外部から統合済みデータを受け取ってExcelに書き込む。
    daily_sync.py 等から呼ばれる。状態ファイルも更新する。
    """
    if not applicants:
        log("No applicants to write (from external)")
        return

    write_to_excel(applicants)

    # 最新日時を更新
    state = load_state()
    max_dt = max(a.get("受付日時", "") for a in applicants)
    state["last_dt"] = max_dt
    save_state(state)
    log(f"Done (external). Updated last DT: {max_dt}")


def sync_memo_updates(s: requests.Session):
    """過去3ヶ月の問い合わせのメモ欄が更新されていたら日報Excelの備考欄(AH列)に反映する。

    WebSupportのメモ欄にアポや電話履歴を書いたら、日報にも転記される。
    """
    import re as _re

    # 過去3ヶ月分のCSVを取得
    s.get(f"{BASE_URL}/contents/boshu/class/applicant/applicantListPre.php")
    r = s.post(
        f"{BASE_URL}/contents/boshu/class/applicant/download.php",
        data={"btn_download": ""},
    )
    text = r.content.decode("cp932")
    reader = csv.DictReader(io.StringIO(text))
    all_rows = list(reader)

    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    # 過去3ヶ月でメモ欄が空でないレコードを抽出
    targets = {}
    for row in all_rows:
        dt = row.get("受付日時", "")
        memo = row.get("メモ", "").strip()
        if dt >= cutoff and memo:
            # 電話番号をキーにして（氏名だけでは同姓同名の可能性）
            phone = row.get("電話番号", "").replace("-", "").strip()
            name = row.get("生徒氏名（漢字）", "").strip() or row.get("保護者氏名（漢字）", "").strip()
            targets[(phone, name)] = row

    if not targets:
        log("Memo sync: no updates found")
        return

    # 対象月のExcelファイルを特定（過去3ヶ月分）
    now = datetime.now()
    months = set()
    for row in targets.values():
        try:
            dt = datetime.strptime(row["受付日時"][:10], "%Y-%m-%d")
            months.add((dt.year, dt.month))
        except:
            pass

    updated_count = 0
    app = xw.App(visible=False)
    try:
        for year, month in sorted(months):
            excel_path = str(EXCEL_DIR / f"日報{year}年{month}月【{CLASSROOM_NAME}】.xlsx")
            if not Path(excel_path).exists():
                continue

            wb = app.books.open(excel_path)
            ws = wb.sheets[SHEET_NAME]

            # Excel内の既存データを走査（B=日付, F=氏名, G=TEL, AH=備考）
            for row_idx in range(DATA_START_ROW, 300):
                b_val = ws.range(f"B{row_idx}").value
                if b_val is None:
                    break

                excel_phone = str(ws.range(f"G{row_idx}").value or "").replace("-", "").strip()
                excel_name = str(ws.range(f"F{row_idx}").value or "").strip()
                current_remarks = str(ws.range(f"AH{row_idx}").value or "").strip()

                # WebSupportのレコードとマッチ
                matched_row = targets.get((excel_phone, excel_name))
                if not matched_row:
                    # 名前が「保護者」形式の場合も試す
                    for (ph, nm), row_data in targets.items():
                        if ph == excel_phone:
                            matched_row = row_data
                            break

                if not matched_row:
                    continue

                # 英検情報
                eiken = extract_eiken_info(matched_row)

                # 備考欄を再構築: 住所 + 英検情報のみ
                addr = build_address(matched_row)
                parts = []
                if addr:
                    parts.append(addr)
                if eiken:
                    parts.append(eiken)
                new_remarks = to_halfwidth(", ".join(parts))

                if new_remarks != current_remarks:
                    ws.range(f"AH{row_idx}").value = new_remarks
                    log(f"  Memo sync Row {row_idx}: {excel_name} -> {new_remarks[:50]}...")
                    updated_count += 1

            wb.save()
            log(f"Memo sync saved: {excel_path}")

    finally:
        app.quit()

    log(f"Memo sync: {updated_count} rows updated")


def main():
    state = load_state()
    last_dt = state.get("last_dt", "")

    s = login()

    # 初回実行: 現在の最新日時を記録するだけ
    if not last_dt:
        log("First run: initializing state...")
        applicants = get_new_applicants(s, "")
        if applicants:
            max_dt = max(a.get("受付日時", "") for a in applicants)
            state["last_dt"] = max_dt
            save_state(state)
            log(f"State initialized. Last DT: {max_dt}")
        return

    # 1. 新規問い合わせの追記
    new_applicants = get_new_applicants(s, last_dt)
    if new_applicants:
        write_to_excel(new_applicants)
        max_dt = max(a.get("受付日時", "") for a in new_applicants)
        state["last_dt"] = max_dt
        save_state(state)
        log(f"New applicants: {len(new_applicants)}. Updated last DT: {max_dt}")
    else:
        log("No new applicants.")

    # 2. 過去3ヶ月のメモ更新同期
    sync_memo_updates(s)


if __name__ == "__main__":
    main()
