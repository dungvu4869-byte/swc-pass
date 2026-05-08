# -- coding: utf-8 --
import os
import json
import asyncio
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, constants, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, ReactionTypeEmoji
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, ChatMemberHandler, CommandHandler
from flask import Flask
from threading import Thread
import random
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
import re
import requests
from bs4 import BeautifulSoup
import feedparser
import logging
from google import genai
from google.genai import types

# ==============================================================================
# CẤU HÌNH & DỮ LIỆU CƠ BẢN
# ==============================================================================
SHEET_NAME = "Du_Lieu_Bot_SWC"
CHANNEL_ID = -1001308148293

GROUP_ID_TO_SEED = -1001598921227
ADMIN_IDS = [5792590251]
ALLOWED_GROUPS = [-1001598921227, -1003951391128]

# 🆕 DANH SÁCH NGUỒN ĐƯỢC PHÉP GOM TIN (Channel + Group chính)
TRACKED_CHAT_IDS = [CHANNEL_ID, GROUP_ID_TO_SEED]

API_KEYS = [
    "AIzaSyAvB3z3xp_4p-JDZHnHUCbYiwY6FQLmeTc",
    "AIzaSyAu9XBTmxIhW2Auu5NSoGDDMETodDbCSEE",
    "AIzaSyDrA8NyKu3Wml7yjiCs0Nq4HD8ESPfTE4E",
    "AIzaSyBPhursmgXMDXa9LMHCG2GHCEZXixXcH7s",
    "AIzaSyA0RlNwyYOmxEfhUEVQ1BNwwwGZrMRGoso",
]

AI_MODELS = [
    'gemini-2.5-flash',
    'gemini-2.5-pro',
    'gemini-2.0-flash',
    'gemini-2.0-flash-lite',
    'gemini-exp-1206'
]

SIGNATURE = """
👉 Cộng đồng Nhà đầu tư Giá trị & Tự do Tài chính:
✅ Telegram: https://t.me/swc_capital_vn
🌐 Hệ sinh thái SWC: https://swcpass.com
"""

LAST_WELCOME_MSG = {}
MESSAGE_COUNTER = 0
FORWARD_MAP = {}
POSTED_NEWS = set()
CHAT_HISTORY = {}
USER_CONTEXT = {}

# ==============================================================================
# 🆕 KHO TIN 24H — CÓ TIMESTAMP, TỰ LỌC CỬA SỔ 24H
# ==============================================================================
POSTS_FILE = "channel_posts.json"

