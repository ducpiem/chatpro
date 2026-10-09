# 8. Màn hình Chat Chính & Quản lý Session an toàn không bị mất lịch sử
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = None

active_sid = st.session_state.current_session_id if st.session_state.role == "user" else st.session_state.get("admin_selected_session")

if not active_sid and st.session_state.role == "user":
    last_sess = run_query("SELECT id FROM sessions WHERE username = %s ORDER BY created_at DESC LIMIT 1", (st.session_state.username,), fetch="one")
    if last_sess:
        st.session_state.current_session_id = last_sess[0]
    else:
        new_id = str(uuid.uuid4())[:8]
        run_query("INSERT INTO sessions (id, username, created_at) VALUES (%s, %s, %s)", (new_id, st.session_state.username, str(datetime.datetime.now())))
        st.session_state.current_session_id = new_id
    active_sid = st.session_state.current_session_id

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

# Bộ chọn/dán ảnh (Đã căn chỉnh lại CSS để không bị khuất giao diện)
st.write("<br>", unsafe_allow_html=True)
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
