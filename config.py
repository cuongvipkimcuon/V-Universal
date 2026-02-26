# config.py - Cấu hình hệ thống, session, và cost
import streamlit as st
import time
from datetime import datetime
from openai import OpenAI
from supabase import create_client
import extra_streamlit_components as stx


# ==========================================
# 🔧 CẤU HÌNH HỆ THỐNG
# ==========================================
class Config:
    """Lớp quản lý cấu hình hệ thống"""

    # OpenRouter API Configuration
    OPENROUTER_API_KEY = st.secrets.get("openrouter", {}).get("API_KEY", "")
    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    # Supabase Configuration
    SUPABASE_URL = st.secrets.get("supabase", {}).get("SUPABASE_URL", "")
    SUPABASE_KEY = st.secrets.get("supabase", {}).get("SUPABASE_KEY", "")

    # Available Models (Đầy đủ các model phổ biến)
    AVAILABLE_MODELS = {
        "🚀 High Performance": [
            "anthropic/claude-opus-4.5",
            "anthropic/claude-sonnet-4.5",
            "google/gemini-3-pro-preview"
        ],
        "⚡ Fast & Balanced": [
            "anthropic/claude-haiku-4.5",
            "google/gemini-3-flash-preview",
            "mistralai/devstral-2512"
        ],
        "💰 Cost Effective": [
            "deepseek/deepseek-chat-v3.1",
            "deepseek/deepseek-v3.2",
            "qwen/qwen3-vl-32b-instruct",
            "meta-llama/llama-4-maverick",
            "google/gemini-2.5-flash",
            "anthropic/claude-3.5-haiku"
        ],
        "🔬 Specialized": [
            "cohere/command-a",
            "perplexity/sonar",
            "nousresearch/hermes-4-405b",
            "meta-llama/llama-3.2-11b-vision-instruct"
        ]
    }

    # Model Costs (USD per 1M tokens)
    MODEL_COSTS = {
        "openai/gpt-5.2": {"input": 1.75, "output": 14.00},
        "anthropic/claude-opus-4.5": {"input": 5.00, "output": 25.00},
        "anthropic/claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
        "anthropic/claude-haiku-4.5": {"input": 1.00, "output": 5.00},
        "anthropic/claude-3.5-haiku": {"input": 0.80, "output": 4.00},
        "google/gemini-3-pro-preview": {"input": 2.00, "output": 12.00},
        "google/gemini-3-flash-preview": {"input": 0.5, "output": 3.00},
        "google/gemini-2.5-flash": {"input": 0.3, "output": 2.50},
        "deepseek/deepseek-chat-v3.1": {"input": 0.15, "output": 0.75},
        "deepseek/deepseek-v3.2": {"input": 0.25, "output": 0.38},
        "qwen/qwen3-vl-32b-instruct": {"input": 0.50, "output": 1.50},
        "mistralai/devstral-2512": {"input": 0.05, "output": 0.22},
        "meta-llama/llama-4-maverick": {"input": 0.15, "output": 0.60},
        "cohere/command-a": {"input": 2.50, "output": 10.00},
        "perplexity/sonar": {"input": 1.00, "output": 1.00},
        "nousresearch/hermes-4-405b": {"input": 1.00, "output": 3.00},
        "meta-llama/llama-3.2-11b-vision-instruct": {"input": 0.049, "output": 0.049},
    }

    # Default settings
    DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
    # Model mặc định cho công cụ (Router, Unified extract, Data Analyze...) — ưu tiên throughput qua provider.sort trong request
    DEFAULT_TOOL_MODEL = "deepseek/deepseek-chat-v3.1"
    EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
    ROUTER_MODEL = "deepseek/deepseek-chat-v3.1"
    # Model rẻ cho auto-summary / metadata (Workstation)
    METADATA_MODEL = "google/gemini-2.5-flash"

    # Context size (token limit) cho Chat - Settings "Context Size"
    CONTEXT_SIZE_TOKENS = {"low": 15000, "medium": 60000, "high": 123000, "max": None}
    # Token tối đa cho một lô Data Analyze (Bible/Chunk...) — tránh lỗi gói tối đa / lag
    DATA_BATCH_MAX_TOKENS = 50000
    # Độ trễ tối thiểu (giây) giữa hai lệnh gọi API khi xử lý theo khoảng chương — tránh quá tải API (5–10s)
    DATA_OPERATION_DELAY_SEC = 7

    # Giới hạn số lần gọi LLM "chính" mỗi turn (intent, planner, draft, numerical). Verification/check không tính. 0 = không giới hạn.
    DEFAULT_MAX_LLM_CALLS_PER_TURN = 5

    @classmethod
    def get_max_llm_calls_per_turn(cls) -> int:
        """Số lần gọi LLM tối đa mỗi turn (chỉ tính intent, planner, draft, numerical; không tính verification/check). 0 = không giới hạn."""
        try:
            services = init_services()
            if services:
                r = services["supabase"].table("settings").select("value").eq("key", "max_llm_calls_per_turn").execute()
                if r.data and r.data[0] is not None:
                    v = r.data[0].get("value")
                    if v is not None:
                        n = int(v) if isinstance(v, (int, float)) else int(str(v).strip() or "0")
                        return max(0, n)
        except Exception:
            pass
        return cls.DEFAULT_MAX_LLM_CALLS_PER_TURN

    @classmethod
    def get_prefixes(cls) -> list:
        """Lấy danh sách prefix dạng [X] từ DB: ưu tiên bảng bible_prefix_config (get_prefix_setup), rồi settings. Không set cứng; không có dữ liệu thì trả về []."""
        try:
            setup = cls.get_prefix_setup()
            if setup:
                return [f"[{p.get('prefix_key', '')}]" for p in setup if p.get('prefix_key')]
        except Exception:
            pass
        try:
            services = init_services()
            if services:
                res = services["supabase"].table("settings").select("value").eq("key", "bible_prefixes").execute()
                if res.data and len(res.data) > 0:
                    val = res.data[0].get("value")
                    if isinstance(val, list) and len(val) > 0:
                        return [str(p) for p in val]
        except Exception:
            pass
        return []

    # Prefix đặc biệt: không lưu trong DB, chỉ dùng khi tạo Bible mà không gán được prefix từ DB (giống RULE, CHAT là hệ thống).
    PREFIX_SPECIAL_SYSTEM = ("RULE", "CHAT", "OTHER")

    @classmethod
    def get_valid_prefix_keys(cls) -> set:
        """Tập prefix_key hợp lệ từ DB (get_prefix_setup), chuẩn hóa HOA. Dùng để lọc inferred_prefixes từ Router."""
        setup = cls.get_prefix_setup()
        return {str(p.get("prefix_key", "")).strip().upper().replace(" ", "_") for p in setup if p.get("prefix_key")}

    @classmethod
    def get_allowed_prefix_keys_for_extract(cls) -> list:
        """Danh sách prefix_key dùng cho Extract (từ DB, loại các key hệ thống RULE/CHAT). OTHER thêm vào prompt khi không khớp."""
        setup = cls.get_prefix_setup()
        exclude = [k for k in (cls.PREFIX_SPECIAL_SYSTEM or ()) if k != "OTHER"]
        return [str(p.get("prefix_key", "")).strip() for p in setup if p.get("prefix_key") and str(p.get("prefix_key", "")).upper() not in exclude]

    @classmethod
    def resolve_prefix_for_bible(cls, ai_type: str) -> str:
        """Từ type do AI trả về (extract/import), trả về prefix_key hợp lệ hoặc OTHER nếu không khớp. Dùng khi tạo Bible."""
        if not ai_type or not str(ai_type).strip():
            return "OTHER"
        allowed = cls.get_allowed_prefix_keys_for_extract()
        normalized = (ai_type or "").strip().upper().replace(" ", "_").replace("[", "").replace("]", "")
        for pk in allowed:
            if (pk or "").strip().upper() == normalized:
                return (pk or "").strip()
        return "OTHER"

    @classmethod
    def get_prefix_setup(cls) -> list:
        """Lấy bảng Setup Tiền tố từ DB: list of {prefix_key, description, sort_order}. Dùng cho Router và Extract. Không set cứng; lỗi hoặc không có dữ liệu trả về []."""
        try:
            services = init_services()
            if not services:
                return []
            try:
                r = services["supabase"].table("entity_setup").select("prefix_key, description, sort_order").order("sort_order").execute()
            except Exception:
                r = services["supabase"].table("bible_prefix_config").select("prefix_key, description, sort_order").order("sort_order").execute()
            if r.data and len(r.data) > 0:
                return [{"prefix_key": x.get("prefix_key", ""), "description": x.get("description", ""), "sort_order": x.get("sort_order", 0)} for x in r.data]
        except Exception:
            pass
        return []

    @classmethod
    def _normalize_for_match(cls, s: str) -> str:
        """Chuẩn hóa chuỗi để so khớp: lower, thay _ bằng space, bỏ khoảng thừa."""
        if not s:
            return ""
        return (s or "").strip().lower().replace("_", " ").replace("  ", " ").strip()

    @classmethod
    def map_extract_type_to_prefix(cls, item_type: str, item_description: str = "") -> str:
        """Ánh xạ type/description từ Extract sang prefix trong bảng; loại trừ RULE, CHAT; không khớp trả về OTHER."""
        try:
            setup = cls.get_prefix_setup()
            exclude = [k for k in (cls.PREFIX_SPECIAL_SYSTEM or ()) if k != "OTHER"]
            allowed = [p for p in setup if p.get("prefix_key") and str(p.get("prefix_key", "")).upper() not in exclude]
            if not allowed:
                return "OTHER"
            key_candidate = (item_type or "").strip().upper().replace(" ", "_")
            key_normalized = cls._normalize_for_match(key_candidate.replace("_", " "))
            combined = f"{(item_type or '')} {(item_description or '')}".strip().lower()
            for p in allowed:
                pk = (p.get("prefix_key") or "").strip().upper()
                if key_candidate == pk:
                    return pk
            for p in allowed:
                pk = (p.get("prefix_key") or "").strip().upper()
                desc_lower = (p.get("description") or "").lower()
                pk_normalized = cls._normalize_for_match(pk.replace("_", " "))
                if key_normalized and (key_normalized in desc_lower or desc_lower in key_normalized):
                    return pk
                if key_candidate and (key_candidate in desc_lower or pk_normalized in key_normalized):
                    return pk
                if desc_lower and desc_lower in combined:
                    return pk
                if key_candidate and pk in key_candidate:
                    return pk
                if desc_lower:
                    words = desc_lower.split()[:4]
                    if words and " ".join(words) in combined:
                        return pk
            return "OTHER"
        except Exception:
            return "OTHER"

    # Cache settings
    CACHE_TTL_HOURS = 24
    MAX_CONTEXT_TOKENS = {
        "low": 15000,
        "medium": 30000,
        "high": 60000,
        "max": 120000
    }

    @classmethod
    def validate(cls):
        """Validate configuration"""
        errors = []
        if not cls.OPENROUTER_API_KEY:
            errors.append("❌ OpenRouter API key not found in secrets")
        if not cls.SUPABASE_URL or not cls.SUPABASE_KEY:
            errors.append("❌ Supabase credentials not found in secrets")

        if errors:
            for error in errors:
                st.error(error)
            return False
        return True


