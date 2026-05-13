"""
schoolie-net CMS 全フィールド装飾サンプルスクリプト

教室HPの各テキストフィールド(教室長挨拶・講師紹介・キャンペーン・合格実績等)に
HTML装飾を適用して指定版に一時保存(status=1)するテンプレート。
各教室はプレースホルダ部分を実際の内容に置き換えて使用する。

環境変数:
  SCHOOLIE_USERNAME       CMSログインID
  SCHOOLIE_PASSWORD       CMSログインパスワード
  SCHOOLIE_CLASSROOM_ID   教室ID(URLパスに使う数値、教室管理画面のURLから確認)
  SCHOOLIE_CLASSROOM_CD   公式フォームの ccd 値(4桁、各教室固有)
  SCHOOLIE_EDITION_ID     対象版ID(コピー後の編集版ID)
  SCHOOLIE_PHONE          (任意) 教室電話番号
  SCHOOLIE_HOURS          (任意) 営業時間表記
"""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests
from bs4 import BeautifulSoup


# ---------------- Config (環境変数) ----------------
USERNAME = os.environ["SCHOOLIE_USERNAME"]
PASSWORD = os.environ["SCHOOLIE_PASSWORD"]
CLASSROOM_ID = os.environ["SCHOOLIE_CLASSROOM_ID"]
CLASSROOM_CD = os.environ["SCHOOLIE_CLASSROOM_CD"]
EDITION_ID = os.environ["SCHOOLIE_EDITION_ID"]
PHONE = os.environ.get("SCHOOLIE_PHONE", "")
HOURS = os.environ.get("SCHOOLIE_HOURS", "")

BASE = "https://www.schoolie-net.jp/console"
S = "#0a1e5c"  # navy theme color
RESERVE_URL = f"https://www.schoolie-net.jp/form/entry.php?mode=2&ccd={CLASSROOM_CD}"
RESERVE_BTN = (
    f'<br><a href="{RESERVE_URL}" '
    'style="display:inline-block;background:#06c;color:#fff;padding:8px 20px;'
    'border-radius:4px;text-decoration:none;font-size:14px;font-weight:bold;">'
    "学習相談・教室見学はこちら</a>"
)
INQUIRY_BTN = (
    f'<br><a href="{RESERVE_URL}" '
    'style="display:inline-block;background:#c00;color:#fff;padding:6px 16px;'
    'border-radius:4px;text-decoration:none;font-size:13px;font-weight:bold;">'
    "検定に関するお問い合わせはこちら</a>"
)
TEL_FOOTER = (
    f'<br><span style="font-size:12px;color:#555;">TEL:{PHONE}'
    + (f"　受付{HOURS}" if HOURS else "")
    + "</span>"
) if PHONE else ""


# ---------------- Login ----------------
s = requests.Session()
s.headers.update(
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
)

r = s.get(f"{BASE}/")
csrf = BeautifulSoup(r.text, "html.parser").find("input", {"name": "_csrfToken"})["value"]
s.post(
    f"{BASE}/",
    data={"_csrfToken": csrf, "username": USERNAME, "password": PASSWORD},
)
print("Logged in")


# ---------------- Fetch edit form ----------------
r = s.get(f"{BASE}/editions/{CLASSROOM_ID}/edit/{EDITION_ID}")
soup = BeautifulSoup(r.text, "html.parser")

form_data: dict = {}
for inp in soup.find_all("input", {"type": "hidden"}):
    n = inp.get("name", "")
    if n:
        form_data[n] = inp.get("value", "") or ""
for inp in soup.find_all("input", {"type": "radio"}):
    if inp.get("checked") is not None:
        form_data[inp["name"]] = inp.get("value", "")
for inp in soup.find_all("input", {"type": "text"}):
    n = inp.get("name", "")
    if n:
        form_data[n] = inp.get("value", "") or ""
for ta in soup.find_all("textarea"):
    n = ta.get("name", "")
    if n:
        form_data[n] = ta.get_text() or ""
for inp in soup.find_all("input", {"type": "checkbox"}):
    if inp.get("checked") is None:
        continue
    n = inp.get("name", "")
    v = inp.get("value", "") or ""
    if n.endswith("[]"):
        form_data.setdefault(n, []).append(v)
    else:
        form_data[n] = v
for sel in soup.find_all("select"):
    n = sel.get("name", "")
    if not n:
        continue
    opt = sel.find("option", selected=True) or sel.find("option")
    if opt is not None:
        form_data[n] = opt.get("value", "") or ""

