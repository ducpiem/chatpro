import datetime
import random
import uuid
import time
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from PIL import Image
import psycopg2
from psycopg2 import pool
import streamlit as st
from streamlit_paste_button import paste_image_button

# 1. Cấu hình trang & CSS (Thiết kế giống Gemini)
st.set_page_config(page_title="Gemini Clone Pro", page_icon="✨", layout="wide")

st.markdown(
    """
<style>
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #dadce0; border-radius: 3px; }

    .block-container { padding-top: 2rem; padding-bottom: 8rem; max-width: 900px; }
    
    [data-testid="stChatMessage"] {
        padding: 1rem 1.5rem;
        border-radius: 12px;
        margin-bottom: 10px;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        background-color: #f0f4f9; 
    }
    
    /* Giao diện nút chọn Model ở trên cùng */
    .model-badge {
        display: inline-flex;
        align-items: center;
        background-color: #f0f4f9;
        color: #1f1f1f;
        padding: 8px 16px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 15px;
        cursor: default;
        margin-bottom: 20px;
        border: 1px solid #e0e0e0;
    }
    
    .bottom-input-container {
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        background: linear-gradient(to top, rgba(255,255,255,1) 85%, rgba(255,255,255,0));
        padding: 10px 0 20px 0;
        z-index: 999;
        display: flex;
        justify-content: center;
    }
    .bottom-input-inner {
        max-width: 900px;
        width: 100%;
        padding: 0 1rem;
    }

    @media (max-width: 768px) {
        .block-container { padding-left: 0.5rem; padding-right: 0.5rem; }
    }
</style>
""",
    unsafe_allow_html=True,
)

# 2. Kiểm tra Secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
    NEON_DB_URL = st.secrets["NEON_DATABASE_URL"]
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets.")
    st.stop()

PERSONAS = {
    "✨ Trợ lý Mặc định": "Bạn là một trợ lý AI thông minh, thân thiện và hữu ích.",
    "💻 Lập trình viên Senior": "Bạn là một chuyên gia lập trình Senior. Viết code tối ưu, ngắn gọn.",
    "✍️ Chuyên gia Content": "Bạn là một chuyên gia sáng tạo nội dung. Viết lôi cuốn, sáng tạo.",
}

# 3. Database Neon
@st.cache_resource
def get_db_pool():
    return psycopg2.pool.SimpleConnectionPool(1, 10, NEON_DB_URL)

def run_query(query, params=(), fetch=None):
    pool_conn = get_db_pool()
    conn = pool_conn.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            res = None
            if fetch == "one": res = cur.fetchone()
            elif fetch == "all": res = cur.fetchall()
        conn.commit()
        return res
    except Exception as e:
        st.error(f"Lỗi Database: {e}")
        return None
    finally:
        pool_conn.putconn(conn)

@st.cache_resource
def init_db():
    run_query("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, username TEXT, is_locked INT DEFAULT 0,
            created_at TEXT, title TEXT, is_pinned INT DEFAULT 0
        )
    """)
    run_query("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY, session_id TEXT, role TEXT, content TEXT
        )
    """)
init_db()

def delete_session(session_id):
    run_query("DELETE FROM messages WHERE session_id = %s", (session_id,))
    run_query("DELETE FROM sessions WHERE id = %s", (session_id,))

def toggle_pin_session(session_id, current_pin):
    new_pin = 0 if current_pin == 1 else 1
    run_query("UPDATE sessions SET is_pinned = %s WHERE id = %s", (new_pin, session_id))

def update_session_title(session_id, new_title):
    run_query("UPDATE sessions SET title = %s WHERE id = %s", (new_title, session_id))

# 4. State Management
for key in ["img_reset_key", "auth_status", "role", "username", "current_session_id", "admin_selected_session", "last_used_model"]:
    if key not in st.session_state: 
        st.session_state[key] = None if key not in ["img_reset_key", "auth_status", "last_used_model"] else (0 if key == "img_reset_key" else (False if key == "auth_status" else "Chưa xác định"))

if "user" in st.query_params and "role" in st.query_params:
    st.session_state.auth_status, st.session_state.username, st.session_state.role = True, st.query_params["user"], st.query_params["role"]

if "current_key_index" not in st.session_state: st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)
if "key_status" not in st.session_state: st.session_state.key_status = {i: "🟢 Sẵn sàng" for i in range(len(API_KEYS))}
if "quota_cooldown" not in st.session_state: st.session_state.quota_cooldown = {}
if "selected_persona" not in st.session_state: st.session_state.selected_persona = list(PERSONAS.keys())[0]

