import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
import random
import sqlite3
import datetime
import uuid

# 1. Cấu hình trang (Tắt sidebar mặc định lúc đăng nhập, dùng layout rộng)
st.set_page_config(page_title="Gemini Clone", page_icon="✨", layout="wide")

# CSS Tùy chỉnh để giấu viền, làm mượt UI giống Gemini
st.markdown("""
<style>
    .block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 900px; }
    .stChatInputContainer { padding-bottom: 20px; }
</style>
""", unsafe_allow_html=True)

# 2. Kiểm tra Secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets (cần GEMINI_API_KEYS, ADMIN_PASSWORD, USER_PASSWORD).")
    st.stop()

# 3. Khởi tạo Database
def init_db():
    conn = sqlite3.connect("chats.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            username TEXT,
            is_locked INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT
        )
    """)
    conn.commit()
    return conn

conn = init_db()

# 4. State Management
if "auth_status" not in st.session_state: st.session_state.auth_status = False
if "role" not in st.session_state: st.session_state.role = None
if "username" not in st.session_state: st.session_state.username = None
if "current_session_id" not in st.session_state: st.session_state.current_session_id = None
if "current_key_index" not in st.session_state: st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)

def rotate_key():
    if len(API_KEYS) <= 1: return False
    available = [i for i in range(len(API_KEYS)) if i != st.session_state.current_key_index]
    if available:
        st.session_state.current_key_index = random.choice(available)
        return True
    return False

# 5. Hàm gọi API an toàn (Bổ sung model dự phòng)
def query_gemini(prompt_text, history_list):
    attempts = 0
    max_attempts = len(API_KEYS)
    # Thêm 'gemini-pro' đời cũ để bao lô mọi trường hợp thư viện cũ
    models_to_try = ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-pro']
    last_error = ""

    while attempts < max_attempts:
        current_key = API_KEYS[st.session_state.current_key_index]
        genai.configure(api_key=current_key)
        
        for model_name in models_to_try:
            try:
                model = genai.GenerativeModel(model_name)
                if history_list:
                    chat = model.start_chat(history=history_list)
                    res = chat.send_message(prompt_text)
                else:
                    res = model.generate_content(prompt_text)
                return res.text
            except ResourceExhausted:
                last_error = "Hết Quota"
                break 
            except Exception as e:
                last_error = f"Lỗi {model_name}: {str(e)}"
                continue 
        
        attempts += 1
        if rotate_key():
            st.toast(f"Đã tự động đổi API Key do lỗi.", icon="🔄")
        else:
            break

    return f"⚠️ **Không thể kết nối API.** Lỗi ghi nhận: `{last_error}`. Hãy nâng cấp `google-generativeai`."

# 6. Màn hình Đăng nhập (Nếu chưa Login)
if not st.session_state.auth_status:
    col_space1, col_box, col_space2 = st.columns([1, 2, 1])
    with col_box:
        st.write("")
        st.write("")
        st.markdown("<h2 style='text-align: center;'>✨ Đăng nhập Hệ thống AI</h2>", unsafe_allow_html=True)
        st.write("")
        mode = st.radio("Đăng nhập với tư cách:", ["User", "Admin"], horizontal=True)
        input_name = st.text_input("Tên hiển thị (Username):", value="User_1") if mode == "User" else "Admin"
        input_pass = st.text_input("Mật khẩu:", type="password")
        
        if st.button("Tiếp tục", type="primary", use_container_width=True):
            is_valid = False
            if mode == "Admin" and input_pass == ADMIN_PASSWORD:
                is_valid = True
                st.session_state.role = "admin"
            elif mode == "User" and input_pass == USER_PASSWORD:
                is_valid = True
                st.session_state.role = "user"
            
            if is_valid:
                st.session_state.auth_status = True
                st.session_state.username = input_name.strip()
                
                if mode == "User":
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM sessions WHERE username = ? ORDER BY created_at DESC LIMIT 1", (st.session_state.username,))
                    last_sess = cursor.fetchone()
                    if last_sess:
                        st.session_state.current_session_id = last_sess[0]
                    else:
                        new_id = str(uuid.uuid4())[:8]
                        cursor.execute("INSERT INTO sessions VALUES (?, ?, 0, ?)", (new_id, st.session_state.username, str(datetime.datetime.now())))
                        conn.commit()
                        st.session_state.current_session_id = new_id
                st.rerun()
            else:
                st.error("Mật khẩu không chính xác.")
    st.stop()

# 7. Giao diện Sidebar (Chuẩn phong cách Gemini)
cursor = conn.cursor()

with st.sidebar:
    if st.session_state.role == "user":
        # Nút tạo chat mới to đùng trên cùng
        if st.button("➕ Chat mới", use_container_width=True, type="primary"):
            new_id = str(uuid.uuid4())[:8]
            cursor.execute("INSERT INTO sessions VALUES (?, ?, 0, ?)", (new_id, st.session_state.username, str(datetime.datetime.now())))
            conn.commit()
            st.session_state.current_session_id = new_id
            st.rerun()
        
        st.write("")
        st.markdown("### Gần đây")
        
        # Load lịch sử chat dưới dạng nút bấm (giống Gemini)
        cursor.execute("SELECT id, created_at, is_locked FROM sessions WHERE username = ? ORDER BY created_at DESC", (st.session_state.username,))
        user_sessions = cursor.fetchall()
        
        for sess in user_sessions:
            s_id, s_time, s_lock = sess
            # Lấy tin nhắn đầu tiên làm tiêu đề (nếu có), nếu không dùng ID
            cursor.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1", (s_id,))
            first_msg = cursor.fetchone()
            
            title = first_msg[0][:25] + "..." if first_msg else f"Phiên chat trống"
            lock_icon = "🔒 " if s_lock else "💬 "
            
            # Đổi màu nền nếu đang chọn session này
            is_active = (s_id == st.session_state.current_session_id)
            if st.button(f"{lock_icon}{title}", key=f"btn_{s_id}", use_container_width=True, type="secondary" if not is_active else "primary"):
                st.session_state.current_session_id = s_id
                st.rerun()
        
    elif st.session_state.role == "admin":
        st.subheader("🛠 Quản trị viên")
        cursor.execute("SELECT id, username FROM sessions ORDER BY created_at DESC")
        all_s = cursor.fetchall()
        options = {f"{s[1]} - {s[0]}": s[0] for s in all_s}
        sel = st.selectbox("Theo dõi chat của user:", list(options.keys()))
        if sel:
            st.session_state.admin_selected_session = options[sel]

    st.write("")
    st.divider()
    st.caption(f"Đang dùng: {st.session_state.username}")
    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False
        st.rerun()

# 8. Màn hình Chat Chính
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")
if not active_sid:
    st.stop()

# Lấy tin nhắn
cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (active_sid,))
db_messages = cursor.fetchall()

# Giao diện chào mừng nếu chưa có tin nhắn (Chuẩn Gemini)
if not db_messages and st.session_state.role == "user":
    st.write("<br><br>", unsafe_allow_html=True)
    st.markdown(f"<h1 style='background: -webkit-linear-gradient(45deg, #4285F4, #D96570); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>Xin chào, {st.session_state.username}</h1>", unsafe_allow_html=True)
    st.markdown("<h2 style='color: #666;'>Tôi có thể giúp gì cho bạn hôm nay?</h2>", unsafe_allow_html=True)
    st.write("<br>", unsafe_allow_html=True)

# Render tin nhắn
gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content)
    g_role = "user" if role == "user" else "model"
    gemini_history.append({"role": g_role, "parts": [content]})

# Ô nhập liệu
if prompt := st.chat_input("Nhập câu hỏi của bạn tại đây..."):
    with st.chat_message("user"):
        st.markdown(prompt)
    
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)", (active_sid, prompt))
    conn.commit()

    with st.chat_message("assistant"):
        with st.spinner("Đang suy luận..."):
            reply = query_gemini(prompt, gemini_history)
            st.markdown(reply)
            
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)", (active_sid, reply))
    conn.commit()
    
    # Reload để cập nhật tiêu đề sidebar nếu đây là tin nhắn đầu tiên
    if len(db_messages) == 0:
        st.rerun()
