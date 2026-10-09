import datetime
import random
import uuid
import time
import os
import requests
from PIL import Image
import psycopg2
from psycopg2 import pool
import streamlit as st
from streamlit_paste_button import paste_image_button

# 1. Cấu hình trang & CSS
st.set_page_config(page_title="Gemini Clone Pro", page_icon="✨", layout="wide")

st.markdown(
    """
<style>
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #f1f1f1; }
    ::-webkit-scrollbar-thumb { background: #ccc; border-radius: 3px; }
    .block-container { padding-top: 1rem; padding-bottom: 4rem; max-width: 900px; }
    .stChatInputContainer { padding-bottom: 10px; }
</style>
""",
    unsafe_allow_html=True,
)

# 2. Kiểm tra Secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "1")
    NEON_DB_URL = st.secrets["NEON_DATABASE_URL"]
    PROXY_BASE_URL = st.secrets.get("GOOGLE_GEMINI_BASE_URL", "https://api.xah.io")
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets trên Streamlit.")
    st.stop()

# Danh sách Vai trò AI
PERSONAS = {
    "✨ Trợ lý Mặc định": "Bạn là một trợ lý AI thông minh, thân thiện và hữu ích.",
    "💻 Lập trình viên Senior": "Bạn là một chuyên gia lập trình Senior. Trả lời tập trung vào mã nguồn tối ưu, ngắn gọn, có giải thích rõ ràng.",
    "✍️ Chuyên gia Content": "Bạn là một chuyên gia sáng tạo nội dung và Marketing. Trả lời với giọng văn lôi cuốn, sáng tạo.",
    "🎓 Giáo sư Giảng dạy": "Bạn là một giáo sư đại học. Hãy giải thích các khái niệm phức tạp một cách vô cùng đơn giản, dễ hiểu.",
}

# 3. Quản lý Kết nối Database Neon
@st.cache_resource
def get_db_pool():
    return psycopg2.pool.SimpleConnectionPool(1, 10, NEON_DB_URL, connect_timeout=15)

def run_query(query, params=(), fetch=None):
    pool_conn = get_db_pool()
    conn = pool_conn.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            res = None
            if fetch == "one":
                res = cur.fetchone()
            elif fetch == "all":
                res = cur.fetchall()
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

def delete_session(session_id):
    run_query("DELETE FROM messages WHERE session_id = %s", (session_id,))
    run_query("DELETE FROM sessions WHERE id = %s", (session_id,))

def delete_user_data(username_to_del):
    run_query("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE username = %s)", (username_to_del,))
    run_query("DELETE FROM sessions WHERE username = %s", (username_to_del,))

def update_session_title(session_id, new_title):
    run_query("UPDATE sessions SET title = %s WHERE id = %s", (new_title, session_id))

def toggle_pin_session(session_id, current_pin):
    new_pin = 0 if current_pin == 1 else 1
    run_query("UPDATE sessions SET is_pinned = %s WHERE id = %s", (new_pin, session_id))

def update_username(old_name, new_name):
    run_query("UPDATE sessions SET username = %s WHERE username = %s", (new_name, old_name))

# 4. State Management
if "img_reset_key" not in st.session_state:
    st.session_state.img_reset_key = 0

if "auth_status" not in st.session_state:
    if "user" in st.query_params and "role" in st.query_params:
        st.session_state.auth_status = True
        st.session_state.username = st.query_params["user"]
        st.session_state.role = st.query_params["role"]
    else:
        st.session_state.auth_status = False

if "current_key_index" not in st.session_state:
    st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)
if "key_status" not in st.session_state:
    st.session_state.key_status = {i: "🟢 Sẵn sàng" for i in range(len(API_KEYS))}
if "selected_persona" not in st.session_state:
    st.session_state.selected_persona = list(PERSONAS.keys())[0]