# ==========================================
# 🔗 KHỞI TẠO DỊCH VỤ
# ==========================================
@st.cache_resource
def init_services():
    """Khởi tạo kết nối đến các dịch vụ"""
    try:
        openai_client = OpenAI(
            base_url=Config.OPENROUTER_BASE_URL,
            api_key=Config.OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": "https://v-universe.streamlit.app",
                "X-Title": "V-Universe AI Hub"
            }
        )
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        supabase.table("stories").select("count", count="exact").limit(1).execute()
        return {
            "openai": openai_client,
            "supabase": supabase
        }
    except Exception as e:
        st.error(f"❌ Failed to initialize services: {str(e)}")
        return None


# ==========================================
# 🍪 QUẢN LÝ PHIÊN & AUTH
# ==========================================
class SessionManager:
    """Quản lý session và authentication"""

    def __init__(self):
        self.cookie_manager = stx.CookieManager(key="v_universe_cookies")

    def initialize_session(self):
        """Khởi tạo session state. V8.9: Không ghi đè 'user' nếu đã có (tránh F5 mất đăng nhập sau khi restore từ cookie)."""
        if 'initialized' not in st.session_state:
            defaults = {
                'initialized': True,
                'current_project': None,
                'project_id': None,
                'chat_messages': [],
                'selected_model': Config.DEFAULT_MODEL,
                'default_ai_model': getattr(Config, 'DEFAULT_TOOL_MODEL', Config.ROUTER_MODEL),
                'temperature': 0.7,
                'context_size': 'medium',
                'persona': 'Writer',
                'current_file_content': '',
                'current_file_review': '',
                'current_file_num': 1,
                'chat_cutoff': "1970-01-01",
                'strict_mode': False,
                'enable_history': True,
                'chat_crystallized_summary': None,
                'chat_crystallized_topic': None,
                'pending_new_rule': None,
                'rule_analysis': None,
                'edit_rule_manual': None,
                'current_arc_id': None,
            }
            for k, v in defaults.items():
                if k not in st.session_state:
                    st.session_state[k] = v
            if 'user' not in st.session_state:
                st.session_state['user'] = None

    def check_login(self):
        """Kiểm tra và quản lý đăng nhập. V8.9: Ưu tiên khôi phục từ cookie trước khi init session để tránh F5 mất đăng nhập."""
        if st.session_state.get('logging_out'):
            self.initialize_session()
            return False

        if 'user' in st.session_state and st.session_state.user:
            self.initialize_session()
            return True

        access_token = self.cookie_manager.get("supabase_access_token")
        refresh_token = self.cookie_manager.get("supabase_refresh_token")

        if access_token and refresh_token:
            try:
                services = init_services()
                if services:
                    session = services['supabase'].auth.set_session(access_token, refresh_token)
                    if session and session.user:
                        st.session_state.user = session.user
                        self.initialize_session()
                        return True
            except Exception:
                try:
                    # Thử refresh token khi access_token hết hạn
                    if refresh_token:
                        services2 = init_services()
                        if services2:
                            session = services2['supabase'].auth.refresh_session(refresh_token)
                            if session and session.user:
                                st.session_state.user = session.user
                                self.cookie_manager.set("supabase_access_token", session.session.access_token, key="refresh_access")
                                self.cookie_manager.set("supabase_refresh_token", session.session.refresh_token, key="refresh_refresh")
                                self.initialize_session()
                                return True
                except Exception:
                    pass
                self.cookie_manager.delete("supabase_access_token", key="del_access_check_login")
                self.cookie_manager.delete("supabase_refresh_token", key="del_refresh_check_login")

        self.initialize_session()
        return False

    def render_login_form(self):
        """Hiển thị form đăng nhập/đăng ký - Ver 7.0"""
        st.markdown("""
        <style>
        .login-hero { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding: 2rem 0; width: 100%; }
        .login-hero h1 { font-size: 2.4rem; font-weight: 700; letter-spacing: -0.02em; background: linear-gradient(135deg, #5b21b6 0%, #7c3aed 50%, #6366f1 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; margin: 0 auto; text-align: center; }
        .login-hero p { margin: 0.5rem 0 0 0; text-align: center; color: #64748b; font-size: 1rem; }
        .login-form-center { margin: 0 auto; max-width: 420px; padding: 0 1rem; }
        .feature-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 2rem 0; }
        .feature-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.06); transition: box-shadow 0.2s, border-color 0.2s; }
        .feature-card:hover { box-shadow: 0 4px 12px rgba(124,58,237,0.12); border-color: #c4b5fd; }
        .feature-card .icon { font-size: 2.5rem; margin-bottom: 12px; }
        .feature-card h4 { color: #2d3748; font-size: 1rem; margin: 0 0 8px 0; }
        .feature-card p { color: #64748b; font-size: 0.85rem; margin: 0; }
        </style>
        """, unsafe_allow_html=True)

        st.markdown("""
        <div class="login-hero">
            <h1>V-Universe AI Hub Pro</h1>
            <p>Ver 7.0 — Your Intelligent Writing & Development Assistant</p>
        </div>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns([1, 3, 1])

        with col2:
            st.markdown('<div class="login-form-center">', unsafe_allow_html=True)
            with st.container():
                st.markdown("<div class='card'>", unsafe_allow_html=True)

                tab_login, tab_register = st.tabs(["🔐 Đăng nhập", "📝 Đăng ký"])

                with tab_login:
                    st.subheader("Welcome Back")

                    email = st.text_input("📧 Email", key="login_email")
                    password = st.text_input("🔑 Password", type="password", key="login_pass")

                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1:
                        if st.button("Login", type="primary", use_container_width=True):
                            if email and password:
                                try:
                                    services = init_services()
                                    res = services['supabase'].auth.sign_in_with_password({
                                        "email": email,
                                        "password": password
                                    })

                                    st.session_state.user = res.user

                                    self.cookie_manager.set(
                                        "supabase_access_token",
                                        res.session.access_token,
                                        key="login_access"
                                    )
                                    self.cookie_manager.set(
                                        "supabase_refresh_token",
                                        res.session.refresh_token,
                                        key="login_refresh"
                                    )

                                    st.success("✅ Login successful!")
                                    time.sleep(1.5)
                                except Exception as e:
                                    st.error(f"Login failed: {str(e)}")

                    with col_btn2:
                        if st.button("Forgot Password?", use_container_width=True):
                            if email:
                                try:
                                    services = init_services()
                                    services['supabase'].auth.reset_password_email(email)
                                    st.success("📧 Password reset email sent!")
                                except Exception:
                                    st.error("Failed to send reset email")

                with tab_register:
                    st.subheader("Create Account")

                    reg_email = st.text_input("📧 Email", key="reg_email")
                    reg_pass = st.text_input("🔑 Password", type="password", key="reg_pass")
                    reg_pass_confirm = st.text_input("🔑 Confirm Password", type="password", key="reg_pass_confirm")
                    reg_invite_code = st.text_input("🎟️ Mã khách mời", key="reg_invite_code")

                    if st.button("Register", type="secondary", use_container_width=True):
                        if not reg_email or not reg_pass or not reg_pass_confirm or not reg_invite_code:
                            st.error("Please fill all fields correctly")
                        elif reg_pass != reg_pass_confirm:
                            st.error("Password and confirmation do not match")
                        elif reg_invite_code.strip() != "CUONGVIPKIMCUONG":
                            st.error("Mã khách mời không hợp lệ. Vui lòng liên hệ admin.")
                        else:
                            try:
                                services = init_services()
                                res = services['supabase'].auth.sign_up({
                                    "email": reg_email,
                                    "password": reg_pass
                                })
                                if res.user:
                                    st.success("✅ Registration successful! Please check your email.")
                                else:
                                    st.warning("⚠️ Please check your confirmation email.")
                            except Exception as e:
                                st.error(f"Registration failed: {str(e)}")

                st.markdown("</div>", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

                st.markdown("""
                <div style='margin-top: 40px;'>
                    <h4 style='text-align: center; color: #2d3748; margin-bottom: 24px;'>✨ Ver 7.0 — Tính năng nổi bật</h4>
                    <div class='feature-grid'>
                        <div class='feature-card'><div class='icon'>🤖</div><h4>Multi-AI</h4><p>20+ models OpenRouter</p></div>
                        <div class='feature-card'><div class='icon'>🎯</div><h4>Semantic Intent</h4><p>Khớp câu hỏi, bỏ Router</p></div>
                        <div class='feature-card'><div class='icon'>📚</div><h4>Knowledge</h4><p>Bible, Rules, Chunking</p></div>
                        <div class='feature-card'><div class='icon'>📐</div><h4>Arc</h4><p>Phân vùng timeline</p></div>
                        <div class='feature-card'><div class='icon'>🧮</div><h4>Python Executor</h4><p>Tính toán số liệu</p></div>
                        <div class='feature-card'><div class='icon'>🧠</div><h4>Rule Mining</h4><p>Học từ chat</p></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

        st.stop()


# ==========================================
# 💰 COST MANAGEMENT
# ==========================================
class CostManager:
    """Quản lý chi phí AI"""

    @staticmethod
    def get_user_budget(user_id: str) -> dict:
        """Lấy thông tin budget của user"""
        try:
            services = init_services()
            supabase = services['supabase']

            res = supabase.table("user_budgets") \
                .select("*") \
                .eq("user_id", user_id) \
                .execute()

            if res.data:
                return res.data[0]
            else:
                default_budget = {
                    "user_id": user_id,
                    "total_credits": 100.0,
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
            services = init_services()
            supabase = services['supabase']

            budget = CostManager.get_user_budget(user_id)

            new_used = budget.get("used_credits", 0.0) + cost
            remaining = budget.get("total_credits", 100.0) - new_used

            supabase.table("user_budgets") \
                .update({
                    "used_credits": new_used,
                    "remaining_credits": remaining,
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("user_id", user_id) \
                .execute()

            return remaining
        except Exception as e:
            print(f"Error updating budget: {e}")
            return None