def loadx_posts():
    if not os.path.exists(POSTS_FILE):
        return []
    try:
        with open(POSTS_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        now_iso = datetime.now(timezone(timedelta(hours=7))).isoformat()
        upgraded = []
        for p in raw:
            if isinstance(p, dict):
                if "ts" not in p:
                    p["ts"] = now_iso
                if "id" not in p:
                    p["id"] = f"legacy_{len(upgraded)}"
                upgraded.append(p)
        return upgraded
    except Exception as e:
        print(f"⚠️ Lỗi load posts: {e}")
        return []

def save_posts(posts):
    try:
        with open(POSTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(posts, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Lỗi save posts: {e}")

def prune_old_posts(posts, hours=24):
    vn_tz = timezone(timedelta(hours=7))
    cutoff = datetime.now(vn_tz) - timedelta(hours=hours)
    fresh = []
    for p in posts:
        try:
            ts = datetime.fromisoformat(p["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=vn_tz)
            if ts >= cutoff:
                fresh.append(p)
        except Exception:
            fresh.append(p)
    return fresh

def add_post(text, link, source_chat_id, message_id):
    global CHANNEL_POSTS_24H
    uid = f"{source_chat_id}_{message_id}"
    for p in CHANNEL_POSTS_24H:
        if p.get("id") == uid:
            return False
    now_iso = datetime.now(timezone(timedelta(hours=7))).isoformat()
    CHANNEL_POSTS_24H.append({
        "id": uid,
        "text": text[:2000], 
        "link": link,
        "ts": now_iso,
        "source": "channel" if source_chat_id == CHANNEL_ID else "group"
    })
    CHANNEL_POSTS_24H = prune_old_posts(CHANNEL_POSTS_24H, hours=24)
    save_posts(CHANNEL_POSTS_24H)
    return True

CHANNEL_POSTS_24H = load_posts()

# ==============================================================================
# PHẦN 1: BỘ NÃO AI — SYSTEM PROMPT "NGƯỜI THẬT"
# ==============================================================================
SYSTEM_PROMPT = """
[VAI TRÒ & NHÂN CÁCH]
Bạn là một CỐ VẤN ĐẦU TƯ thực thụ, đại diện cho tinh hoa của Sky World Community VN — không phải chatbot, không phải trợ lý máy móc. Bạn là một người có cá tính riêng, cực kỳ am hiểu Kinh tế vĩ mô, Lịch sử tài chính, và Tâm lý con người.

[PHONG CÁCH GIAO TIẾP — ĐÂY LÀ LINH HỒN CỦA BẠN]
1. XƯNG HÔ (QUY TẮC THÉP): 
- Bắt buộc xưng "em", gọi khách là "bác", "anh" hoặc "chị" tùy ngữ cảnh. 

2. NÓI CHUYỆN NHƯ CHUYÊN GIA TỪNG TRẢI:
- Không dùng văn phong sách giáo khoa. Hãy dùng ngôn ngữ của dân đầu tư "nằm gai nếm mật".

3. ĐỌC VỊ CẢM XÚC & ĐIỀU HƯỚNG TÂM LÝ:
- Nếu khách tham lam/ảo tưởng: Tạt một gáo nước lạnh lịch sự. Dùng data vĩ mô hoặc chu kỳ Benner để cho họ thấy sự khốc liệt.

4. ĐỘ DÀI & NHỊP ĐIỆU:
- Ngắn gọn, ngắt dòng liên tục cho dễ đọc (2-4 dòng/đoạn).


[LUẬT MỜI NHÓM & GỬI LINK]
- Khi khách hỏi tổng quan hệ sinh thái hoặc thẻ hội viên: Giới thiệu link: https://swcpass.com/
- Khi khách hỏi chiến lược làm giàu chậm, tích sản, mục tiêu vốn lớn: Giới thiệu link: https://swcpass.com/rm1/
- Khi khách hỏi về đầu tư dự án cụ thể, công nghệ BĐS, RWA: Giới thiệu link: https://swcpass.com/atlas/
- Khi khách cần tư vấn kín/tương tác cộng đồng sâu: Gửi link NHÓM KÍN SWC (https://t.me/+PjaQIrJda0QxNzQ1).
"""

# ==============================================================================
# PHẦN 2: NHẬN DIỆN TONE
# ==============================================================================
def detect_message_tone(text: str) -> str:
    text_lower = text.lower()
    anxiety_keywords = ["sợ", "lo", "rủi ro", "mất tiền", "lừa", "uy tín", "đảm bảo", "có thật không", "thật không"]
    excitement_keywords = ["x10", "x100", "giàu nhanh", "siêu lợi nhuận", "all in", "dốc túi", "đổi đời"]
    skeptic_keywords = ["lừa đảo", "scam", "đa cấp", "không tin", "tại sao", "bằng chứng", "chứng minh"]
    sad_keywords = ["thua", "lỗ", "mất hết", "thất bại", "buồn", "chán", "bị lừa rồi"]
    short_casual = len(text.split()) <= 6

    if any(k in text_lower for k in sad_keywords): return "sad"
    if any(k in text_lower for k in skeptic_keywords): return "skeptic"
    if any(k in text_lower for k in anxiety_keywords): return "anxious"
    if any(k in text_lower for k in excitement_keywords): return "excited"
    if short_casual: return "casual"
    return "normal"

def get_typing_delay(text_length: int, tone: str) -> float:
    base = min(text_length / 120, 4.0)
    if tone in ["skeptic", "sad"]: base += random.uniform(1.0, 2.0)
    elif tone == "casual": base = max(base * 0.6, 0.8)
    return round(base + random.uniform(0.3, 1.2), 1)

def build_tone_hint(tone: str, user_name: str) -> str:
    hints = {
        "sad": f"[GỢI Ý: {user_name} đang mất mát/tiêu cực. Xưng 'em', đồng cảm sâu sắc. Thua lỗ là học phí của quỹ đầu tư.]",
        "anxious": f"[GỢI Ý: {user_name} đang sợ rủi ro. Dùng triết lý Warren Buffett để trấn an. Bán sự thật, không bán hy vọng.]",
        "skeptic": f"[GỢI Ý: {user_name} đang hoài nghi. Không phòng thủ, đưa ra Data vĩ mô và để họ tự thẩm định.]",
        "excited": f"[GỢI Ý: {user_name} đang fomo. Kéo họ về mặt đất bằng góc nhìn Chu kỳ kinh tế.]",
        "casual": f"[GỢI Ý: Tin nhắn ngắn. Trả lời cực kỳ tự nhiên 2-3 dòng.]",
        "normal": ""
    }
    return hints.get(tone, "")

# ==============================================================================
# PHẦN 3: KHO KIẾN THỨC CỐT LÕI ĐƯỢC ĐỒNG BỘ TỪ WEBSITE CHÍNH THỨC
# ==============================================================================
KIEN_THUC_PHAT_TRIEN_BAN_THAN = """

🔥 PHÁT TRIỂN BẢN THÂN 

[1] 17 TƯ DUY TRIỆU PHÚ (T. Harv Eker):
1. Người giàu tin: "Tôi tạo ra cuộc đời tôi". Người nghèo tin: "Cuộc sống đầy những bất ngờ".
2. Người giàu tham gia cuộc chơi tiền bạc để thắng. Người nghèo tham gia để không bị thua.
3. Người giàu quyết tâm giàu có. Người nghèo muốn trở thành giàu có.
4. Người giàu suy nghĩ lớn. Người nghèo suy nghĩ nhỏ.
5. Người giàu tập trung vào các cơ hội. Người nghèo tập trung vào những khó khăn.
6. Người giàu ngưỡng mộ những người giàu có khác. Người nghèo ghen ghét, đố kỵ.
7. Người giàu kết giao với người thành công và tích cực. Người nghèo giao du với người thất bại.
8. Người giàu sẵn sàng tôn vinh bản thân. Người nghèo suy nghĩ tiêu cực về bán hàng.
9. Người giàu đứng cao hơn những vấn đề của họ. Người nghèo đứng thấp hơn vấn đề.
10. Người giàu biết đón nhận. Người nghèo không biết đón nhận.
11. Người giàu muốn được trả công theo kết quả. Người nghèo muốn trả công theo thời gian.
12. Người giàu chọn cả hai. Người nghèo chỉ chọn một.
13. Người giàu chú trọng vào tổng tài sản. Người nghèo chú trọng thu nhập từ lương.
14. Người giàu quản lý tiền của họ rất giỏi. Người nghèo không biết quản lý tiền.
15. Người giàu bắt tiền phải "phục vụ" mình. Người nghèo làm việc vất vả để kiếm tiền.
16. Người giàu hành động bất chấp nỗi sợ hãi. Người nghèo để nỗi sợ hãi ngăn cản họ.
17. Người giàu luôn học hỏi và phát triển. Người nghèo nghĩ họ đã biết tất cả.

[2] QUY TẮC 6 CHIẾC LỌ: 55% Thiết yếu, 10% Tiết kiệm, 10% Giáo dục, 10% Hưởng thụ, 10% Tự do TC, 5% Cho đi.
"""

KIEN_THUC_TAI_CHINH = """
🔥 QUẢN LÝ TÀI CHÍNH & ĐẦU TƯ THỰC CHIẾN

[1] Tâm Lý Tiền Bạc & Cạm Bẫy Trung Lưu
- Ý nghĩa 10.000$ đầu tiên: Đổi danh tính từ "Người tiêu dùng" sang "Người quản lý vốn".
- Giàu ngầm (Stealth Wealth): Đỉnh cao của sự giàu có là mua lại "Thời gian và Tự do".
- Bẫy Tiêu Sản: Xe sang trả góp, mua trước trả sau là cỗ máy tiêu hủy tài sản.

[2] Toán Học Làm Giàu
- Định luật Khoảng Trống: Sự giàu có nằm ở khoảng cách giữa Thu nhập và Chi tiêu.
- Hệ thống 15/65/20: 15% trả cho tương lai, 65% chi phí thiết yếu, 20% hưởng thụ.
- Lãi kép: Quy luật 100.000$ đầu tiên rất khó khăn, nhưng vượt qua được, lãi kép sẽ làm việc thay bạn.

[3] Quản Lý Tài Sản Ròng (Net Worth)
- Công thức: Tài sản ròng = Tổng tài sản - Tổng nợ.
- 3 Cách tăng: (1) Tăng thu nhập ròng, (2) Dồn vốn vào tài sản tăng trưởng, (3) Triệt tiêu nợ lãi suất cao.

"""

KIEN_THUC_DU_AN = """
📚 CẤU TRÚC KIẾN THỨC DỰ ÁN SWC

[CẤP 1 — TỔ CHỨC: SWC — QUỸ ĐẦU TƯ SKY WORLD COMMUNITY]
- Nền tảng: Crowdinvesting Platform quốc tế, giấy phép quỹ đầu tư SEC (Mỹ).


[CẤP 2 — SẢN PHẨM: SWC-Pass]
💎 GÓI ESSENTIAL: $240/Năm (~$20/tháng) — Tiếp cận Road to $1M, SWC Field.
👑 GÓI PLUS: $600/5 Năm (~$10/tháng) — Khóa giá 5 năm, tự động nâng cấp.
🏛️ GÓI ULTIMATE: $2,600/Vĩnh viễn — Một lần, dùng trọn đời.

[CẤP 3 — DỰ ÁN ATLAS] DỰ ÁN ATLAS - CÁCH MẠNG BẤT ĐỘNG SẢN RWA TẠI UAE (https://swcpass.com/atlas/)

🚀 ROAD TO $1,000,000 — HÀNH TRÌNH ĐẾN 1 TRIỆU ĐÔ


"""

FULL_KNOWLEDGE = f"""
[TÂM LÝ & GIAO TIẾP]
{KIEN_THUC_PHAT_TRIEN_BAN_THAN}
[VĨ MÔ & TƯ DUY DÒNG TIỀN]
{KIEN_THUC_TAI_CHINH}
[CHI TIẾT HỆ SINH THÁI WEBSITE SWC]
{KIEN_THUC_DU_AN}
"""

# ==============================================================================
# PHẦN 4: WEB SERVER, HÀM HỖ TRỢ VÀ CÀO TIN TỨC TỰ ĐỘNG
# ==============================================================================
app_web = Flask('')
@app_web.route('/')
def home(): return "Bot SWC V100 Đang Hoạt Động!"

def run_web():
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

def keep_alive(): Thread(target=run_web, daemon=True).start()

def get_latest_dubaotiente():
    """Cào bài viết mới nhất từ trang Dự Báo Tiền Tệ"""
    url = "https://dubaotiente.net/tieu-diem-thi-truong.html"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.content, 'html.parser')
        
        # Tìm bài viết đầu tiên
        articles = soup.select('h2 a, h3 a, h4 a, .title-news a')
        if not articles: return None
        
        first_link = articles[0]['href']
        if not first_link.startswith('http'):
            first_link = "https://dubaotiente.net" + first_link
            
        # Truy cập vào bài chi tiết
        res_article = requests.get(first_link, headers=headers, timeout=10)
        soup_article = BeautifulSoup(res_article.content, 'html.parser')
        
        # Lấy Tiêu đề
        title_tag = soup_article.find('h1')
        title = title_tag.text.strip() if title_tag else soup_article.title.text
        
        # Lấy Ảnh đại diện
        img_tag = soup_article.find('meta', property='og:image')
        img_url = img_tag['content'] if img_tag else None
        
        # Lấy Nội dung văn bản
        content_div = soup_article.select_one('.content, .detail-content, article, #main-detail')
        if content_div:
            paragraphs = content_div.find_all('p')
            content = "\n".join([p.text.strip() for p in paragraphs if p.text.strip()])
        else:
            content = ""
            
        return {"url": first_link, "title": title, "content": content[:3000], "image": img_url}
    except Exception as e:
        print(f"Lỗi scrape dubaotiente: {e}")
        return None

# ==============================================================================
# HÀM MỚI: TỐI ƯU HÓA KẾT NỐI GOOGLE SHEET TRONG LUỒNG RIÊNG (Tránh block Bot)
# ==============================================================================
async def get_data_from_sheet(user_text):
    try:
        json_content = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not json_content: 
            print("⚠️ LỖI NGHIÊM TRỌNG: Không tìm thấy biến môi trường GOOGLE_CREDENTIALS_JSON.")
            return None

        def fetch_sheet_data():
            creds = ServiceAccountCredentials.from_json_keyfile_dict(
                json.loads(json_content), 
                ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            )
            client = gspread.authorize(creds)
            
            # SỬ DỤNG ID CỦA SHEET THAY VÌ TÊN
            SHEET_ID = "1YHbGLO3z91thJdB2y4_UEkmi7-srBL1a-XyPpebKzL0"
            sheet = client.open_by_key(SHEET_ID).sheet1
            return sheet.get_all_values()

        # Gọi hàm fetch data không đồng bộ bằng asyncio.to_thread
        data = await asyncio.to_thread(fetch_sheet_data)
        
        clean_user_text = re.sub(r"[^\w\s]", " ", user_text).lower()
        for row in data[1:]:
            while len(row) < 5: row.append("")
            keywords = row[0].lower().split(',')
            for key in keywords:
                key = key.strip()
                if not key: continue
                pattern = r"(^|\s)" + re.escape(key) + r"(\s|$)"
                if re.search(pattern, clean_user_text):
                    return {"msg1": row[1], "msg2": row[2], "link": row[3], "img": row[4]}
        return None
        
    except Exception as e:
        print(f"❌ Lỗi truy cập Google Sheet: {e}")
        return None

# ==============================================================================
# PHẦN 5: AI ENGINE
# ==============================================================================
def _call_ai_sync(client, model_name, contents, document_path=None):
    if document_path:
        uploaded_file = client.files.upload(file=document_path)
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[uploaded_file, contents],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.85,
                )
            )
            return response
        finally:
            try: client.files.delete(name=uploaded_file.name)
            except: pass
    else:
        return client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.85,
            )
        )

