import streamlit as st
import requests
import json
import re
import pandas as pd
import time
from datetime import datetime, timedelta
import hashlib
from typing import Dict, List, Optional, Tuple, Any
from supabase import create_client, Client
import extra_streamlit_components as stx
import uuid

# ==========================================
# 🎨 1. CẤU HÌNH & CSS NÂNG CẤP
# ==========================================
st.set_page_config(
    page_title="V-Universe Hub Pro",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS tùy chỉnh nâng cao
st.markdown("""
<style>
    /* Main container */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    
    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #2d3748 0%, #1a202c 100%);
    }
    
    [data-testid="stSidebar"] .stButton > button {
        background: #4299e1;
        color: white;
        border: none;
        border-radius: 8px;
        padding: 10px 16px;
        font-weight: 500;
        transition: all 0.3s;
    }
    
    [data-testid="stSidebar"] .stButton > button:hover {
        background: #3182ce;
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(49, 130, 206, 0.4);
    }
    
    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        padding: 0 4px;
        border-bottom: 2px solid #e2e8f0;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 48px;
        padding: 0 20px;
        background-color: #f7fafc;
        border-radius: 8px 8px 0 0;
        font-weight: 500;
        color: #4a5568;
        border: 1px solid #e2e8f0;
        border-bottom: none;
        transition: all 0.3s;
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-color: #667eea;
        box-shadow: 0 4px 6px rgba(102, 126, 234, 0.2);
    }
    
    /* Chat messages */
    .stChatMessage {
        padding: 16px;
        border-radius: 12px;
        margin: 8px 0;
        border-left: 4px solid #4299e1;
    }
    
    .stChatMessage[data-testid*="user"] {
        background-color: #ebf8ff;
        border-left-color: #4299e1;
    }
    
    .stChatMessage[data-testid*="assistant"] {
        background-color: #f0fff4;
        border-left-color: #48bb78;
    }
    
    /* Buttons */
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s;
    }
    
    .stButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    }
    
    /* Metrics cards */
    .metric-card {
        background: white;
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
        transition: all 0.3s;
    }
    
    .metric-card:hover {
        box-shadow: 0 8px 16px rgba(0, 0, 0, 0.1);
        transform: translateY(-2px);
    }
    
    /* Status styling */
    .status-success {
        color: #38a169;
        font-weight: 600;
    }
    
    .status-warning {
        color: #d69e2e;
        font-weight: 600;
    }
    
    .status-danger {
        color: #e53e3e;
        font-weight: 600;
    }
    
    /* Custom expander */
    .streamlit-expanderHeader {
        font-weight: 600;
        color: #2d3748;
    }
    
    /* Tooltip */
    .tooltip {
        position: relative;
        display: inline-block;
    }
    
    .tooltip .tooltiptext {
        visibility: hidden;
        background-color: #2d3748;
        color: white;
        text-align: center;
        border-radius: 6px;
        padding: 8px;
        position: absolute;
        z-index: 1;
        bottom: 125%;
        left: 50%;
        transform: translateX(-50%);
        white-space: nowrap;
    }
    
    .tooltip:hover .tooltiptext {
        visibility: visible;
    }
    
    /* Badges */
    .badge {
        display: inline-block;
        padding: 4px 8px;
        border-radius: 12px;
        font-size: 0.75rem;
        font-weight: 600;
        margin: 0 4px;
    }
    
    .badge-primary {
        background-color: #ebf8ff;
        color: #4299e1;
    }
    
    .badge-success {
        background-color: #f0fff4;
        color: #48bb78;
    }
    
    .badge-warning {
        background-color: #fefcbf;
        color: #d69e2e;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 🔧 2. CẤU HÌNH HỆ THỐNG
# ==========================================
class Config:
    """Lớp quản lý cấu hình hệ thống"""
    
    # OpenRouter API
    OPENROUTER_API_KEY = st.secrets.get("openrouter", {}).get("API_KEY", "")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
    
    # Supabase
    SUPABASE_URL = st.secrets.get("supabase", {}).get("SUPABASE_URL", "")
    SUPABASE_KEY = st.secrets.get("supabase", {}).get("SUPABASE_KEY", "")
    
    # Models
    ROUTER_MODEL = "deepseek/deepseek-chat"
    EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
    
    # Available models for user selection
    AVAILABLE_MODELS = {
        "Creative": [
            "openai/gpt-4-turbo-preview",
            "anthropic/claude-3-haiku",
            "google/gemini-pro-1.5"
        ],
        "Balanced": [
            "openai/gpt-3.5-turbo",
            "anthropic/claude-3-sonnet",
            "mistralai/mixtral-8x7b-instruct"
        ],
        "Economy": [
            "openai/gpt-3.5-turbo-16k",
            "google/gemini-flash-1.5",
            "meta-llama/llama-3.1-8b-instruct"
        ]
    }
    
    # Cost per 1M tokens (USD)
    MODEL_COSTS = {
        "openai/gpt-4-turbo-preview": {"input": 10.00, "output": 30.00},
        "openai/gpt-4": {"input": 30.00, "output": 60.00},
        "openai/gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
        "openai/gpt-3.5-turbo-16k": {"input": 1.50, "output": 2.00},
        "anthropic/claude-3-opus": {"input": 15.00, "output": 75.00},
        "anthropic/claude-3-sonnet": {"input": 3.00, "output": 15.00},
        "anthropic/claude-3-haiku": {"input": 0.25, "output": 1.25},
        "google/gemini-pro-1.5": {"input": 1.25, "output": 2.50},
        "google/gemini-flash-1.5": {"input": 0.075, "output": 0.30},
        "mistralai/mixtral-8x7b-instruct": {"input": 0.24, "output": 0.24},
        "meta-llama/llama-3.1-8b-instruct": {"input": 0.18, "output": 0.18},
        "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
        "qwen/qwen-2.5-7b-instruct": {"input": 0.12, "output": 0.12}
    }
    
    # Cache settings
    CACHE_TTL_HOURS = 24
    MAX_CONTEXT_TOKENS = {
        "low": 4000,
        "medium": 8000,
        "high": 16000
    }
    
    # Rate limiting
    REQUESTS_PER_MINUTE = 30
    
    @classmethod
    def validate(cls):
        """Validate configuration"""
        errors = []
        if not cls.OPENROUTER_API_KEY:
            errors.append("OpenRouter API key not found in secrets")
        if not cls.SUPABASE_URL or not cls.SUPABASE_KEY:
            errors.append("Supabase credentials not found in secrets")
        return errors

# Validate config
config_errors = Config.validate()
if config_errors:
    st.error("❌ Configuration errors found:")
    for error in config_errors:
        st.error(f"  - {error}")
    st.stop()

# ==========================================
# 🔗 3. KHỞI TẠO DỊCH VỤ
# ==========================================
@st.cache_resource
def init_services():
    """Khởi tạo kết nối đến các dịch vụ"""
    try:
        # Supabase client
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        
        # Test connection
        supabase.table("stories").select("count", count="exact").limit(1).execute()
        
        st.success("✅ Services initialized successfully!")
        return supabase
    except Exception as e:
        st.error(f"❌ Failed to initialize services: {str(e)}")
        return None

supabase = init_services()
if not supabase:
    st.stop()

# ==========================================
# 🍪 4. QUẢN LÝ PHIÊN & COOKIE
# ==========================================
cookie_manager = stx.CookieManager(key="v_universe_cookies")

def check_login_status():
    """Kiểm tra và quản lý trạng thái đăng nhập"""
    if 'user' in st.session_state:
        return
    
    # Check cookies
    if 'cookie_check_done' not in st.session_state:
        try:
            access_token = cookie_manager.get("supabase_access_token")
            refresh_token = cookie_manager.get("supabase_refresh_token")
            
            if access_token and refresh_token:
                session = supabase.auth.set_session(access_token, refresh_token)
                if session:
                    st.session_state.user = session.user
                    st.toast("👋 Welcome back!", icon="🎉")
                    time.sleep(0.5)
                    st.rerun()
        except Exception as e:
            print(f"Cookie auth error: {e}")
        
        st.session_state['cookie_check_done'] = True
    
    # Show login form
    if 'user' not in st.session_state:
        render_login_form()

def render_login_form():
    """Hiển thị form đăng nhập/đăng ký"""
    st.title("🚀 V-Universe Hub Pro")
    st.markdown("---")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        with st.container():
            st.subheader("🔐 Authentication")
            
            tab_login, tab_register = st.tabs(["Login", "Register"])
            
            with tab_login:
                email = st.text_input("📧 Email", key="login_email")
                password = st.text_input("🔑 Password", type="password", key="login_pass")
                
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button("Login", type="primary", use_container_width=True):
                        if email and password:
                            try:
                                res = supabase.auth.sign_in_with_password({
                                    "email": email, 
                                    "password": password
                                })
                                st.session_state.user = res.user
                                
                                # Set cookies
                                cookie_manager.set(
                                    "supabase_access_token", 
                                    res.session.access_token,
                                    key="login_access"
                                )
                                cookie_manager.set(
                                    "supabase_refresh_token",
                                    res.session.refresh_token,
                                    key="login_refresh"
                                )
                                
                                st.success("✅ Login successful!")
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Login failed: {str(e)}")
                
                with col_btn2:
                    if st.button("Forgot Password?", use_container_width=True):
                        if email:
                            try:
                                supabase.auth.reset_password_email(email)
                                st.success("Password reset email sent!")
                            except Exception as e:
                                st.error(f"Failed: {str(e)}")
            
            with tab_register:
                reg_email = st.text_input("📧 Email", key="reg_email")
                reg_pass = st.text_input("🔑 Password", type="password", key="reg_pass")
                reg_pass_confirm = st.text_input("🔑 Confirm Password", type="password", key="reg_pass_confirm")
                
                if st.button("Register", type="secondary", use_container_width=True):
                    if reg_email and reg_pass and reg_pass == reg_pass_confirm:
                        try:
                            res = supabase.auth.sign_up({
                                "email": reg_email,
                                "password": reg_pass
                            })
                            if res.user:
                                st.success("✅ Registration successful! Please check your email.")
                            else:
                                st.warning("⚠️ Please check your confirmation email.")
                        except Exception as e:
                            st.error(f"Registration failed: {str(e)}")
                    else:
                        st.error("Please fill all fields correctly")
        
        st.markdown("---")
        st.caption("✨ Powered by OpenRouter AI • Supabase • Streamlit")
    
    st.stop()

check_login_status()

# ==========================================
# 🧠 5. PERSONA SYSTEM
# ==========================================
class PersonaSystem:
    """Hệ thống quản lý Persona"""
    
    PERSONAS = {
        "Writer": {
            "icon": "✍️",
            "role": "Editor Văn Học (Writer Mode)",
            "core_instruction": """Bạn là V - Biên tập viên lão làng 30 tuổi. 
            Tính cách: Sắc sảo, khó tính, thẳng thắn nhưng có tâm.
            Xưng hô: "Tôi" với "Anh/Chị".
            Nhiệm vụ: Phê bình văn học sắc bén, chỉ ra điểm mạnh/yếu.""",
            "prefix": "[WRITER]",
            "temperature": 0.8,
            "max_tokens": 2000
        },
        "Coder": {
            "icon": "💻",
            "role": "Senior Tech Lead (Coder Mode)",
            "core_instruction": """Bạn là V - Tech Lead 10 năm kinh nghiệm.
            Tính cách: Thực dụng, yêu clean code, ghét overengineering.
            Xưng hô: "Tôi" với "Anh/Chị".
            Nhiệm vụ: Review code, tối ưu thuật toán, cảnh báo bảo mật.""",
            "prefix": "[CODER]",
            "temperature": 0.3,
            "max_tokens": 1500
        },
        "Content Creator": {
            "icon": "🎬",
            "role": "Viral Content Strategist",
            "core_instruction": """Bạn là V - Chuyên gia Content Marketing.
            Tính cách: Sáng tạo, bắt trend nhanh, hiểu tâm lý đám đông.
            Xưng hô: "Tôi" với "Anh/Chị".
            Nhiệm vụ: Tối ưu Hook, tăng tương tác, viral content.""",
            "prefix": "[CONTENT]",
            "temperature": 0.9,
            "max_tokens": 1800
        }
    }
    
    @classmethod
    def get_persona(cls, persona_type: str) -> Dict:
        """Lấy cấu hình persona"""
        return cls.PERSONAS.get(persona_type, cls.PERSONAS["Writer"])
    
    @classmethod
    def get_available_personas(cls) -> List[str]:
        """Danh sách persona có sẵn"""
        return list(cls.PERSONAS.keys())

# ==========================================
# 🤖 6. AI SERVICE (OPENROUTER)
# ==========================================
class AIService:
    """Dịch vụ AI qua OpenRouter"""
    
    @staticmethod
    def call_openrouter(
        messages: List[Dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        stream: bool = False
    ) -> Dict:
        """Gọi OpenRouter API"""
        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://v-universe.streamlit.app",
            "X-Title": "V-Universe Hub"
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }
        
        try:
            response = requests.post(
                f"{Config.OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            raise Exception(f"OpenRouter API error: {str(e)}")
    
    @staticmethod
    def get_embedding(text: str) -> Optional[List[float]]:
        """Lấy embedding từ OpenRouter"""
        if not text or not isinstance(text, str) or not text.strip():
            return None
            
        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": Config.EMBEDDING_MODEL,
            "input": text
        }
        
        try:
            response = requests.post(
                f"{Config.OPENROUTER_BASE_URL}/embeddings",
                headers=headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            return data.get("data", [{}])[0].get("embedding")
        except Exception as e:
            print(f"Embedding error: {e}")
            return None
    
    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Ước tính số token (xấp xỉ)"""
        # Simple estimation: 1 token ≈ 4 characters for English
        # For Vietnamese: 1 token ≈ 3 characters
        if not text:
            return 0
        return len(text) // 3
    
    @staticmethod
    def calculate_cost(
        input_tokens: int,
        output_tokens: int,
        model: str
    ) -> float:
        """Tính chi phí cho request"""
        model_costs = Config.MODEL_COSTS.get(model, {"input": 0.0, "output": 0.0})
        
        input_cost = (input_tokens / 1_000_000) * model_costs["input"]
        output_cost = (output_tokens / 1_000_000) * model_costs["output"]
        
        return round(input_cost + output_cost, 6)

# ==========================================
# 🧭 7. AI ROUTER (DEEPSEEK)
# ==========================================
class AIRouter:
    """Bộ định tuyến AI sử dụng DeepSeek"""
    
    CACHE_TABLE = "router_cache"
    
    @staticmethod
    def create_query_hash(query: str, project_id: str) -> str:
        """Tạo hash cho câu query để cache"""
        content = f"{query}_{project_id}"
        return hashlib.md5(content.encode()).hexdigest()
    
    @staticmethod
    def get_cached_route(query_hash: str) -> Optional[Dict]:
        """Lấy kết quả router từ cache"""
        try:
            res = supabase.table(AIRouter.CACHE_TABLE)\
                .select("*")\
                .eq("query_hash", query_hash)\
                .gt("expires_at", datetime.utcnow().isoformat())\
                .execute()
            
            if res.data:
                return res.data[0].get("router_response")
        except Exception as e:
            print(f"Cache read error: {e}")
        return None
    
    @staticmethod
    def save_to_cache(query_hash: str, response: Dict, intent: str):
        """Lưu kết quả vào cache"""
        try:
            supabase.table(AIRouter.CACHE_TABLE).insert({
                "query_hash": query_hash,
                "router_response": response,
                "intent": intent,
                "expires_at": (datetime.utcnow() + timedelta(hours=Config.CACHE_TTL_HOURS)).isoformat()
            }).execute()
        except Exception as e:
            print(f"Cache save error: {e}")
    
    @staticmethod
    def analyze_intent(user_query: str, chat_history: List[Dict], project_id: str) -> Dict:
        """Phân tích intent sử dụng DeepSeek"""
        # Tạo hash cho cache
        query_hash = AIRouter.create_query_hash(user_query, project_id)
        
        # Kiểm tra cache
        cached = AIRouter.get_cached_route(query_hash)
        if cached:
            return cached
        
        # Chuẩn bị prompt cho router
        history_text = "\n".join([
            f"{msg.get('role', 'user')}: {msg.get('content', '')}" 
            for msg in chat_history[-5:]
        ])
        
        router_prompt = f"""
        Bạn là Router thông minh. Phân tích câu hỏi và xác định:
        
        1. INTENT (một trong các loại):
           - "chat": Chào hỏi, trò chuyện thông thường
           - "summarize": Tóm tắt, đúc kết
           - "rewrite": Viết lại, chỉnh sửa
           - "analyze": Phân tích, đánh giá
           - "create": Tạo mới, viết tiếp
           - "search": Tìm kiếm thông tin
           - "code": Viết code, debug
           - "review": Review, phê bình
        
        2. PRIORITY (1-5): Mức độ quan trọng
        3. CONTEXT_NEEDED: Loại context cần thiết
        4. SPECIFIC_REQUESTS: Yêu cầu đặc biệt từ user
        
        USER QUERY: {user_query}
        
        CHAT HISTORY (5 tin gần nhất):
        {history_text}
        
        PHÂN TÍCH @COMMAND:
        - @file(filename): Cần file cụ thể
        - @bible(entity): Cần bible entity
        - @project(name): Cần project khác
        - @rule(type): Cần rule cụ thể
        
        OUTPUT JSON:
        {{
            "intent": "loại intent",
            "priority": số_1_5,
            "context_needed": {{
                "files": ["tên_file1", "tên_file2"],
                "bible_entities": ["entity1", "entity2"],
                "rules": true/false,
                "cross_project": "tên_project" hoặc null
            }},
            "specific_requests": ["yêu_cầu_1", "yêu_cầu_2"],
            "estimated_tokens": ước_lượng_token_cần_thiết,
            "suggested_model": "openai/gpt-3.5-turbo" hoặc model_phù_hợp
        }}
        """
        
        messages = [
            {"role": "system", "content": "Bạn là Router AI. Trả về JSON duy nhất."},
            {"role": "user", "content": router_prompt}
        ]
        
        try:
            # Gọi DeepSeek qua OpenRouter
            response = AIService.call_openrouter(
                messages=messages,
                model=Config.ROUTER_MODEL,
                temperature=0.1,
                max_tokens=500
            )
            
            # Parse JSON response
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            
            # Clean JSON
            content = content.replace("```json", "").replace("```", "").strip()
            
            # Find JSON object
            start = content.find("{")
            end = content.rfind("}") + 1
            
            if start != -1 and end != 0:
                json_str = content[start:end]
                result = json.loads(json_str)
                
                # Lưu vào cache
                AIRouter.save_to_cache(query_hash, result, result.get("intent", "chat"))
                
                return result
            else:
                return {
                    "intent": "chat",
                    "priority": 3,
                    "context_needed": {"files": [], "bible_entities": [], "rules": False, "cross_project": None},
                    "specific_requests": [],
                    "estimated_tokens": 1000,
                    "suggested_model": "openai/gpt-3.5-turbo"
                }
                
        except Exception as e:
            print(f"Router error: {e}")
            return {
                "intent": "chat",
                "priority": 3,
                "context_needed": {"files": [], "bible_entities": [], "rules": False, "cross_project": None},
                "specific_requests": [],
                "estimated_tokens": 1000,
                "suggested_model": "openai/gpt-3.5-turbo"
            }
    
    @staticmethod
    def extract_commands(query: str) -> Dict:
        """Trích xuất command từ query (@file, @bible, etc.)"""
        commands = {
            "files": [],
            "bible_entities": [],
            "projects": [],
            "rules": []
        }
        
        # Tìm @file(name)
        file_matches = re.findall(r'@file\(([^)]+)\)', query, re.IGNORECASE)
        commands["files"] = [f.strip() for f in file_matches]
        
        # Tìm @bible(name)
        bible_matches = re.findall(r'@bible\(([^)]+)\)', query, re.IGNORECASE)
        commands["bible_entities"] = [b.strip() for b in bible_matches]
        
        # Tìm @project(name)
        project_matches = re.findall(r'@project\(([^)]+)\)', query, re.IGNORECASE)
        commands["projects"] = [p.strip() for p in project_matches]
        
        # Tìm @rule(type)
        rule_matches = re.findall(r'@rule\(([^)]+)\)', query, re.IGNORECASE)
        commands["rules"] = [r.strip() for r in rule_matches]
        
        return commands

# ==========================================
# 📚 8. CONTEXT MANAGER
# ==========================================
class ContextManager:
    """Quản lý context cho AI"""
    
    @staticmethod
    def load_files(file_names: List[str], project_id: str, cross_project: Optional[str] = None) -> Tuple[str, List[str]]:
        """Tải nội dung file"""
        if not file_names:
            return "", []
        
        full_text = ""
        loaded_sources = []
        target_project_id = project_id
        
        # Nếu có cross-project
        if cross_project:
            # Tìm project khác của user
            try:
                projects = supabase.table("stories")\
                    .select("id")\
                    .eq("user_id", st.session_state.user.id)\
                    .ilike("title", f"%{cross_project}%")\
                    .execute()
                
                if projects.data:
                    target_project_id = projects.data[0]["id"]
                    loaded_sources.append(f"📂 Cross-Project: {cross_project}")
            except:
                pass
        
        for file_name in file_names:
            # Tìm trong chapters
            try:
                res = supabase.table("chapters")\
                    .select("title, content")\
                    .eq("story_id", target_project_id)\
                    .ilike("title", f"%{file_name}%")\
                    .execute()
                
                if res.data:
                    for item in res.data[:3]:  # Giới hạn 3 kết quả
                        full_text += f"\n\n📄 FILE: {item['title']}\n{item['content']}\n"
                        loaded_sources.append(f"📄 {item['title']}")
                else:
                    # Tìm bằng chapter number
                    if file_name.isdigit():
                        res = supabase.table("chapters")\
                            .select("title, content")\
                            .eq("story_id", target_project_id)\
                            .eq("chapter_number", int(file_name))\
                            .execute()
                        
                        if res.data:
                            item = res.data[0]
                            full_text += f"\n\n📄 FILE #{file_name}: {item['title']}\n{item['content']}\n"
                            loaded_sources.append(f"📄 #{file_name} {item['title']}")
            except Exception as e:
                print(f"Error loading file {file_name}: {e}")
        
        return full_text, loaded_sources
    
    @staticmethod
    def load_bible_entities(entity_names: List[str], project_id: str) -> Tuple[str, List[str]]:
        """Tải các bible entities"""
        if not entity_names:
            return "", []
        
        full_text = ""
        loaded_sources = []
        
        for entity_name in entity_names:
            try:
                # Tìm exact match hoặc partial
                res = supabase.table("story_bible")\
                    .select("entity_name, description, prefix")\
                    .eq("story_id", project_id)\
                    .or_(f"entity_name.ilike.%{entity_name}%,description.ilike.%{entity_name}%")\
                    .execute()
                
                if res.data:
                    for item in res.data[:5]:  # Giới hạn 5 kết quả
                        prefix = item.get('prefix', '')
                        full_text += f"\n\n{prefix} {item['entity_name']}:\n{item['description']}\n"
                        loaded_sources.append(f"{prefix} {item['entity_name']}")
            except Exception as e:
                print(f"Error loading bible entity {entity_name}: {e}")
        
        return full_text, loaded_sources
    
    @staticmethod
    def load_rules(project_id: str, rule_types: Optional[List[str]] = None) -> str:
        """Tải các rules"""
        try:
            query = supabase.table("story_bible")\
                .select("entity_name, description")\
                .eq("story_id", project_id)\
                .ilike("entity_name", "%[RULE]%")
            
            if rule_types:
                # Thêm filter cho rule types cụ thể
                or_conditions = " OR ".join([f"entity_name.ilike.%{rt}%" for rt in rule_types])
                query = query.or_(or_conditions)
            
            res = query.execute()
            
            if res.data:
                rules_text = "\n".join([
                    f"📌 {item['entity_name']}:\n{item['description']}\n"
                    for item in res.data
                ])
                return f"\n⚖️ RULES:\n{rules_text}\n"
        except Exception as e:
            print(f"Error loading rules: {e}")
        
        return ""
    
    @staticmethod
    def build_context(
        router_result: Dict,
        project_id: str,
        persona: Dict
    ) -> Tuple[str, List[str], int]:
        """Xây dựng context từ router result"""
        context_parts = []
        sources = []
        total_tokens = 0
        
        context_needed = router_result.get("context_needed", {})
        
        # 1. Luôn thêm Persona Instruction
        persona_text = f"🎭 PERSONA: {persona['role']}\n{persona['core_instruction']}\n"
        context_parts.append(persona_text)
        total_tokens += AIService.estimate_tokens(persona_text)
        
        # 2. Rules (nếu cần)
        if context_needed.get("rules"):
            rules_text = ContextManager.load_rules(project_id, context_needed.get("rule_types"))
            if rules_text:
                context_parts.append(rules_text)
                total_tokens += AIService.estimate_tokens(rules_text)
        
        # 3. Bible Entities
        bible_entities = context_needed.get("bible_entities", [])
        if bible_entities:
            bible_text, bible_sources = ContextManager.load_bible_entities(bible_entities, project_id)
            if bible_text:
                context_parts.append(f"\n📚 BIBLE CONTEXT:\n{bible_text}")
                total_tokens += AIService.estimate_tokens(bible_text)
                sources.extend(bible_sources)
        
        # 4. Files
        files = context_needed.get("files", [])
        cross_project = context_needed.get("cross_project")
        
        if files:
            file_text, file_sources = ContextManager.load_files(files, project_id, cross_project)
            if file_text:
                context_parts.append(f"\n📄 FILE CONTEXT:\n{file_text}")
                total_tokens += AIService.estimate_tokens(file_text)
                sources.extend(file_sources)
        
        # 5. Cross-project context
        if cross_project and not files:
            # Load overview of cross-project
            try:
                projects = supabase.table("stories")\
                    .select("id, title, category")\
                    .eq("user_id", st.session_state.user.id)\
                    .ilike("title", f"%{cross_project}%")\
                    .execute()
                
                if projects.data:
                    target_project = projects.data[0]
                    context_parts.append(f"\n🔗 CROSS-PROJECT: {target_project['title']} ({target_project['category']})")
                    
                    # Load some bible items from cross-project
                    bible_items = supabase.table("story_bible")\
                        .select("entity_name, description")\
                        .eq("story_id", target_project['id'])\
                        .limit(5)\
                        .execute()
                    
                    if bible_items.data:
                        cross_text = "\n".join([
                            f"- {item['entity_name']}: {item['description'][:200]}..."
                            for item in bible_items.data
                        ])
                        context_parts.append(f"Relevant items:\n{cross_text}")
                        total_tokens += AIService.estimate_tokens(cross_text)
            except:
                pass
        
        # 6. Specific requests từ router
        specific_requests = router_result.get("specific_requests", [])
        if specific_requests:
            requests_text = f"\n🎯 SPECIFIC REQUIREMENTS:\n" + "\n".join([f"- {req}" for req in specific_requests])
            context_parts.append(requests_text)
            total_tokens += AIService.estimate_tokens(requests_text)
        
        return "\n".join(context_parts), sources, total_tokens

# ==========================================
# 💰 9. COST MANAGEMENT
# ==========================================
class CostManager:
    """Quản lý chi phí AI"""
    
    @staticmethod
    def get_user_budget(user_id: str) -> Dict:
        """Lấy thông tin budget của user"""
        try:
            res = supabase.table("user_budgets")\
                .select("*")\
                .eq("user_id", user_id)\
                .execute()
            
            if res.data:
                return res.data[0]
            else:
                # Tạo mới nếu chưa có
                default_budget = {
                    "user_id": user_id,
                    "total_credits": 100.0,  # $100 credit mặc định
                    "used_credits": 0.0,
                    "remaining_credits": 100.0,
                    "last_reset_date": datetime.utcnow().date().isoformat()
                }
                
                supabase.table("user_budgets").insert(default_budget).execute()
                return default_budget
        except Exception as e:
            print(f"Error getting budget: {e}")
            return {
                "total_credits": 100.0,
                "used_credits": 0.0,
                "remaining_credits": 100.0
            }
    
    @staticmethod
    def update_budget(user_id: str, cost: float):
        """Cập nhật budget sau khi sử dụng"""
        try:
            # Get current budget
            budget = CostManager.get_user_budget(user_id)
            
            new_used = budget.get("used_credits", 0.0) + cost
            remaining = budget.get("total_credits", 100.0) - new_used
            
            supabase.table("user_budgets")\
                .update({
                    "used_credits": new_used,
                    "remaining_credits": remaining,
                    "updated_at": datetime.utcnow().isoformat()
                })\
                .eq("user_id", user_id)\
                .execute()
            
            return remaining
        except Exception as e:
            print(f"Error updating budget: {e}")
            return None
    
    @staticmethod
    def log_cost(
        user_id: str,
        project_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        request_type: str = "chat"
    ):
        """Ghi log chi phí"""
        try:
            log_entry = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "story_id": project_id,
                "model_name": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "estimated_cost": cost,
                "request_type": request_type,
                "created_at": datetime.utcnow().isoformat()
            }
            
            supabase.table("cost_logs").insert(log_entry).execute()
        except Exception as e:
            print(f"Error logging cost: {e}")
    
    @staticmethod
    def get_cost_history(user_id: str, days: int = 7) -> List[Dict]:
        """Lấy lịch sử chi phí"""
        try:
            since_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
            
            res = supabase.table("cost_logs")\
                .select("*")\
                .eq("user_id", user_id)\
                .gte("created_at", since_date)\
                .order("created_at", desc=True)\
                .execute()
            
            return res.data if res.data else []
        except Exception as e:
            print(f"Error getting cost history: {e}")
            return []

# ==========================================
# 🎯 10. MAIN APPLICATION
# ==========================================
def render_sidebar():
    """Render sidebar với thông tin user và project"""
    with st.sidebar:
        st.image("https://via.placeholder.com/300x80/667eea/ffffff?text=V-Universe+Pro", use_container_width=True)
        
        if 'user' in st.session_state:
            user_email = st.session_state.user.email
            st.markdown(f"### 👤 {user_email.split('@')[0]}")
            
            # User budget info
            budget = CostManager.get_user_budget(st.session_state.user.id)
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric(
                    "💰 Credits",
                    f"${budget.get('remaining_credits', 0):.2f}",
                    delta=f"-${budget.get('used_credits', 0):.2f}"
                )
            with col2:
                st.metric(
                    "📊 Usage",
                    f"{budget.get('used_credits', 0)/budget.get('total_credits', 100)*100:.1f}%",
                    delta="This month"
                )
            
            st.markdown("---")
            
            # Project selection
            st.subheader("📂 Projects")
            
            projects = supabase.table("stories")\
                .select("*")\
                .eq("user_id", st.session_state.user.id)\
                .execute()
            
            proj_map = {p['title']: p for p in projects.data}
            
            selected_proj_name = st.selectbox(
                "Chọn Project",
                ["+ New Project"] + list(proj_map.keys()),
                key="project_selector"
            )
            
            if selected_proj_name == "+ New Project":
                with st.form("new_project_form"):
                    title = st.text_input("Project Name")
                    category = st.selectbox(
                        "Category",
                        PersonaSystem.get_available_personas()
                    )
                    
                    if st.form_submit_button("Create Project", type="primary"):
                        if title:
                            supabase.table("stories").insert({
                                "title": title,
                                "category": category,
                                "user_id": st.session_state.user.id
                            }).execute()
                            st.success("Project created!")
                            time.sleep(1)
                            st.rerun()
                st.stop()
            
            current_proj = proj_map[selected_proj_name]
            proj_id = current_proj['id']
            proj_type = current_proj.get('category', 'Writer')
            
            # Store in session state
            st.session_state['current_project'] = current_proj
            st.session_state['project_id'] = proj_id
            
            # Persona info
            persona = PersonaSystem.get_persona(proj_type)
            st.info(f"{persona['icon']} **{proj_type} Mode** - {persona['role']}")
            
            # AI Model selection
            st.markdown("---")
            st.subheader("🤖 AI Settings")
            
            model_category = st.selectbox(
                "Model Category",
                list(Config.AVAILABLE_MODELS.keys()),
                index=1
            )
            
            available_models = Config.AVAILABLE_MODELS[model_category]
            selected_model = st.selectbox(
                "Select Model",
                available_models,
                index=0
            )
            
            st.session_state['selected_model'] = selected_model
            
            # Temperature slider
            temperature = st.slider(
                "Temperature",
                min_value=0.0,
                max_value=1.0,
                value=persona.get('temperature', 0.7),
                step=0.1,
                help="Higher = more creative, Lower = more focused"
            )
            
            st.session_state['temperature'] = temperature
            
            # Context window
            context_size = st.select_slider(
                "Context Size",
                options=["low", "medium", "high"],
                value="medium",
                help="Amount of context to include"
            )
            
            st.session_state['context_size'] = context_size
            
            st.markdown("---")
            
            # Feedback form
            with st.expander("💬 Feedback & Support"):
                feedback = st.text_area("Your feedback", height=100)
                if st.button("Submit Feedback"):
                    if feedback:
                        try:
                            supabase.table("feedback").insert({
                                "user_id": st.session_state.user.id,
                                "feedback": feedback,
                                "created_at": datetime.utcnow().isoformat()
                            }).execute()
                            st.success("Thank you for your feedback!")
                        except:
                            st.warning("Feedback table not available")
            
            # Logout button
            st.markdown("---")
            if st.button("🚪 Logout", use_container_width=True, type="secondary"):
                # Clear cookies
                cookie_manager.delete("supabase_access_token")
                cookie_manager.delete("supabase_refresh_token")
                
                # Clear session
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                
                st.success("Logged out successfully!")
                time.sleep(1)
                st.rerun()
            
            return proj_id, persona, proj_type
        else:
            st.warning("Please login")
            st.stop()

def render_workstation_tab(project_id, persona):
    """Tab Workstation - Quản lý files và content"""
    st.header("✍️ Workstation")
    
    # File management
    col1, col2 = st.columns([3, 1])
    
    with col1:
        # Load existing files
        files = supabase.table("chapters")\
            .select("chapter_number, title")\
            .eq("story_id", project_id)\
            .order("chapter_number")\
            .execute()
        
        file_options = {}
        for f in files.data:
            display_name = f"📄 #{f['chapter_number']}"
            if f['title']:
                display_name += f": {f['title']}"
            file_options[display_name] = f['chapter_number']
        
        selected_file = st.selectbox(
            "Select File",
            ["+ New File"] + list(file_options.keys())
        )
        
        if selected_file == "+ New File":
            chap_num = len(files.data) + 1
            db_content = ""
            db_title = ""
            db_review = ""
        else:
            chap_num = file_options[selected_file]
            
            # Load file content
            try:
                res = supabase.table("chapters")\
                    .select("content, review_content, title")\
                    .eq("story_id", project_id)\
                    .eq("chapter_number", chap_num)\
                    .execute()
                
                if res.data:
                    db_content = res.data[0].get('content', '')
                    db_review = res.data[0].get('review_content', '')
                    db_title = res.data[0].get('title', '')
                else:
                    db_content = ""
                    db_title = ""
                    db_review = ""
            except:
                db_content = ""
                db_title = ""
                db_review = ""
    
    with col2:
        st.markdown("### 🔧 Tools")
        
        # Quick actions
        if st.button("🚀 AI Review", use_container_width=True):
            if 'current_file_content' in st.session_state and st.session_state.current_file_content:
                st.session_state['review_mode'] = True
                st.rerun()
        
        if st.button("📥 Extract to Bible", use_container_width=True):
            if 'current_file_content' in st.session_state and st.session_state.current_file_content:
                st.session_state['extract_mode'] = True
                st.rerun()
        
        if st.button("💾 Save All", use_container_width=True, type="primary"):
            if 'current_file_content' in st.session_state:
                # Save to database
                supabase.table("chapters").upsert({
                    "story_id": project_id,
                    "chapter_number": chap_num,
                    "title": st.session_state.get('current_file_title', db_title),
                    "content": st.session_state.current_file_content,
                    "review_content": st.session_state.get('current_file_review', db_review)
                }).execute()
                st.success("Saved successfully!")
                time.sleep(1)
                st.rerun()
    
    # Main editor
    st.markdown("---")
    
    col_editor, col_preview = st.columns([2, 1])
    
    with col_editor:
        st.subheader("📝 Editor")
        
        # File title
        file_title = st.text_input(
            "File Title",
            value=db_title,
            key=f"file_title_{chap_num}",
            placeholder="Enter file title..."
        )
        
        st.session_state['current_file_title'] = file_title
        
        # Content editor
        content = st.text_area(
            "Content",
            value=db_content,
            height=500,
            key=f"file_content_{chap_num}",
            placeholder="Start writing here..."
        )
        
        st.session_state['current_file_content'] = content
        st.session_state['current_file_num'] = chap_num
    
    with col_preview:
        st.subheader("🔍 Preview")
        
        if content:
            # Word count
            words = len(content.split())
            chars = len(content)
            
            col_stat1, col_stat2 = st.columns(2)
            with col_stat1:
                st.metric("Words", words)
            with col_stat2:
                st.metric("Characters", chars)
            
            # Preview
            with st.expander("Content Preview", expanded=True):
                st.markdown(content[:1000] + ("..." if len(content) > 1000 else ""))
        
        # Review section
        if db_review or ('current_file_review' in st.session_state and st.session_state.current_file_review):
            st.markdown("---")
            st.subheader("📋 AI Review")
            
            review_content = st.session_state.get('current_file_review', db_review)
            
            with st.expander("View Review", expanded=True):
                st.markdown(review_content)
            
            if st.button("Clear Review"):
                st.session_state['current_file_review'] = ""
                st.rerun()

def render_chat_tab(project_id, persona):
    """Tab Chat - AI Conversation với context thông minh"""
    st.header("💬 Smart Chat")
    
    # Chat controls sidebar
    with st.sidebar:
        st.markdown("### 🎛️ Chat Controls")
        
        # Model info
        model = st.session_state.get('selected_model', 'openai/gpt-3.5-turbo')
        st.info(f"**Model:** {model.split('/')[-1]}")
        
        # Context controls
        st.markdown("#### 🧠 Context")
        
        auto_context = st.toggle(
            "Auto Context",
            value=True,
            help="Automatically include relevant context"
        )
        
        include_rules = st.toggle(
            "Include Rules",
            value=True,
            help="Include project rules in context"
        )
        
        include_history = st.toggle(
            "Chat History",
            value=True,
            help="Include recent chat history"
        )
        
        # Budget warning
        budget = CostManager.get_user_budget(st.session_state.user.id)
        remaining = budget.get('remaining_credits', 0)
        
        if remaining < 1.0:
            st.error(f"⚠️ Low balance: ${remaining:.2f}")
        elif remaining < 10.0:
            st.warning(f"💰 Balance: ${remaining:.2f}")
        else:
            st.success(f"✅ Balance: ${remaining:.2f}")
        
        # Clear chat button
        if st.button("🗑️ Clear Chat", use_container_width=True):
            if 'chat_messages' in st.session_state:
                del st.session_state['chat_messages']
            st.rerun()
    
    # Main chat area
    col_chat, col_info = st.columns([3, 1])
    
    with col_chat:
        # Initialize chat messages
        if 'chat_messages' not in st.session_state:
            st.session_state.chat_messages = []
        
        # Display chat history
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"], avatar=msg.get("avatar", None)):
                st.markdown(msg["content"])
                
                # Show metadata if available
                if "metadata" in msg:
                    with st.expander("📊 Details"):
                        st.json(msg["metadata"], expanded=False)
        
        # Chat input
        if prompt := st.chat_input(f"Ask {persona['icon']} V..."):
            # Add user message
            st.session_state.chat_messages.append({
                "role": "user",
                "content": prompt,
                "avatar": "👤"
            })
            
            # Display user message immediately
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
            
            # Prepare AI response
            with st.chat_message("assistant", avatar=persona['icon']):
                message_placeholder = st.empty()
                full_response = ""
                
                try:
                    # 1. Analyze intent với DeepSeek Router
                    with st.spinner("🔄 Analyzing intent..."):
                        router_result = AIRouter.analyze_intent(
                            prompt,
                            st.session_state.chat_messages[-10:],  # Last 10 messages
                            project_id
                        )
                    
                    # 2. Build context
                    with st.spinner("📚 Gathering context..."):
                        context_text, sources, context_tokens = ContextManager.build_context(
                            router_result,
                            project_id,
                            persona
                        )
                    
                    # 3. Prepare messages for AI
                    messages = []
                    
                    # System message với context
                    system_message = f"""{persona['core_instruction']}

CONTEXT INFORMATION:
{context_text}

INSTRUCTIONS:
- Answer based on the context provided
- If information is not in context, say so
- Be helpful and concise
- Current project context: {st.session_state.get('current_project', {}).get('title', 'Unknown')}
"""
                    
                    messages.append({"role": "system", "content": system_message})
                    
                    # Add chat history (last 5 messages)
                    if include_history and len(st.session_state.chat_messages) > 1:
                        for msg in st.session_state.chat_messages[-6:-1]:  # Exclude current
                            messages.append({
                                "role": msg["role"],
                                "content": msg["content"]
                            })
                    
                    # Add current user message
                    messages.append({"role": "user", "content": prompt})
                    
                    # 4. Call AI
                    with st.spinner("🤖 Thinking..."):
                        model = st.session_state.get('selected_model', 'openai/gpt-3.5-turbo')
                        temperature = st.session_state.get('temperature', 0.7)
                        
                        response = AIService.call_openrouter(
                            messages=messages,
                            model=model,
                            temperature=temperature,
                            max_tokens=persona.get('max_tokens', 1500),
                            stream=True
                        )
                    
                    # 5. Stream response
                    for chunk in response.iter_lines():
                        if chunk:
                            try:
                                chunk_data = json.loads(chunk.decode('utf-8').replace('data: ', ''))
                                
                                if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                                    delta = chunk_data['choices'][0].get('delta', {})
                                    if 'content' in delta:
                                        content = delta['content']
                                        full_response += content
                                        message_placeholder.markdown(full_response + "▌")
                            except:
                                continue
                    
                    message_placeholder.markdown(full_response)
                    
                    # 6. Calculate costs
                    input_tokens = AIService.estimate_tokens(system_message + prompt)
                    output_tokens = AIService.estimate_tokens(full_response)
                    
                    cost = AIService.calculate_cost(input_tokens, output_tokens, model)
                    
                    # 7. Update budget
                    remaining = CostManager.update_budget(st.session_state.user.id, cost)
                    
                    # 8. Log cost
                    CostManager.log_cost(
                        user_id=st.session_state.user.id,
                        project_id=project_id,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost=cost,
                        request_type="chat"
                    )
                    
                    # 9. Add AI response to chat
                    st.session_state.chat_messages.append({
                        "role": "assistant",
                        "content": full_response,
                        "avatar": persona['icon'],
                        "metadata": {
                            "model": model,
                            "cost": f"${cost:.6f}",
                            "tokens": {
                                "input": input_tokens,
                                "output": output_tokens,
                                "total": input_tokens + output_tokens
                            },
                            "context_sources": sources,
                            "router_intent": router_result.get("intent", "chat")
                        }
                    })
                    
                    # 10. Show cost info
                    st.caption(f"💡 Used {input_tokens + output_tokens} tokens (${cost:.6f}) | Remaining: ${remaining:.2f}")
                    
                    # Show context info
                    if sources:
                        with st.expander("📚 Context Sources"):
                            for source in sources:
                                st.write(f"• {source}")
                
                except Exception as e:
                    st.error(f"Error: {str(e)}")
                    st.session_state.chat_messages.append({
                        "role": "assistant",
                        "content": f"Sorry, I encountered an error: {str(e)}",
                        "avatar": "❌"
                    })
    
    with col_info:
        st.markdown("### 🧭 Router Info")
        
        if st.session_state.chat_messages:
            last_msg = st.session_state.chat_messages[-1]
            if last_msg["role"] == "assistant" and "metadata" in last_msg:
                metadata = last_msg["metadata"]
                
                st.metric("Intent", metadata.get("router_intent", "chat"))
                st.metric("Cost", metadata.get("cost", "$0.0000"))
                
                st.markdown("#### 📊 Token Usage")
                tokens = metadata.get("tokens", {})
                st.write(f"Input: {tokens.get('input', 0)}")
                st.write(f"Output: {tokens.get('output', 0)}")
                st.write(f"Total: {tokens.get('total', 0)}")
                
                if metadata.get("context_sources"):
                    st.markdown("#### 📚 Sources")
                    for source in metadata.get("context_sources", [])[:5]:
                        st.write(f"• {source}")

def render_bible_tab(project_id, persona):
    """Tab Bible - Quản lý knowledge base"""
    st.header("📚 Project Bible")
    
    # Search and filters
    col_search, col_filters, col_actions = st.columns([3, 2, 1])
    
    with col_search:
        search_query = st.text_input(
            "🔍 Search Bible",
            placeholder="Search entities, descriptions..."
        )
    
    with col_filters:
        filter_prefix = st.selectbox(
            "Filter by Type",
            ["All", "[RULE]", "[CHARACTER]", "[LOCATION]", "[ITEM]", "[CONCEPT]"],
            index=0
        )
    
    with col_actions:
        st.markdown("###")
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()
    
    # Load bible data
    try:
        query = supabase.table("story_bible")\
            .select("*")\
            .eq("story_id", project_id)\
            .order("created_at", desc=True)
        
        # Apply filters
        if search_query:
            query = query.or_(f"entity_name.ilike.%{search_query}%,description.ilike.%{search_query}%")
        
        if filter_prefix != "All":
            query = query.ilike("entity_name", f"%{filter_prefix}%")
        
        bible_data = query.execute().data
        
    except Exception as e:
        st.error(f"Error loading bible: {e}")
        bible_data = []
    
    # Stats
    col1, col2, col3, col4 = st.columns(4)
    
    total_items = len(bible_data)
    rules_count = len([b for b in bible_data if '[RULE]' in b.get('entity_name', '')])
    characters_count = len([b for b in bible_data if '[CHARACTER]' in b.get('entity_name', '')])
    locations_count = len([b for b in bible_data if '[LOCATION]' in b.get('entity_name', '')])
    
    with col1:
        st.metric("Total Items", total_items)
    with col2:
        st.metric("Rules", rules_count)
    with col3:
        st.metric("Characters", characters_count)
    with col4:
        st.metric("Locations", locations_count)
    
    st.markdown("---")
    
    # Bible management
    tab_view, tab_add, tab_manage = st.tabs(["📖 View", "➕ Add", "⚙️ Manage"])
    
    with tab_view:
        if bible_data:
            # Group by prefix
            grouped_data = {}
            for item in bible_data:
                prefix = item.get('prefix', '')
                if not prefix:
                    # Extract prefix from entity_name
                    if '[RULE]' in item['entity_name']:
                        prefix = '[RULE]'
                    elif '[CHARACTER]' in item['entity_name']:
                        prefix = '[CHARACTER]'
                    elif '[LOCATION]' in item['entity_name']:
                        prefix = '[LOCATION]'
                    else:
                        prefix = '[OTHER]'
                
                if prefix not in grouped_data:
                    grouped_data[prefix] = []
                grouped_data[prefix].append(item)
            
            # Display grouped items
            for prefix, items in grouped_data.items():
                with st.expander(f"{prefix} ({len(items)} items)", expanded=True):
                    for item in items:
                        col_left, col_right = st.columns([4, 1])
                        
                        with col_left:
                            st.markdown(f"**{item['entity_name']}**")
                            st.caption(item.get('description', '')[:200] + ("..." if len(item.get('description', '')) > 200 else ""))
                        
                        with col_right:
                            if st.button("Edit", key=f"edit_{item['id']}"):
                                st.session_state['edit_item'] = item
                                st.rerun()
        
        else:
            st.info("No bible items found. Add some using the 'Add' tab.")
    
    with tab_add:
        st.subheader("Add New Bible Entry")
        
        with st.form("add_bible_form"):
            col_type, col_name = st.columns([1, 3])
            
            with col_type:
                entry_type = st.selectbox(
                    "Type",
                    ["RULE", "CHARACTER", "LOCATION", "ITEM", "CONCEPT", "CUSTOM"]
                )
            
            with col_name:
                if entry_type == "CUSTOM":
                    entity_name = st.text_input("Entity Name")
                    prefix = st.text_input("Custom Prefix", value="[CUSTOM]")
                else:
                    entity_name = st.text_input(f"{entry_type} Name")
                    prefix = f"[{entry_type}]"
            
            description = st.text_area("Description", height=200)
            
            # Advanced options
            with st.expander("Advanced Options"):
                tags = st.text_input("Tags (comma-separated)", value="")
                source_file = st.number_input("Source File Number", min_value=0, value=0)
            
            if st.form_submit_button("Add to Bible", type="primary"):
                if entity_name and description:
                    # Create embedding
                    embedding_text = f"{prefix} {entity_name}: {description}"
                    embedding = AIService.get_embedding(embedding_text)
                    
                    if embedding:
                        # Insert into database
                        supabase.table("story_bible").insert({
                            "story_id": project_id,
                            "entity_name": f"{prefix} {entity_name}",
                            "description": description,
                            "embedding": embedding,
                            "prefix": prefix,
                            "source_chapter": source_file,
                            "tags": [tag.strip() for tag in tags.split(",")] if tags else []
                        }).execute()
                        
                        st.success("✅ Entry added successfully!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Failed to create embedding")
                else:
                    st.warning("Please fill in all required fields")
    
    with tab_manage:
        st.subheader("Manage Bible Entries")
        
        if bible_data:
            # Selection for bulk operations
            item_options = {f"{item['entity_name']}": item['id'] for item in bible_data}
            selected_items = st.multiselect(
                "Select items for bulk operations",
                list(item_options.keys())
            )
            
            if selected_items:
                col_merge, col_delete, col_export = st.columns(3)
                
                with col_merge:
                    if st.button("🧬 Merge Selected", use_container_width=True):
                        st.info("Merge feature coming soon!")
                
                with col_delete:
                    if st.button("🗑️ Delete Selected", use_container_width=True, type="secondary"):
                        # Delete selected items
                        selected_ids = [item_options[item] for item in selected_items]
                        supabase.table("story_bible").delete().in_("id", selected_ids).execute()
                        st.success("Items deleted!")
                        time.sleep(1)
                        st.rerun()
                
                with col_export:
                    if st.button("📤 Export Selected", use_container_width=True):
                        # Export to JSON
                        selected_ids = [item_options[item] for item in selected_items]
                        export_data = [item for item in bible_data if item['id'] in selected_ids]
                        
                        st.download_button(
                            label="Download JSON",
                            data=json.dumps(export_data, indent=2, ensure_ascii=False),
                            file_name=f"bible_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                            mime="application/json"
                        )
            
            # Edit mode
            if 'edit_item' in st.session_state:
                st.markdown("---")
                st.subheader("Edit Entry")
                
                item = st.session_state['edit_item']
                
                with st.form("edit_bible_form"):
                    new_name = st.text_input("Entity Name", value=item['entity_name'])
                    new_desc = st.text_area("Description", value=item.get('description', ''), height=150)
                    
                    col_save, col_cancel = st.columns(2)
                    
                    with col_save:
                        if st.form_submit_button("💾 Save Changes", use_container_width=True):
                            # Update entry
                            supabase.table("story_bible")\
                                .update({
                                    "entity_name": new_name,
                                    "description": new_desc
                                })\
                                .eq("id", item['id'])\
                                .execute()
                            
                            del st.session_state['edit_item']
                            st.success("Entry updated!")
                            time.sleep(1)
                            st.rerun()
                    
                    with col_cancel:
                        if st.form_submit_button("❌ Cancel", use_container_width=True):
                            del st.session_state['edit_item']
                            st.rerun()
        
        else:
            st.info("No items to manage")

def render_cost_tab():
    """Tab quản lý chi phí"""
    st.header("💰 Cost Management")
    
    # Get user budget
    user_id = st.session_state.user.id
    budget = CostManager.get_user_budget(user_id)
    
    # Budget overview
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            "Total Credits",
            f"${budget.get('total_credits', 0):.2f}"
        )
    
    with col2:
        st.metric(
            "Used Credits",
            f"${budget.get('used_credits', 0):.2f}",
            delta=f"-${budget.get('used_credits', 0):.2f}"
        )
    
    with col3:
        remaining = budget.get('remaining_credits', 0)
        st.metric(
            "Remaining",
            f"${remaining:.2f}"
        )
    
    with col4:
        usage_percent = (budget.get('used_credits', 0) / budget.get('total_credits', 100)) * 100
        st.metric(
            "Usage",
            f"{usage_percent:.1f}%"
        )
    
    # Progress bar
    st.progress(min(usage_percent / 100, 1.0))
    
    st.markdown("---")
    
    # Cost history
    st.subheader("📊 Cost History")
    
    days_filter = st.select_slider(
        "Show history for",
        options=[1, 3, 7, 14, 30],
        value=7
    )
    
    cost_history = CostManager.get_cost_history(user_id, days_filter)
    
    if cost_history:
        # Convert to DataFrame
        df = pd.DataFrame(cost_history)
        
        # Format datetime
        df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d %H:%M')
        
        # Display table
        st.dataframe(
            df[['created_at', 'model_name', 'total_tokens', 'estimated_cost', 'request_type']],
            use_container_width=True,
            hide_index=True
        )
        
        # Charts
        col_chart1, col_chart2 = st.columns(2)
        
        with col_chart1:
            st.subheader("Cost by Model")
            model_costs = df.groupby('model_name')['estimated_cost'].sum().reset_index()
            st.bar_chart(model_costs.set_index('model_name'))
        
        with col_chart2:
            st.subheader("Daily Usage")
            df['date'] = pd.to_datetime(df['created_at']).dt.date
            daily_costs = df.groupby('date')['estimated_cost'].sum().reset_index()
            st.line_chart(daily_costs.set_index('date'))
        
        # Export option
        st.download_button(
            label="📥 Export as CSV",
            data=df.to_csv(index=False).encode('utf-8'),
            file_name=f"cost_history_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )
    
    else:
        st.info("No cost history available for this period.")
    
    # Add credits section (simplified)
    st.markdown("---")
    st.subheader("💳 Add Credits")
    
    with st.form("add_credits_form"):
        amount = st.number_input(
            "Amount to add ($)",
            min_value=10.0,
            max_value=1000.0,
            value=50.0,
            step=10.0
        )
        
        # In a real app, you would integrate with a payment provider here
        if st.form_submit_button("Proceed to Payment", type="primary"):
            st.info("🔄 Payment integration would be implemented here")
            st.info(f"Simulating addition of ${amount} to your account")
            
            # Update budget
            new_total = budget.get('total_credits', 0) + amount
            supabase.table("user_budgets")\
                .update({
                    "total_credits": new_total,
                    "remaining_credits": new_total - budget.get('used_credits', 0)
                })\
                .eq("user_id", user_id)\
                .execute()
            
            st.success(f"${amount} added to your account!")
            time.sleep(2)
            st.rerun()

def render_settings_tab():
    """Tab cài đặt"""
    st.header("⚙️ Settings")
    
    tab_persona, tab_api, tab_ui = st.tabs(["Persona", "API", "UI"])
    
    with tab_persona:
        st.subheader("🎭 Persona Settings")
        
        current_persona = st.session_state.get('current_project', {}).get('category', 'Writer')
        available_personas = PersonaSystem.get_available_personas()
        
        selected_persona = st.selectbox(
            "Current Persona",
            available_personas,
            index=available_personas.index(current_persona) if current_persona in available_personas else 0
        )
        
        if selected_persona != current_persona:
            if st.button("Update Persona"):
                # Update project persona
                supabase.table("stories")\
                    .update({"category": selected_persona})\
                    .eq("id", st.session_state.get('project_id'))\
                    .execute()
                
                st.success(f"Persona updated to {selected_persona}!")
                time.sleep(1)
                st.rerun()
        
        # Persona customization
        st.markdown("---")
        st.subheader("Customize Persona")
        
        persona = PersonaSystem.get_persona(selected_persona)
        
        custom_instruction = st.text_area(
            "Custom Instruction",
            value=persona['core_instruction'],
            height=200
        )
        
        if st.button("Save Custom Instructions"):
            st.info("Custom persona saving would be implemented here")
    
    with tab_api:
        st.subheader("🔑 API Settings")
        
        # API Key display (masked)
        api_key = st.text_input(
            "OpenRouter API Key",
            value="•" * 40 if Config.OPENROUTER_API_KEY else "",
            type="password",
            disabled=True
        )
        
        st.caption("API key is managed via Streamlit secrets")
        
        # Model preferences
        st.markdown("---")
        st.subheader("Model Preferences")
        
        default_category = st.selectbox(
            "Default Model Category",
            list(Config.AVAILABLE_MODELS.keys()),
            index=1
        )
        
        if st.button("Save Preferences"):
            st.success("Preferences saved!")
    
    with tab_ui:
        st.subheader("🎨 UI Settings")
        
        # Theme selection
        theme = st.selectbox(
            "Theme",
            ["Light", "Dark", "Auto"]
        )
        
        # Chat preferences
        chat_font = st.selectbox(
            "Chat Font Size",
            ["Small", "Medium", "Large"]
        )
        
        # Auto-refresh
        auto_refresh = st.checkbox(
            "Auto-refresh content",
            value=True
        )
        
        if st.button("Apply UI Settings"):
            st.success("UI settings applied!")
            time.sleep(1)
            st.rerun()

# ==========================================
# 🚀 11. MAIN APP FLOW
# ==========================================
def main():
    """Hàm chính của ứng dụng"""
    
    # Check login
    if 'user' not in st.session_state:
        render_login_form()
        return
    
    # Render sidebar và lấy project info
    project_id, persona, project_type = render_sidebar()
    
    # Main content area
    st.title(f"{persona['icon']} {st.session_state.get('current_project', {}).get('title', 'Untitled Project')}")
    st.caption(f"{project_type} Mode • {persona['role']}")
    
    # Tabs chính
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "✍️ Workstation",
        "💬 Smart Chat",
        "📚 Project Bible",
        "💰 Cost Management",
        "⚙️ Settings"
    ])
    
    with tab1:
        render_workstation_tab(project_id, persona)
    
    with tab2:
        render_chat_tab(project_id, persona)
    
    with tab3:
        render_bible_tab(project_id, persona)
    
    with tab4:
        render_cost_tab()
    
    with tab5:
        render_settings_tab()

if __name__ == "__main__":
    main()
