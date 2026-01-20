import streamlit as st
import google.generativeai as genai
from supabase import create_client, Client
import json
import pandas as pd
from persona import V_CORE_INSTRUCTION, REVIEW_PROMPT, EXTRACTOR_PROMPT
# [QUAN TRỌNG] Import thư viện để tháo xích bộ lọc an toàn
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- 1. SETUP & AUTH ---
st.set_page_config(page_title="V-Reviewer", page_icon="🔥", layout="wide")

# Lấy Key từ secrets
try:
    SUPABASE_URL = st.secrets["supabase"]["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["supabase"]["SUPABASE_KEY"]
    GEMINI_KEY = st.secrets["gemini"]["API_KEY"]
except:
    st.error("❌ Chưa cấu hình secrets.toml! Xem lại hướng dẫn Bước 3.")
    st.stop()

# Kết nối
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
genai.configure(api_key=GEMINI_KEY)

# Hàm Login đơn giản
def login_page():
    st.title("🔐 Đăng nhập V-Reviewer")
    st.write("Hệ thống trợ lý viết truyện cực chiến")
    
    col_main, _ = st.columns([1, 1])
    with col_main:
        email = st.text_input("Email")
        password = st.text_input("Mật khẩu", type="password")
        
        col1, col2 = st.columns(2)
        if col1.button("Đăng Nhập", type="primary", use_container_width=True):
            try:
                res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.user = res.user
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi đăng nhập: {e}")
                
        if col2.button("Đăng Ký Mới", use_container_width=True):
            try:
                res = supabase.auth.sign_up({"email": email, "password": password})
                st.session_state.user = res.user
                st.success("Đã tạo user! Hãy đăng nhập lại.")
            except Exception as e:
                st.error(f"Lỗi đăng ký: {e}")

if 'user' not in st.session_state:
    login_page()
    st.stop()

# --- 2. CÁC HÀM "NÃO BỘ" THÔNG MINH ---

def get_embedding(text):
    return genai.embed_content(
        model="models/text-embedding-004",
        content=text,
        task_type="retrieval_document"
    )['embedding']

def smart_search(query_text, story_id, current_chap=None, top_k=7): 
    try:
        query_vec = get_embedding(query_text)
        
        # 1. Tìm kiếm Vector trước
        response = supabase.rpc("match_bible", {
            "query_embedding": query_vec,
            "match_threshold": 0.45, 
            "match_count": 20 # Lấy dư ra để lọc
        }).execute()
        
        results = []
        if response.data:
            bible_ids = [item['id'] for item in response.data]
            if bible_ids:
                # 2. Query lại DB để lọc Story ID và Chapter (Chặn tương lai)
                query = supabase.table("story_bible").select("*").in_("id", bible_ids).eq("story_id", story_id)
                
                # Logic chặn tương lai (Chỉ lấy kiến thức cũ hơn chap hiện tại)
                if current_chap:
                    query = query.lt("source_chapter", current_chap)
                
                valid_data = query.execute()
                
                # Format kết quả
                for item in valid_data.data:
                    chap_info = f"(Chap {item.get('source_chapter', '?')})"
                    results.append(f"- {item['entity_name']} {chap_info}: {item['description']}")
                    
        return "\n".join(results[:top_k]) if results else "Không tìm thấy dữ liệu QUÁ KHỨ liên quan."
    except Exception as e:
        print(f"Lỗi Search: {e}")
        return ""

# --- 3. GIAO DIỆN CHÍNH ---

with st.sidebar:
    st.title("🔥 V-Reviewer")
    st.caption(f"Logged in: {st.session_state.user.email}")
    if st.button("Đăng xuất"):
        supabase.auth.sign_out()
        del st.session_state.user
        st.rerun()
    st.divider()

# Chọn Truyện
stories = supabase.table("stories").select("*").eq("user_id", st.session_state.user.id).execute()
story_map = {s['title']: s['id'] for s in stories.data}
selected_story_name = st.selectbox("📖 Chọn bộ truyện", ["-- Tạo mới --"] + list(story_map.keys()))

if selected_story_name == "-- Tạo mới --":
    st.title("✨ Khởi tạo thế giới mới")
    st.info("👈 Nhìn sang cột bên trái để chọn truyện hoặc tạo mới tại đây.")
    new_title = st.text_input("Tên truyện mới")
    if st.button("Tạo Truyện Ngay"):
        if new_title:
            supabase.table("stories").insert({
                "title": new_title,
                "user_id": st.session_state.user.id 
            }).execute()
            st.success(f"Đã tạo truyện: {new_title}")
            st.rerun()
    st.stop()

story_id = story_map[selected_story_name]

# TAB CHỨC NĂNG
tab1, tab2, tab3 = st.tabs(["✍️ Viết & Review", "💬 Chat với V (Smart)", "📚 Story Bible (CMS)"])

# === TAB 1: VIẾT & REVIEW ===
with tab1:
    st.header(f"Soạn thảo: {selected_story_name}")
    
    col_l, col_r = st.columns([2, 1])
    
    with col_l:
        chap_num = st.number_input("Chương số", value=1, min_value=1)
        
        # Tải dữ liệu cũ
        existing_data = supabase.table("chapters").select("*").eq("story_id", story_id).eq("chapter_number", chap_num).execute()
        
        loaded_content = ""
        loaded_review = ""
        
        if existing_data.data:
            record = existing_data.data[0]
            loaded_content = record['content']
            loaded_review = record['review_content']
            st.toast(f"📂 Đã tải lại nội dung cũ của Chương {chap_num}!", icon="✅")

        display_content = st.session_state.get('temp_content', loaded_content) if st.session_state.get('temp_chap') == chap_num else loaded_content
        
        content = st.text_area(
            "Nội dung chương", 
            height=450, 
            value=display_content, 
            placeholder="Chương này chưa có nội dung...",
            key=f"editor_{story_id}_{chap_num}"
        )
        
    with col_r:
        st.write("### 🎮 Điều khiển")
        
        if loaded_review and 'temp_review' not in st.session_state:
            st.info("✅ Chương này đã được Review và Lưu trước đó.")
        
        if st.button("🚀 Gửi V Thẩm Định", type="primary", use_container_width=True):
            if not content:
                st.warning("Viết gì đi đã cha nội!")
            else:
                with st.spinner("V đang đọc, lục lại trí nhớ và soi mói..."):
                    related_context = smart_search(content[:1000], story_id, current_chap=chap_num)
                    
                    final_prompt = f"""
                    THÔNG TIN BỐI CẢNH TÌM ĐƯỢC TỪ QUÁ KHỨ:
                    {related_context}
                    
                    NỘI DUNG CHƯƠNG {chap_num} CẦN REVIEW:
                    {content}
                    """
                    
                    # [CỰC QUAN TRỌNG] CẤU HÌNH BỘ LỌC XUỐNG MỨC THẤP NHẤT (BLOCK_NONE)
                    safe_config = {
                        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
                    }
                    
                    # --- GỌI REVIEW ---
                    try:
                        # Model Review (Dùng Pro để soi kỹ)
                        model_review = genai.GenerativeModel('gemini-3-pro-preview', system_instruction=REVIEW_PROMPT)
                        review_res = model_review.generate_content(final_prompt, safety_settings=safe_config)
                        
                        if review_res.text:
                            st.session_state['temp_review'] = review_res.text
                    except ValueError:
                        st.error("🚫 V từ chối review chương này!")
                        st.warning("Lý do: Bộ lọc an toàn (Safety Filter). Vui lòng thử lại hoặc giảm bớt độ gắt của Persona.")
                        st.stop()
                    except Exception as e:
                        st.error(f"Lỗi lạ: {e}")
                        st.stop()

                    # --- GỌI EXTRACT ---
                    try:
                        # Model Extract (Dùng Flash cho nhanh & rẻ)
                        model_extract = genai.GenerativeModel('gemini-3-flash-preview', system_instruction=EXTRACTOR_PROMPT)
                        extract_res = model_extract.generate_content(content, safety_settings=safe_config)
                        st.session_state['temp_bible'] = extract_res.text
                    except:
                        st.session_state['temp_bible'] = "[]"

                    st.session_state['temp_content'] = content
                    st.session_state['temp_chap'] = chap_num
                    st.rerun()

    # --- KHU VỰC HIỂN THỊ KẾT QUẢ ---
    st.divider()
    
    temp_r = st.session_state.get('temp_review')
    if st.session_state.get('temp_chap') == chap_num and temp_r:
        display_review = temp_r
    else:
        display_review = loaded_review
    
    if display_review:
        st.subheader("🧐 Kết quả thẩm định")
        
        if display_review == loaded_review and 'temp_review' not in st.session_state:
            st.success("Dưới đây là kết quả review ĐÃ ĐƯỢC LƯU trong Database:")
        elif 'temp_review' in st.session_state:
            st.warning("Đây là bản Review MỚI (Chưa lưu). Bấm nút Lưu bên dưới nếu ưng ý.")

        with st.chat_message("assistant", avatar="🔥"):
            st.markdown(display_review)
            
        st.divider()
        
        if 'temp_review' in st.session_state and st.session_state['temp_chap'] == chap_num:
            c1, c2 = st.columns([1, 3])
            with c1:
                if st.button("💾 LƯU KẾT QUẢ MỚI", type="primary", use_container_width=True):
                    try:
                        # 1. Lưu Bible
                        json_str = st.session_state['temp_bible'].strip()
                        if json_str.startswith("```json"): json_str = json_str[7:-3]
                        try:
                            data_points = json.loads(json_str)
                            for point in data_points:
                                vec = get_embedding(point['description'])
                                supabase.table("story_bible").insert({
                                    "story_id": story_id,
                                    "entity_name": point['entity_name'],
                                    "description": point['description'],
                                    "embedding": vec,
                                    "source_chapter": st.session_state['temp_chap']
                                }).execute()
                        except: pass

                        # 2. Lưu Chương
                        supabase.table("chapters").delete().eq("story_id", story_id).eq("chapter_number", st.session_state['temp_chap']).execute()
                        
                        supabase.table("chapters").insert({
                            "story_id": story_id,
                            "chapter_number": st.session_state['temp_chap'],
                            "content": st.session_state['temp_content'],
                            "review_content": st.session_state['temp_review']
                        }).execute()
                        
                        st.success("✅ Đã cập nhật dữ liệu thành công!")
                        del st.session_state['temp_review']
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"Lỗi lưu: {e}")

