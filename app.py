import streamlit as st
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
import random

# Cấu hình giao diện
st.set_page_config(page_title="Gemini Pro - Secured", page_icon="🔒", layout="wide")

# 1. ĐỌC DỮ LIỆU TỪ SECRETS
try:
    API_KEYS = st.secrets["GEMINI_API_KEYS"]
    APP_PASSWORD = st.secrets["APP_PASSWORD"]
except KeyError:
    st.error("⚠️ Chưa cấu hình Secrets. Vui lòng thêm APP_PASSWORD và GEMINI_API_KEYS trên Streamlit Cloud.")
    st.stop()

# 2. KHỞI TẠO CÁC BIẾN TRẠNG THÁI
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_key_index" not in st.session_state:
    # Chọn ngẫu nhiên 1 key ngay khi khởi động
    st.session_state.current_key_index = random.randint(0, len(API_KEYS) - 1)

# Hàm chuyển API key ngẫu nhiên (không trùng key hiện tại)
def rotate_key_randomly():
    if len(API_KEYS) <= 1:
        return False
    
    available_indices = [i for i in range(len(API_KEYS)) if i != st.session_state.current_key_index]
    st.session_state.current_key_index = random.choice(available_indices)
    return True

# Lấy một đoạn che giấu của Key để hiển thị (Ví dụ: ...A1B2)
def get_masked_key(index):
    key = API_KEYS[index]
    return f"...{key[-4:]}"

# 3. MÀN HÌNH ĐĂNG NHẬP (BẢO MẬT)
if not st.session_state.authenticated:
    st.title("🔒 Ứng dụng AI Bảo mật")
    st.info("Vui lòng nhập mật khẩu để truy cập hệ thống.")
    
    pwd = st.text_input("Mật khẩu:", type="password")
    if st.button("Mở khóa"):
        if pwd == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Mật khẩu không chính xác!")
    st.stop() # Dừng thực thi các mã bên dưới nếu chưa đăng nhập

# ==========================================
# 4. GIAO DIỆN CHÍNH (SAU KHI ĐĂNG NHẬP)
# ==========================================

# SIDEBAR: CÔNG CỤ QUẢN LÝ
with st.sidebar:
    st.header("⚙️ Quản trị hệ thống")
    st.write(f"**Tổng số API Keys:** {len(API_KEYS)}")
    
    # Hiển thị API đang dùng để theo dõi xem nó có nhảy hay không
    st.success(f"🟢 Đang dùng Key số {st.session_state.current_key_index + 1} ({get_masked_key(st.session_state.current_key_index)})")
    
    st.divider()
    
    # Chức năng khóa màn hình / Xóa lịch sử
    if st.button("🗑️ Xóa ngữ cảnh trò chuyện", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
        
    if st.button("🔒 Khóa ứng dụng ngay lập tức", type="primary", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.messages = [] # Xóa luôn lịch sử để người sau không đọc được
        st.rerun()

st.title("🤖 Chatbot Gemini (Random API Rotation)")

# Hiển thị lại lịch sử
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# KHUNG CHAT & XỬ LÝ LƯỢT QUAY
if prompt := st.chat_input("Nhập câu hỏi..."):
    # Hiển thị và lưu câu hỏi
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Chuyển đổi lịch sử cho Gemini
    gemini_history = []
    for m in st.session_state.messages[:-1]:
        role = "user" if m["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [m["content"]]})

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        success = False
        attempts = 0
        max_attempts = len(API_KEYS)

        while not success and attempts < max_attempts:
            try:
                # Cấu hình API Key hiện tại
                current_key = API_KEYS[st.session_state.current_key_index]
                genai.configure(api_key=current_key)
                model = genai.GenerativeModel('gemini-1.5-pro-latest')
                
                chat = model.start_chat(history=gemini_history)
                
                with st.spinner(f'Đang xử lý (Key #{st.session_state.current_key_index + 1})...'):
                    response = chat.send_message(prompt)
                
                message_placeholder.markdown(response.text)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                success = True
                
            except ResourceExhausted:
                # Nếu lỗi 429 -> Random sang key khác
                attempts += 1
                old_index = st.session_state.current_key_index
                
                if rotate_key_randomly():
                    new_index = st.session_state.current_key_index
                    st.toast(f"Key {old_index + 1} hết hạn! Đã nhảy ngẫu nhiên sang Key {new_index + 1} ({get_masked_key(new_index)})", icon="🔄")
                else:
                    message_placeholder.error("Tất cả API keys đều bị quá tải. Vui lòng thử lại sau.")
                    break
            except Exception as e:
                message_placeholder.error(f"Lỗi: {str(e)}")
                break