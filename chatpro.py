import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
import random
import sqlite3
import datetime
import uuid

st.set_page_config(page_title="Gemini Multi-User Pro", page_icon="🔒", layout="wide")

# Đọc secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets trên Streamlit Cloud (cần ADMIN_PASSWORD, USER_PASSWORD, GEMINI_API_KEYS).")
    st.stop()

# Khởi tạo SQLite database cục bộ
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
    st.title("🔒 Đăng nhập hệ thống AI")
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
                cursor.execute("INSERT INTO sessions VALUES (?, ?, 0, ?)", 
                               (sess_id, st.session_state.username, str(datetime.datetime.now())))
                conn.commit()
                st.session_state.current_session_id = sess_id
                st.rerun()
            else:
                st.error("Sai mật khẩu hoặc thông tin đăng nhập!")
    st.stop()

# Xử lý xoay vòng API gửi prompt (Đổi sang gemini-1.5-flash để fix lỗi 404)
def query_gemini_with_rotation(prompt_text, history_list):
    success = False
    attempts = 0
    max_attempts = len(API_KEYS)
    response_text = ""

    while not success and attempts < max_attempts:
        try:
            current_key = API_KEYS[st.session_state.current_key_index]
            genai.configure(api_key=current_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            chat = model.start_chat(history=history_list)
            res = chat.send_message(prompt_text)
            response_text = res.text
            success = True
        except ResourceExhausted:
            attempts += 1
            old_idx = st.session_state.current_key_index
            if rotate_key_randomly():
                st.toast(f"Key {old_idx+1} quá tải, đã đổi sang Key {st.session_state.current_key_index+1}", icon="🔄")
            else:
                break
        except Exception as e:
            return f"Lỗi gọi API: {str(e)}"
    
    if not success:
        return "Tất cả API keys đều đang quá tải, vui lòng thử lại sau vài giây."
    return response_text

# Sidebar quản trị / User menu
with st.sidebar:
    st.write(f"👤 **{st.session_state.username}** ({st.session_state.role.upper()})")
    
    if st.session_state.role == "user":
        # Tính năng đổi tên user
        new_username = st.text_input("Đổi tên hiển thị:", value=st.session_state.username)
        if st.button("Lưu tên mới"):
            if new_username.strip():
                st.session_state.username = new_username.strip()
                cursor = conn.cursor()
                cursor.execute("UPDATE sessions SET username = ? WHERE id = ?", (st.session_state.username, st.session_state.current_session_id))
                conn.commit()
                st.success("Đã cập nhật tên mới!")
                st.rerun()

        st.subheader("⚙️ Quản lý phiên của bạn")
        cursor = conn.cursor()
        cursor.execute("SELECT is_locked FROM sessions WHERE id = ?", (st.session_state.current_session_id,))
        row = cursor.fetchone()
        is_locked = row[0] if row else 0
        
        if is_locked == 0:
            if st.button("🔒 Khóa cuộc trò chuyện này (Private)", type="primary"):
                cursor.execute("UPDATE sessions SET is_locked = 1 WHERE id = ?", (st.session_state.current_session_id,))
                conn.commit()
                st.rerun()
        else:
            st.warning("🔒 Cuộc trò chuyện này đang bị khóa.")
            if st.button("🔓 Mở khóa trò chuyện"):
                cursor.execute("UPDATE sessions SET is_locked = 0 WHERE id = ?", (st.session_state.current_session_id,))
                conn.commit()
                st.rerun()
                
        if st.button("➕ Tạo phiên chat mới"):
            sess_id = str(uuid.uuid4())[:8]
            cursor.execute("INSERT INTO sessions VALUES (?, ?, 0, ?)", 
                           (sess_id, st.session_state.username, str(datetime.datetime.now())))
            conn.commit()
            st.session_state.current_session_id = sess_id
            st.rerun()

    elif st.session_state.role == "admin":
        st.subheader("🛡️ Admin Panel - Giám sát")
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, is_locked, created_at FROM sessions ORDER BY created_at DESC")
        all_sessions = cursor.fetchall()
        
        sess_options = {f"[{'LOCKED' if s[2] else 'OPEN'}] {s[1]} ({s[0]})": s[0] for s in all_sessions}
        selected_label = st.selectbox("Chọn phiên chat của người dùng:", list(sess_options.keys()) if sess_options else ["Không có"])
        
        if sess_options and selected_label:
            st.session_state.admin_selected_session = sess_options[selected_label]
            c_id = st.session_state.admin_selected_session
            cursor.execute("SELECT is_locked FROM sessions WHERE id = ?", (c_id,))
            l_val = cursor.fetchone()[0]
            col_a, col_b = st.columns(2)
            with col_a:
                if l_val == 0 and st.button("Admin Khóa"):
                    cursor.execute("UPDATE sessions SET is_locked = 1 WHERE id = ?", (c_id,))
                    conn.commit()
                    st.rerun()
            with col_b:
                if l_val == 1 and st.button("Admin Mở khóa"):
                    cursor.execute("UPDATE sessions SET is_locked = 0 WHERE id = ?", (c_id,))
                    conn.commit()
                    st.rerun()

    st.divider()
    st.caption(f"Đang dùng Key #{st.session_state.current_key_index + 1} {get_masked_key(st.session_state.current_key_index)}")
    if st.button("Đăng xuất / Khóa ứng dụng"):
        st.session_state.auth_status = False
        st.rerun()

# Giao diện chính hiển thị chat
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")

if not active_sid:
    st.info("Vui lòng chọn phiên chat hoặc tạo phiên mới.")
    st.stop()

# Kiểm tra quyền truy cập locked
cursor = conn.cursor()
cursor.execute("SELECT username, is_locked FROM sessions WHERE id = ?", (active_sid,))
sess_info = cursor.fetchone()

if sess_info:
    owner_name, locked_state = sess_info
    if locked_state == 1 and st.session_state.role == "user" and st.session_state.username != owner_name:
        st.error("🚫 Cuộc trò chuyện này đã bị người dùng khác khóa riêng tư!")
        st.stop()

st.title(f"🤖 Chat Pro (Phiên: {active_sid})")

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
if prompt := st.chat_input("Nhập câu hỏi..."):
    cursor.execute("SELECT is_locked FROM sessions WHERE id = ?", (active_sid,))
    if cursor.fetchone()[0] == 1 and st.session_state.role == "user" and st.session_state.username != owner_name:
        st.error("Không thể gửi tin nhắn vào phiên đã khóa.")
        st.stop()

    with st.chat_message("user"):
        st.markdown(prompt)
    
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)", (active_sid, prompt))
    conn.commit()
    gemini_history.append({"role": "user", "parts": [prompt]})

    with st.chat_message("assistant"):
        with st.spinner("Đang suy luận..."):
            reply = query_gemini_with_rotation(prompt, gemini_history[:-1])
            st.markdown(reply)
            
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)", (active_sid, reply))
    conn.commit()