# === TAB 2: CHAT THÔNG MINH ===
with tab2:
    st.header("Chém gió với V (Có não)")
    
    history = supabase.table("chat_history").select("*").eq("story_id", story_id).order("created_at", desc=False).execute()
    
    for msg in history.data:
        role = "user" if msg['role'] == 'user' else "assistant"
        with st.chat_message(role):
            st.markdown(msg['content'])
            
    if prompt := st.chat_input("Hỏi gì đi (VD: Thằng Hùng chap trước bị sao?)"):
        with st.chat_message("user"):
            st.markdown(prompt)
        
        with st.spinner("V đang nhớ lại..."):
            context = smart_search(prompt, story_id, top_k=7) 
            full_prompt = f"CONTEXT TỪ DATABASE (Các chap liên quan):\n{context}\n\nUSER HỎI:\n{prompt}"
            
            # Cấu hình an toàn cho Chat luôn
            safe_config_chat = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }

            try:
                model_chat = genai.GenerativeModel('gemini-3-pro-preview', system_instruction=V_CORE_INSTRUCTION)
                response = model_chat.generate_content(full_prompt, safety_settings=safe_config_chat)
                
                if response.text:
                    with st.chat_message("assistant"):
                        st.markdown(response.text)
                        with st.expander("🔍 V đã tìm thấy gì trong ký ức?"):
                            st.info(context)
                    
                    supabase.table("chat_history").insert([
                        {"story_id": story_id, "role": "user", "content": prompt},
                        {"story_id": story_id, "role": "model", "content": response.text}
                    ]).execute()
            except Exception as e:
                 with st.chat_message("assistant"):
                    st.error("🚫 V từ chối trả lời (Lỗi Safety hoặc Timeout)!")

