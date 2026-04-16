"""
schoolie-net CMS 全フィールド装飾スクリプト
版252355に対してHTML装飾を適用し一時保存する
"""
import os
import sys
import io
import re
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests
from bs4 import BeautifulSoup

S = "#0a1e5c"  # navy

s = requests.Session()
s.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
)

# Login
r = s.get("https://www.schoolie-net.jp/console/")
soup = BeautifulSoup(r.text, "html.parser")
csrf = soup.find("input", {"name": "_csrfToken"})["value"]
s.post(
    "https://www.schoolie-net.jp/console/",
    data={
        "_csrfToken": csrf,
        "username": os.environ["SCHOOLIE_USERNAME"],
        "password": os.environ["SCHOOLIE_PASSWORD"],
    },
)
print("Logged in")

NEW_ID = "252421"

# NOTE: 252421版にはテスト用scriptタグが残っている可能性がある
# 全フィールド上書きなので自動的にクリーンになる

# Get edit page
r = s.get(f"https://www.schoolie-net.jp/console/editions/696/edit/{NEW_ID}")
soup = BeautifulSoup(r.text, "html.parser")

# Build complete form data
form_data = {}
for inp in soup.find_all("input", {"type": "hidden"}):
    name = inp.get("name", "")
    if name:
        form_data[name] = inp.get("value", "")
for inp in soup.find_all("input", {"type": "radio"}):
    if inp.get("checked") is not None:
        form_data[inp["name"]] = inp.get("value", "")
for inp in soup.find_all("input", {"type": "text"}):
    name = inp.get("name", "")
    if name:
        form_data[name] = inp.get("value", "")
for ta in soup.find_all("textarea"):
    name = ta.get("name", "")
    if name:
        form_data[name] = ta.text
for inp in soup.find_all("input", {"type": "checkbox"}):
    if inp.get("checked") is not None:
        name = inp.get("name", "")
        val = inp.get("value", "")
        if name.endswith("[]"):
            if name not in form_data:
                form_data[name] = []
            form_data[name].append(val)
        else:
            form_data[name] = val

print(f"Form fields: {len(form_data)}")

# 予約リンク（schoolie-net 公式フォーム）
RESERVE_URL = "https://www.schoolie-net.jp/form/entry.php?mode=2&ccd=5558"
RESERVE_BTN = f'<br><a href="{RESERVE_URL}" style="display:inline-block;background:#06c;color:#fff;padding:8px 20px;border-radius:4px;text-decoration:none;font-size:14px;font-weight:bold;">学習相談・教室見学はこちら</a>'

# 訪問者カウンター（不可視トラッキングピクセル + イベント計測JS）
TRACKING_PIXEL = '<img src="https://yarukiswitch.net/hp-counter/pixel.gif" width="1" height="1" style="position:absolute;left:-9999px;" alt="">'
# 外部JSファイルとして読み込む（インラインscriptタグはCMSがブロックする）
TRACKING_SCRIPT = '<script src="https://yarukiswitch.net/hp-counter/static/ui.js"></script>'

# ========== STAFF ==========
form_data["classroom_staff[greetings_header]"] = '<span style="font-size:20px;">\u300c昨日より今日、今日より明日\u300dを信じて</span>'
form_data["classroom_staff[greetings]"] = (
    f'<span style="font-size:15px;line-height:1.8;color:{S};font-weight:bold;">'
    "2012年以来、塾業界一筋で勤務して参りました。<br>"
    "中学・高校・大学受験いずれも対応して来ました。<br>"
    "教務、受験に関する知識を活かして、生徒さんの目標達成を応援したいと思います。<br><br>"
    "仕事に携わっていて嬉しい瞬間は、なんといっても生徒さんが目標を達成したその笑顔を見たとき。<br>"
    "その子の中で、昨日より今日、今日より明日と伸びて行けたなら、いつしか大きな進歩を遂げるものと信じています！</span><br><br>"
    '<span style="font-size:13px;color:#555;line-height:1.6;">'
    "<b>略歴</b>：1991年生。青森県立八戸東高校、茨城大学農学部卒。新卒で塾業界に就職。2015年、当社入社。<br>"
    "<b>資格</b>：英検 準1級、漢語水平考試（HSK）3級<br>"
    "<b>趣味</b>：The Beatles、バイクツーリング、ギター、料理、PC開発関連、古典籍現代語訳<br>"
    "<b>指導科目</b>：小中5科目＋高校数学・英語</span><br><br>"
    '<span style="font-size:16px;color:#c00;font-weight:bold;">'
    "皆さまのお役に立てるよう力を尽くします。お気軽にお声がけください！</span>"
    + RESERVE_BTN
)