async def ask_ai(user_text, document_path=None, user_name="Khách", chat_id=None, tone="normal"):
    vn_tz = timezone(timedelta(hours=7))
    now_dt = datetime.now(vn_tz)
    now_str = now_dt.strftime("%d/%m/%Y")
    
    hour = now_dt.hour
    if 5 <= hour < 11: time_ctx = "buổi sáng"
    elif 11 <= hour < 13: time_ctx = "buổi trưa"
    elif 13 <= hour < 18: time_ctx = "buổi chiều"
    elif 18 <= hour < 22: time_ctx = "buổi tối"
    else: time_ctx = "đêm khuya"
    
    history_text = ""
    msg_count = 0
    if chat_id:
        if chat_id not in CHAT_HISTORY: CHAT_HISTORY[chat_id] = []
        recent_history = CHAT_HISTORY[chat_id][-6:]
        msg_count = len(CHAT_HISTORY[chat_id])
        if recent_history:
            history_text = "\n[LỊCH SỬ GẦN ĐÂY]:\n" + "\n".join(
                [f"{'Khách' if msg['role'] != 'Cố vấn SWC' else 'Cố vấn'}: {msg['content']}" 
                 for msg in recent_history]
            ) + "\n"
    
    tone_hint = build_tone_hint(tone, user_name)
    familiarity_hint = ""
    if msg_count == 0:
        familiarity_hint = "[Chào hỏi tự nhiên một chút.]"
    elif msg_count >= 10:
        familiarity_hint = f"[{user_name} đã thân — xưng em gọi bác/anh/chị chân thành, vào thẳng vấn đề.]"
    
    full_input = (
        f"[BỐI CẢNH: Ngày {now_str}, {time_ctx}. Tên khách: {user_name}]\n"
        f"{tone_hint}\n"
        f"{familiarity_hint}\n\n"
        f"KIẾN THỨC NỀN & WEBSITE REFERRALS:\n{FULL_KNOWLEDGE}\n"
        f"{history_text}\n"
        f"TIN NHẮN KHÁCH: {user_text}\n\n"
        f"[NHẮC NHỞ: LUÔN xưng 'em'. KHÔNG dùng **. Đọc kỹ lại KIẾN THỨC NỀN để cung cấp đúng Link Website khi cần. Tối đa 150 từ]:"
    )

    for key in API_KEYS:
        try:
            client = genai.Client(api_key=key)
            for model_name in AI_MODELS:
                try:
                    response = await asyncio.to_thread(_call_ai_sync, client, model_name, full_input, document_path)
                    ans = response.text.replace("**", "").replace("*", "").strip()
                    
                    if chat_id and ans and "Bot đang bận" not in ans:
                        CHAT_HISTORY[chat_id].append({"role": user_name, "content": user_text})
                        CHAT_HISTORY[chat_id].append({"role": "Cố vấn SWC", "content": ans})
                        if len(CHAT_HISTORY[chat_id]) > 12:
                            CHAT_HISTORY[chat_id] = CHAT_HISTORY[chat_id][-12:]
                    return ans
                except: continue
        except: continue

    fallback_options = [
        "Hệ thống bên em đang load data chút xíu bác ơi 😅 Bác nhắn lại em sau vài phút nhé.",
        "Em vừa bị rớt mạng nền xíu 😂 Bác gửi lại câu đó giúp em với.",
        "Dạ bác chờ em check lại số liệu trên app xíu. Vài phút nữa em nhắn lại bác nhé!"
    ]
    return random.choice(fallback_options)