# === TAB 3: QUẢN LÝ BIBLE (NÂNG CẤP: AI CLEANER & MANUAL ADD) ===
with tab3:
    st.header("📚 Quản lý Dữ liệu Cốt truyện")
    st.caption("CMS xịn xò: Thêm bằng tay & Dọn rác bằng AI.")
    
    # Lấy dữ liệu
    data = supabase.table("story_bible").select("*").eq("story_id", story_id).order("created_at", desc=True).execute()
    
    # --- TÍNH NĂNG 1: THÊM DỮ LIỆU THỦ CÔNG (MANUAL ADD) ---
    with st.expander("➕ Thêm dữ liệu Bible thủ công", expanded=False):
        c1, c2 = st.columns([1, 2])
        with c1:
            m_name = st.text_input("Tên thực thể (VD: Hùng)", placeholder="Nhân vật, địa danh...")
            m_chap = st.number_input("Thuộc chương (Source)", value=st.session_state.get('temp_chap', 1), min_value=1)
        with c2:
            m_desc = st.text_area("Mô tả chi tiết", placeholder="VD: Là main chính, có vết sẹo trên trán...", height=100)
            
        if st.button("💾 Lưu vào Database ngay"):
            if m_name and m_desc:
                with st.spinner("Đang mã hóa Vector và lưu..."):
                    try:
                        # 1. Tạo Embedding cho mô tả (Quan trọng để search được)
                        vec = get_embedding(m_desc)
                        
                        # 2. Insert vào DB
                        supabase.table("story_bible").insert({
                            "story_id": story_id,
                            "entity_name": m_name,
                            "description": m_desc,
                            "embedding": vec,
                            "source_chapter": m_chap
                        }).execute()
                        st.success(f"Đã thêm '{m_name}' vào kho tàng kiến thức!")
                        st.rerun() 
                    except Exception as e:
                        st.error(f"Lỗi lưu: {e}")
            else:
                st.warning("Nhập thiếu tên hoặc mô tả rồi ông giáo ơi!")

    st.divider()

    # --- TÍNH NĂNG 2: AI SEMANTIC CLEANER (DỌN RÁC THÔNG MINH) ---
    if not data.data:
        st.info("Chưa có dữ liệu Bible nào.")
    else:
        df = pd.DataFrame(data.data)
        
        with st.expander("🧠 AI Dọn Rác (Thông minh hơn)", expanded=True):
            st.write("AI sẽ đọc và phát hiện các thông tin **trùng lặp về ý nghĩa**.")
            
            if st.button("🤖 Quét rác bằng Gemini Flash", type="primary"):
                with st.spinner("Gemini đang đọc toàn bộ Bible để tìm sạn..."):
                    # 1. Chuẩn bị dữ liệu 
                    grouped_data = {}
                    for item in data.data:
                        name = item['entity_name']
                        if name not in grouped_data: grouped_data[name] = []
                        grouped_data[name].append({
                            "id": item['id'],
                            "desc": item['description'],
                            "chap": item.get('source_chapter', '?')
                        })
                    
                    # Chỉ gửi những nhóm có > 1 dòng
                    candidates = {k: v for k, v in grouped_data.items() if len(v) > 1}
                    
                    if not candidates:
                        st.info("Dữ liệu quá sạch! Mỗi nhân vật chỉ có 1 dòng mô tả.")
                    else:
                        # 2. Soạn Prompt
                        prompt_cleaner = f"""
                        Bạn là một 'Database Cleaner'. Nhiệm vụ của bạn là tìm ra các dòng dữ liệu bị trùng lặp ý nghĩa (Semantic Duplicates).
                        
                        Dữ liệu đầu vào (JSON Grouped by Name):
                        {json.dumps(candidates, ensure_ascii=False)}
                        
                        YÊU CẦU:
                        - Với mỗi nhóm tên (Key), hãy đọc các mô tả (desc).
                        - Nếu có nhiều dòng mô tả mang ý nghĩa GIỐNG NHAU (hoặc dòng này bao hàm dòng kia), hãy chọn giữ lại dòng chi tiết nhất/mới nhất.
                        - Trả về danh sách các `id` cần XÓA (Delete).
                        
                        OUTPUT FORMAT (JSON Only, list of IDs):
                        ["uuid-1", "uuid-2", ...]
                        """
                        
                        try:
                            model_cleaner = genai.GenerativeModel('gemini-3-flash-preview', 
                                                                  system_instruction="Trả về JSON thuần. Chỉ chứa list các ID cần xóa.")
                            res = model_cleaner.generate_content(prompt_cleaner)
                            
                            clean_text = res.text.strip()
                            if clean_text.startswith("```json"): clean_text = clean_text[7:-3]
                            ids_to_delete = json.loads(clean_text)
                            
                            if ids_to_delete:
                                st.session_state['ids_to_delete'] = ids_to_delete
                                st.rerun()
                            else:
                                st.success("AI nhận thấy các dòng mô tả đều khác biệt nhau. Không có gì để xóa!")
                                
                        except Exception as e:
                            st.error(f"AI bị ngáo hoặc lỗi JSON: {e}")

            # 3. Hiển thị xác nhận xóa
            if 'ids_to_delete' in st.session_state and st.session_state['ids_to_delete']:
                ids = st.session_state['ids_to_delete']
                st.warning(f"⚠️ AI đề xuất xóa {len(ids)} dòng trùng lặp ý nghĩa:")
                
                rows_to_del = df[df['id'].isin(ids)]
                st.dataframe(rows_to_del[['entity_name', 'description', 'source_chapter']], use_container_width=True)
                
                c1, c2 = st.columns(2)
                if c1.button("🗑️ Đồng ý xóa ngay"):
                    supabase.table("story_bible").delete().in_("id", ids).execute()
                    del st.session_state['ids_to_delete']
                    st.success("Đã dọn sạch rác!")
                    st.rerun()
                
                if c2.button("Hủy bỏ"):
                    del st.session_state['ids_to_delete']
                    st.rerun()

        st.divider()

        # --- HIỂN THỊ DANH SÁCH & XÓA THỦ CÔNG ---
        st.subheader("Danh sách chi tiết")
        
        options = {f"[Chap {row.get('source_chapter', '?')}] {row['entity_name']} | {row['description'][:50]}...": row['id'] for index, row in df.iterrows()}
        selected_items = st.multiselect("🗑️ Chọn xóa thủ công:", options=options.keys())
        if selected_items and st.button(f"Xác nhận xóa {len(selected_items)} dòng"):
            ids_to_remove = [options[item] for item in selected_items]
            supabase.table("story_bible").delete().in_("id", ids_to_remove).execute()
            st.rerun()

        cols_show = ['source_chapter', 'entity_name', 'description', 'created_at'] if 'source_chapter' in df.columns else ['entity_name', 'description', 'created_at']
        st.dataframe(
            df[cols_show],
            column_config={"source_chapter": "Chap", "entity_name": "Tên", "description": "Mô tả", "created_at": "Ngày tạo"},
            use_container_width=True, height=500
        )
