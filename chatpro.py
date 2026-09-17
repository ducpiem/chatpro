import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
import random
import sqlite3
import datetime
import uuid

# 1. Cấu hình trang
st.set_page_config(page_title="Gemini Multi-User Pro", page_icon="⚡", layout="wide")

# 2. Kiểm tra Secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets (cần GEMINI_API_KEYS, ADMIN_PASSWORD, USER_PASSWORD).")
    st.stop()

# 3. Khởi tạo Database SQLite
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

# 4. Khởi tạo State
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
    if available:
        st.session_state.current_key_index = random.choice(available)
        return True
    return False

def get_masked_key(index):
    key_str = str(API_KEYS[index])
    return f"...{key_str[-4:]}" if len(key_str) >= 4 else "Key"

# 5. Màn hình Đăng nhập
if not st.session_state.auth_status:
    st.title("🔒 Hệ thống AI Chat Multi-User")
    col1, _ = st.columns([1, 1])
    with col1:
        mode = st.radio("Vai trò:", ["User thường", "Admin quản trị"])
        input_pass = st.text_input("Mật khẩu:", type="password")
        input_name = st.text_input("Tên hiển thị (Username):", value="User_1")
        
        if st.button("Đăng nhập", type="primary"):
            if mode == "Admin quản trị" and input_pass == ADMIN_PASSWORD:
                st.session_state.auth_status = True
                st.session_state.role = "admin"
                st.session_state.username = "Admin"
                st.rerun()
            elif mode == "User thường" and input_pass == USER_PASSWORD:
                st.session_state.auth_status = True
                st.session_state.role = "user"
                st.session_state.username = input_name.strip()
                
                # Check xem user đã có session nào chưa, chưa có thì tạo mới
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM sessions WHERE username = ? ORDER BY created_at DESC LIMIT 1", (st.session_state.username,))
                last_sess = cursor.fetchone()
                
                if last_sess:
                    st.session_state.current_session_id = last_sess[0]
                else:
                    sess_id = str(uuid.uuid4())[:8]
                    cursor.execute("INSERT INTO sessions VALUES (?, ?, 0, ?)", 
                                   (sess_id, st.session_state.username, str(datetime.datetime.now())))
                    conn.commit()
                    st.session_state.current_session_id = sess_id
                st.rerun()
            else:
                st.error("Sai mật khẩu!")
    st.stop()

# 6. Hàm xử lý gọi API bắt lỗi chi tiết
def query_gemini_with_rotation(prompt_text, history_list):
    attempts = 0
    max_attempts = len(API_KEYS)
    preferred_models = ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-1.5-pro', 'gemini-1.5-flash']
    last_error = ""

    while attempts < max_attempts:
        current_key = API_KEYS[st.session_state.current_key_index]
        genai.configure(api_key=current_key)
        
        for model_name in preferred_models:
            try:
                model = genai.GenerativeModel(model_name)
                if history_list:
                    chat = model.start_chat(history=history_list)
                    res = chat.send_message(prompt_text)
                else:
                    res = model.generate_content(prompt_text)
                return res.text
            except ResourceExhausted:
                last_error = "Hết Quota (429 ResourceExhausted)"
                break # Văng ra khỏi vòng for để xoay key khác
            except Exception as e:
                last_error = f"{model_name} báo lỗi: {str(e)}"
                continue # Model này lỗi (vd: 404), thử model khác trong list
        
        attempts += 1
        old_idx = st.session_state.current_key_index
        if rotate_key_randomly():
            st.toast(f"Xoay key: Từ Key #{old_idx+1} sang Key #{st.session_state.current_key_index+1}", icon="🔄")
        else:
            break

    return f"⚠️ **Lỗi API chi tiết:** {last_error}"