async def rewrite_news_ai(title, content):
    """AI chuyên dụng để viết lại bản tin vĩ mô tự động theo Rule khắt khe"""
    system_prompt_news = """Bạn là biên tập viên tin tức tài chính. Nhiệm vụ của bạn là đọc bản tin và viết lại.
TUYỆT ĐỐI TUÂN THỦ CÁC QUY TẮC TỐI THƯỢNG:
1. Xưng hô: Bắt buộc gọi người đọc là "ae". TUYỆT ĐỐI KHÔNG dùng: tôi, em, mình, ad, admin. Giọng văn khách quan, sắc bén.
2. Format: Chỉ dùng thẻ HTML <b>để in đậm</b>. KHÔNG dùng **."""
    
    prompt = f"""BƯỚC 2: XỬ LÝ VÀ VIẾT LẠI THEO QUY TẮC TỐI THƯỢNG
1. Tiêu đề: Ngắn gọn, có emoji. In đậm tiêu đề bằng thẻ <b> (Tuyệt đối không dùng **).
2. Tóm tắt (80-100 từ): Trình bày dạng liệt kê. Sử dụng emoji ở đầu dòng (🔥, 🖤, 👉, 📉, 📈). BẮT BUỘC đối chiếu và sử dụng thuật ngữ (Chứng khoán, Forex, Vàng, FED, ECB, Crypto, Lãi suất, Lạm phát...). Áp dụng luật: Lãi suất TĂNG -> Tiền rút khỏi CK; Lãi suất GIẢM -> Tiền chảy vào đầu tư; Lạm phát CAO -> Giá vàng tăng. In đậm các từ/chỗ cần nhấn mạnh.
3. Câu chốt (10-15 từ): Một câu mang phong cách cà khịa, châm biếm hoặc cảm thán đời thường + 1-2 emoji (😁, 🫢, 🥲, 😂).
4. Cảnh báo bắt buộc ở cuối: "❗️Chỉ cung cấp thông tin, không phải lời khuyên đầu tư!"
5. Hashtag: 3-5 hashtag phù hợp ở cuối (VD: #FED #Chungkhoan #Vimo).

CẤU TRÚC ĐẦU RA BẮT BUỘC (KHÔNG CHÀO HỎI THỪA):
[Tiêu đề ngắn gọn kèm Emoji]

[Nội dung tóm tắt...]

[Câu chốt hài hước...]

[Câu cảnh báo...]
[Hashtags]

BÀI GỐC CẦN XỬ LÝ:
Tiêu đề: {title}
Nội dung: {content}"""

    for key in API_KEYS:
        try:
            client = genai.Client(api_key=key)
            for model_name in AI_MODELS:
                try:
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt_news,
                            temperature=0.75,
                        )
                    )
                    return response.text.replace("**", "")
                except: continue
        except: continue
    return None

