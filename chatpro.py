import datetime
import random
import uuid
import time
import os
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from PIL import Image
import psycopg2
from psycopg2 import pool
import streamlit as st
from streamlit_paste_button import paste_image_button

# 1. Cấu hình trang & CSS
st.set_page_config(page_title="Gemini Clone Pro", page_icon="✨", layout="wide")

st.markdown("""
<style>
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #f1f1f1; }
    ::-webkit-scrollbar-thumb { background: #ccc; border-radius: 3px; }
    .block-container { padding-top: 1rem; padding-bottom: 4rem; max-width: 900px; }
    .stChatInputContainer { padding-bottom: 10px; }
</style>
""", unsafe_allow_html=True)

# 2. Kiểm tra Secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
    NEON_DB_URL = st.secrets["NEON_DATABASE_URL"]
    PROXY_BASE_URL = st.secrets.get("GOOGLE_GEMINI_BASE_URL", "https://api.xah.io")
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets trên Streamlit.")
    st.stop()

# QUAN TRỌNG: Cấu hình biến môi trường trỏ qua cổng trung gian độc lập
os.environ["GOOGLE_GEMINI_BASE_URL"] = PROXY_BASE_URL

PERSONAS = {
    "✨ Trợ lý Mặc định": "Bạn là một trợ lý AI thông minh, thân thiện và hữu ích.",
    "💻 Lập trình viên Senior": "Bạn là một chuyên gia lập trình Senior. Trả lời tập trung vào mã nguồn tối ưu, ngắn gọn.",
    "✍️ Chuyên gia Content": "Bạn là một chuyên gia sáng tạo nội dung và Marketing.",
    "🎓 Giáo sư Giảng dạy": "Bạn là một giáo sư đại học. Hãy giải thích các khái niệm phức tạp một cách đơn giản.",
}

# 3. Kết nối Neon Database
@st.cache_resource
def get_db_pool():
    return psycopg2.pool.SimpleConnectionPool(1, 10, NEON_DB_URL)

def run_query(query, params=(), fetch=None):
    pool_conn = get_db_pool()
    conn = pool_conn.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            res = cur.fetchone() if fetch == "one" else (cur.fetchall() if fetch == "all" else None)
        conn.commit()
        return res
    except Exception as e:
        st.error(f"Lỗi Database: {e}")
        return None
    finally:
        pool_conn.putconn(conn)

@st.cache_resource
def init_db():
    run_query("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, username TEXT, is_locked INT DEFAULT 0, created_at TEXT, title TEXT, is_pinned INT DEFAULT 0)")
    run_query("CREATE TABLE IF NOT EXISTS messages (id SERIAL PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")

init_db()

# Các hàm phụ trợ cơ sở dữ liệu giữ nguyên
def delete_session(session_id):
    run_query("DELETE FROM messages WHERE session_id = %s", (session_id,))
    run_query("DELETE FROM sessions WHERE id = %s", (session_id,))

def delete_user_data(username_to_del):
    run_query("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE username = %s)", (username_to_del,))
    run_query("DELETE FROM sessions WHERE username = %s", (username_to_del,))

def update_session_title(session_id, new_title):
    run_query("UPDATE sessions SET title = %s WHERE id = %s", (new_title, session_id))

def toggle_pin_session(session_id, current_pin):
    run_query("UPDATE sessions SET is_pinned = %s WHERE id = %s", (0 if current_pin == 1 else 1, session_id))

def update_username(old_name, new_name):
    run_query("UPDATE sessions SET username = %s WHERE username = %s", (new_name, old_name))

# 4. State Management
if "auth_status" not in st.session_state:
    st.session_state.auth_status = "user" in st.query_params and "role" in st.query_params
    if st.session_state.auth_status:
        st.session_state.username = st.query_params["user"]
        st.session_state.role = st.query_params["role"]

if "current_key_index" not in st.session_state:
    st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)
if "key_status" not in st.session_state:
    st.session_state.key_status = {i: "🟢 Sẵn sàng" for i in range(len(API_KEYS))}
if "quota_cooldown" not in st.session_state:
    st.session_state.quota_cooldown = {}
if "img_reset_key" not in st.session_state:
    st.session_state.img_reset_key = 0
if "selected_persona" not in st.session_state:
    st.session_state.selected_persona = list(PERSONAS.keys())[0]

