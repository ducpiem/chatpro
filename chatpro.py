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

# 3. Khởi tạo Database SQLite (Đã sửa lỗi dấu ngoặc chuẩn 100%)
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
    st.session_state.current_key_index = random.choice(available)
    return True

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
                sess_id = str(uuid.uuid4())[:8]
                cursor = conn.cursor()
                cursor.execute("INSERT INTO sessions VALUES (?, ?, 0, ?)", 
                               (sess_id, st.session_state.username, str(datetime.datetime.now())))
                conn.commit()
                st.session_state.current_session_id = sess_id
                st.rerun()
            else:
                st.error("Sai mật khẩu!")
    st.stop()

# 6. Hàm xử lý gọi API + Xoay Key an toàn
def query_gemini_with_rotation(prompt_text, history_list):
    attempts = 0
    max_attempts = len(API_KEYS)
    # Danh sách các model ưu tiên thử nghiệm (từ Pro xịn đến Flash ổn định)
    preferred_models = ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-pro-latest', 'gemini-flash-latest']

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
                # Key bị hết quota -> Break vòng model để xoay sang Key mới
                break
            except Exception:
                # Thử model tiếp theo trong danh sách nếu model này lỗi
                continue
        
        # Nếu đã thử hết model mà key này vẫn lỗi / hết quota -> Đổi key
        attempts += 1
        old_idx = st.session_state.current_key_index
        if rotate_key_randomly():
            st.toast(f"Chuyển từ Key #{old_idx+1} sang Key #{st.session_state.current_key_index+1}", icon="🔄")
        else:
            break

    return "⚠️ Tất cả API Key đều hết quota hoặc không khởi tạo được model. Vui lòng kiểm tra lại Key trên AI Studio."

# 7. Thanh Sidebar
with st.sidebar:
    st.markdown(f"### 👤 **{st.session_state.username}** (`{st.session_state.role.upper()}`)")
    
    if st.session_state.role == "user":
        new_username = st.text_input("Đổi tên hiển thị:", value=st.session_state.username)
        if st.button("Lưu tên mới"):
            if new_username.strip():
                st.session_state.username = new_username.strip()
                cursor = conn.cursor()
                cursor.execute("UPDATE sessions SET username = ? WHERE id = ?", (st.session_state.username, st.session_state.current_session_id))
                conn.commit()
                st.success("Đã cập nhật!")
                st.rerun()

        st.divider()
        st.subheader("⚙️ Quản lý phiên Chat")
        cursor = conn.cursor()
        cursor.execute("SELECT is_locked FROM sessions WHERE id = ?", (st.session_state.current_session_id,))
        row = cursor.fetchone()
        is_locked = row[0] if row else 0
        
        if is_locked == 0:
            if st.button("🔒 Khóa riêng tư cuộc trò chuyện", type="primary"):
                cursor.execute("UPDATE sessions SET is_locked = 1 WHERE id = ?", (st.session_state.current_session_id,))
                conn.commit()
                st.rerun()
        else:
            st.warning("🔒 Phiên chat này đang Khóa.")
            if st.button("🔓 Mở khóa"):
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
    st.caption(f"🔑 Đang dùng Key #{st.session_state.current_key_index + 1} ({get_masked_key(st.session_state.current_key_index)})")
    if st.button("Đăng xuất"):
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

cursor.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (active_sid,))
db_messages = cursor.fetchall()

gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content)
    g_role = "user" if role == "user" else "model"
    gemini_history.append({"role": g_role, "parts": [content]})

if prompt := st.chat_input("Nhập tin nhắn..."):
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