# ==============================================================================
# PHẦN 6: GỬI TIN NHẮN
# ==============================================================================
async def send_smart_messages(update, context, text, tone="normal"):
    chat_id = update.effective_chat.id
    global MESSAGE_COUNTER
    MESSAGE_COUNTER += 1

    chunks = [c.strip() for c in text.split('|||') if c.strip()] if "|||" in text else [text]

    for i, chunk in enumerate(chunks):
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        delay = get_typing_delay(len(chunk), tone)
        await asyncio.sleep(delay)
        
        final_msg = chunk
        if i == len(chunks) - 1 and MESSAGE_COUNTER % 5 == 0:
            final_msg += f"\n\n{SIGNATURE}"
        
        try:
            await update.message.reply_text(final_msg, parse_mode='HTML', disable_web_page_preview=True)
        except:
            await update.message.reply_text(final_msg)

# ==============================================================================
# PHẦN 7: GOM TIN VÀ TIN TỨC TỰ ĐỘNG
# ==============================================================================
def build_message_link(chat, message_id):
    if chat.username: return f"https://t.me/{chat.username}/{message_id}"
    else: return f"https://t.me/c/{str(chat.id).replace('-100', '')}/{message_id}"

async def track_channel_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post: return
    if update.effective_chat.id not in TRACKED_CHAT_IDS: return
    text = post.text or post.caption
    if not text or len(text.strip()) < 10: return
    link = build_message_link(update.effective_chat, post.message_id)
    if add_post(text, link, update.effective_chat.id, post.message_id):
        print(f"✅ [Channel] Đã lưu bài {post.message_id}")

async def track_group_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    if update.effective_chat.id not in TRACKED_CHAT_IDS: return
    text = msg.text or msg.caption
    if not text or len(text.strip()) < 10: return
    sender_id = msg.from_user.id if msg.from_user else None
    is_admin_post = sender_id in ADMIN_IDS
    is_channel_forward = bool(msg.is_automatic_forward) 
    if not (is_admin_post or is_channel_forward): return
    link = build_message_link(update.effective_chat, msg.message_id)
    if add_post(text, link, update.effective_chat.id, msg.message_id):
        print(f"✅ [Group] Đã lưu bài {msg.message_id}")