print(f"Form fields: {len(form_data)}")


# ---------------- フィールド書き込み(各教室で内容を埋める) ----------------
# 以下は装飾用のプレースホルダ。各教室で自校の文章に書き換えてから実行する。
#
# テキスト系フィールド(textarea)はHTMLサニタイズされない仕様なので、
# <span style="..."> 等の装飾HTMLを直接埋め込み可能。
# 装飾のテーマカラーは S 変数で統一。

# 教室長あいさつ
form_data["classroom_staff[greetings_header]"] = (
    f'<span style="font-size:20px;">(教室長メッセージ見出し)</span>'
)
form_data["classroom_staff[greetings]"] = (
    f'<span style="font-size:15px;line-height:1.8;color:{S};font-weight:bold;">'
    "(教室長メッセージ本文)</span>"
    + RESERVE_BTN
)

# 講師紹介(最大10名)
for i in range(10):
    form_data[f"classroom_staff[staff_details][{i}][header]"] = (
        f'<span style="font-size:20px;">(講師見出し{i+1})</span>'
    )
    form_data[f"classroom_staff[staff_details][{i}][content]"] = (
        f'<span style="font-size:15px;line-height:1.8;color:{S};font-weight:bold;">'
        f"(講師{i+1}の自己紹介本文)</span>"
        f'<br><span style="font-size:13px;color:#555;">'
        f"<b>担当科目</b>:(科目)</span>"
    )

# 詳細情報(近隣学校・合格実績)
form_data["classroom_detail[access]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(アクセス情報)</span>'
)
form_data["classroom_detail[native_school1]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(近隣小学校一覧)</span>'
)
form_data["classroom_detail[native_school2]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(近隣中学校一覧)</span>'
)
form_data["classroom_detail[native_school3]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(近隣高校一覧)</span>'
)
form_data["classroom_detail[success_record3]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(中学受験合格実績)</span>'
)
form_data["classroom_detail[success_record4]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(高校受験合格実績)</span>'
)
form_data["classroom_detail[success_record5]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(大学受験合格実績)</span>'
)

# お知らせ(教室紹介・キャンペーン・トピックス・検定)
form_data["classroom_info[introduction]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(教室紹介本文)</span>'
    + TEL_FOOTER
    + RESERVE_BTN
)
form_data["classroom_info[info_details][campaign][0][content]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(キャンペーン内容・期限)</span>'
    + RESERVE_BTN + TEL_FOOTER
)
form_data["classroom_info[info_details][topics][0][content]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">(トピックス本文)</span>'
    + RESERVE_BTN + TEL_FOOTER
)
form_data["classroom_info[info_details][classroom][0][content]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">(教室情報本文)</span>'
    + TEL_FOOTER
)
form_data["classroom_info[info_details][free][0][header]"] = (
    "(検定情報見出し:英検/漢検/数検等)"
)
form_data["classroom_info[info_details][free][0][content]"] = (
    f'<span style="font-size:13px;color:{S};font-weight:bold;">(検定の日程一覧)</span>'
    + INQUIRY_BTN + TEL_FOOTER
)
form_data["classroom_info[info_details][free][1][content]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">(テスト対策案内)</span>'
    + TEL_FOOTER
)
form_data["classroom_info[info_details][free][2][content]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(開校日程の変則案内)</span>'
)

# 体験談(最大4名)
for i in range(4):
    form_data[f"classroom_experiences[{i}][content]"] = (
        f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">'
        f"(体験談{i+1}の本文)</span>"
    )

# コース・時間割・料金
form_data["classroom_course[timetable_info]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(時間割の説明)</span>'
    + RESERVE_BTN
)
form_data["classroom_course[fee_info]"] = (
    f'<span style="font-size:14px;color:{S};font-weight:bold;">(料金体系の説明)</span>'
)


# ---------------- Save (一時保存 status=1) ----------------
form_data["status"] = "1"
r = s.post(
    f"{BASE}/editions/{CLASSROOM_ID}/edit/{EDITION_ID}",
    data=form_data,
    allow_redirects=True,
)
print(f"Save: {r.status_code} -> {r.url}")
if "edit" in r.url:
    print("SAVE SUCCESS")
    print(f"Preview: {BASE}/editions/{CLASSROOM_ID}/preview/{EDITION_ID}")
else:
    print("Save may have failed")
