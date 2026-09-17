import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
import random
import sqlite3
import datetime
import uuid

st.set_page_config(page_title="Gemini Pro (Rotation)", page_icon="⚡", layout="wide")

# Đọc secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets trên Streamlit Cloud (cần ADMIN_PASSWORD, USER_PASSWORD, GEMINI_API_KEYS).")
    st.stop()

# Khởi tạo SQLite database cục bộ (lưu trữ lịch sử dài hạn, không có chức năng xóa)
def init_db():
    conn = sqlite3.connect("chats.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            username TEXT,
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

# Khởi tạo session state
if "auth_status" not in st.session_state:
    st.session_state.auth_status = False
if "role" not in st.session_state:
    st.session_state.role = None
if "username" not in st.session_state:
    st.session_state.username = None
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "current_key_index" not in st.session_state:
    st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)

def rotate_key_randomly():
    if len(API_KEYS) <= 1:
        return False
    available = [i for i in range(len(API_KEYS)) if i != st.session_state.current_key_index]
    st.session_state.current_key_index = random.choice(available)
    return True

def get_masked_key(index):
    return f"...{API_KEYS[index][-4:]}"

# Màn hình đăng nhập
if not st.session_state.auth_status:
    st.title("⚡ Đăng nhập hệ thống AI Pro")
    col1, col2 = st.columns(2)
    with col1:
        mode = st.radio("Chọn vai trò:", ["User thường", "Admin quản trị"])
        input_pass = st.text_input("Mật khẩu:", type="password")
        input_name = st.text_input("Tên định danh của bạn (Username):", value="User_1")
        
        if st.button("Đăng nhập"):
            if mode == "Admin quản trị" and input_pass == ADMIN_PASSWORD:
                st.session_state.auth_status = True
                st.session_state.role = "admin"
                st.session_state.username = "Admin"
                st.rerun()
            elif mode == "User thường" and input_pass == USER_PASSWORD:
                st.session_state.auth_status = True
                st.session_state.role = "user"
                st.session_state.username = input_name.strip()
                # Tạo session mới cho user
                sess_id = str(uuid.uuid4())[:8]
                cursor = conn.cursor()
                cursor.execute("INSERT INTO sessions VALUES (?, ?, ?)", 
                               (sess_id, st.session_state.username, str(datetime.datetime.now())))
                conn.commit()
                st.session_state.current_session_id = sess_id
                st.rerun()
            else:
                st.error("Sai mật khẩu hoặc thông tin đăng nhập!")
    st.stop()

# Xử lý xoay vòng API gửi prompt (Dùng model Pro mạnh nhất)
def query_gemini_with_rotation(prompt_text, history_list):
    success = False
    attempts = 0
    max_attempts = len(API_KEYS)
    response_text = ""

    while not success and attempts < max_attempts:
        try:
            current_key = API_KEYS[st.session_state.current_key_index]
            genai.configure(api_key=current_key)
            # Dùng model Pro mạnh nhất (nếu project/key chưa bật 2.5-pro, đổi lại thành 'gemini-1.5-pro')
            model = genai.GenerativeModel('gemini-2.5-pro')
            chat = model.start_chat(history=history_list)
            res = chat.send_message(prompt_text)
            response_text = res.text
            success = True
        except ResourceExhausted:
            attempts += 1
            old_idx = st.session_state.current_key_index
            if rotate_key_randomly():
                st.toast(f"Key {old_idx+1} quá tải, đã đổi ngẫu nhiên sang Key {st.session_state.current_key_index+1}", icon="🔄")
            else:
                break
        except Exception as e:
            return f"Lỗi gọi API: {str(e)}"
    
    if not success:
        return "Tất cả API keys đều đang quá tải, vui lòng thử lại sau vài giây."
    return response_text

# Sidebar quản lý (chỉ hiện info, trạng thái key, đổi phiên mới, không có nút xóa/khóa)
with st.sidebar:
    st.write(f"👤 **{st.session_state.username}** ({st.session_state.role.upper()})")
    
    if st.session_state.role == "user":
        if st.button("➕ Tạo phiên chat mới"):
            sess_id = str(uuid.uuid4())[:8]
            cursor = conn.cursor()
            cursor.execute("INSERT INTO sessions VALUES (?, ?, ?)", 
                           (sess_id, st.session_state.username, str(datetime.datetime.now())))
            conn.commit()
            st.session_state.current_session_id = sess_id
            st.rerun()

    elif st.session_state.role == "admin":
        st.subheader("🛡️ Admin Panel - Giám sát")
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, created_at FROM sessions ORDER BY created_at DESC")
        all_sessions = cursor.fetchall()
        
        sess_options = {f"{s[1]} ({s[0]})": s[0] for s in all_sessions}
        selected_label = st.selectbox("Xem phiên chat của user:", list(sess_options.keys()) if sess_options else ["Không có"])
        
        if sess_options and selected_label:
            st.session_state.admin_selected_session = sess_options[selected_label]

    st.divider()
    st.success(f"🟢 Đang dùng Key #{st.session_state.current_key_index + 1} {get_masked_key(st.session_state.current_key_index)}")
    if st.button("Đăng xuất ứng dụng"):
        st.session_state.auth_status = False
        st.rerun()

# Giao diện chính hiển thị chat
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")

if not active_sid:
    st.info("Vui lòng chọn phiên chat hoặc tạo phiên mới.")
    st.stop()

st.title(f"⚡ Gemini 2.5 Pro (Phiên: {active_sid})")

# Load lịch sử từ DB
cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (active_sid,))
db_messages = cursor.fetchall()

gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content)
    g_role = "user" if role == "user" else "model"
    gemini_history.append({"role": g_role, "parts": [content]})

# Xử lý input chat
if prompt := st.chat_input("Nhập câu hỏi tư duy sâu..."):
    with st.chat_message("user"):
        st.markdown(prompt)
    
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)", (active_sid, prompt))
    conn.commit()
    gemini_history.append({"role": "user", "parts": [prompt]})

    with st.chat_message("assistant"):
        with st.spinner("Đang dùng Gemini 2.5 Pro suy luận..."):
            reply = query_gemini_with_rotation(prompt, gemini_history[:-1])
            st.markdown(reply)
            
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)", (active_sid, reply))
    conn.commit()