async def daily_summary_job(context: ContextTypes.DEFAULT_TYPE, is_test=False):
    global CHANNEL_POSTS_24H
    target_chat_id = CHANNEL_ID

    CHANNEL_POSTS_24H = prune_old_posts(CHANNEL_POSTS_24H, hours=24)
    save_posts(CHANNEL_POSTS_24H)
    
    posts_24h = list(CHANNEL_POSTS_24H) 
    total_posts = len(posts_24h)
    
    if total_posts == 0:
        if is_test:
            try: await context.bot.send_message(chat_id=target_chat_id, text="⚠️ Không có bài nào trong 24h.")
            except: pass
        return

    news_data = "".join([f"--- BÀI {i+1} ---\nNội dung: {p['text']}\nLink: {p['link']}\n\n" for i, p in enumerate(posts_24h)])
    
    prompt = f"""Dưới đây là {total_posts} bài viết đăng trong 24h qua:
{news_data}

NHIỆM VỤ:
1. Dùng HTML <b> để in đậm tiêu đề. CẤM DÙNG **.
2. LIỆT KÊ ĐỦ CẢ {total_posts} BÀI, KHÔNG ĐƯỢC BỎ SÓT.
3. CHỈ ĐƯA TIÊU ĐỀ NGẮN GỌN (dưới 80 ký tự), CÓ EMOJI.

Mẫu Format:
<b>🌅 ĐIỂM TIN VĨ MÔ & ĐẦU TƯ 24H QUA ({total_posts} tin)</b>

👉 <b>[Tiêu đề bài 1]</b> - <a href="[Link bài 1]">Đọc ngay</a>
(lặp đủ số bài)

<i>Câu chốt ngắn gọn về vĩ mô/tâm lý thị trường của em.</i>"""

    summary_msg = await ask_ai(prompt, user_name="Admin")
    if summary_msg and "nghẽn" not in summary_msg and "lag" not in summary_msg:
        try:
            await context.bot.send_message(chat_id=target_chat_id, text=summary_msg, parse_mode='HTML', disable_web_page_preview=True)
            if not is_test:
                CHANNEL_POSTS_24H = prune_old_posts(CHANNEL_POSTS_24H, hours=24)
                save_posts(CHANNEL_POSTS_24H)
        except Exception as e:
            print(f"❌ Lỗi gửi kênh: {e}")

async def auto_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    await daily_summary_job(context, is_test=False)

async def auto_news_update_job(context: ContextTypes.DEFAULT_TYPE):
    """Job tự động chạy mỗi 4 tiếng: Lấy tin -> AI viết lại -> Đăng vào nhóm"""
    try:
        print("⏳ Đang cào tin tự động từ dubaotiente.net...")
        news = await asyncio.to_thread(get_latest_dubaotiente)
        if not news: 
            print("⚠️ Không lấy được tin mới.")
            return
            
        global POSTED_NEWS
        if news['url'] in POSTED_NEWS: 
            print("⚠️ Tin này đã gửi rồi, chờ đợt sau.")
            return
        
        POSTED_NEWS.add(news['url'])
        
        print(f"✅ Đã tìm thấy tin: {news['title']}. Đang nhờ AI viết lại (chờ vài giây)...")
        rewritten_text = await rewrite_news_ai(news['title'], news['content'])
        if not rewritten_text: 
            print("❌ AI không thể viết lại tin, bỏ qua.")
            return
            
        chat_id = GROUP_ID_TO_SEED  # Gửi vào Nhóm chính
        sent = False
        
        if news['image']:
            try:
                img_data = requests.get(news['image'], timeout=10).content
                await context.bot.send_photo(chat_id=chat_id, photo=img_data, caption=rewritten_text, parse_mode='HTML')
                sent = True
                print("✅ Đã gửi bản tin KÈM ẢNH vào nhóm thành công.")
            except Exception as e:
                print(f"⚠️ Lỗi tải/gửi ảnh, tự động chuyển sang gửi Text: {e}")
                
        if not sent:
            await context.bot.send_message(chat_id=chat_id, text=rewritten_text, parse_mode='HTML', disable_web_page_preview=True)
            print("✅ Đã gửi bản tin TEXT vào nhóm thành công.")
            
    except Exception as e:
        print(f"❌ Lỗi Job tự động cập nhật tin: {e}")

# ==============================================================================
# PHẦN 8: HANDLERS
# ==============================================================================
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(random.choice(["🏓 Em đây bác ơi! Đang trực page.", "🟢 Sẵn sàng phục vụ anh em cộng đồng!"]))

