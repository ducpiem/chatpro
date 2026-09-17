import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from supabase import create_client, Client
import random
import datetime
import uuid
from PIL import Image

# 1. Cấu hình trang
st.set_page_config(page_title="Gemini Cloud Pro", page_icon="✨", layout="wide")

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 950px; }
    .stChatInputContainer { padding-bottom: 10px; }
</style>
""", unsafe_allow_html=True)

# 2. Kiểm tra Secrets
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    ADMIN_PASSWORD = st.secrets["ADMIN_PASSWORD"]
    USER_PASSWORD = st.secrets.get("USER_PASSWORD", "123456")
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except KeyError:
    st.error("⚠️ Thiếu cấu hình Secrets (cần GEMINI_API_KEYS, ADMIN_PASSWORD, USER_PASSWORD, SUPABASE_URL, SUPABASE_KEY).")
    st.stop()

# 3. Khởi tạo Kết nối Supabase (Cloud Database)
@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# Danh sách Nhân dạng AI (System Persona)
PERSONAS = {
    "✨ Trợ lý Mặc định": "Bạn là một trợ lý AI thông minh, thân thiện và hữu ích.",
    "💻 Lập trình viên Senior": "Bạn là một chuyên gia lập trình Senior. Trả lời tập trung vào mã nguồn tối ưu, ngắn gọn, có giải thích rõ ràng.",
    "✍️ Chuyên gia Content": "Bạn là một chuyên gia sáng tạo nội dung và Marketing. Trả lời với giọng văn lôi cuốn, sáng tạo.",
    "🎓 Giáo sư Giảng dạy": "Bạn là một giáo sư đại học. Hãy giải thích các khái niệm phức tạp một cách vô cùng đơn giản, dễ hiểu."
}

# 4. State Management
if "auth_status" not in st.session_state: st.session_state.auth_status = False
if "role" not in st.session_state: st.session_state.role = None
if "username" not in st.session_state: st.session_state.username = None
if "current_session_id" not in st.session_state: st.session_state.current_session_id = None
if "current_key_index" not in st.session_state: st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)
if "key_status" not in st.session_state: 
    st.session_state.key_status = {i: "🟢 Ổn định" for i in range(len(API_KEYS))}
if "selected_persona" not in st.session_state: st.session_state.selected_persona = list(PERSONAS.keys())[0]

def rotate_key(reason="Lỗi"):
    idx = st.session_state.current_key_index
    st.session_state.key_status[idx] = f"🔴 {reason}"
    available = [i for i in range(len(API_KEYS)) if "🔴" not in st.session_state.key_status[i]]
    if available:
        st.session_state.current_key_index = random.choice(available)
        st.session_state.key_status[st.session_state.current_key_index] = "🟡 Đang sử dụng"
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

        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            best_model = None
            for target in ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash-exp', 'gemini-1.0-pro']:
                if any(target in m for m in available_models):
                    best_model = target
                    break

            if not best_model and available_models:
                best_model = available_models[0].replace('models/', '')

            if not best_model:
                return "⚠️ API Key của bạn không có quyền truy cập vào bất kỳ model Chat nào."

            system_instruction = PERSONAS.get(st.session_state.selected_persona, "")
            model = genai.GenerativeModel(best_model, system_instruction=system_instruction)

            contents = []
            if image_data: contents.append(image_data)
            contents.append(prompt_text)

            if history_list and not image_data:
                chat = model.start_chat(history=history_list)
                res = chat.send_message(prompt_text)
            else:
                res = model.generate_content(contents)

            st.session_state.key_status[st.session_state.current_key_index] = "🟢 Ổn định"
            return res.text

        except ResourceExhausted:
            last_error = "Key hết Quota (Lỗi 429)"
            rotate_key("Hết Quota")
        except Exception as e:
            last_error = str(e)
            rotate_key("Lỗi API")

        attempts += 1

    return f"⚠️ **Không thể kết nối API.** Lỗi gần nhất: `{last_error}`"

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

                if mode == "User":
                    # Lấy phiên gần nhất từ Supabase
                    res = supabase.table("sessions").select("id").eq("username", st.session_state.username).order("created_at", desc=True).limit(1).execute()
                    if res.data:
                        st.session_state.current_session_id = res.data[0]["id"]
                    else:
                        new_id = str(uuid.uuid4())[:8]
                        supabase.table("sessions").insert({"id": new_id, "username": st.session_state.username}).execute()
                        st.session_state.current_session_id = new_id
                st.rerun()
            else:
                st.error("Mật khẩu không chính xác.")
    st.stop()

# 7. Giao diện Sidebar
with st.sidebar:
    st.title("✨ Gemini Cloud Pro")
    st.session_state.selected_persona = st.selectbox("🎭 Vai trò AI (Persona):", list(PERSONAS.keys()))
    st.divider()

    if st.session_state.role == "user":
        if st.button("➕ Chat mới", use_container_width=True, type="primary"):
            new_id = str(uuid.uuid4())[:8]
            supabase.table("sessions").insert({"id": new_id, "username": st.session_state.username}).execute()
            st.session_state.current_session_id = new_id
            st.rerun()

        st.write("")
        st.markdown("### 💬 Lịch sử trò chuyện")

        # Lấy danh sách session từ Supabase
        sessions_res = supabase.table("sessions").select("id, is_locked, created_at").eq("username", st.session_state.username).order("created_at", desc=True).execute()
        user_sessions = sessions_res.data

        for sess in user_sessions:
            s_id = sess["id"]
            # Lấy tin nhắn đầu tiên làm tiêu đề
            msg_res = supabase.table("messages").select("content").eq("session_id", s_id).eq("role", "user").order("created_at", desc=False).limit(1).execute()
            first_msg = msg_res.data[0]["content"] if msg_res.data else "Phiên chat trống"

            title = first_msg[:20] + "..." if len(first_msg) > 20 else first_msg
            is_active = (s_id == st.session_state.current_session_id)

            col_btn, col_del = st.columns([0.8, 0.2])
            with col_btn:
                if st.button(f"{'💬' if not is_active else '🔹'} {title}", key=f"btn_{s_id}", use_container_width=True, type="secondary" if not is_active else "primary"):
                    st.session_state.current_session_id = s_id
                    st.rerun()
            with col_del:
                with st.popover("🗑️"):
                    st.write("Xóa cuộc hội thoại này?")
                    if st.button("Xác nhận xóa", key=f"del_{s_id}", type="primary"):
                        # Xóa trên Supabase Cloud
                        supabase.table("sessions").delete().eq("id", s_id).execute()
                        if st.session_state.current_session_id == s_id:
                            rem = supabase.table("sessions").select("id").eq("username", st.session_state.username).order("created_at", desc=True).limit(1).execute()
                            st.session_state.current_session_id = rem.data[0]["id"] if rem.data else None
                        st.rerun()

    elif st.session_state.role == "admin":
        st.subheader("🛠 Quản trị viên")
        all_s = supabase.table("sessions").select("id, username").order("created_at", desc=True).execute().data
        options = {f"{s['username']} - {s['id']}": s['id'] for s in all_s}
        sel = st.selectbox("Theo dõi chat:", list(options.keys())) if options else None
        if sel:
            st.session_state.admin_selected_session = options[sel]

    st.divider()
    with st.expander("🔑 Trạng thái API Keys", expanded=False):
        for idx, status in st.session_state.key_status.items():
            active_mark = "👈 (Đang dùng)" if idx == st.session_state.current_key_index else ""
            st.caption(f"**Key #{idx+1}:** {status} {active_mark}")

    st.caption(f"👤 Tài khoản: **{st.session_state.username}**")
    if st.button("Đăng xuất", use_container_width=True):
        st.session_state.auth_status = False
        st.rerun()

# 8. Màn hình Chat Chính
active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")

if not active_sid and st.session_state.role == "user":
    new_id = str(uuid.uuid4())[:8]
    supabase.table("sessions").insert({"id": new_id, "username": st.session_state.username}).execute()
    st.session_state.current_session_id = new_id
    st.rerun()

# Lấy tin nhắn từ Supabase
db_messages = []
if active_sid:
    msgs_res = supabase.table("messages").select("role, content").eq("session_id", active_sid).order("created_at", desc=False).execute()
    db_messages = [(m["role"], m["content"]) for m in msgs_res.data]

if db_messages:
    chat_text = "\n\n".join([f"**{m[0].upper()}**: {m[1]}" for m in db_messages])
    st.download_button("📥 Tải lịch sử chat (.md)", data=chat_text, file_name=f"chat_{active_sid}.md", mime="text/markdown")

if not db_messages and st.session_state.role == "user":
    st.write("<br>", unsafe_allow_html=True)
    st.markdown(f"<h1 style='background: -webkit-linear-gradient(45deg, #4285F4, #D96570); -webkit-background-clip: text; -webkit-text
-fill-color: transparent;'>Xin chào, {st.session_state.username}</h1>", unsafe_allow_html=True)
    st.markdown("<h3 style='color: #666;'>Tôi có thể giúp gì cho bạn hôm nay?</h3>", unsafe_allow_html=True)

    st.write("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    quick_prompt = None
    if c1.button("💡 Lập kế hoạch du lịch 3 ngày 2 đêm", use_container_width=True):
        quick_prompt = "Hãy lập cho tôi kế hoạch du lịch Đà Nẵng 3 ngày 2 đêm tối ưu chi phí."
    if c2.button("💻 Viết code Python đọc file Excel", use_container_width=True):
        quick_prompt = "Hướng dẫn viết code Python dùng pandas để đọc và xử lý file Excel."
    if c3.button("✍️ Viết Email xin nghỉ phép lịch sự", use_container_width=True):
        quick_prompt = "Soạn cho tôi một mẫu email xin nghỉ phép 2 ngày vì lý do cá nhân."

    if quick_prompt:
        supabase.table("messages").insert({"session_id": active_sid, "role": "user", "content": quick_prompt}).execute()
        st.rerun()

# Render lịch sử tin nhắn
gemini_history = []
for role, content in db_messages:
    with st.chat_message(role):
        st.markdown(content)
    g_role = "user" if role == "user" else "model"
    gemini_history.append({"role": g_role, "parts": [content]})

# Upload File / Ảnh
uploaded_file = st.file_uploader("🖼️ Tải ảnh lên (Tùy chọn):", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed")
img_data = None
if uploaded_file:
    img_data = Image.open(uploaded_file)
    st.image(img_data, caption="Ảnh bạn đã tải lên", width=250)

# Ô nhập nội dung
if prompt := st.chat_input("Nhập câu hỏi của bạn tại đây..."):
    with st.chat_message("user"):
        if img_data: st.image(img_data, width=200)
        st.markdown(prompt)

    user_msg_store = prompt if not uploaded_file else f"[Đã gửi 1 hình ảnh] {prompt}"
    supabase.table("messages").insert({"session_id": active_sid, "role": "user", "content": user_msg_store}).execute()

    with st.chat_message("assistant"):
        with st.spinner("Đang suy luận..."):
            reply = query_gemini(prompt, gemini_history, image_data=img_data)
            st.markdown(reply)

    supabase.table("messages").insert({"session_id": active_sid, "role": "assistant", "content": reply}).execute()
    st.rerun()