# 252355版のスタッフ配置に合わせる（[6]を中村先生に修正）
staff = [
    # [0] 肥沼先生
    ("一緒に勉強のニガテをなくしましょう！", "スクールIE鳩ヶ谷校の肥沼 希（こいぬま のぞみ）です。<br>社会人の専任講師として勤務しています。<br>勉強はニガテ、という生徒さんにも寄り添い、丁寧でわかりやすい授業を行います。<br>「わかった！」を増やせるよう、一緒にがんばりましょう！", "国語、算数・数学、理科、社会、英語"),
    # [1] 高野先生
    ("理系科目はお任せください", "皆さんはじめまして！鳩ヶ谷校講師の高野です。<br>数学や化学が得意科目です。<br>一歩ずつ着実にできるよう丁寧な指導を心がけています。<br>焦らずにできることを１つずつ一緒に増やしていきましょう！", "算数・数学、英語、理科、物理基礎、生物基礎、化学"),
    # [2] 髙野理香子先生
    ("一緒に頑張りましょう！", "こんにちは、髙野理香子です。<br>勉強が楽しいと思ってもらえるような授業ができるようにがんばります！<br>明るく元気に授業していきますので一緒にがんばりましょう。", "英語、数学、理科、高校化学"),
    # [3] ことばの力
    ("ことばの力で、あなたの未来を応援します", "大学では日本語学を専攻しています。将来は国語の先生になるのが夢です。<br>「ことば」の面白さや奥深さを、授業を通じて皆さんに伝えていけたらと思っています。<br><br>勉強は大変なこともあるけれど、一歩ずつ進めば必ず力になります。<br>皆さんの「できた！」「わかった！」の瞬間を一緒に増やしていきましょう！全力で応援します！", "国語、社会、英語"),
    # [4] ★中村滿先生（講師情報5 — 旧版250175から復元）
    ("小さなキッカケが大きな喜びに", "スクールIE 鳩ヶ谷校講師の中村と申します。<br>皆さんの中に、勉強はつまらない・ツラいものだと感じている人はいませんか？<br>「わかった！」「できた！」「楽しい！」と言ってもらえるように、日々工夫して指導を行っています。<br>小さな成功体験の積み重ねが、やがて大きな自信につながっていくものです。", ""),
    # [5]
    ("楽しく勉強ができるように教えます！", "昔から人に教えるのが好きで、分かった！と言ってもらえるのが私の喜びです。<br>最後までやり遂げることを旨としており、生徒さんにもその信念を伝えて行けたらと思います。", "英語、算数・数学、中学理科、高校化学・物理、地理"),
    # [6] 講師情報7（252355版の現行を維持）
    ("生徒一人ひとりに寄り添った指導を目指します！", "中学、高校を通じてクラス委員やまとめ役を務めることが多く、勉強が苦手な子に教えることをしてきました。<br>生徒に「やる気」を出させて、勉強が心から楽しいと思えるような指導を全力で行います！", "英語、算数・数学、国語、理科、高校物理・化学"),
    # [7] 村上先生
    ("やればできる！生徒の可能性を広げます！", "スクールIE 鳩ヶ谷校講師の村上です。<br>英語や日本史といった文系科目中心に指導しています。<br>勉強は正しい方法でやり始めさえすれば少しずつできるようになるものです。<br>苦手な勉強も一緒に乗り越えていきましょう！", "英語、算数、国語、理科、社会、高校日本史"),
    # [8]
    ("分からないことは、最後まで教えます", "高校の頃から人に教えることが好きで友人たちに教えていました。<br>この経験を活かし、さらにスキルを磨いて行きたいと考えました。<br>分からないところは納得行くまでトコトン教えます。", "英語、算数・数学、国語、理科、高校化学、社会、高校地理"),
]

for i, (title, body, subj) in enumerate(staff):
    form_data[f"classroom_staff[staff_details][{i}][header]"] = f'<span style="font-size:20px;">{title}</span>'
    c = f'<span style="font-size:15px;line-height:1.8;color:{S};font-weight:bold;">{body}</span>'
    if subj:
        c += f'<br><span style="font-size:13px;color:#555;"><b>担当科目</b>：{subj}</span>'
    form_data[f"classroom_staff[staff_details][{i}][content]"] = c

