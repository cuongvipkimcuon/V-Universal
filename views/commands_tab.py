"""
Tab Chỉ lệnh — danh sách lệnh @@, mô tả, cú pháp, ví dụ; tùy biến alias (cách kích hoạt) theo dự án.
Kích hoạt bằng @@ (tránh nhầm email @). Fallback: lệnh thiếu/sai -> hỏi lại, không đoán ý.
"""
import streamlit as st

from config import init_services

KNOWLEDGE_PAGE_SIZE = 10


def _get_command_definitions(project_id=None):
    """Lấy danh sách command_definitions từ DB; fallback built-in nếu chưa có bảng."""
    try:
        svc = init_services()
        if svc:
            r = svc["supabase"].table("command_definitions").select("*").order("sort_order").execute()
            if r.data:
                return r.data
    except Exception:
        pass
    from core.command_parser import BUILTIN_TRIGGERS, COMMAND_TO_ROUTER
    seen = set()
    builtin = []
    for trigger, key in sorted(BUILTIN_TRIGGERS.items(), key=lambda x: (x[1], x[0])):
        if key in COMMAND_TO_ROUTER and key not in seen:
            seen.add(key)
            t = COMMAND_TO_ROUTER[key]
            builtin.append({
                "command_key": key,
                "name_vi": key.replace("_", " ").title(),
                "description": "",
                "args_schema": [],
                "example_usage": f"@@{trigger} ...",
                "default_trigger": trigger,
                "intent": t[0],
                "execution_note": "",
                "sort_order": len(builtin),
            })
    return builtin


def _get_aliases(story_id):
    """Lấy alias hiện tại của dự án (story_id)."""
    if not story_id:
        return []
    try:
        svc = init_services()
        if svc:
            r = svc["supabase"].table("command_aliases").select("id, alias, command_key").eq("story_id", story_id).execute()
            return r.data or []
    except Exception:
        pass
    return []


def _add_alias(story_id, alias: str, command_key: str):
    """Thêm alias cho lệnh trong dự án. alias không chứa @@."""
    if not story_id or not alias or not command_key:
        return False, "Thiếu dự án, alias hoặc lệnh."
    alias_clean = alias.strip().lower().lstrip("@")
    if not alias_clean:
        return False, "Alias không được để trống."
    try:
        svc = init_services()
        if not svc:
            return False, "Không kết nối được dịch vụ."
        svc["supabase"].table("command_aliases").upsert({
            "story_id": story_id,
            "alias": alias_clean,
            "command_key": command_key,
        }, on_conflict="story_id,alias").execute()
        return True, "Đã lưu."
    except Exception as e:
        return False, str(e)


def _delete_alias(story_id, alias: str):
    """Xóa alias (theo story_id, alias)."""
    if not story_id or not alias:
        return False
    alias_clean = alias.strip().lower().lstrip("@")
    try:
        svc = init_services()
        if not svc:
            return False
        svc["supabase"].table("command_aliases").delete().eq("story_id", story_id).eq("alias", alias_clean).execute()
        return True
    except Exception:
        return False