# 5. Hàm gọi API "Vét Ngang" - Bắt Lỗi Triệt Để
def query_gemini(prompt_text, history_list, image_data=None):
    # Đổi tên model sang bản -latest để tránh lỗi 404 của v1beta
    MODEL_TIERS = [
        ["gemini-1.5-pro-latest"],       # TIER 0
        ["gemini-1.5-flash-latest"]      # TIER 1
    ]

    current_time = time.time()
    last_error = ""
    system_instruction = PERSONAS.get(st.session_state.selected_persona, "")
    all_keys_cooldown = True # Cờ kiểm tra xem tất cả các key có đang bị khóa 60s không

    for tier_idx, tier_models in enumerate(MODEL_TIERS):
        start_key = st.session_state.current_key_index
        for offset in range(len(API_KEYS)):
            key_idx = (start_key + offset) % len(API_KEYS)
            
            if current_time < st.session_state.quota_cooldown.get((key_idx, tier_idx), 0):
                continue 
            
            all_keys_cooldown = False # Có ít nhất 1 key sẵn sàng
            genai.configure(api_key=API_KEYS[key_idx])

            for model_name in tier_models:
                try:
                    model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
                    
                    if image_data:
                        res = model.generate_content([image_data, prompt_text])
                    elif history_list:
                        chat = model.start_chat(history=history_list)
                        res = chat.send_message(prompt_text)
                    else:
                        res = model.generate_content(prompt_text)

                    st.session_state.current_key_index = key_idx
                    tier_label = ["Pro", "Flash"][tier_idx]
                    st.session_state.key_status[key_idx] = f"🟢 Đang dùng ({tier_label})"
                    return res.text, model_name

                except ResourceExhausted:
                    st.session_state.quota_cooldown[(key_idx, tier_idx)] = current_time + 60
                    st.session_state.key_status[key_idx] = f"🟡 Hết Quota {model_name}"
                    last_error = f"{model_name} hết quota (Lỗi 429)"
                    break 
                except Exception as e:
                    last_error = f"Lỗi {model_name}: {str(e)}"
                    continue

    if all_keys_cooldown:
        return "⚠️ **Tất cả các API Key đều đang hết Quota.**\n\nHệ thống đang trong thời gian đếm ngược (60 giây) để thử lại. Vui lòng đợi một lát!", "Error"
        
    return f"⚠️ **Các API Key hiện tại đang gặp lỗi kết nối.** \n\nVui lòng thử lại. \n*(Lỗi cuối: {last_error})*", "Error"

# 6. Màn hình Đăng nhập
if not st.session_state.auth_status:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.write("<br><br>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>✨ Đăng nhập AI</h2>", unsafe_allow_html=True)
        mode = st.radio("Vai trò:", ["User", "Admin"], horizontal=True)
        uname = st.text_input("Tên hiển thị:", value="User_1") if mode == "User" else "Admin"
        upass = st.text_input("Mật khẩu:", type="password")
        if st.button("Tiếp tục", type="primary", use_container_width=True):
            if (mode == "Admin" and upass == ADMIN_PASSWORD) or (mode == "User" and upass == USER_PASSWORD):
                st.session_state.update({"auth_status": True, "username": uname.strip(), "role": mode.lower()})
                st.query_params.update({"user": uname.strip(), "role": mode.lower()})
                st.rerun()
            else:
                st.error("Mật khẩu sai.")
    st.stop()

# 7. Sidebar
with st.sidebar:
    st.title("✨ Gemini Clone")
    st.session_state.selected_persona = st.selectbox("🎭 Chế độ:", list(PERSONAS.keys()))
    
    if st.session_state.role == "user":
        if st.button("➕ Chat mới", use_container_width=True, type="primary"):
            new_id = str(uuid.uuid4())[:8]
            run_query("INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)", (new_id, st.session_state.username, str(datetime.datetime.now())))
            st.session_state.current_session_id = new_id
            st.rerun()

        st.markdown("### 💬 Lịch sử")
        for s_id, s_title, s_pin in (run_query("SELECT id, title, is_pinned FROM sessions WHERE username = %s ORDER BY is_pinned DESC, created_at DESC", (st.session_state.username,), fetch="all") or []):
            if not s_title:
                f_msg = run_query("SELECT content FROM messages WHERE session_id = %s AND role = 'user' ORDER BY id ASC LIMIT 1", (s_id,), fetch="one")
                s_title = f_msg[0][:18] + "..." if f_msg else "Phiên chat trống"

            c1, c2 = st.columns([0.8, 0.2])
            with c1:
                if st.button(f"{'📌 ' if s_pin else ''}{s_title}", key=f"b_{s_id}", use_container_width=True, type="primary" if s_id == st.session_state.current_session_id else "secondary"):
                    st.session_state.current_session_id = s_id
                    st.rerun()
            with c2:
                with st.popover("⚙️"):
                    if st.button("📍 Bỏ ghim" if s_pin else "📌 Ghim", key=f"p_{s_id}"):
                        toggle_pin_session(s_id, s_pin); st.rerun()
                    nt = st.text_input("Tên mới:", value=s_title, key=f"i_{s_id}")
                    if st.button("Lưu", key=f"r_{s_id}") and nt.strip():
                        update_session_title(s_id, nt.strip()); st.rerun()
                    if st.button("🗑️ Xóa", key=f"d_{s_id}", type="primary"):
                        delete_session(s_id); st.rerun()

    with st.expander("🔑 Trạng thái API Keys", expanded=False):
        for idx, status in st.session_state.key_status.items():
            st.caption(f"**Key #{idx+1}:** {status} {'👈' if idx == st.session_state.current_key_index else ''}")

    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False; st.query_params.clear(); st.rerun()