# ========== DETAIL ==========
form_data["classroom_detail[access]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">通り沿いに立っている大きな看板が目印です。<br>駐車場・駐輪場完備。お車・自転車でも安心してご来塾ください。</span>'
form_data["classroom_detail[native_school1]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">新郷南小、鳩ヶ谷小、新郷小、中居小、桜町小 など</span>'
form_data["classroom_detail[native_school2]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">川口東中、鳩ケ谷中、八幡木中、里中、上青木中、安行中、神根中、川口北中、榛松中 など</span>'
form_data["classroom_detail[native_school3]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">川口市立高、県立川口高、鳩ケ谷高、川口東高、浦和東高、浦和北高、草加南高、浦和実業高、武南高、春日部共栄高 など</span>'
form_data["classroom_detail[success_record3]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">開智中、栄東中、東京成徳大中、桜丘中、十文字中、郁文館中、浦和実業学園中、武南中</span>'
form_data["classroom_detail[success_record4]"] = f'<span style="font-size:14px;color:#c00;font-weight:bold;">【公立】</span><span style="font-size:14px;color:{S};font-weight:bold;">蕨、川口北、川口市立、浦和南、県立川口、浦和東、鳩ヶ谷、川口東、川口青陵、草加南、吉川美南、三郷、いづみ</span><br><span style="font-size:14px;color:#c00;font-weight:bold;">【私立】</span><span style="font-size:14px;color:{S};font-weight:bold;">武南、浦和実業、浦和学院、浦和麗明、叡明、立正大附属立正、大宮開成、東京成徳大、桜丘</span>'
form_data["classroom_detail[success_record5]"] = f'<span style="font-size:14px;color:#c00;font-weight:bold;">【国公立】</span><span style="font-size:14px;color:{S};font-weight:bold;">埼玉大学</span><br><span style="font-size:14px;color:#c00;font-weight:bold;">【私立】</span><span style="font-size:14px;color:{S};font-weight:bold;">早稲田大、明治薬科大、法政大、立教大、芝浦工大、東洋大、日本大、國學院大、東海大、帝京大、亜細亜大、東京薬科大、帝京平成大、城西大、大妻女子大、文京学院大</span>'

# ========== INFO ==========
form_data["classroom_info[introduction]"] = f'<span style="font-size:15px;color:#c00;font-weight:bold;">新規入会生徒募集中です\u203c</span><br><span style="font-size:14px;color:{S};font-weight:bold;">「個別を超えた個性別指導」を是非実感して下さい\u203c<br>学校授業のフォロー、定期テスト対策、各種検定・模試対策など幅広く受付中。<br>自習ブースも随時無料開放しています。</span><br><span style="font-size:12px;color:#555;">TEL：048-299-4511（直通）受付14:00\u301c21:30 ※日祝除く</span>' + RESERVE_BTN + TRACKING_PIXEL + TRACKING_SCRIPT

form_data["classroom_info[info_details][campaign][0][content]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">ただいまオリジナルキャンペーン実施中！（4/30まで）<br><br>「テスト対策って何をどうすれば…？」<br>「苦手科目の勉強の仕方が分からない…」<br>そんな方はぜひご相談ください！</span><br><br><span style="font-size:14px;color:#c00;font-weight:bold;">\u2460入会金無料！</span><span style="font-size:13px;color:{S};font-weight:bold;">通常23,100円\u2192無料（先着40名）</span><br><span style="font-size:14px;color:#c00;font-weight:bold;">\u2461授業料1\u30f6月無料！</span><span style="font-size:13px;color:{S};font-weight:bold;">個別授業の料金が無料（先着40名）</span>' + RESERVE_BTN + f'<br><span style="font-size:12px;color:#555;">TEL:048-299-4511（直通）</span>'

form_data["classroom_info[info_details][topics][0][content]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">生徒さん一人ひとりに合わせた「オーダーメイドカリキュラム」で指導しています。<br><br>「予習で進めたい」「宿題を教えてほしい」「検定対策」「学習習慣をつけたい」など、あらゆる声にお応えします！<br><br>スタートする時期や通塾回数、教科も生徒さんに合った形で進められます。</span>' + RESERVE_BTN + f'<br><span style="font-size:12px;color:#555;">TEL：048-299-4511（直通）受付14:00\u301c21:30 ※日祝除く</span>'

form_data["classroom_info[info_details][classroom][0][content]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">自習ブースを無料開放しています！回数・時間の上限なし。<br><br>定期テスト前の土曜日は教室自習開放日。<br>"わからないところは先生に質問し放題"の人気企画です。<br><br>予想問題プリントも無料配布。塾に通っていないお友達も参加OK！</span><br><span style="font-size:12px;color:#555;">TEL：048-299-4511（直通）受付14:00\u301c21:30 ※日祝除く</span>'

# 検定
form_data["classroom_info[info_details][free][0][header]"] = '英検<sup style="font-size:60%;">\u00ae</sup>・漢検・数検を当教室で受検できます！\n検定対策授業も実施可能です。'

INQUIRY_URL = "https://www.schoolie-net.jp/form/entry.php?mode=2&ccd=5558"
INQUIRY_BTN = f'<br><a href="{INQUIRY_URL}" style="display:inline-block;background:#c00;color:#fff;padding:6px 16px;border-radius:4px;text-decoration:none;font-size:13px;font-weight:bold;">検定に関するお問い合わせはこちら</a>'
form_data["classroom_info[info_details][free][0][content]"] = (
    f'<span style="font-size:15px;color:#c00;font-weight:bold;">\u25c6 英検<sup style="font-size:9px;">\u00ae</sup>日程</span><br>'
    f'<span style="font-size:13px;color:{S};font-weight:bold;">第1回 1次 5/30（土） 2次 7/5（日）<br>第2回 1次 9/26（土） 2次 11/8（日）<br>第3回 1次 1/16（土） 2次 3/7（日）</span><br><br>'
    f'<span style="font-size:15px;color:#c00;font-weight:bold;">\u25c6 漢検日程</span><br>'
    f'<span style="font-size:13px;color:{S};font-weight:bold;">第1回 6/27（土）<br>第2回 10/24（土）<br>第3回 1/30（土）</span><br><br>'
    f'<span style="font-size:15px;color:#c00;font-weight:bold;">\u25c6 数検日程</span><br>'
    f'<span style="font-size:13px;color:{S};font-weight:bold;">第1回 8/22（土）<br>第2回 11/14（土）<br>第3回 2/13（土）</span><br>'
    + INQUIRY_BTN
    + f'<br><span style="font-size:12px;color:#555;">TEL：048-299-4511（直通）</span>'
)

form_data["classroom_info[info_details][free][1][content]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">定期テストで確実に点数を伸ばす為、一人ひとりにあったテスト対策授業を実施しています。<br><br>各学校ごとにテスト傾向を分析、内容を絞って点数アップに重点を置いて進めます。<br><br>テスト前に限らず自習ブースも無料開放。</span><br><span style="font-size:12px;color:#555;">TEL：048-299-4511（直通）受付14:00\u301c21:30 ※日祝除く</span>'

form_data["classroom_info[info_details][free][2][content]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">4/29, 30　<span style="color:#c00;">開校</span>（通常授業あり）<br>5/1\u301c5/6　<span style="color:#999;">休校</span><br>5/7　14時\u301c22時開校（自習等にご活用ください）<br>5/8\u301c　<span style="color:#c00;">通常開校</span></span>'

# 体験談
exp = [
    ("むずかしい勉強もがんばります！", "私は中学受験をしたいと思い、IEに通って勉強を教えてもらっています。<br>学校の勉強とはちがってむずかしいことばかりだけど、IEの先生たちはやさしく分かるまで繰り返し教えてくれます。私も合格できるように勉強をがんばりたいです。"),
    ("高校受験で第一志望に受かりたい！", "僕は今行きたい高校がありますが、わからないところが多くなってきたので近所にあるIEに通い始めました。<br>個別でじっくり教えてくれるので、苦手だった英語も少しづつ分かるようになってきました。第一志望校に合格できるように頑張ります。"),
    ("IEで一緒に成功体験を！", "大学受験対策で高3からIEに入会しました。先生方の熱心な指導のおかげで無事に第一志望校に合格。<br>この成功体験を生徒の皆さんにも味わってもらいたく、今では講師として指導に当たっています。"),
    ("テスト対策の伴走をしてくれています！", "中学生になって特に数学でつまずきました。友達の紹介でIEに入会。<br>先生が一つひとつ丁寧に教えてくださるので、分からなかった問題も自分で解けるように。今ではテストで点数が安定してきました。"),
]
for i, (title, body) in enumerate(exp):
    form_data[f"classroom_experiences[{i}][content]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;line-height:1.7;">{body}</span>'

# コース
form_data["classroom_course[timetable_info]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">スクールIEなら、曜日や時間帯、教科や回数を自由に選べます。<br>「習い事や部活と両立したい」「苦手な教科だけ受講したい」「受験対策に集中したい」など、部活動や学校行事で忙しいお子さまも、ムリなく勉強できるのが特長です。<br>ご不明な点やご要望は、是非ともお気軽にご相談ください。</span>' + RESERVE_BTN
form_data["classroom_course[fee_info]"] = f'<span style="font-size:14px;color:{S};font-weight:bold;">お子さまの現在の学力や目標、性格や学習習慣などから総合的に判断し、必要な授業回数をご提案いたします。<br>ご希望の曜日・時間帯をお選びいただけるのはもちろん、週1回から何回でも受けることができます。</span>'

# Save (status: 0=default, 1=一時保存, 2=保存後承認依頼)
form_data["status"] = "1"
r = s.post(
    f"https://www.schoolie-net.jp/console/editions/696/edit/{NEW_ID}",
    data=form_data,
    allow_redirects=True,
)
print(f"Save: {r.status_code} -> {r.url}")
if "edit" in r.url:
    print("SAVE SUCCESS")
    print(f"Preview: https://www.schoolie-net.jp/console/editions/696/preview/{NEW_ID}")
else:
    print("Save may have failed")