# 5. Hàm gọi API trực tiếp qua endpoint chuẩn Gemini của Gateway (Khắc phục hoàn toàn lỗi 404)
def query_ai_gateway(prompt_text, history_list, chosen_model, image_data=None):
    system_instruction = PERSONAS.get(st.session_state.selected_persona, "")
    
    key_idx = st.session_state.current_key_index
    active_key = API_KEYS[key_idx]

    # Endpoint chuẩn Gemini cho gateway
    endpoint_url = f"{PROXY_BASE_URL.rstrip('/')}/v1beta/models/{chosen_model}:generateContent"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {active_key}"
    }

    contents = []
    
    if system_instruction:
        contents.append({"role": "user", "parts": [{"text": f"System Instruction: {system_instruction}"}]})
        contents.append({"role": "model", "parts": [{"text": "Đã hiểu."}]})

    for h in history_list[-6:]:
        g_role = "user" if h["role"] == "user" else "model"
        contents.append({"role": g_role, "parts": [{"text": h["parts"][0]}]})

    current_parts = []
    if image_data:
        import base64
        from io import BytesIO
        
        buffered = BytesIO()
        image_data.save(buffered, format="JPEG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        current_parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": img_base64
            }
        })
    
    current_parts.append({"text": prompt_text})
    contents.append({"role": "user", "parts": current_parts})

    payload = {
        "contents": contents
    }

    try:
        response = requests.post(endpoint_url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            res_json = response.json()
            candidates = res_json.get("candidates", [])
            if candidates:
                text_reply = candidates[0]["content"]["parts"][0]["text"]
                return text_reply, chosen_model
            else:
                return "⚠️ Phản hồi từ cổng gateway trống.", "Error"
        else:
            st.session_state.current_key_index = (key_idx + 1) % len(API_KEYS)
            return f"⚠️ Lỗi HTTP {response.status_code}: {response.text}", "Error"

    except Exception as e:
        st.session_state.current_key_index = (key_idx + 1) % len(API_KEYS)
        return f"⚠️ Lỗi kết nối đến gateway: {str(e)}", "Error"

# 6. Màn hình Đăng nhập
if not st.session_state.auth_status:
    col_space1, col_box, col_space2 = st.columns([1, 2, 1])
    with col_box:
        st.write("<br><br>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>✨ Đăng nhập Hệ thống AI</h2>", unsafe_allow_html=True)
        mode = st.radio("Tư cách đăng nhập:", ["User", "Admin"], horizontal=True)
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
                st.query_params["user"] = st.session_state.username
                st.query_params["role"] = st.session_state.role

                if mode == "User":
                    last_sess = run_query("SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC LIMIT 1", (st.session_state.username,), fetch="one")
                    if last_sess:
                        st.session_state.current_session_id = last_sess[0]
                    else:
                        new_id = str(uuid.uuid4())[:8]
                        run_query("INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)", (new_id, st.session_state.username, str(datetime.datetime.now())))
                        st.session_state.current_session_id = new_id
                st.rerun()
            else:
                st.error("Mật khẩu không chính xác.")
    st.stop()

if st.session_state.role == "user" and not st.session_state.get("current_session_id"):
    last_sess = run_query("SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC LIMIT 1", (st.session_state.username,), fetch="one")
    if last_sess:
        st.session_state.current_session_id = last_sess[0]

# 7. Giao diện Sidebar (Cố định model nhatnam201104/gemini-3.6)
with st.sidebar:
    st.title("✨ Gemini Clone Pro")
    st.session_state.selected_persona = st.selectbox("🎭 Vai trò AI (Persona):", list(PERSONAS.keys()))
    
    st.divider()
    st.subheader("⚙️ Chọn Model AI")
    
    AVAILABLE_MODELS = [
        "nhatnam201104/gemini-3.6"
    ]
    
    selected_model = st.selectbox("Chọn Model sử dụng:", AVAILABLE_MODELS)
    st.divider()

    if st.session_state.role == "user":
        if st.button("➕ Chat mới", use_container_width=True, type="primary"):
            new_id = str(uuid.uuid4())[:8]
            run_query("INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)", (new_id, st.session_state.username, str(datetime.datetime.now())))
            st.session_state.current_session_id = new_id
            st.rerun()

        st.write("")
        st.markdown("### 💬 Lịch sử trò chuyện")
        user_sessions = run_query("SELECT id, title, is_pinned FROM sessions WHERE username = %s ORDER BY is_pinned DESC, created_at DESC", (st.session_state.username,), fetch="all")

        if user_sessions:
            for s_id, s_title, s_pin in user_sessions:
                if not s_title:
                    first_msg = run_query("SELECT content FROM messages WHERE session_id = %s AND role = 'user' ORDER BY id ASC LIMIT 1", (s_id,), fetch="one")
                    s_title = first_msg[0][:18] + "..." if first_msg else "Phiên chat trống"

                is_active = (s_id == st.session_state.current_session_id)
                pin_icon = "📌 " if s_pin else ""

                col_btn, col_opt = st.columns([0.75, 0.25])
                with col_btn:
                    if st.button(f"{pin_icon}{s_title}", key=f"btn_{s_id}", use_container_width=True, type="secondary" if not is_active else "primary"):
                        st.session_state.current_session_id = s_id
                        st.rerun()

                with col_opt:
                    with st.popover("⚙️"):
                        pin_label = "📍 Bỏ ghim" if s_pin else "📌 Ghim lên đầu"
                        if st.button(pin_label, key=f"pin_{s_id}"):
                            toggle_pin_session(s_id, s_pin)
                            st.rerun()

                        new_title_input = st.text_input("Tên mới:", value=s_title, key=f"inp_{s_id}")
                        if st.button("Lưu tên", key=f"ren_{s_id}"):
                            if new_title_input.strip():
                                update_session_title(s_id, new_title_input.strip())
                                st.rerun()

                        st.divider()
                        if st.button("🗑️ Xóa chat", key=f"del_{s_id}", type="primary"):
                            delete_session(s_id)
                            if st.session_state.current_session_id == s_id:
                                rem = run_query("SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC LIMIT 1", (st.session_state.username,), fetch="one")
                                st.session_state.current_session_id = rem[0] if rem else None
                            st.rerun()

    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False
        st.query_params.clear()
        st.rerun()

# 8. Màn hình Chat Chính
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")

if not active_sid and st.session_state.role == "user":
    new_id = str(uuid.uuid4())[:8]
    run_query("INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)", (new_id, st.session_state.username, str(datetime.datetime.now())))
    st.session_state.current_session_id = new_id
    st.rerun()

db_messages = []
if active_sid:
    db_messages = run_query("SELECT role, content FROM messages WHERE session_id = %s ORDER BY id ASC", (active_sid,), fetch="all") or []

if db_messages:
    chat_text = "\n\n".join([f"**{m[0].upper()}**: {m[1]}" for m in db_messages])
    st.download_button("📥 Tải lịch sử chat (.md)", data=chat_text, file_name=f"chat_{active_sid}.md", mime="text/markdown")

if not db_messages and st.session_state.role == "user":
    st.write("<br>", unsafe_allow_html=True)
    st.markdown(f"<h1 style='background: -webkit-linear-gradient(45deg, #4285F4, #D96570); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>Xin chào, {st.session_state.username}</h1>", unsafe_allow_html=True)
    st.markdown("<h3 style='color: #666;'>Tôi có thể giúp gì cho bạn hôm nay?</h3>", unsafe_allow_html=True)

# Render lịch sử tin nhắn
gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content, unsafe_allow_html=True)
    g_role = "user" if role == "user" else "assistant"
    clean_content = content.split("<div style='text-align: right")[0].strip()
    gemini_history.append({"role": g_role, "parts": [clean_content]})