# 8. Giao diện Chat Chính
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.admin_selected_session

if not active_sid and st.session_state.role == "user":
    new_id = str(uuid.uuid4())[:8]
    run_query("INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)", (new_id, st.session_state.username, str(datetime.datetime.now())))
    st.session_state.current_session_id = new_id
    st.rerun()

db_messages = run_query("SELECT role, content FROM messages WHERE session_id = %s ORDER BY id ASC", (active_sid,), fetch="all") or []

# HIỂN THỊ NÚT CHỌN MODEL ĐỘNG Ở ĐẦU TRANG GIỐNG GEMINI
display_model = "Gemini 1.5 Pro" if "pro" in st.session_state.last_used_model.lower() else ("Gemini 1.5 Flash" if "flash" in st.session_state.last_used_model.lower() else "Đang khởi tạo...")
st.markdown(f'<div class="model-badge">✨ {display_model} <span style="color:#666; font-size:12px; margin-left:8px;">({st.session_state.last_used_model})</span></div>', unsafe_allow_html=True)

if not db_messages and st.session_state.role == "user":
    st.markdown(f"<h1 style='background: -webkit-linear-gradient(45deg, #4285F4, #D96570); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>Xin chào, {st.session_state.username}</h1>", unsafe_allow_html=True)
    
gemini_history = []
for role, content in db_messages:
    with st.chat_message(role, avatar="👤" if role == "user" else "✨"):
        st.markdown(content)
    gemini_history.append({"role": "user" if role == "user" else "model", "parts": [content]})

# KHU VỰC NHẬP LIỆU GẮN CHẶT XUỐNG DƯỚI
st.markdown('<div class="bottom-input-container"><div class="bottom-input-inner">', unsafe_allow_html=True)

# Khung chọn ảnh
col_up, col_paste = st.columns([0.6, 0.4])
rk = st.session_state.img_reset_key
with col_up:
    uploaded_file = st.file_uploader("🖼️ Gửi ảnh", type=["jpg", "png", "webp"], label_visibility="collapsed", key=f"up_{rk}")
with col_paste:
    paste_result = paste_image_button("📋 Dán ảnh", background_color="#f0f4f9", text_color="#000", key=f"pst_{rk}")

img_data = Image.open(uploaded_file) if uploaded_file else (paste_result.image_data if paste_result and getattr(paste_result, "image_data", None) else None)

if img_data:
    c_img, c_del = st.columns([0.8, 0.2])
    with c_img: st.image(img_data, width=150)
    with c_del:
        if st.button("❌ Hủy", key=f"dx_{rk}"): st.session_state.img_reset_key += 1; st.rerun()

# Ô Chat Input
if prompt := st.chat_input("Nhập câu hỏi của bạn tại đây..."):
    with st.chat_message("user", avatar="👤"):
        if img_data: st.image(img_data, width=200)
        st.markdown(prompt)

    msg_store = f"[Đã gửi 1 hình ảnh] {prompt}" if img_data else prompt
    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (active_sid, msg_store))

    with st.chat_message("assistant", avatar="✨"):
        with st.spinner("Đang suy luận..."):
            reply, used_model = query_gemini(prompt, gemini_history, image_data=img_data)
            
            if used_model != "Error":
                st.session_state.last_used_model = used_model # Lưu lại model thành công để hiển thị lên trên đỉnh
                
            st.markdown(reply)

    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'assistant', %s)", (active_sid, reply))
    
    if img_data: st.session_state.img_reset_key += 1
    st.rerun()

st.markdown('</div></div>', unsafe_allow_html=True)