# 7. Thanh Sidebar
with st.sidebar:
    st.markdown(f"### 👤 **{st.session_state.username}** (`{st.session_state.role.upper()}`)")
    
    if st.session_state.role == "user":
        # DANH SÁCH LỊCH SỬ CHAT CHO USER
        st.subheader("📚 Lịch sử Chat của bạn")
        cursor = conn.cursor()
        cursor.execute("SELECT id, created_at, is_locked FROM sessions WHERE username = ? ORDER BY created_at DESC", (st.session_state.username,))
        user_sessions = cursor.fetchall()
        
        if user_sessions:
            # Tạo dictionary để ánh xạ tên hiển thị -> session ID
            sess_dict = {f"Phiên {s[0]} ({s[1][:16]}) {'🔒' if s[2] else ''}": s[0] for s in user_sessions}
            
            # Lấy index của session hiện tại để chọn đúng mục trong listbox
            try:
                current_index = list(sess_dict.values()).index(st.session_state.current_session_id)
            except ValueError:
                current_index = 0

            selected_sess_name = st.selectbox("Chọn cuộc trò chuyện:", list(sess_dict.keys()), index=current_index)
            
            # Nếu người dùng chọn một phiên khác, cập nhật lại state
            if selected_sess_name:
                selected_id = sess_dict[selected_sess_name]
                if selected_id != st.session_state.current_session_id:
                    st.session_state.current_session_id = selected_id
                    st.rerun()

        st.divider()
        st.subheader("⚙️ Quản lý & Cài đặt")
        if st.button("➕ Tạo phiên chat mới", use_container_width=True):
            sess_id = str(uuid.uuid4())[:8]
            cursor.execute("INSERT INTO sessions VALUES (?, ?, 0, ?)", 
                           (sess_id, st.session_state.username, str(datetime.datetime.now())))
            conn.commit()
            st.session_state.current_session_id = sess_id
            st.rerun()

        cursor.execute("SELECT is_locked FROM sessions WHERE id = ?", (st.session_state.current_session_id,))
        row = cursor.fetchone()
        is_locked = row[0] if row else 0
        
        if is_locked == 0:
            if st.button("🔒 Khóa riêng tư trò chuyện này", type="primary", use_container_width=True):
                cursor.execute("UPDATE sessions SET is_locked = 1 WHERE id = ?", (st.session_state.current_session_id,))
                conn.commit()
                st.rerun()
        else:
            if st.button("🔓 Mở khóa trò chuyện này", use_container_width=True):
                cursor.execute("UPDATE sessions SET is_locked = 0 WHERE id = ?", (st.session_state.current_session_id,))
                conn.commit()
                st.rerun()

    elif st.session_state.role == "admin":
        st.subheader("🛡️ Admin Monitoring")
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, is_locked, created_at FROM sessions ORDER BY created_at DESC")
        all_sessions = cursor.fetchall()
        
        sess_options = {f"[{'LOCKED' if s[2] else 'OPEN'}] {s[1]} ({s[0]})": s[0] for s in all_sessions}
        selected_label = st.selectbox("Chọn phiên chat theo dõi:", list(sess_options.keys()) if sess_options else ["Không có"])
        
        if sess_options and selected_label:
            st.session_state.admin_selected_session = sess_options[selected_label]
            c_id = st.session_state.admin_selected_session
            cursor.execute("SELECT is_locked FROM sessions WHERE id = ?", (c_id,))
            l_val = cursor.fetchone()[0]
            col_a, col_b = st.columns(2)
            with col_a:
                if l_val == 0 and st.button("Khóa phiên này"):
                    cursor.execute("UPDATE sessions SET is_locked = 1 WHERE id = ?", (c_id,))
                    conn.commit()
                    st.rerun()
            with col_b:
                if l_val == 1 and st.button("Mở khóa phiên này"):
                    cursor.execute("UPDATE sessions SET is_locked = 0 WHERE id = ?", (c_id,))
                    conn.commit()
                    st.rerun()

    st.divider()
    st.caption(f"🔑 Key đang dùng: #{st.session_state.current_key_index + 1} ({get_masked_key(st.session_state.current_key_index)})")
    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False
        st.rerun()

# 8. Màn hình Chat chính
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")

if not active_sid:
    st.info("Vui lòng tạo hoặc chọn một phiên chat.")
    st.stop()

cursor = conn.cursor()
cursor.execute("SELECT username, is_locked FROM sessions WHERE id = ?", (active_sid,))
sess_info = cursor.fetchone()

if sess_info:
    owner_name, locked_state = sess_info
    if locked_state == 1 and st.session_state.role == "user" and st.session_state.username != owner_name:
        st.error("🚫 Cuộc trò chuyện này đã bị chủ sở hữu khóa riêng tư!")
        st.stop()

st.title(f"💬 Chat AI (Session: `{active_sid}`)")

# Lấy tin nhắn
cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (active_sid,))
db_messages = cursor.fetchall()

gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content)
    g_role = "user" if role == "user" else "model"
    gemini_history.append({"role": g_role, "parts": [content]})

if prompt := st.chat_input("Nhập tin nhắn..."):
    # Kiểm tra khóa
    cursor.execute("SELECT is_locked FROM sessions WHERE id = ?", (active_sid,))
    row_lock = cursor.fetchone()
    if row_lock and row_lock[0] == 1 and st.session_state.role == "user" and st.session_state.username != owner_name:
        st.error("Không thể nhắn tin vào phiên đã bị khóa.")
        st.stop()

    with st.chat_message("user"):
        st.markdown(prompt)
    
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)", (active_sid, prompt))
    conn.commit()

    with st.chat_message("assistant"):
        with st.spinner("Đang suy luận..."):
            reply = query_gemini_with_rotation(prompt, gemini_history)
            st.markdown(reply)
            
    cursor.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)", (active_sid, reply))
    conn.commit()