# Bộ chọn/dán ảnh
col_up, col_paste = st.columns([0.6, 0.4])
reset_k = st.session_state.img_reset_key

with col_up:
    uploaded_file = st.file_uploader("🖼️ Tải ảnh từ máy:", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed", key=f"uploader_{reset_k}")

with col_paste:
    paste_result = paste_image_button(label="📋 Dán ảnh từ Clipboard (Ctrl+V)", background_color="#4285F4", hover_background_color="#3367D6", text_color="#ffffff", key=f"paste_btn_{reset_k}")

img_data = None
if uploaded_file:
    img_data = Image.open(uploaded_file)
elif paste_result is not None and getattr(paste_result, "image_data", None) is not None:
    img_data = paste_result.image_data

if img_data:
    col_img_view, col_img_del = st.columns([0.7, 0.3])
    with col_img_view:
        st.image(img_data, caption="Ảnh chờ gửi", width=180)
    with col_img_del:
        if st.button("❌ Hủy/Xóa ảnh", key=f"del_img_{reset_k}"):
            st.session_state.img_reset_key += 1
            st.rerun()

# Badge hiển thị model đang chọn
st.markdown(f"""
<style>
.gemini-badge {{
    position: fixed;
    bottom: 92px;
    right: max(20px, calc(50% - 430px));
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 12px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
    color: #444746;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    z-index: 9999;
    display: flex;
    align-items: center;
    gap: 6px;
    pointer-events: none;
}}
@media (max-width: 768px) {{
    .gemini-badge {{ right: 15px; bottom: 82px; }}
}}
</style>
<div class="gemini-badge">✨ {selected_model.split('/')[-1]} ⌄</div>
""", unsafe_allow_html=True)

# Luồng xử lý chat
if prompt := st.chat_input("Nhập câu hỏi của bạn tại đây..."):
    with st.chat_message("user"):
        if img_data:
            st.image(img_data, width=200)
        st.markdown(prompt)

    user_msg_store = prompt if not img_data else f"[Đã gửi 1 hình ảnh] {prompt}"
    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (active_sid, user_msg_store))

    with st.chat_message("assistant"):
        with st.spinner("Đang suy luận..."):
            reply, used_model = query_ai_gateway(prompt, gemini_history, chosen_model=selected_model, image_data=img_data)
            st.markdown(reply)

    reply_to_db = reply if used_model == "Error" else reply + f"\n\n<div style='text-align: right; font-size: 11px; color: #888; font-style: italic;'>(Model: {used_model})</div>"
    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'assistant', %s)", (active_sid, reply_to_db))

    if img_data:
        st.session_state.img_reset_key += 1

    st.rerun()
