import random
import uuid
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from PIL import Image
import psycopg2
from psycopg2 import pool
import streamlit as st
from streamlit_paste_button import paste_image_button

# -----------------------------------------------------------------------------
# 1. CẤU HÌNH TRANG & CSS
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Gemini Pro Neon", page_icon="✨", layout="wide")

st.markdown(
    """
<style>
    ::-webkit-scrollbar { width: 8px; }
    ::-webkit-scrollbar-track { background: #f1f1f1; }
    ::-webkit-scrollbar-thumb { background: #bbb; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #888; }

    .block-container { padding-top: 1.5rem; padding-bottom: 5rem; max-width: 950px; }
    .stChatInputContainer { padding-bottom: 10px; }

    .scroll-btn-container {
        position: fixed;
        bottom: 80px;
        right: 20px;
        z-index: 99999;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .scroll-btn {
        background-color: #4285F4;
        color: white;
        border: none;
        border-radius: 50%;
        width: 38px;
        height: 38px;
        font-size: 18px;
        cursor: pointer;
        box-shadow: 0 4px 10px rgba(0,0,0,0.15);
        display: flex;
        align-items: center;
        justify-content: center;
        opacity: 0.85;
        transition: 0.2s;
    }
    .scroll-btn:hover {
        opacity: 1;
        transform: scale(1.1);
        background-color: #3367D6;
    }
</style>

<div class="scroll-btn-container">
    <button class="scroll-btn" onclick="window.scrollTo({top: 0, behavior: 'smooth'});" title="Lên đầu">⬆️</button>
    <button class="scroll-btn" onclick="window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});" title="Xuống cuối">⬇️</button>
</div>
""",
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# 2. KIỂM TRA SECRETS
# -----------------------------------------------------------------------------
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
    NEON_DB_URL = st.secrets["NEON_DATABASE_URL"]
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets trong Streamlit Dashboard!")
    st.stop()

PERSONAS = {
    "✨ Trợ lý Mặc định": "Bạn là một trợ lý AI thông minh, thân thiện và hữu ích.",
    "💻 Lập trình viên Senior": "Bạn là một chuyên gia lập trình Senior. Trả lời tập trung vào mã nguồn tối ưu, ngắn gọn, giải thích rõ ràng.",
    "✍️ Chuyên gia Content": "Bạn là chuyên gia sáng tạo nội dung và Marketing. Trả lời với giọng văn lôi cuốn, sáng tạo.",
    "🎓 Giáo sư Giảng dạy": "Bạn là một giáo sư. Hãy giải thích các khái niệm phức tạp một cách vô cùng đơn giản, trực quan.",
}

# Các Model chuẩn hoá mới nhất
PREFERRED_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

# -----------------------------------------------------------------------------
# 3. KẾT NỐI DATABASE (OPTIMIZED CONNECTION POOLING)
# -----------------------------------------------------------------------------
@st.cache_resource
def init_connection_pool():
    """Tạo Pool kết nối tái sử dụng, giúp query cực nhanh."""
    return pool.SimpleConnectionPool(1, 10, dsn=NEON_DB_URL)

def run_query(query, params=(), fetch=None):
    """Hàm chạy SQL tối ưu lấy connection từ Pool."""
    db_pool = init_connection_pool()
    conn = db_pool.getconn()
    res = None
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch == "one":
                res = cur.fetchone()
            elif fetch == "all":
                res = cur.fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        db_pool.putconn(conn)
    return res

def init_db():
    run_query("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            username TEXT,
            is_locked INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            title TEXT,
            is_pinned INT DEFAULT 0
        )
    """)
    run_query("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

init_db()

# Các hàm DB trợ năng
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

# -----------------------------------------------------------------------------
# 4. STATE MANAGEMENT
# -----------------------------------------------------------------------------
if "img_reset_key" not in st.session_state:
    st.session_state.img_reset_key = 0

if "auth_status" not in st.session_state:
    if "user" in st.query_params and "role" in st.query_params:
        st.session_state.auth_status = True
        st.session_state.username = st.query_params["user"]
        st.session_state.role = st.query_params["role"]
    else:
        st.session_state.auth_status = False

if "role" not in st.session_state:
    st.session_state.role = None
if "username" not in st.session_state:
    st.session_state.username = None
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None
if "current_key_index" not in st.session_state:
    st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)
if "key_status" not in st.session_state:
    st.session_state.key_status = {i: "🟢 Ổn định" for i in range(len(API_KEYS))}
if "selected_persona" not in st.session_state:
    st.session_state.selected_persona = list(PERSONAS.keys())[0]

def rotate_key(reason="Lỗi"):
    idx = st.session_state.current_key_index
    st.session_state.key_status[idx] = f"🔴 {reason}"
    available = [i for i in range(len(API_KEYS)) if "🔴" not in st.session_state.key_status[i]]
    if available:
        st.session_state.current_key_index = random.choice(available)
        st.session_state.key_status[st.session_state.current_key_index] = "🟡 Đang dùng"
        return True
    st.session_state.current_key_index = (idx + 1) % len(API_KEYS)
    return False

# -----------------------------------------------------------------------------
# 5. HÀM GỌI GEMINI API STREAMING (MƯỢT VÀ REALTIME)
# -----------------------------------------------------------------------------
def query_gemini_stream(prompt_text, history_list, image_data=None):
    attempts = 0
    max_attempts = len(API_KEYS)

    while attempts < max_attempts:
        current_key = API_KEYS[st.session_state.current_key_index]
        genai.configure(api_key=current_key)
        system_instruction = PERSONAS.get(st.session_state.selected_persona, "")

        for model_name in PREFERRED_MODELS:
            try:
                model = genai.GenerativeModel(model_name, system_instruction=system_instruction)

                if image_data:
                    # Xử lý có ảnh
                    contents = [image_data, prompt_text]
                    response = model.generate_content(contents, stream=True)
                else:
                    # Chat văn bản có history
                    chat = model.start_chat(history=history_list)
                    response = chat.send_message(prompt_text, stream=True)

                st.session_state.key_status[st.session_state.current_key_index] = f"🟢 Ổn định ({model_name})"

                for chunk in response:
                    if chunk.text:
                        yield chunk.text
                return

            except ResourceExhausted:
                break
            except Exception:
                continue

        rotate_key("Hết Quota / Lỗi")
        attempts += 1

    yield "⚠️ **Không thể kết nối Gemini API.** Vui lòng thử lại sau ít phút!"

# -----------------------------------------------------------------------------
# 6. MÀN HÌNH ĐĂNG NHẬP
# -----------------------------------------------------------------------------
if not st.session_state.auth_status:
    _, col_box, _ = st.columns([1, 1.5, 1])
    with col_box:
        st.write("<br><br>", unsafe_allow_html=True)
        st.markdown("<h2 style='text-align: center;'>✨ Đăng nhập Hệ thống</h2>", unsafe_allow_html=True)
        mode = st.radio("Tư cách:", ["User", "Admin"], horizontal=True)
        input_name = st.text_input("Tên hiển thị:", value="User_1") if mode == "User" else "Admin"
        input_pass = st.text_input("Mật khẩu:", type="password")

        if st.button("Tiếp tục 🚀", type="primary", use_container_width=True):
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
                        run_query("INSERT INTO sessions (id, username) VALUES (%s, %s)", (new_id, st.session_state.username))
                        st.session_state.current_session_id = new_id
                st.rerun()
            else:
                st.error("❌ Mật khẩu không chính xác.")
    st.stop()

# Khôi phục session ID
if st.session_state.role == "user" and not st.session_state.current_session_id:
    last_sess = run_query("SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC LIMIT 1", (st.session_state.username,), fetch="one")
    if last_sess:
        st.session_state.current_session_id = last_sess[0]

# -----------------------------------------------------------------------------
# 7. GIAO DIỆN SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("✨ Gemini Pro")
    st.session_state.selected_persona = st.selectbox("🎭 Vai trò AI:", list(PERSONAS.keys()))
    st.divider()

    if st.session_state.role == "user":
        if st.button("➕ Đoạn chat mới", use_container_width=True, type="primary"):
            new_id = str(uuid.uuid4())[:8]
            run_query("INSERT INTO sessions (id, username) VALUES (%s, %s)", (new_id, st.session_state.username))
            st.session_state.current_session_id = new_id
            st.rerun()

        st.markdown("### 💬 Lịch sử trò chuyện")
        user_sessions = run_query(
            "SELECT id, title, is_pinned FROM sessions WHERE username = %s ORDER BY is_pinned DESC, created_at DESC",
            (st.session_state.username,), fetch="all"
        )

        if user_sessions:
            for s_id, s_title, s_pin in user_sessions:
                display_title = s_title if s_title else "Phiên chat mới"
                is_active = (s_id == st.session_state.current_session_id)
                pin_icon = "📌 " if s_pin else ""

                col_btn, col_opt = st.columns([0.8, 0.2])
                with col_btn:
                    if st.button(f"{pin_icon}{display_title}", key=f"btn_{s_id}", use_container_width=True, type="secondary" if not is_active else "primary"):
                        st.session_state.current_session_id = s_id
                        st.rerun()

                with col_opt:
                    with st.popover("⚙️"):
                        if st.button("📍 Bỏ ghim" if s_pin else "📌 Ghim", key=f"pin_{s_id}"):
                            toggle_pin_session(s_id, s_pin)
                            st.rerun()

                        new_t = st.text_input("Sửa tên:", value=display_title, key=f"inp_{s_id}")
                        if st.button("Lưu", key=f"ren_{s_id}"):
                            if new_t.strip():
                                update_session_title(s_id, new_t.strip())
                                st.rerun()

                        if st.button("🗑️ Xóa", key=f"del_{s_id}", type="primary"):
                            delete_session(s_id)
                            if st.session_state.current_session_id == s_id:
                                rem = run_query("SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC LIMIT 1", (st.session_state.username,), fetch="one")
                                st.session_state.current_session_id = rem[0] if rem else None
                            st.rerun()

    elif st.session_state.role == "admin":
        st.subheader("🛠 Quản trị Admin")
        all_s = run_query("SELECT id, username FROM sessions ORDER BY created_at DESC", fetch="all")
        options = {f"{s[1]} - {s[0]}": s[0] for s in all_s} if all_s else {}
        sel = st.selectbox("👁️ Giám sát chat:", list(options.keys())) if options else None
        if sel:
            st.session_state.admin_selected_session = options[sel]

        st.divider()
        all_users = [u[0] for u in (run_query("SELECT DISTINCT username FROM sessions", fetch="all") or [])]
        if all_users:
            u_del = st.selectbox("Xóa User:", all_users)
            if st.button(f"🗑️ Xóa sạch data {u_del}", type="primary"):
                delete_user_data(u_del)
                st.success(f"Đã xóa {u_del}")
                st.rerun()

    st.divider()
    if st.session_state.role == "user":
        with st.expander(f"👤 Account: {st.session_state.username}"):
            new_un = st.text_input("Đổi tên:", value=st.session_state.username)
            if st.button("Cập nhật"):
                if new_un.strip() and new_un != st.session_state.username:
                    update_username(st.session_state.username, new_un.strip())
                    st.session_state.username = new_un.strip()
                    st.query_params["user"] = new_un.strip()
                    st.rerun()

    with st.expander("🔑 API Key Status"):
        for idx, status in st.session_state.key_status.items():
            mark = "👈" if idx == st.session_state.current_key_index else ""
            st.caption(f"Key #{idx+1}: {status} {mark}")

    if st.button("🚪 Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False
        st.query_params.clear()
        st.rerun()

# -----------------------------------------------------------------------------
# 8. MÀN HÌNH CHAT CHÍNH
# -----------------------------------------------------------------------------
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")

if not active_sid and st.session_state.role == "user":
    new_id = str(uuid.uuid4())[:8]
    run_query("INSERT INTO sessions (id, username) VALUES (%s, %s)", (new_id, st.session_state.username))
    st.session_state.current_session_id = new_id
    st.rerun()

db_messages = run_query("SELECT role, content FROM messages WHERE session_id = %s ORDER BY id ASC", (active_sid,), fetch="all") or []

if db_messages:
    chat_text = "\n\n".join([f"**{m[0].upper()}**: {m[1]}" for m in db_messages])
    st.download_button("📥 Tải lịch sử (.md)", data=chat_text, file_name=f"chat_{active_sid}.md", mime="text/markdown")

if not db_messages and st.session_state.role == "user":
    st.write("<br>", unsafe_allow_html=True)
    st.markdown(f"<h1 style='background: -webkit-linear-gradient(45deg, #4285F4, #D96570); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>Xin chào, {st.session_state.username}</h1>", unsafe_allow_html=True)
    st.markdown("<h3 style='color: #666;'>Tôi có thể giúp gì cho bạn hôm nay?</h3>", unsafe_allow_html=True)

    st.write("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    quick_prompt = None
    if c1.button("💡 Kế hoạch du lịch 3N2Đ", use_container_width=True):
        quick_prompt = "Hãy lập kế hoạch du lịch Đà Nẵng 3 ngày 2 đêm tối ưu chi phí."
    if c2.button("💻 Viết code Python Excel", use_container_width=True):
        quick_prompt = "Hướng dẫn viết code Python dùng pandas để xử lý file Excel."
    if c3.button("✍️ Viết Email nghỉ phép", use_container_width=True):
        quick_prompt = "Soạn mẫu email xin nghỉ phép 2 ngày lịch sự."

    if quick_prompt:
        run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (active_sid, quick_prompt))
        update_session_title(active_sid, quick_prompt[:20] + "...")
        st.rerun()

# Hiển thị tin nhắn cũ
gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content)
    gemini_history.append({"role": "user" if role == "user" else "model", "parts": [content]})

# Upload / Dán Ảnh
col_up, col_paste = st.columns([0.6, 0.4])
reset_k = st.session_state.img_reset_key

with col_up:
    uploaded_file = st.file_uploader("🖼️ Chọn ảnh:", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed", key=f"uploader_{reset_k}")
with col_paste:
    paste_result = paste_image_button(label="📋 Dán ảnh (Ctrl+V)", background_color="#4285F4", hover_background_color="#3367D6", text_color="#ffffff", key=f"paste_btn_{reset_k}")

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
        if st.button("❌ Hủy ảnh", key=f"del_img_{reset_k}"):
            st.session_state.img_reset_key += 1
            st.rerun()

# -----------------------------------------------------------------------------
# 9. XỬ LÝ NHẬP CÂU HỎI VÀ STREAMING PHẢN HỒI
# -----------------------------------------------------------------------------
if prompt := st.chat_input("Nhập tin nhắn..."):
    with st.chat_message("user"):
        if img_data:
            st.image(img_data, width=200)
        st.markdown(prompt)

    # Nếu là tin nhắn đầu tiên, tự động cập nhật tên Session
    if not db_messages:
        update_session_title(active_sid, prompt[:22] + "...")

    user_msg_store = prompt if not img_data else f"[Hình ảnh] {prompt}"
    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'user', %s)", (active_sid, user_msg_store))

    with st.chat_message("assistant"):
        # Gõ chữ Realtime cực mượt
        full_response = st.write_stream(query_gemini_stream(prompt, gemini_history, image_data=img_data))

    run_query("INSERT INTO messages (session_id, role, content) VALUES (%s, 'assistant', %s)", (active_sid, full_response))

    if img_data:
        st.session_state.img_reset_key += 1

    st.rerun()