# 5. Hàm gọi API tương thích Cổng Trung Gian (Proxy Gateway)
def query_gemini(prompt_text, history_list, image_data=None):
    # LƯU Ý: Thay thế các tên model dưới đây bằng đúng định dạng tên model 
    # mà trang cấp API thuê trả về (ví dụ thêm tiền tố tài khoản của bạn nếu có)
    MODEL_TIERS = [
        ["dungcsnd113/gemini-3.1-pro-preview", "gemini-3.1-pro-preview"], 
        ["dungcsnd113/gemini-3.6-flash", "gemini-3.6-flash"],
        ["dungcsnd113/gemini-3.5-flash-lite"]
    ]

    current_time = time.time()
    last_error = ""
    system_instruction = PERSONAS.get(st.session_state.selected_persona, "")
    all_keys_cooldown = True 
    trimmed_history = history_list[-6:] if history_list else []

    for tier_idx, tier_models in enumerate(MODEL_TIERS):
        start_key = st.session_state.current_key_index
        for offset in range(len(API_KEYS)):
            key_idx = (start_key + offset) % len(API_KEYS)
            
            if current_time < st.session_state.quota_cooldown.get((key_idx, tier_idx), 0):
                continue 
            
            all_keys_cooldown = False 
            
            # Gán key hiện tại vào môi trường cục bộ để SDK gọi qua gateway
            os.environ["GEMINI_API_KEY"] = API_KEYS[key_idx]
            genai.configure(api_key=API_KEYS[key_idx])

            for model_name in tier_models:
                try:
                    model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
                    
                    if image_data:
                        res = model.generate_content([image_data, prompt_text])
                    elif trimmed_history:
                        chat = model.start_chat(history=trimmed_history)
                        res = chat.send_message(prompt_text)
                    else:
                        res = model.generate_content(prompt_text)

                    st.session_state.current_key_index = key_idx
                    return res.text, model_name

                except ResourceExhausted:
                    st.session_state.quota_cooldown[(key_idx, tier_idx)] = current_time + 60
                    last_error = f"{model_name} hết quota"
                    break 
                except Exception as e:
                    last_error = str(e)
                    continue

    if all_keys_cooldown:
        return "⚠️ Tất cả các API Key đều đang chờ hồi phục quota.", "Error"
        
    return f"⚠️ Lỗi kết nối API qua Gateway. Chi tiết: {last_error}", "Error"

# 6. Màn hình Đăng nhập (Giữ nguyên logic của bạn)
if not st.session_state.get("auth_status", False):
    _, col_box, _ = st.columns([1, 2, 1])
    with col_box:
        st.markdown("<h2 style='text-align: center;'>✨ Đăng nhập Hệ thống AI</h2>", unsafe_allow_html=True)
        mode = st.radio("Tư cách:", ["User", "Admin"], horizontal=True)
        input_name = st.text_input("Username:", value="User_1") if mode == "User" else "Admin"
        input_pass = st.text_input("Mật khẩu:", type="password")

        if st.button("Tiếp tục", type="primary", use_container_width=True):
            if (mode == "Admin" and input_pass == ADMIN_PASSWORD) or (mode == "User" and input_pass == USER_PASSWORD):
                st.session_state.auth_status = True
                st.session_state.username = input_name.strip()
                st.session_state.role = mode.lower()
                st.query_params["user"] = st.session_state.username
                st.query_params["role"] = st.session_state.role
                st.rerun()
            else:
                st.error("Sai mật khẩu.")
    st.stop()

# 7. Giao diện Sidebar & Khung chat chính (Giữ nguyên toàn bộ cấu trúc cũ)
active_sid = st.session_state.get("current_session_id")
if st.session_state.role == "user" and not active_sid:
    last_sess = run_query("SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC LIMIT 1", (st.session_state.username,), fetch="one")
    if last_sess:
        st.session_state.current_session_id = last_sess[0]
    else:
        new_id = str(uuid.uuid4())[:8]
        run_query("INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)", (new_id, st.session_state.username, str(datetime.datetime.now())))
        st.session_state.current_session_id = new_id
    active_sid = st.session_state.current_session_id

with st.sidebar:
    st.title("✨ Gemini Clone Pro")
    st.session_state.selected_persona = st.selectbox("🎭 Vai trò AI:", list(PERSONAS.keys()))
    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False
        st.query_params.clear()
        st.rerun()

# Luồng xử lý chat chính
db_messages = run_query("SELECT role, content FROM messages WHERE session_id = %s ORDER BY id ASC", (active_sid,), fetch="all") or [] if active_sid else []

gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content, unsafe_style=True if 'unsafe_style' in locals() else None)
    g_role = "user" if role == "user" else "model"
    clean_content = content.split("<div style='text-align: right")[0].strip()
    gemini_history.append({"role": g_role, "parts": [clean_content]})

if prompt := st.chat_input("Nhập câu hỏi..."):
    with st.chat_message("user"):
        st.markdown(prompt)
    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (active_sid, prompt))

    with st.chat_message("assistant"):
        with st.spinner("Đang suy luận..."):
            reply, used_model = query_gemini(prompt, gemini_history)
            st.markdown(reply)

    reply_to_db = reply if used_model == "Error" else reply + f"\n\n<div style='text-align: right; font-size: 11px; color: #888;'>(Model: {used_model})</div>"
    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'assistant', %s)", (active_sid, reply_to_db))
    st.rerun()
