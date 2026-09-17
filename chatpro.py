import datetime
import random
import uuid
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from PIL import Image
import psycopg2  # Thay sqlite3 bằng psycopg2
import streamlit as st
from streamlit_paste_button import paste_image_button

# 1. Cấu hình trang
st.set_page_config(page_title="Gemini Clone Pro", page_icon="✨", layout="wide")

# CSS Tùy chỉnh
st.markdown(
    """
<style>
    ::-webkit-scrollbar { width: 10px; }
    ::-webkit-scrollbar-track { background: #f1f1f1; }
    ::-webkit-scrollbar-thumb { background: #888; border-radius: 5px; }
    ::-webkit-scrollbar-thumb:hover { background: #555; }

    .block-container { padding-top: 1.5rem; padding-bottom: 5rem; max-width: 950px; }
    .stChatInputContainer { padding-bottom: 10px; }

    .scroll-btn-container {
        position: fixed;
        bottom: 80px;
        right: 25px;
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
        width: 42px;
        height: 42px;
        font-size: 20px;
        cursor: pointer;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        display: flex;
        align-items: center;
        justify-content: center;
        opacity: 0.85;
        transition: 0.3s;
    }
    .scroll-btn:hover {
        opacity: 1;
        transform: scale(1.1);
        background-color: #3367D6;
    }
</style>

<div class="scroll-btn-container">
    <button class="scroll-btn" onclick="window.scrollTo({top: 0, behavior: 'smooth'});" title="Lên đầu trang">⬆️</button>
    <button class="scroll-btn" onclick="window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});" title="Xuống cuối trang">⬇️</button>
</div>
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
    st.error(
        "⚠️ Thiếu cấu hình Secrets (Cần GEMINI_API_KEYS, ADMIN_PASSWORD,"
        " USER_PASSWORD, NEON_DATABASE_URL)."
    )
    st.stop()

# 🎭 Danh sách Vai trò AI
PERSONAS = {
    "✨ Trợ lý Mặc định": (
        "Bạn là một trợ lý AI thông minh, thân thiện và hữu ích."
    ),
    "💻 Lập trình viên Senior": (
        "Bạn là một chuyên gia lập trình Senior. Trả lời tập trung vào mã"
        " nguồn tối ưu, ngắn gọn, có giải thích rõ ràng."
    ),
    "✍️ Chuyên gia Content": (
        "Bạn là một chuyên gia sáng tạo nội dung và Marketing. Trả lời với giọng"
        " văn lôi cuốn, sáng tạo."
    ),
    "🎓 Giáo sư Giảng dạy": (
        "Bạn là một giáo sư đại học. Hãy giải thích các khái niệm phức tạp một"
        " cách vô cùng đơn giản, dễ hiểu."
    ),
}

PREFERRED_MODELS = [
    "gemini-3.6-pro",
    "gemini-1.5-pro",
    "gemini-3.6-flash",
    "gemini-1.5-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]


# 3. Quản lý Kết nối Database Neon Postgres
def run_query(query, params=(), fetch=None):
    """Hàm wrapper hỗ trợ thực thi SQL an toàn với Neon PostgreSQL."""
    conn = psycopg2.connect(NEON_DB_URL)
    with conn.cursor() as cur:
        cur.execute(query, params)
        res = None
        if fetch == "one":
            res = cur.fetchone()
        elif fetch == "all":
            res = cur.fetchall()
    conn.commit()
    conn.close()
    return res


def init_db():
    run_query("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            username TEXT,
            is_locked INT DEFAULT 0,
            created_at TEXT,
            title TEXT,
            is_pinned INT DEFAULT 0
        )
    """)
    run_query("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT
        )
    """)


init_db()


# Các hàm thao tác Database
def delete_session(session_id):
    run_query("DELETE FROM messages WHERE session_id = %s", (session_id,))
    run_query("DELETE FROM sessions WHERE id = %s", (session_id,))


def delete_user_data(username_to_del):
    run_query(
        "DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions"
        " WHERE username = %s)",
        (username_to_del,),
    )
    run_query("DELETE FROM sessions WHERE username = %s", (username_to_del,))


def update_session_title(session_id, new_title):
    run_query(
        "UPDATE sessions SET title = %s WHERE id = %s", (new_title, session_id)
    )


def toggle_pin_session(session_id, current_pin):
    new_pin = 0 if current_pin == 1 else 1
    run_query(
        "UPDATE sessions SET is_pinned = %s WHERE id = %s", (new_pin, session_id)
    )


def update_username(old_name, new_name):
    run_query(
        "UPDATE sessions SET username = %s WHERE username = %s",
        (new_name, old_name),
    )


# 4. State Management & Query Params
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
    st.session_state.key_status = {
        i: "🟢 Ổn định" for i in range(len(API_KEYS))
    }
if "selected_persona" not in st.session_state:
    st.session_state.selected_persona = list(PERSONAS.keys())[0]


def rotate_key(reason="Lỗi"):
    idx = st.session_state.current_key_index
    st.session_state.key_status[idx] = f"🔴 {reason}"
    available = [
        i
        for i in range(len(API_KEYS))
        if "🔴" not in st.session_state.key_status[i]
    ]
    if available:
        st.session_state.current_key_index = random.choice(available)
        st.session_state.key_status[st.session_state.current_key_index] = (
            "🟡 Đang sử dụng"
        )
        return True
    else:
        st.session_state.current_key_index = (idx + 1) % len(API_KEYS)
        return False


# 5. Hàm gọi API Gemini
def query_gemini(prompt_text, history_list, image_data=None):
    attempts = 0
    max_attempts = len(API_KEYS)
    last_error = ""

    while attempts < max_attempts:
        current_key = API_KEYS[st.session_state.current_key_index]
        genai.configure(api_key=current_key)
        system_instruction = PERSONAS.get(st.session_state.selected_persona, "")

        try:
            available_models = [
                m.name.replace("models/", "")
                for m in genai.list_models()
                if "generateContent" in m.supported_generation_methods
            ]
        except Exception:
            available_models = []

        models_to_try = []
        for target in PREFERRED_MODELS:
            matched = [am for am in available_models if target in am]
            if matched:
                models_to_try.extend(matched)
            else:
                models_to_try.append(target)

        for am in available_models:
            if am not in models_to_try:
                models_to_try.append(am)

        for model_name in models_to_try:
            try:
                model = genai.GenerativeModel(
                    model_name, system_instruction=system_instruction
                )
                contents = []
                if image_data:
                    contents.append(image_data)
                contents.append(prompt_text)

                if history_list and not image_data:
                    chat = model.start_chat(history=history_list)
                    res = chat.send_message(prompt_text)
                else:
                    res = model.generate_content(contents)

                st.session_state.key_status[
                    st.session_state.current_key_index
                ] = f"🟢 Ổn định ({model_name})"
                return res.text

            except ResourceExhausted:
                last_error = "Key hết Quota (Lỗi 429)"
                break
            except Exception as e:
                last_error = f"{model_name}: {str(e)}"
                continue

        rotate_key("Hết Quota / Lỗi")
        attempts += 1

    return f"⚠️ **Không thể kết nối API.** Lỗi gần nhất: `{last_error}`"


# 6. Màn hình Đăng nhập
if not st.session_state.auth_status:
    col_space1, col_box, col_space2 = st.columns([1, 2, 1])
    with col_box:
        st.write("<br><br>", unsafe_allow_html=True)
        st.markdown(
            "<h2 style='text-align: center;'>✨ Đăng nhập Hệ thống AI</h2>",
            unsafe_allow_html=True,
        )
        mode = st.radio(
            "Tư cách đăng nhập:", ["User", "Admin"], horizontal=True
        )
        input_name = (
            st.text_input("Tên hiển thị (Username):", value="User_1")
            if mode == "User"
            else "Admin"
        )
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
                    last_sess = run_query(
                        "SELECT id FROM sessions WHERE username = %s ORDER BY"
                        " created_at DESC LIMIT 1",
                        (st.session_state.username,),
                        fetch="one",
                    )
                    if last_sess:
                        st.session_state.current_session_id = last_sess[0]
                    else:
                        new_id = str(uuid.uuid4())[:8]
                        run_query(
                            "INSERT INTO sessions (id, username, created_at)"
                            " VALUES (%s, %s, %s)",
                            (
                                new_id,
                                st.session_state.username,
                                str(datetime.datetime.now()),
                            ),
                        )
                        st.session_state.current_session_id = new_id
                st.rerun()
            else:
                st.error("Mật khẩu không chính xác.")
    st.stop()

# Khôi phục session ID nếu vừa được auto-login
if st.session_state.role == "user" and not st.session_state.current_session_id:
    last_sess = run_query(
        "SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC"
        " LIMIT 1",
        (st.session_state.username,),
        fetch="one",
    )
    if last_sess:
        st.session_state.current_session_id = last_sess[0]

# 7. Giao diện Sidebar
with st.sidebar:
    st.title("✨ Gemini Clone Pro")
    st.session_state.selected_persona = st.selectbox(
        "🎭 Vai trò AI (Persona):", list(PERSONAS.keys())
    )
    st.divider()

    if st.session_state.role == "user":
        if st.button(
            "➕ Chat mới", use_container_width=True, type="primary"
        ):
            new_id = str(uuid.uuid4())[:8]
            run_query(
                "INSERT INTO sessions (id, username, created_at) VALUES (%s,"
                " %s, %s)",
                (
                    new_id,
                    st.session_state.username,
                    str(datetime.datetime.now()),
                ),
            )
            st.session_state.current_session_id = new_id
            st.rerun()

        st.write("")
        st.markdown("### 💬 Lịch sử trò chuyện")

        user_sessions = run_query(
            "SELECT id, title, is_pinned FROM sessions WHERE username = %s"
            " ORDER BY is_pinned DESC, created_at DESC",
            (st.session_state.username,),
            fetch="all",
        )

        if user_sessions:
            for sess in user_sessions:
                s_id, s_title, s_pin = sess

                if not s_title:
                    first_msg = run_query(
                        "SELECT content FROM messages WHERE session_id = %s AND"
                        " role = 'user' ORDER BY id ASC LIMIT 1",
                        (s_id,),
                        fetch="one",
                    )
                    s_title = (
                        first_msg[0][:18] + "..."
                        if first_msg
                        else "Phiên chat trống"
                    )

                is_active = s_id == st.session_state.current_session_id
                pin_icon = "📌 " if s_pin else ""

                col_btn, col_opt = st.columns([0.75, 0.25])
                with col_btn:
                    if st.button(
                        f"{pin_icon}{s_title}",
                        key=f"btn_{s_id}",
                        use_container_width=True,
                        type="secondary" if not is_active else "primary",
                    ):
                        st.session_state.current_session_id = s_id
                        st.rerun()

                with col_opt:
                    with st.popover("⚙️"):
                        pin_label = (
                            "📍 Bỏ ghim" if s_pin else "📌 Ghim lên đầu"
                        )
                        if st.button(pin_label, key=f"pin_{s_id}"):
                            toggle_pin_session(s_id, s_pin)
                            st.rerun()

                        new_title_input = st.text_input(
                            "Tên mới:", value=s_title, key=f"inp_{s_id}"
                        )
                        if st.button("Lưu tên", key=f"ren_{s_id}"):
                            if new_title_input.strip():
                                update_session_title(
                                    s_id, new_title_input.strip()
                                )
                                st.rerun()

                        st.divider()
                        if st.button(
                            "🗑️ Xóa chat", key=f"del_{s_id}", type="primary"
                        ):
                            delete_session(s_id)
                            if st.session_state.current_session_id == s_id:
                                rem = run_query(
                                    "SELECT id FROM sessions WHERE username ="
                                    " %s ORDER BY created_at DESC LIMIT 1",
                                    (st.session_state.username,),
                                    fetch="one",
                                )
                                st.session_state.current_session_id = (
                                    rem[0] if rem else None
                                )
                            st.rerun()

    elif st.session_state.role == "admin":
        st.subheader("🛠 Quản trị viên")

        all_s = run_query(
            "SELECT id, username FROM sessions ORDER BY created_at DESC",
            fetch="all",
        )
        options = {f"{s[1]} - {s[0]}": s[0] for s in all_s} if all_s else {}
        sel = (
            st.selectbox("👁️ Theo dõi chat:", list(options.keys()))
            if options
            else None
        )
        if sel:
            st.session_state.admin_selected_session = options[sel]

        st.divider()

        st.markdown("### 👥 Quản lý User")
        all_users_res = run_query(
            "SELECT DISTINCT username FROM sessions", fetch="all"
        )
        all_users = [u[0] for u in all_users_res] if all_users_res else []

        if all_users:
            selected_user_to_del = st.selectbox(
                "Chọn User cần xóa:", all_users
            )
            with st.popover(f"🗑️ Xóa User: {selected_user_to_del}"):
                st.warning(
                    "⚠️ Thao tác này sẽ XÓA SẠCH toàn bộ dữ liệu lịch sử chat"
                    f" của user '{selected_user_to_del}'!"
                )
                if st.button(
                    "Xác nhận Xóa User",
                    type="primary",
                    key="confirm_del_user",
                ):
                    delete_user_data(selected_user_to_del)
                    st.success(
                        f"Đã xóa thành công User {selected_user_to_del}!"
                    )
                    st.session_state.admin_selected_session = None
                    st.rerun()
        else:
            st.caption("Chưa có User nào trong hệ thống.")

    st.divider()

    if st.session_state.role == "user":
        with st.expander(
            f"👤 Tài khoản: {st.session_state.username}", expanded=False
        ):
            new_username = st.text_input(
                "Đổi tên hiển thị:", value=st.session_state.username
            )
            if st.button("Cập nhật tên"):
                if (
                    new_username.strip()
                    and new_username != st.session_state.username
                ):
                    update_username(
                        st.session_state.username, new_username.strip()
                    )
                    st.session_state.username = new_username.strip()
                    st.query_params["user"] = st.session_state.username
                    st.success("Đã đổi tên!")
                    st.rerun()

    with st.expander("🔑 Trạng thái API Keys", expanded=False):
        for idx, status in st.session_state.key_status.items():
            active_mark = (
                "👈 (Đang dùng)"
                if idx == st.session_state.current_key_index
                else ""
            )
            st.caption(f"**Key #{idx+1}:** {status} {active_mark}")

    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False
        st.query_params.clear()
        st.rerun()

# 8. Màn hình Chat Chính
active_sid = (
    st.session_state.current_session_id
    if st.session_state.role == "user"
    else st.session_state.get("admin_selected_session")
)

if not active_sid and st.session_state.role == "user":
    new_id = str(uuid.uuid4())[:8]
    run_query(
        "INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)",
        (new_id, st.session_state.username, str(datetime.datetime.now())),
    )
    st.session_state.current_session_id = new_id
    st.rerun()

db_messages = []
if active_sid:
    db_messages = (
        run_query(
            "SELECT role, content FROM messages WHERE session_id = %s ORDER BY"
            " id ASC",
            (active_sid,),
            fetch="all",
        )
        or []
    )

if db_messages:
    chat_text = "\n\n".join(
        [f"**{m[0].upper()}**: {m[1]}" for m in db_messages]
    )
    st.download_button(
        "📥 Tải lịch sử chat (.md)",
        data=chat_text,
        file_name=f"chat_{active_sid}.md",
        mime="text/markdown",
    )

if not db_messages and st.session_state.role == "user":
    st.write("<br>", unsafe_allow_html=True)
    st.markdown(
        "<h1 style='background: -webkit-linear-gradient(45deg, #4285F4,"
        " #D96570); -webkit-background-clip: text; -webkit-text-fill-color:"
        f" transparent;'>Xin chào, {st.session_state.username}</h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<h3 style='color: #666;'>Tôi có thể giúp gì cho bạn hôm nay?</h3>",
        unsafe_allow_html=True,
    )

    st.write("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    quick_prompt = None
    if c1.button("💡 Kế hoạch du lịch 3 ngày 2 đêm", use_container_width=True):
        quick_prompt = (
            "Hãy lập cho tôi kế hoạch du lịch Đà Nẵng 3 ngày 2 đêm tối ưu"
            " chi phí."
        )
    if c2.button(
        "💻 Viết code Python đọc file Excel", use_container_width=True
    ):
        quick_prompt = (
            "Hướng dẫn viết code Python dùng pandas để đọc và xử lý file Excel."
        )
    if c3.button(
        "✍️ Viết Email xin nghỉ phép lịch sự", use_container_width=True
    ):
        quick_prompt = (
            "Soạn cho tôi một mẫu email xin nghỉ phép 2 ngày vì lý do cá nhân."
        )

    if quick_prompt:
        run_query(
            "INSERT INTO messages (session_id, role, content) VALUES (%s,"
            " 'user', %s)",
            (active_sid, quick_prompt),
        )
        st.rerun()

# Render lịch sử tin nhắn
gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content)
    g_role = "user" if role == "user" else "model"
    gemini_history.append({"role": g_role, "parts": [content]})

# 🖼️ Bộ chọn/dán ảnh
col_up, col_paste = st.columns([0.6, 0.4])
reset_k = st.session_state.img_reset_key

with col_up:
    uploaded_file = st.file_uploader(
        "🖼️ Tải ảnh từ máy:",
        type=["jpg", "jpeg", "png", "webp"],
        label_visibility="collapsed",
        key=f"uploader_{reset_k}",
    )

with col_paste:
    paste_result = paste_image_button(
        label="📋 Dán ảnh từ Clipboard (Ctrl+V)",
        background_color="#4285F4",
        hover_background_color="#3367D6",
        text_color="#ffffff",
        key=f"paste_btn_{reset_k}",
    )

img_data = None
if uploaded_file:
    img_data = Image.open(uploaded_file)
elif (
    paste_result is not None
    and getattr(paste_result, "image_data", None) is not None
):
    img_data = paste_result.image_data

if img_data:
    col_img_view, col_img_del = st.columns([0.7, 0.3])
    with col_img_view:
        st.image(img_data, caption="Ảnh chờ gửi", width=180)
    with col_img_del:
        if st.button("❌ Hủy/Xóa ảnh", key=f"del_img_{reset_k}"):
            st.session_state.img_reset_key += 1
            st.rerun()

# Ô nhập nội dung
if prompt := st.chat_input("Nhập câu hỏi của bạn tại đây..."):
    with st.chat_message("user"):
        if img_data:
            st.image(img_data, width=200)
        st.markdown(prompt)

    user_msg_store = prompt if not img_data else f"[Đã gửi 1 hình ảnh] {prompt}"
    run_query(
        "INSERT INTO messages (session_id, role, content) VALUES (%s, 'user',"
        " %s)",
        (active_sid, user_msg_store),
    )

    with st.chat_message("assistant"):
        with st.spinner("Đang suy luận..."):
            reply = query_gemini(prompt, gemini_history, image_data=img_data)
            st.markdown(reply)

    run_query(
        "INSERT INTO messages (session_id, role, content) VALUES (%s,"
        " 'assistant', %s)",
        (active_sid, reply),
    )

    if img_data:
        st.session_state.img_reset_key += 1

    st.rerun()