def render_commands_tab(project_id, persona=None):
    """Render tab Chỉ lệnh: danh sách lệnh + mô tả + ví dụ + tùy biến alias."""
    st.header("📌 Chỉ lệnh (@@)")
    st.markdown(
        "Các lệnh dạng **@@tên_lệnh** (hoặc alias bạn đặt) để thực hiện nhanh thao tác. "
        "Kích hoạt bằng **@@** (tránh nhầm với email @). Bạn có thể **tùy biến cách gọi** (alias) cho từng lệnh bên dưới. "
        "Nếu gõ thiếu tham số hoặc lệnh không tồn tại, hệ thống sẽ **hỏi lại** thay vì đoán ý."
    )
    if not project_id:
        st.info("Chọn một dự án để xem và tùy biến alias theo dự án.")
        project_id = None

    aliases = _get_aliases(project_id) if project_id else []
    alias_by_cmd = {}
    for a in aliases:
        alias_by_cmd.setdefault(a.get("command_key"), []).append(a.get("alias"))

    # Phân trang: ưu tiên DB (command_definitions), fallback builtin (slice 10/trang)
    page = max(1, int(st.session_state.get("commands_page", 1)))
    commands = []
    total_commands = 0
    total_pages = 1
    try:
        svc = init_services()
        if svc:
            count_res = svc["supabase"].table("command_definitions").select("id", count="exact").limit(0).execute()
            total_commands = getattr(count_res, "count", None) or 0
            if total_commands > 0:
                total_pages = max(1, (total_commands + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
                page = max(1, min(page, total_pages))
                offset = (page - 1) * KNOWLEDGE_PAGE_SIZE
                r = svc["supabase"].table("command_definitions").select("*").order("sort_order").range(offset, offset + KNOWLEDGE_PAGE_SIZE - 1).execute()
                commands = r.data or []
    except Exception:
        pass
    if not commands and total_commands == 0:
        builtin_all = _get_command_definitions(project_id)
        total_commands = len(builtin_all)
        total_pages = max(1, (total_commands + KNOWLEDGE_PAGE_SIZE - 1) // KNOWLEDGE_PAGE_SIZE)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * KNOWLEDGE_PAGE_SIZE
        commands = builtin_all[offset : offset + KNOWLEDGE_PAGE_SIZE]

    st.session_state["commands_page"] = page
    st.subheader("Cấu trúc chỉ lệnh")
    st.markdown("""
    - **Kích hoạt:** `@@<trigger> [tham_số...]` (hai dấu @@, tránh nhầm email @)
    - **Trigger** có thể là tên mặc định (vd: `extract_bible`) hoặc **alias** bạn đặt (vd: `ex`).
    - **Tham số:** do hệ thống định nghĩa — *chương* (số hoặc khoảng `1-10`) hoặc *nội dung* (chuỗi sau trigger).
    """)
    if total_pages > 1:
        st.caption(f"**Trang {page} / {total_pages}** (tối đa {KNOWLEDGE_PAGE_SIZE} mục/trang, tổng {total_commands} lệnh)")
        pc1, pc2, pc3 = st.columns([1, 2, 1])
        with pc1:
            if st.button("⬅️ Trang trước", key="cmd_prev", disabled=(page <= 1)):
                st.session_state["commands_page"] = max(1, page - 1)
                st.rerun()
        with pc3:
            if st.button("Trang sau ➡️", key="cmd_next", disabled=(page >= total_pages)):
                st.session_state["commands_page"] = min(total_pages, page + 1)
                st.rerun()

    for cmd in commands:
        key = cmd.get("command_key") or ""
        if key == "ask_user_clarification":
            continue
        name_vi = cmd.get("name_vi") or key
        desc = cmd.get("description") or ""
        args_schema = cmd.get("args_schema") or []
        example = cmd.get("example_usage") or f"@@{cmd.get('default_trigger','')} ..."
        note = cmd.get("execution_note") or ""
        default_trigger = (cmd.get("default_trigger") or "").strip() or key

        with st.expander(f"**{name_vi}** — `@@{default_trigger}`", expanded=False):
            st.markdown(f"**Mô tả:** {desc}")
            if args_schema:
                st.markdown("**Tham số:**")
                for arg in args_schema:
                    req = " (bắt buộc)" if arg.get("required", True) else ""
                    st.markdown(f"- `{arg.get('name','')}`{req}: {arg.get('description','')}")
            st.markdown(f"**Ví dụ:** `{example}`")
            if note:
                st.caption(f"Cách thực hiện: {note}")

            st.markdown("---")
            st.markdown("**Tùy biến kích hoạt (alias)**")
            current = alias_by_cmd.get(key, [])
            if current:
                for a in current:
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        st.code(f"@@{a}")
                    with col2:
                        if project_id and st.button("Xóa alias", key=f"del_alias_{key}_{a}"):
                            _delete_alias(project_id, a)
            if project_id:
                new_alias = st.text_input(
                    "Thêm alias (không cần gõ @@)",
                    placeholder="vd: ex, trích",
                    key=f"new_alias_{key}",
                )
                if new_alias and st.button("Lưu alias", key=f"save_alias_{key}"):
                    ok, msg = _add_alias(project_id, new_alias, key)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

    st.subheader("Fallback khi lệnh thiếu hoặc sai")
    st.markdown("""
    Khi bạn gõ **@@** nhưng:
    - **Lệnh không tồn tại** (sai tên/alias) → Hệ thống hỏi lại: *"Không nhận diện được lệnh. Bạn muốn thực hiện thao tác gì?"*
    - **Thiếu tham số bắt buộc** (vd: @@extract_bible mà không ghi chương) → Hệ thống hỏi: *"Lệnh cần chỉ rõ chương. Bạn muốn áp dụng cho chương nào?"*

    Hệ thống **không đoán ý** — luôn dùng **ask_user_clarification** để bạn bổ sung thông tin hoặc xem lại tab Chỉ lệnh.
    """)