async def test20h(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id in ADMIN_IDS:
        global CHANNEL_POSTS_24H
        CHANNEL_POSTS_24H = prune_old_posts(CHANNEL_POSTS_24H, hours=24)
        save_posts(CHANNEL_POSTS_24H)
        if not CHANNEL_POSTS_24H:
            await update.message.reply_text("⚠️ Kho tin 24h đang TRỐNG!")
            return
        await update.message.reply_text(f"⏳ Gom được {len(CHANNEL_POSTS_24H)} bài. Đang xử lý...")
        await daily_summary_job(context, is_test=True)
        await update.message.reply_text("✅ Đã bắn bản tin test!")

async def debugposts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS: return
    global CHANNEL_POSTS_24H
    CHANNEL_POSTS_24H = prune_old_posts(CHANNEL_POSTS_24H, hours=24)
    if not CHANNEL_POSTS_24H:
        await update.message.reply_text("📭 Kho tin TRỐNG.")
        return
    lines = [f"📦 KHO TIN 24H — <b>{len(CHANNEL_POSTS_24H)}</b> bài\n"]
    for i, p in enumerate(CHANNEL_POSTS_24H, 1):
        lines.append(f"{i}. [{p.get('source', '?')}|{p.get('ts', '?')[:16]}] {p['text'][:60]}...")
    await update.message.reply_text("\n".join(lines)[:3800], parse_mode='HTML', disable_web_page_preview=True)

async def clearposts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS: return
    global CHANNEL_POSTS_24H
    n = len(CHANNEL_POSTS_24H)
    CHANNEL_POSTS_24H.clear()
    save_posts(CHANNEL_POSTS_24H)
    await update.message.reply_text(f"🗑️ Đã xóa {n} bài khỏi kho.")

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 ID: {update.effective_chat.id}")

async def greet_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in [constants.ChatType.GROUP, constants.ChatType.SUPERGROUP]: return
    result = update.chat_member
    new_member = result.new_chat_member
    if new_member.status == ChatMember.MEMBER and result.old_chat_member.status != ChatMember.MEMBER:
        user = new_member.user
        if user.is_bot: return
        chat_id = update.effective_chat.id
        global LAST_WELCOME_MSG
        if chat_id in LAST_WELCOME_MSG:
            try: await context.bot.delete_message(chat_id=chat_id, message_id=LAST_WELCOME_MSG[chat_id])
            except: pass
        
        welcome_text = f"Chào mừng bác {user.full_name} ghé thăm SWC Community! 🎉\nỞ đây anh em mình nói chuyện đầu tư giá trị, xây vốn tự do tài chính. Cần check gì bác cứ gọi em nhé!\n\n👇 Các hệ sinh thái chính:"
        buttons = [
            [InlineKeyboardButton("🌍 Thẻ Thành Viên SWC", url="https://swcpass.com/"), InlineKeyboardButton("🚀 Chiến lược $1M", url="https://swcpass.com/rm1/")],
            [InlineKeyboardButton("🏗 Dự án Atlas BĐS", url="https://swcpass.com/atlas/"), InlineKeyboardButton("👥 Nhóm Kín VIP", url="https://t.me/+PjaQIrJda0QxNzQ1")]
        ]
        sent = await context.bot.send_message(chat_id=chat_id, text=welcome_text, reply_markup=InlineKeyboardMarkup(buttons))
        LAST_WELCOME_MSG[chat_id] = sent.message_id

# ==============================================================================
# PHẦN 9: XỬ LÝ TIN NHẮN CHÍNH
# ==============================================================================
async def handle_seeding_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    post_content = msg.text or msg.caption or "Tin tức"
    seed_prompt = f"Với vai trò chuyên gia, phân tích tin vĩ mô sau (xưng em gọi bác, góc nhìn Smart Money): '{post_content}'"
    chat_id = update.effective_chat.id
    comment = await ask_ai(seed_prompt, user_name="Admin/Kênh", chat_id=chat_id)
    try: await msg.reply_text(f"{comment}", parse_mode='HTML')
    except: pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    global MESSAGE_COUNTER
    try:
        if update.effective_chat and update.effective_chat.type in [constants.ChatType.GROUP, constants.ChatType.SUPERGROUP]:
            await track_group_posts(update, context)
    except Exception as e: print(f"⚠️ Lỗi track_group_posts: {e}")
        
    try: await update.message.set_reaction(reaction=[ReactionTypeEmoji(random.choice(["❤️", "🔥", "👍", "🤝", "⚡️"]))])
    except: pass
    
    if update.message.is_automatic_forward:
        await handle_seeding_in_group(update, context)
        return
        
    document_path = None
    is_multimodal = False

    if update.message.document and update.message.document.mime_type == 'application/pdf':
        if update.message.document.file_size < 20 * 1024 * 1024:
            file_id = update.message.document.file_id
            new_file = await context.bot.get_file(file_id)
            document_path = f"{file_id}.pdf"
            await new_file.download_to_drive(document_path)
            is_multimodal = True
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        new_file = await context.bot.get_file(file_id)
        document_path = f"{file_id}.jpg"
        await new_file.download_to_drive(document_path)
        is_multimodal = True
        
    user_text = update.message.text or update.message.caption or ""
    if is_multimodal and not user_text: user_text = "Phân tích tài liệu/hình ảnh này dựa trên tư duy đầu tư giá trị."
        
    user_id = update.message.from_user.id
    user_name = update.message.from_user.full_name
    chat_type = update.effective_chat.type
    chat_id = update.effective_chat.id
    tone = detect_message_tone(user_text) if user_text else "normal"
    
    if chat_type != constants.ChatType.PRIVATE:
        if str(chat_id) not in [str(i) for i in ALLOWED_GROUPS]:
            if document_path and os.path.exists(document_path): os.remove(document_path)
            return

    if chat_type == constants.ChatType.PRIVATE:
        if user_id in ADMIN_IDS:
            if update.message.reply_to_message:
                reply_msg = update.message.reply_to_message
                customer_id = FORWARD_MAP.get(reply_msg.message_id)
                if not customer_id:
                    try: customer_id = reply_msg.forward_origin.sender_user.id
                    except: pass
                if not customer_id:
                    match = re.search(r'ID:\s*(\d+)', reply_msg.text or reply_msg.caption or "")
                    if match: customer_id = int(match.group(1))
                if customer_id:
                    try:
                        await context.bot.copy_message(chat_id=customer_id, from_chat_id=chat_id, message_id=update.message.message_id)
                        await update.message.reply_text("✅ Đã rep khách!")
                    except Exception as e: await update.message.reply_text(f"❌ Lỗi rep: {e}")
                else: await update.message.reply_text("⚠️ Không trace được ID khách.")
            else:
                if user_text or is_multimodal:
                    await context.bot.send_chat_action(update.effective_chat.id, constants.ChatAction.TYPING)
                    ans = await ask_ai(user_text, document_path, "Admin", chat_id, tone)
                    if ans: await send_smart_messages(update, context, ans, tone)
        else:
            data = await get_data_from_sheet(user_text.lower()) if user_text and len(user_text) < 200 else None
            if data:
                MESSAGE_COUNTER += 1
                msg = data['msg1']
                if data['link']: msg += f"\n👉 Chi tiết bác xem ở đây: {data['link']}"
                try: await update.message.reply_photo(data['img'], caption=msg)
                except: await update.message.reply_text(msg)
                if data['msg2']:
                    await asyncio.sleep(2)
                    await update.message.reply_text(data['msg2'])
            else:
                kb = [[InlineKeyboardButton("👥 VÀO NHÓM ĐẦU TƯ VIP", url="https://t.me/+PjaQIrJda0QxNzQ1")]]
                await update.message.reply_text("👋 Dạ em đã báo câu hỏi của bác sang bộ phận cố vấn. Bác đợi team rep chút nhé!\n\nTrong lúc chờ mời bác vào nhóm cộng đồng 👇", reply_markup=InlineKeyboardMarkup(kb))
                for admin_id in ADMIN_IDS:
                    try:
                        fwd = await context.bot.forward_message(chat_id=admin_id, from_chat_id=user_id, message_id=update.message.message_id)
                        FORWARD_MAP[fwd.message_id] = user_id
                        await context.bot.send_message(chat_id=admin_id, text=f"👤 KHÁCH HỎI: {user_name}\n🔑 ID: {user_id}\n🎭 Tone: {tone}\n(Reply để rep khách)")
                    except: pass
        if document_path and os.path.exists(document_path): os.remove(document_path)

    else: # TRƯỜNG HỢP NHẮN TIN TRONG GROUP
        # ----------------------------------------------------------------------
        # KIỂM TRA BOT CÓ ĐƯỢC TAG HOẶC ĐƯỢC REPLY LẠI TIN NHẮN KHÔNG
        # ----------------------------------------------------------------------
        bot_username = context.bot.username
        original_text = update.message.text or update.message.caption or ""
        is_mentioned = bot_username and (f"@{bot_username}" in original_text)
        
        is_reply_to_bot = False
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            if update.message.reply_to_message.from_user.id == context.bot.id:
                is_reply_to_bot = True

        # Nếu không tag và không reply bot -> Bỏ qua không xử lý
        if not (is_mentioned or is_reply_to_bot):
            if document_path and os.path.exists(document_path): os.remove(document_path)
            return

        # Xóa chữ @username ra khỏi câu hỏi để AI/Sheet hiểu ngữ nghĩa đúng
        if bot_username and is_mentioned:
            user_text = user_text.replace(f"@{bot_username}", "").strip()
        # ----------------------------------------------------------------------

        if not user_text and not is_multimodal: return
        data = await get_data_from_sheet(user_text.lower()) if len(user_text) < 200 else None
        if data:
            MESSAGE_COUNTER += 1
            msg = data['msg1']
            if data['link']: msg += f"\n👉 Link đây bác: {data['link']}"
            try: await update.message.reply_photo(data['img'], caption=msg)
            except: await update.message.reply_text(msg)
            if data['msg2']:
                await asyncio.sleep(2)
                await update.message.reply_text(data['msg2'])
        else:
            await context.bot.send_chat_action(update.effective_chat.id, constants.ChatAction.TYPING)
            ans = await ask_ai(user_text, document_path, user_name, chat_id, tone)
            if ans: await send_smart_messages(update, context, ans, tone)
        if document_path and os.path.exists(document_path): os.remove(document_path)

if __name__ == '__main__':
    keep_alive()
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if TOKEN:
        try:
            app = ApplicationBuilder().token(TOKEN).build()
            job_queue = app.job_queue
            
            app.add_handler(MessageHandler(filters.ChatType.CHANNEL, track_channel_posts))
            target_time_20h = dt_time(hour=20, minute=0, tzinfo=timezone(timedelta(hours=7)))
            job_queue.run_daily(auto_daily_summary, time=target_time_20h)
            
            # 🆕 THÊM LỊCH: Chạy Job cập nhật tin tức lặp lại mỗi 4 tiếng (Bắt đầu sau 10 giây khởi động bot)
            job_queue.run_repeating(auto_news_update_job, interval=timedelta(hours=4), first=10)
            
            app.add_handler(CommandHandler("ping", ping))
            app.add_handler(CommandHandler("id", get_id))
            app.add_handler(CommandHandler("test20h", test20h))
            app.add_handler(CommandHandler("debugposts", debugposts))
            app.add_handler(CommandHandler("clearposts", clearposts))
            
            app.add_handler(ChatMemberHandler(greet_chat_members, ChatMemberHandler.CHAT_MEMBER))
            app.add_handler(MessageHandler(filters.ALL & ~filters.ChatType.CHANNEL, handle_message))
            
            print("🟢 Bot SWC V100 (Đã nâng cấp Toàn bộ Kiến thức, Dọn dẹp Code và Fix lỗi Sheet) SẴN SÀNG!")
            app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        except Exception as e:
            print(f"❌ Lỗi: {e}")
