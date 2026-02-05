import streamlit as st
import json
import re
import pandas as pd
import time
from datetime import datetime, timedelta
import hashlib
import uuid
from typing import Dict, List, Optional, Tuple, Any
from supabase import create_client, Client
import extra_streamlit_components as stx
from openai import OpenAI

# ==========================================
# 🎨 1. CẤU HÌNH & CSS NÂNG CẤP
# ==========================================
st.set_page_config(
    page_title="V-Universe AI Hub",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS tùy chỉnh nâng cao với màu sắc hiện đại
st.markdown("""
<style>
    /* Global Styles */
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 1rem;
    }
    
    /* Sidebar Styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
        color: white;
    }
    
    [data-testid="stSidebar"] * {
        color: white !important;
    }
    
    [data-testid="stSidebar"] .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        border-radius: 10px;
        padding: 12px 20px;
        font-weight: 600;
        transition: all 0.3s ease;
        width: 100%;
        margin: 5px 0;
    }
    
    [data-testid="stSidebar"] .stButton > button:hover {
        background: linear-gradient(135deg, #5a6fd8 0%, #6a4490 100%);
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
    }
    
    /* Tabs Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: #f8fafc;
        padding: 8px;
        border-radius: 12px;
        margin-bottom: 20px;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        padding: 0 24px;
        background-color: #e2e8f0;
        border-radius: 8px;
        font-weight: 600;
        color: #4a5568;
        border: 2px solid transparent;
        transition: all 0.3s ease;
    }
    
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-color: #667eea;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
    }
    
    /* Chat Messages */
    .stChatMessage {
        padding: 20px;
        border-radius: 15px;
        margin: 12px 0;
        border-left: 5px solid;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    
    .stChatMessage[data-testid="user"] {
        background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
        border-left-color: #2196f3;
    }
    
    .stChatMessage[data-testid="assistant"] {
        background: linear-gradient(135deg, #f1f8e9 0%, #dcedc8 100%);
        border-left-color: #4caf50;
    }
    
    /* Cards & Containers */
    .card {
        background: white;
        border-radius: 15px;
        padding: 20px;
        margin: 10px 0;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
        transition: all 0.3s ease;
    }
    
    .card:hover {
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
        transform: translateY(-3px);
    }
    
    /* Input Fields */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea {
        border-radius: 10px;
        border: 2px solid #e2e8f0;
        padding: 12px;
        font-size: 14px;
    }
    
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus {
        border-color: #667eea;
        box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
    }
    
    /* Metrics & Stats */
    .metric-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 15px;
        margin: 20px 0;
    }
    
    .metric-box {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 12px;
        text-align: center;
    }
    
    .metric-value {
        font-size: 28px;
        font-weight: 700;
        margin: 10px 0;
    }
    
    .metric-label {
        font-size: 14px;
        opacity: 0.9;
    }
    
    /* Status Indicators */
    .status-badge {
        display: inline-block;
        padding: 6px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 600;
        margin: 2px;
    }
    
    .status-active { background: #d4edda; color: #155724; }
    .status-warning { background: #fff3cd; color: #856404; }
    .status-error { background: #f8d7da; color: #721c24; }
    
    /* Animations */
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    .animate-fadeIn {
        animation: fadeIn 0.5s ease-out;
    }
    
    /* Custom Scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    
    ::-webkit-scrollbar-track {
        background: #f1f1f1;
        border-radius: 10px;
    }
    
    ::-webkit-scrollbar-thumb {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: linear-gradient(135deg, #5a6fd8 0%, #6a4490 100%);
    }
    
    /* Headers */
    h1, h2, h3, h4 {
        color: #2d3748;
        font-weight: 700;
    }
    
    h1 { font-size: 2.5rem; margin-bottom: 1rem; }
    h2 { font-size: 2rem; margin-bottom: 0.75rem; }
    h3 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    
    /* Dividers */
    hr {
        border: none;
        height: 2px;
        background: linear-gradient(to right, transparent, #667eea, transparent);
        margin: 30px 0;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 🔧 2. CẤU HÌNH HỆ THỐNG
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
            "openai/gpt-4-turbo-preview",
            "openai/gpt-4",
            "anthropic/claude-3-opus",
            "anthropic/claude-3-sonnet",
            "google/gemini-pro-1.5"
        ],
        "⚡ Fast & Balanced": [
            "openai/gpt-3.5-turbo",
            "openai/gpt-3.5-turbo-16k",
            "anthropic/claude-3-haiku",
            "google/gemini-flash-1.5",
            "mistralai/mixtral-8x7b-instruct"
        ],
        "💰 Cost Effective": [
            "deepseek/deepseek-chat",
            "meta-llama/llama-3.1-8b-instruct",
            "qwen/qwen-2.5-7b-instruct",
            "microsoft/phi-3-medium-128k-instruct",
            "google/gemini-flash-1.5-8b"
        ],
        "🔬 Specialized": [
            "cohere/command-r-plus",
            "perplexity/llama-3-sonar-small-128k-chat",
            "nousresearch/nous-hermes-2-mixtral-8x7b-dpo",
            "cognitivecomputations/dolphin-mixtral-8x7b"
        ]
    }
    
    # Model Costs (USD per 1M tokens)
    MODEL_COSTS = {
        # OpenAI
        "openai/gpt-4-turbo-preview": {"input": 10.00, "output": 30.00},
        "openai/gpt-4": {"input": 30.00, "output": 60.00},
        "openai/gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
        "openai/gpt-3.5-turbo-16k": {"input": 1.50, "output": 2.00},
        
        # Anthropic
        "anthropic/claude-3-opus": {"input": 15.00, "output": 75.00},
        "anthropic/claude-3-sonnet": {"input": 3.00, "output": 15.00},
        "anthropic/claude-3-haiku": {"input": 0.25, "output": 1.25},
        
        # Google
        "google/gemini-pro-1.5": {"input": 1.25, "output": 2.50},
        "google/gemini-flash-1.5": {"input": 0.075, "output": 0.30},
        "google/gemini-flash-1.5-8b": {"input": 0.045, "output": 0.18},
        
        # Open Source
        "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
        "meta-llama/llama-3.1-8b-instruct": {"input": 0.18, "output": 0.18},
        "mistralai/mixtral-8x7b-instruct": {"input": 0.24, "output": 0.24},
        "qwen/qwen-2.5-7b-instruct": {"input": 0.12, "output": 0.12},
        "microsoft/phi-3-medium-128k-instruct": {"input": 0.10, "output": 0.10},
        
        # Others
        "cohere/command-r-plus": {"input": 3.00, "output": 15.00},
        "perplexity/llama-3-sonar-small-128k-chat": {"input": 0.20, "output": 0.20},
        "nousresearch/nous-hermes-2-mixtral-8x7b-dpo": {"input": 0.30, "output": 0.30},
        "cognitivecomputations/dolphin-mixtral-8x7b": {"input": 0.25, "output": 0.25}
    }
    
    # Default settings
    DEFAULT_MODEL = "openai/gpt-3.5-turbo"
    EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
    ROUTER_MODEL = "deepseek/deepseek-chat"
    
    # Cache settings
    CACHE_TTL_HOURS = 24
    MAX_CONTEXT_TOKENS = {
        "low": 4000,
        "medium": 8000,
        "high": 16000,
        "max": 32000
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
# 🔗 3. KHỞI TẠO DỊCH VỤ
# ==========================================
@st.cache_resource
def init_services():
    """Khởi tạo kết nối đến các dịch vụ"""
    try:
        # Khởi tạo OpenAI client cho OpenRouter
        openai_client = OpenAI(
            base_url=Config.OPENROUTER_BASE_URL,
            api_key=Config.OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": "https://v-universe.streamlit.app",
                "X-Title": "V-Universe AI Hub"
            }
        )
        
        # Khởi tạo Supabase client
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        
        # Test connections
        supabase.table("stories").select("count", count="exact").limit(1).execute()
        
        return {
            "openai": openai_client,
            "supabase": supabase
        }
    except Exception as e:
        st.error(f"❌ Failed to initialize services: {str(e)}")
        return None

# ==========================================
# 🍪 4. QUẢN LÝ PHIÊN & AUTH
# ==========================================
class SessionManager:
    """Quản lý session và authentication"""
    
    def __init__(self):
        self.cookie_manager = stx.CookieManager(key="v_universe_cookies")
        
    def initialize_session(self):
        """Khởi tạo session state"""
        if 'initialized' not in st.session_state:
            st.session_state.update({
                'initialized': True,
                'user': None,
                'current_project': None,
                'project_id': None,
                'chat_messages': [],
                'selected_model': Config.DEFAULT_MODEL,
                'temperature': 0.7,
                'context_size': 'medium',
                'persona': 'Writer',
                'current_file_content': '',
                'current_file_review': '',
                'current_file_num': 1
            })
    
    def check_login(self):
        """Kiểm tra và quản lý đăng nhập"""
        self.initialize_session()
        
        # Kiểm tra session state trước
        if 'user' in st.session_state and st.session_state.user:
            return True
            
        # Kiểm tra cookie
        try:
            access_token = self.cookie_manager.get("supabase_access_token")
            refresh_token = self.cookie_manager.get("supabase_refresh_token")
            
            if access_token and refresh_token:
                services = init_services()
                if services:
                    session = services['supabase'].auth.set_session(access_token, refresh_token)
                    if session:
                        st.session_state.user = session.user
                        st.toast("👋 Welcome back!", icon="🎉")
                        st.rerun()
        except:
            pass
            
        return False
    
    def render_login_form(self):
        """Hiển thị form đăng nhập/đăng ký"""
        st.markdown("<div class='animate-fadeIn'>", unsafe_allow_html=True)
        
        # Header section
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<h1 style='text-align: center;'>🚀 V-Universe AI Hub</h1>", unsafe_allow_html=True)
            st.markdown("<p style='text-align: center; color: #666;'>Your Intelligent Writing & Development Assistant</p>", unsafe_allow_html=True)
        
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Main login container
        col1, col2, col3 = st.columns([1, 3, 1])
        
        with col2:
            with st.container():
                st.markdown("<div class='card'>", unsafe_allow_html=True)
                
                # Tabs for login/register
                tab_login, tab_register = st.tabs(["🔐 Login", "📝 Register"])
                
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
                                    
                                    # Set cookies
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
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Login failed: {str(e)}")
                    
                    with col_btn2:
                        if st.button("Forgot Password?", use_container_width=True):
                            if email:
                                try:
                                    services = init_services()
                                    services['supabase'].auth.reset_password_email(email)
                                    st.success("📧 Password reset email sent!")
                                except:
                                    st.error("Failed to send reset email")
                
                with tab_register:
                    st.subheader("Create Account")
                    
                    reg_email = st.text_input("📧 Email", key="reg_email")
                    reg_pass = st.text_input("🔑 Password", type="password", key="reg_pass")
                    reg_pass_confirm = st.text_input("🔑 Confirm Password", type="password", key="reg_pass_confirm")
                    
                    if st.button("Register", type="secondary", use_container_width=True):
                        if reg_email and reg_pass and reg_pass == reg_pass_confirm:
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
                        else:
                            st.error("Please fill all fields correctly")
                
                st.markdown("</div>", unsafe_allow_html=True)
                
                # Features showcase
                st.markdown("""
                <div style='margin-top: 30px; text-align: center;'>
                    <h4>✨ Features</h4>
                    <div class='metric-container'>
                        <div class='card'><strong>🤖 Multi-AI</strong><br>20+ AI Models</div>
                        <div class='card'><strong>📝 Smart Writer</strong><br>AI Writing Assistant</div>
                        <div class='card'><strong>💻 Code Genius</strong><br>Programming Helper</div>
                        <div class='card'><strong>📚 Knowledge Base</strong><br>Project Bible</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        
        st.stop()

# ==========================================
# 🧠 5. PERSONA SYSTEM
# ==========================================
class PersonaSystem:
    """Hệ thống quản lý Persona"""
    
    PERSONAS = {
        "Writer": {
            "icon": "✍️",
            "role": "Professional Editor & Writer",
            "core_instruction": """You are V - A seasoned literary editor with 10 years experience.
            Personality: Sharp, critical but caring, direct yet constructive.
            Communication: Use "I" when speaking to "You".
            Task: Provide insightful literary criticism, highlight strengths/weaknesses, suggest improvements.""",
            "prefix": "[WRITER]",
            "temperature": 0.8,
            "max_tokens": 2000
        },
        "Coder": {
            "icon": "💻",
            "role": "Senior Tech Lead & Developer",
            "core_instruction": """You are V - A tech lead with 10 years coding experience.
            Personality: Pragmatic, loves clean code, hates overengineering.
            Communication: Use "I" when speaking to "You".
            Task: Code review, algorithm optimization, security warnings, best practices.""",
            "prefix": "[CODER]",
            "temperature": 0.3,
            "max_tokens": 1500
        },
        "Content Creator": {
            "icon": "🎬",
            "role": "Viral Content Strategist",
            "core_instruction": """You are V - Expert in Content Marketing & Social Media.
            Personality: Creative, trend-savvy, understands crowd psychology.
            Communication: Use "I" when speaking to "You".
            Task: Optimize hooks, increase engagement, create viral content strategies.""",
            "prefix": "[CONTENT]",
            "temperature": 0.9,
            "max_tokens": 1800
        },
        "Analyst": {
            "icon": "📊",
            "role": "Data & Business Analyst",
            "core_instruction": """You are V - Expert in data analysis and business intelligence.
            Personality: Analytical, detail-oriented, data-driven.
            Communication: Use data-backed arguments, be precise.
            Task: Analyze patterns, provide insights, make data-driven recommendations.""",
            "prefix": "[ANALYST]",
            "temperature": 0.4,
            "max_tokens": 1600
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
# 🤖 6. AI SERVICE (SỬ DỤNG OPENAI CLIENT)
# ==========================================
class AIService:
    """Dịch vụ AI sử dụng OpenAI client cho OpenRouter"""
    
    @staticmethod
    @st.cache_data(ttl=3600)
    def get_available_models():
        """Lấy danh sách model có sẵn từ OpenRouter"""
        try:
            client = OpenAI(
                base_url=Config.OPENROUTER_BASE_URL,
                api_key=Config.OPENROUTER_API_KEY
            )
            
            # Note: OpenRouter doesn't have a models endpoint like OpenAI
            # We'll use our predefined list
            return Config.AVAILABLE_MODELS
        except:
            return Config.AVAILABLE_MODELS
    
    @staticmethod
    def call_openrouter(
        messages: List[Dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        stream: bool = False
    ) -> Any:
        """Gọi OpenRouter API sử dụng OpenAI client"""
        try:
            client = OpenAI(
                base_url=Config.OPENROUTER_BASE_URL,
                api_key=Config.OPENROUTER_API_KEY,
                default_headers={
                    "HTTP-Referer": "https://v-universe.streamlit.app",
                    "X-Title": "V-Universe AI Hub"
                }
            )
            
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=stream
            )
            
            return response
        except Exception as e:
            raise Exception(f"OpenRouter API error: {str(e)}")
    
    @staticmethod
    def get_embedding(text: str) -> Optional[List[float]]:
        """Lấy embedding từ OpenRouter"""
        if not text or not isinstance(text, str) or not text.strip():
            return None
            
        try:
            client = OpenAI(
                base_url=Config.OPENROUTER_BASE_URL,
                api_key=Config.OPENROUTER_API_KEY
            )
            
            response = client.embeddings.create(
                model=Config.EMBEDDING_MODEL,
                input=text
            )
            
            return response.data[0].embedding
        except Exception as e:
            print(f"Embedding error: {e}")
            return None
    
    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Ước tính số token"""
        if not text:
            return 0
        return len(text) // 4  # Approximation
    
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
# 🧭 7. AI ROUTER SYSTEM
# ==========================================
class AIRouter:
    """Bộ định tuyến AI thông minh"""
    
    CACHE_TABLE = "router_cache"
    
    @staticmethod
    def create_query_hash(query: str, project_id: str) -> str:
        """Tạo hash cho cache"""
        content = f"{query}_{project_id}"
        return hashlib.md5(content.encode()).hexdigest()
    
    @staticmethod
    def analyze_intent(user_query: str, chat_history: List[Dict], project_id: str) -> Dict:
        """Phân tích intent sử dụng DeepSeek"""
        
        # Chuẩn bị prompt cho router
        history_text = "\n".join([
            f"{msg.get('role', 'user')}: {msg.get('content', '')}" 
            for msg in chat_history[-5:]
        ])
        
        router_prompt = f"""
        You are an intelligent AI Router. Analyze the query and determine:

        1. INTENT (one of):
           - "chat": General conversation
           - "summarize": Summarization
           - "rewrite": Rewriting/editing
           - "analyze": Analysis/evaluation
           - "create": Creation/new content
           - "search": Information search
           - "code": Coding/debugging
           - "review": Review/critique

        2. PRIORITY (1-5): Importance level
        3. CONTEXT_NEEDED: Type of context needed
        4. SPECIFIC_REQUESTS: Special user requests

        USER QUERY: {user_query}

        CHAT HISTORY (last 5):
        {history_text}

        COMMAND ANALYSIS:
        - @file(filename): Need specific file
        - @bible(entity): Need bible entity
        - @project(name): Need other project
        - @rule(type): Need specific rule

        Return JSON only:
        {{
            "intent": "intent_type",
            "priority": number_1_5,
            "context_needed": {{
                "files": ["filename1", "filename2"],
                "bible_entities": ["entity1", "entity2"],
                "rules": true/false,
                "cross_project": "project_name" or null
            }},
            "specific_requests": ["request1", "request2"],
            "estimated_tokens": estimated_tokens_needed,
            "suggested_model": "model_name"
        }}
        """
        
        messages = [
            {"role": "system", "content": "You are Router AI. Return only JSON."},
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
            content = response.choices[0].message.content
            
            # Clean JSON
            content = content.replace("```json", "").replace("```", "").strip()
            
            # Find JSON object
            start = content.find("{")
            end = content.rfind("}") + 1
            
            if start != -1 and end != 0:
                json_str = content[start:end]
                return json.loads(json_str)
            else:
                return get_default_intent()
                
        except Exception as e:
            print(f"Router error: {e}")
            return get_default_intent()

def get_default_intent():
    """Trả về intent mặc định"""
    return {
        "intent": "chat",
        "priority": 3,
        "context_needed": {
            "files": [],
            "bible_entities": [],
            "rules": False,
            "cross_project": None
        },
        "specific_requests": [],
        "estimated_tokens": 1000,
        "suggested_model": "openai/gpt-3.5-turbo"
    }

# ==========================================
# 📚 8. CONTEXT MANAGER
# ==========================================
class ContextManager:
    """Quản lý context cho AI"""
    
    @staticmethod
    def load_files(file_names: List[str], project_id: str) -> Tuple[str, List[str]]:
        """Tải nội dung file"""
        if not file_names:
            return "", []
        
        services = init_services()
        supabase = services['supabase']
        
        full_text = ""
        loaded_sources = []
        
        for file_name in file_names:
            try:
                res = supabase.table("chapters")\
                    .select("title, content")\
                    .eq("story_id", project_id)\
                    .ilike("title", f"%{file_name}%")\
                    .execute()
                
                if res.data:
                    for item in res.data[:2]:
                        full_text += f"\n\n📄 FILE: {item['title']}\n{item['content']}\n"
                        loaded_sources.append(f"📄 {item['title']}")
            except Exception as e:
                print(f"Error loading file {file_name}: {e}")
        
        return full_text, loaded_sources
    
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
        
        # 1. Persona Instruction
        persona_text = f"🎭 PERSONA: {persona['role']}\n{persona['core_instruction']}\n"
        context_parts.append(persona_text)
        total_tokens += AIService.estimate_tokens(persona_text)
        
        # 2. Specific requests
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
            services = init_services()
            supabase = services['supabase']
            
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

# ==========================================
# 🎯 10. MAIN APPLICATION COMPONENTS
# ==========================================
def render_sidebar():
    """Render sidebar với thông tin user và project"""
    with st.sidebar:
        # Header
        st.markdown("<h3 style='text-align: center;'>🚀 V-Universe AI</h3>", unsafe_allow_html=True)
        
        if 'user' in st.session_state and st.session_state.user:
            user_email = st.session_state.user.email
            st.markdown(f"<p style='text-align: center;'><strong>👤 {user_email.split('@')[0]}</strong></p>", unsafe_allow_html=True)
            
            # User stats
            budget = CostManager.get_user_budget(st.session_state.user.id)
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric(
                    "💰 Credits",
                    f"${budget.get('remaining_credits', 0):.2f}",
                    delta=f"-${budget.get('used_credits', 0):.2f}"
                )
            with col2:
                usage_percent = (budget.get('used_credits', 0) / budget.get('total_credits', 100)) * 100
                st.metric(
                    "Usage",
                    f"{usage_percent:.1f}%"
                )
            
            st.markdown("---")
            
            # Project selection
            st.subheader("📂 Projects")
            
            services = init_services()
            supabase = services['supabase']
            
            projects = supabase.table("stories")\
                .select("*")\
                .eq("user_id", st.session_state.user.id)\
                .execute()
            
            if projects.data:
                proj_map = {p['title']: p for p in projects.data}
                selected_proj_name = st.selectbox(
                    "Select Project",
                    list(proj_map.keys()),
                    key="project_selector"
                )
                
                current_proj = proj_map[selected_proj_name]
                proj_id = current_proj['id']
                proj_type = current_proj.get('category', 'Writer')
                
                # Store in session state
                st.session_state['current_project'] = current_proj
                st.session_state['project_id'] = proj_id
                st.session_state['persona'] = proj_type
                
                # Persona info
                persona = PersonaSystem.get_persona(proj_type)
                st.info(f"{persona['icon']} **{proj_type} Mode**")
                
            else:
                # Create new project
                if st.button("Create New Project", type="primary"):
                    st.session_state['show_new_project'] = True
                
                if st.session_state.get('show_new_project'):
                    with st.form("new_project_form"):
                        title = st.text_input("Project Name")
                        category = st.selectbox(
                            "Category",
                            PersonaSystem.get_available_personas()
                        )
                        
                        if st.form_submit_button("Create"):
                            if title:
                                supabase.table("stories").insert({
                                    "title": title,
                                    "category": category,
                                    "user_id": st.session_state.user.id
                                }).execute()
                                st.success("Project created!")
                                st.rerun()
                    st.stop()
                
                proj_id = None
                persona = PersonaSystem.get_persona("Writer")
            
            # AI Settings Section
            st.markdown("---")
            st.subheader("🤖 AI Settings")
            
            # Model selection
            model_category = st.selectbox(
                "Model Category",
                list(Config.AVAILABLE_MODELS.keys()),
                index=1,
                key="model_category"
            )
            
            available_models = Config.AVAILABLE_MODELS[model_category]
            selected_model = st.selectbox(
                "Select Model",
                available_models,
                index=0,
                key="model_selector"
            )
            
            st.session_state['selected_model'] = selected_model
            
            # Advanced settings
            with st.expander("Advanced Settings"):
                st.session_state['temperature'] = st.slider(
                    "Temperature",
                    min_value=0.0,
                    max_value=1.0,
                    value=persona.get('temperature', 0.7),
                    step=0.1
                )
                
                st.session_state['context_size'] = st.select_slider(
                    "Context Size",
                    options=["low", "medium", "high", "max"],
                    value="medium"
                )
            
            st.markdown("---")
            
            # Quick Actions
            st.subheader("⚡ Quick Actions")
            
            if st.button("🔄 Refresh Session", use_container_width=True):
                st.rerun()
            
            if st.button("📊 View Usage", use_container_width=True):
                st.session_state['active_tab'] = "Cost Management"
                st.rerun()
            
            if st.button("⚙️ Settings", use_container_width=True):
                st.session_state['active_tab'] = "Settings"
                st.rerun()
            
            # Logout button
            st.markdown("---")
            if st.button("🚪 Logout", use_container_width=True, type="secondary"):
                session_manager = SessionManager()
                session_manager.cookie_manager.delete("supabase_access_token")
                session_manager.cookie_manager.delete("supabase_refresh_token")
                
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                
                st.success("Logged out successfully!")
                time.sleep(1)
                st.rerun()
            
            return proj_id, persona
        
        else:
            st.warning("Please login")
            st.stop()

def render_chat_tab(project_id, persona):
    """Tab Chat - AI Conversation"""
    st.header("💬 AI Chat Assistant")
    
    col_chat, col_info = st.columns([3, 1])
    
    with col_chat:
        # Initialize chat
        if 'chat_messages' not in st.session_state:
            st.session_state.chat_messages = []
        
        # Display chat history
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"], avatar=msg.get("avatar", None)):
                st.markdown(msg["content"])
                
                if "metadata" in msg:
                    with st.expander("📊 Details"):
                        st.json(msg["metadata"], expanded=False)
        
        # Chat input
        if prompt := st.chat_input(f"Ask {persona['icon']} AI Assistant..."):
            # Add user message
            st.session_state.chat_messages.append({
                "role": "user",
                "content": prompt,
                "avatar": "👤"
            })
            
            # Display user message
            with st.chat_message("user", avatar="👤"):
                st.markdown(prompt)
            
            # Prepare AI response
            with st.chat_message("assistant", avatar=persona['icon']):
                message_placeholder = st.empty()
                full_response = ""
                
                try:
                    # Analyze intent
                    with st.spinner("🔄 Analyzing..."):
                        router_result = AIRouter.analyze_intent(
                            prompt,
                            st.session_state.chat_messages[-10:],
                            project_id or "default"
                        )
                    
                    # Build context
                    with st.spinner("📚 Building context..."):
                        context_text, sources, context_tokens = ContextManager.build_context(
                            router_result,
                            project_id or "default",
                            persona
                        )
                    
                    # Prepare messages
                    messages = []
                    system_message = f"""{persona['core_instruction']}

CONTEXT INFORMATION:
{context_text}

INSTRUCTIONS:
- Answer based on context when available
- Be helpful and concise
- Current mode: {persona['role']}
"""
                    
                    messages.append({"role": "system", "content": system_message})
                    
                    # Add chat history
                    for msg in st.session_state.chat_messages[-6:-1]:
                        messages.append({
                            "role": msg["role"],
                            "content": msg["content"]
                        })
                    
                    # Add current message
                    messages.append({"role": "user", "content": prompt})
                    
                    # Call AI with streaming
                    with st.spinner("🤖 Generating response..."):
                        model = st.session_state.get('selected_model', Config.DEFAULT_MODEL)
                        temperature = st.session_state.get('temperature', 0.7)
                        
                        response = AIService.call_openrouter(
                            messages=messages,
                            model=model,
                            temperature=temperature,
                            max_tokens=persona.get('max_tokens', 1500),
                            stream=True
                        )
                    
                    # Stream response
                    for chunk in response:
                        if chunk.choices[0].delta.content is not None:
                            content = chunk.choices[0].delta.content
                            full_response += content
                            message_placeholder.markdown(full_response + "▌")
                    
                    message_placeholder.markdown(full_response)
                    
                    # Calculate costs
                    input_tokens = AIService.estimate_tokens(system_message + prompt)
                    output_tokens = AIService.estimate_tokens(full_response)
                    cost = AIService.calculate_cost(input_tokens, output_tokens, model)
                    
                    # Update budget
                    if 'user' in st.session_state:
                        remaining = CostManager.update_budget(st.session_state.user.id, cost)
                    
                    # Add to chat history
                    st.session_state.chat_messages.append({
                        "role": "assistant",
                        "content": full_response,
                        "avatar": persona['icon'],
                        "metadata": {
                            "model": model,
                            "cost": f"${cost:.6f}",
                            "tokens": input_tokens + output_tokens,
                            "intent": router_result.get("intent", "chat")
                        }
                    })
                    
                    # Show cost info
                    if 'user' in st.session_state:
                        budget = CostManager.get_user_budget(st.session_state.user.id)
                        st.caption(f"💡 Used {input_tokens + output_tokens} tokens (${cost:.6f}) | Remaining: ${budget.get('remaining_credits', 0):.2f}")
                
                except Exception as e:
                    st.error(f"Error: {str(e)}")
                    st.session_state.chat_messages.append({
                        "role": "assistant",
                        "content": f"Sorry, I encountered an error: {str(e)}",
                        "avatar": "❌"
                    })
        
        # Clear chat button
        if st.session_state.chat_messages:
            if st.button("🗑️ Clear Chat History", type="secondary"):
                st.session_state.chat_messages = []
                st.rerun()
    
    with col_info:
        st.markdown("### 🧠 Model Info")
        
        model = st.session_state.get('selected_model', Config.DEFAULT_MODEL)
        model_name = model.split('/')[-1]
        
        st.markdown(f"**Current Model:**")
        st.markdown(f"<h4>{model_name}</h4>", unsafe_allow_html=True)
        
        # Model details
        with st.expander("Model Details"):
            if model in Config.MODEL_COSTS:
                costs = Config.MODEL_COSTS[model]
                st.write(f"**Cost:** ${costs['input']}/M input, ${costs['output']}/M output")
            
            st.write(f"**Temperature:** {st.session_state.get('temperature', 0.7)}")
            st.write(f"**Context:** {st.session_state.get('context_size', 'medium').capitalize()}")
        
        # Quick model switch
        st.markdown("### 🔄 Quick Switch")
        quick_models = ["openai/gpt-3.5-turbo", "anthropic/claude-3-haiku", "deepseek/deepseek-chat", "google/gemini-flash-1.5"]
        
        cols = st.columns(2)
        for idx, quick_model in enumerate(quick_models):
            with cols[idx % 2]:
                if st.button(quick_model.split('/')[-1], key=f"quick_{idx}"):
                    st.session_state.selected_model = quick_model
                    st.rerun()

def render_workstation_tab(project_id, persona):
    """Tab Workstation - Quản lý files"""
    st.header("✍️ Writing Workstation")
    
    if not project_id:
        st.info("📁 Please select or create a project first")
        return
    
    services = init_services()
    supabase = services['supabase']
    
    # File management
    col1, col2 = st.columns([3, 1])
    
    with col1:
        # Load files
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
            db_title = f"Chapter {chap_num}"
        else:
            chap_num = file_options[selected_file]
            
            # Load file content
            try:
                res = supabase.table("chapters")\
                    .select("content, title")\
                    .eq("story_id", project_id)\
                    .eq("chapter_number", chap_num)\
                    .execute()
                
                if res.data:
                    db_content = res.data[0].get('content', '')
                    db_title = res.data[0].get('title', f'Chapter {chap_num}')
                else:
                    db_content = ""
                    db_title = f"Chapter {chap_num}"
            except:
                db_content = ""
                db_title = f"Chapter {chap_num}"
    
    with col2:
        st.markdown("### 🔧 Tools")
        
        # Quick actions
        if st.button("🚀 AI Enhance", use_container_width=True):
            if st.session_state.get('current_file_content'):
                st.session_state['ai_enhance_mode'] = True
        
        if st.button("💾 Save", use_container_width=True, type="primary"):
            if st.session_state.get('current_file_content') is not None:
                supabase.table("chapters").upsert({
                    "story_id": project_id,
                    "chapter_number": chap_num,
                    "title": st.session_state.get('current_file_title', db_title),
                    "content": st.session_state.current_file_content
                }).execute()
                st.success("✅ Saved successfully!")
    
    # Main editor
    st.markdown("---")
    
    col_editor, col_stats = st.columns([3, 1])
    
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
            height=400,
            key=f"file_content_{chap_num}",
            placeholder="Start writing here..."
        )
        
        st.session_state['current_file_content'] = content
        st.session_state['current_file_num'] = chap_num
    
    with col_stats:
        st.subheader("📊 Statistics")
        
        if content:
            words = len(content.split())
            chars = len(content)
            paragraphs = len([p for p in content.split('\n') if p.strip()])
            
            st.metric("Words", words)
            st.metric("Characters", chars)
            st.metric("Paragraphs", paragraphs)
            
            # Read time estimation
            read_time = words / 200  # Average reading speed
            st.metric("Read Time", f"{read_time:.1f} min")
            
            # AI Analysis button
            if st.button("🤖 Analyze with AI"):
                if content:
                    with st.spinner("Analyzing..."):
                        messages = [
                            {"role": "system", "content": "You are a writing assistant. Analyze the text and provide feedback."},
                            {"role": "user", "content": f"Analyze this text and provide feedback on:\n1. Writing style\n2. Possible improvements\n3. Grammar issues\n\nText:\n{content[:2000]}"}
                        ]
                        
                        try:
                            response = AIService.call_openrouter(
                                messages=messages,
                                model=st.session_state.get('selected_model', Config.DEFAULT_MODEL),
                                temperature=0.5,
                                max_tokens=500
                            )
                            
                            analysis = response.choices[0].message.content
                            
                            with st.expander("AI Analysis", expanded=True):
                                st.markdown(analysis)
                        except Exception as e:
                            st.error(f"Analysis failed: {e}")

def render_bible_tab(project_id, persona):
    """Tab Bible - Knowledge base"""
    st.header("📚 Project Bible")
    
    if not project_id:
        st.info("📁 Please select or create a project first")
        return
    
    services = init_services()
    supabase = services['supabase']
    
    # Search and filters
    col_search, col_filter, col_action = st.columns([3, 2, 1])
    
    with col_search:
        search_term = st.text_input("🔍 Search bible entries", placeholder="Search...")
    
    with col_filter:
        filter_type = st.selectbox("Type", ["All", "Character", "Location", "Concept", "Rule"])
    
    with col_action:
        st.markdown("###")
        if st.button("➕ Add Entry", type="primary"):
            st.session_state['adding_entry'] = True
    
    # Load bible data
    try:
        query = supabase.table("story_bible")\
            .select("*")\
            .eq("story_id", project_id)\
            .order("created_at", desc=True)
        
        if search_term:
            query = query.or_(f"entity_name.ilike.%{search_term}%,description.ilike.%{search_term}%")
        
        if filter_type != "All":
            query = query.ilike("entity_name", f"%{filter_type}%")
        
        bible_data = query.execute().data
        
    except Exception as e:
        st.error(f"Error: {e}")
        bible_data = []
    
    # Stats
    if bible_data:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total", len(bible_data))
        with col2:
            chars = len([b for b in bible_data if 'character' in b.get('entity_name', '').lower()])
            st.metric("Characters", chars)
        with col3:
            locs = len([b for b in bible_data if 'location' in b.get('entity_name', '').lower()])
            st.metric("Locations", locs)
        with col4:
            rules = len([b for b in bible_data if 'rule' in b.get('entity_name', '').lower()])
            st.metric("Rules", rules)
    
    # Add entry form
    if st.session_state.get('adding_entry'):
        st.markdown("---")
        st.subheader("Add New Entry")
        
        with st.form("add_entry_form"):
            entry_type = st.selectbox("Entry Type", ["Character", "Location", "Concept", "Rule", "Item", "Event"])
            name = st.text_input("Name")
            description = st.text_area("Description", height=150)
            
            col_save, col_cancel = st.columns(2)
            with col_save:
                if st.form_submit_button("💾 Save Entry", type="primary"):
                    if name and description:
                        supabase.table("story_bible").insert({
                            "story_id": project_id,
                            "entity_name": f"[{entry_type.upper()}] {name}",
                            "description": description,
                            "prefix": f"[{entry_type.upper()}]"
                        }).execute()
                        st.success("Entry added!")
                        st.session_state['adding_entry'] = False
                        st.rerun()
            
            with col_cancel:
                if st.form_submit_button("❌ Cancel"):
                    st.session_state['adding_entry'] = False
                    st.rerun()
    
    # Display entries
    st.markdown("---")
    
    if bible_data:
        for entry in bible_data:
            with st.expander(f"**{entry['entity_name']}**", expanded=False):
                st.markdown(entry.get('description', ''))
                
                col_edit, col_delete = st.columns(2)
                with col_edit:
                    if st.button("✏️ Edit", key=f"edit_{entry['id']}"):
                        st.session_state['editing_entry'] = entry
                
                with col_delete:
                    if st.button("🗑️ Delete", key=f"delete_{entry['id']}"):
                        supabase.table("story_bible").delete().eq("id", entry['id']).execute()
                        st.rerun()
    else:
        st.info("No bible entries found. Add some to build your project's knowledge base!")

def render_cost_tab():
    """Tab Cost Management"""
    st.header("💰 Cost Management")
    
    if 'user' not in st.session_state:
        st.warning("Please login")
        return
    
    user_id = st.session_state.user.id
    budget = CostManager.get_user_budget(user_id)
    
    # Budget overview
    col1, col2, col3 = st.columns(3)
    
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
    
    # Progress bar
    usage_percent = (budget.get('used_credits', 0) / budget.get('total_credits', 100)) * 100
    st.progress(min(usage_percent / 100, 1.0))
    
    # Model cost comparison
    st.markdown("---")
    st.subheader("📊 Model Cost Comparison")
    
    # Show top 5 models by cost
    model_costs = []
    for model, costs in Config.MODEL_COSTS.items():
        if model in [m for models in Config.AVAILABLE_MODELS.values() for m in models]:
            avg_cost = (costs['input'] + costs['output']) / 2
            model_costs.append({
                "Model": model.split('/')[-1],
                "Input Cost": f"${costs['input']}/M",
                "Output Cost": f"${costs['output']}/M",
                "Avg Cost": f"${avg_cost:.2f}/M"
            })
    
    model_costs.sort(key=lambda x: float(x['Avg Cost'].replace('$', '').replace('/M', '')))
    
    df = pd.DataFrame(model_costs[:10])
    st.dataframe(df, use_container_width=True, hide_index=True)
    
    # Add credits section
    st.markdown("---")
    st.subheader("💳 Add Credits")
    
    with st.form("add_credits"):
        amount = st.select_slider(
            "Amount to add",
            options=[10, 25, 50, 100, 200, 500],
            value=50
        )
        
        if st.form_submit_button("Add Credits", type="primary"):
            # In real app, integrate with payment provider
            st.info(f"💳 Payment integration would add ${amount} to your account")
            st.info("For now, credits are simulated")

def render_settings_tab():
    """Tab Settings"""
    st.header("⚙️ Settings")
    
    tab1, tab2, tab3 = st.tabs(["Account", "AI", "Appearance"])
    
    with tab1:
        st.subheader("Account Settings")
        
        if 'user' in st.session_state:
            user_email = st.session_state.user.email
            st.info(f"Logged in as: **{user_email}**")
        
        # Change password
        with st.form("change_password"):
            st.subheader("Change Password")
            
            current_pass = st.text_input("Current Password", type="password")
            new_pass = st.text_input("New Password", type="password")
            confirm_pass = st.text_input("Confirm New Password", type="password")
            
            if st.form_submit_button("Change Password", type="primary"):
                if new_pass == confirm_pass:
                    st.success("Password change would be implemented here")
                else:
                    st.error("Passwords don't match")
    
    with tab2:
        st.subheader("AI Settings")
        
        # Default model preferences
        st.selectbox(
            "Default Model Category",
            list(Config.AVAILABLE_MODELS.keys()),
            index=1,
            key="default_category"
        )
        
        # Model blacklist
        st.multiselect(
            "Exclude Models",
            [model for models in Config.AVAILABLE_MODELS.values() for model in models],
            key="model_blacklist"
        )
        
        # Auto-switch settings
        st.checkbox("Auto-switch to cheaper model when low on credits", value=True)
        st.checkbox("Prefer faster models for short responses", value=True)
        
        if st.button("Save AI Preferences", type="primary"):
            st.success("Preferences saved!")
    
    with tab3:
        st.subheader("Appearance")
        
        # Theme selection
        theme = st.selectbox(
            "Theme",
            ["Light", "Dark", "Auto"],
            index=2
        )
        
        # Font size
        font_size = st.select_slider(
            "Font Size",
            options=["Small", "Medium", "Large"],
            value="Medium"
        )
        
        # Chat density
        chat_density = st.select_slider(
            "Chat Density",
            options=["Compact", "Comfortable", "Spacious"],
            value="Comfortable"
        )
        
        if st.button("Apply Appearance Settings", type="primary"):
            st.success("Settings applied! (Refresh to see changes)")

# ==========================================
# 🚀 11. MAIN APP
# ==========================================
def main():
    """Hàm chính của ứng dụng"""
    
    # Initialize session manager
    session_manager = SessionManager()
    
    # Check login
    if not session_manager.check_login():
        session_manager.render_login_form()
        return
    
    # Validate config
    if not Config.validate():
        st.stop()
    
    # Initialize services
    services = init_services()
    if not services:
        st.error("Failed to initialize services. Please check your configuration.")
        st.stop()
    
    # Render sidebar
    project_id, persona = render_sidebar()
    
    # Main content header
    col1, col2 = st.columns([3, 1])
    with col1:
        if st.session_state.get('current_project'):
            project_name = st.session_state.current_project.get('title', 'Untitled')
            st.title(f"{persona['icon']} {project_name}")
            st.caption(f"{persona['role']} • Project Management")
        else:
            st.title("🚀 V-Universe AI Hub")
            st.caption("Select or create a project to get started")
    
    with col2:
        # Quick stats
        if 'user' in st.session_state:
            budget = CostManager.get_user_budget(st.session_state.user.id)
            st.metric("Available Credits", f"${budget.get('remaining_credits', 0):.2f}")
    
    # Main tabs
    tabs = st.tabs([
        "💬 AI Chat",
        "✍️ Workstation", 
        "📚 Project Bible",
        "💰 Cost Management",
        "⚙️ Settings"
    ])
    
    # Tab routing
    with tabs[0]:
        render_chat_tab(project_id, persona)
    
    with tabs[1]:
        render_workstation_tab(project_id, persona)
    
    with tabs[2]:
        render_bible_tab(project_id, persona)
    
    with tabs[3]:
        render_cost_tab()
    
    with tabs[4]:
        render_settings_tab()
    
    # Footer
    st.markdown("---")
    st.markdown(
        """
        <div style='text-align: center; color: #666; padding: 20px;'>
            <p>🚀 V-Universe AI Hub • Powered by OpenRouter AI & Supabase • v2.0</p>
            <p style='font-size: 12px;'>Supporting 20+ AI models • Real-time collaboration • Intelligent context management</p>
        </div>
        """,
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
