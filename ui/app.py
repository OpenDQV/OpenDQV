"""
OpenDQV Streamlit Workbench.

A developer/governance UI for:
  - Browsing data contracts (with lifecycle status)
  - Testing validation (single record + batch)
  - Integration guide — ready-to-paste code for source system admins
  - Code export (push-down mode)
"""

import os
import json
import time
import logging

import yaml
import streamlit as st
import requests
import pandas as pd

API_URL = os.environ.get("API_URL", "http://localhost:8000")

# ── UI Audit Logger ──────────────────────────────────────────────────
# Writes structured access events to the same log stream as the API,
# so UI actions appear in the unified audit trail.
_ui_logger = logging.getLogger("opendqv.ui")
if not _ui_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [ui] %(levelname)s %(message)s"))
    _ui_logger.addHandler(_h)
_ui_logger.setLevel(logging.INFO)

_UI_SESSION_TIMEOUT_SECONDS = int(os.environ.get("UI_SESSION_TIMEOUT", "1800"))  # 30 min default

def _ui_audit(event: str, detail: str = ""):
    """Emit a structured audit log line for a UI action."""
    _ui_logger.info("ui_event=%s detail=%s", event, detail or "-")

st.set_page_config(
    page_title="OpenDQV Workbench",
    page_icon=os.path.join(os.path.dirname(__file__), "favicon-32.png"),
    layout="wide",
)

# ── Navigation styling ────────────────────────────────────────────────
# Transform the sidebar radio widget into a proper nav menu:
# hide the radio circles, style each label as a full-width clickable item,
# highlight the selected item with the brand teal.
st.markdown("""
<style>
/* ── Sidebar nav buttons ───────────────────────────────────── */

/* Reset all nav buttons to plain text style */
section[data-testid="stSidebar"] .stButton button {
    background: none !important;
    border: none !important;
    box-shadow: none !important;
    color: inherit !important;
    font-size: 0.9rem !important;
    font-weight: 400 !important;
    padding: 0.45rem 0.85rem !important;
    border-radius: 0.4rem !important;
    margin-bottom: 1px !important;
    width: 100% !important;
    text-align: left !important;
    transition: background-color 0.15s ease !important;
}

/* Hover state */
section[data-testid="stSidebar"] .stButton button:hover {
    background-color: rgba(0, 180, 216, 0.12) !important;
    color: #00b4d8 !important;
}

/* Active / primary button — teal highlight matching brand */
section[data-testid="stSidebar"] .stButton button[kind="primary"],
section[data-testid="stSidebar"] .stButton button[data-testid="baseButton-primary"] {
    background-color: rgba(0, 180, 216, 0.18) !important;
    color: #00b4d8 !important;
    font-weight: 600 !important;
    border-left: 3px solid #00b4d8 !important;
    padding-left: calc(0.85rem - 3px) !important;
}

/* Tighten gap between button items */
section[data-testid="stSidebar"] .stButton {
    margin-bottom: 0 !important;
}
</style>
""", unsafe_allow_html=True)

# ── UI Access Control ────────────────────────────────────────────────
# Shared-secret gate for the governance workbench.
#
# Enforcement rules:
#   - If UI_ACCESS_TOKEN is set: require it before showing the workbench (always).
#   - If UI_ACCESS_TOKEN is NOT set: check the API health endpoint.
#     If the API is in token mode (maker_checker_enforced=true), the workbench
#     refuses to render — a token-mode deployment without UI auth is a
#     misconfiguration that must be corrected before the workbench can be used.
#   - If UI_ACCESS_TOKEN is not set AND the API is in open mode: allow access
#     (development default — never use in production).
#
# In production, ALSO restrict port 8501 to your corporate network or VPN.
_UI_TOKEN = os.environ.get("UI_ACCESS_TOKEN", "")

def _api_maker_checker_enforced() -> bool:
    """Return True if the API reports maker_checker_enforced=true."""
    try:
        r = requests.get(f"{API_URL}/health", timeout=3)
        return r.json().get("maker_checker_enforced", False)
    except Exception:
        # If we can't reach the API, assume the safest default.
        return True

if _UI_TOKEN:
    if "ui_authenticated" not in st.session_state:
        st.session_state.ui_authenticated = False
    if "ui_auth_time" not in st.session_state:
        st.session_state.ui_auth_time = None

    # Session timeout: if authenticated but idle beyond the timeout, force re-auth.
    if st.session_state.ui_authenticated and st.session_state.ui_auth_time is not None:
        if time.time() - st.session_state.ui_auth_time > _UI_SESSION_TIMEOUT_SECONDS:
            st.session_state.ui_authenticated = False
            st.session_state.ui_auth_time = None
            _ui_audit("session_timeout", "idle timeout expired — re-authentication required")

    if not st.session_state.ui_authenticated:
        st.title("OpenDQV Workbench")
        st.warning("This workbench is access-controlled. Enter the UI access token to continue.")
        entered = st.text_input("Access token", type="password", key="ui_access_input")
        if st.button("Unlock"):
            if entered == _UI_TOKEN:
                st.session_state.ui_authenticated = True
                st.session_state.ui_auth_time = time.time()
                _ui_audit("auth_success", "UI access token accepted")
                st.rerun()
            else:
                _ui_audit("auth_failure", "invalid UI access token presented")
                st.error("Invalid access token.")
        st.stop()
elif _api_maker_checker_enforced():
    # Token-mode API with no UI gate configured — block access and explain clearly.
    st.title("OpenDQV Workbench — Access Restricted")
    st.error(
        "**UI_ACCESS_TOKEN is not set, but the API is running in token mode "
        "(maker_checker_enforced=true).**\n\n"
        "This workbench cannot be safely accessed without an access token in a "
        "token-mode deployment. Set `UI_ACCESS_TOKEN` in your `.env` file and "
        "restart the UI service. See `SECURITY.md` for deployment requirements."
    )
    st.stop()

# ── Sidebar — token state (needed before headers) ────────────────────

if "token" not in st.session_state:
    st.session_state.token = ""

headers = {"Authorization": f"Bearer {st.session_state.token}"} if st.session_state.token else {}

# ── Sidebar: logo ─────────────────────────────────────────────────────
_logo_path = os.path.join(os.path.dirname(__file__), "opendqv-mark.png")
if os.path.exists(_logo_path):
    st.sidebar.image(_logo_path, width=64)
st.sidebar.caption("Trust is cheaper to build than to repair.")
st.sidebar.markdown("---")


# ── Helper ───────────────────────────────────────────────────────────

def api_get(path, **kwargs):
    try:
        return requests.get(f"{API_URL}{path}", headers=headers, **kwargs)
    except requests.ConnectionError:
        st.error(f"Cannot connect to API at {API_URL}")
        return None


def api_post(path, method="POST", extra_headers=None, **kwargs):
    _h = {**headers, **(extra_headers or {})}
    try:
        if method == "PUT":
            return requests.put(f"{API_URL}{path}", headers=_h, **kwargs)
        elif method == "DELETE":
            return requests.delete(f"{API_URL}{path}", headers=_h, **kwargs)
        else:
            return requests.post(f"{API_URL}{path}", headers=_h, **kwargs)
    except requests.ConnectionError:
        st.error(f"Cannot connect to API at {API_URL}")
        return None


# ── Navigation ───────────────────────────────────────────────────────
# New-contract detection: diff the current contract list against what the
# workbench last saw. Works across Docker container boundaries (no /tmp sharing).
# Also honours a /tmp/.opendqv_session hint for pure-host (non-Docker) setups.

import json as _json
import pathlib as _pathlib

if "section" not in st.session_state:
    st.session_state["section"] = "Contracts"  # default; key= keeps it in sync
if "known_contracts" not in st.session_state:
    # Seed from session file if available (non-Docker host run)
    # tempfile.gettempdir() is cross-platform (/tmp on Unix, %TEMP% on Windows)
    import tempfile as _tempfile
    _session_file = _pathlib.Path(_tempfile.gettempdir()) / ".opendqv_session"
    _hint: str = ""
    if _session_file.exists():
        try:
            _hint = _json.loads(_session_file.read_text()).get("contract", "")
        except Exception:
            pass
        try:
            _session_file.unlink()
        except Exception:
            pass
    st.session_state["known_contracts"] = set()
    if _hint:
        st.session_state["jump_to_contract"] = _hint

_SECTIONS = [
    "Contracts",
    "Validate",
    "Monitoring",
    "Audit Trail",
    "Catalogs & AI",
    "Integration Guide",
    "Code Export",
    "Import Rules",
    "Profiler",
    "Webhooks",
    "Federation",
    "CLI Guide",
]

_NAV_GROUPS = {
    "CORE": ["Contracts", "Validate", "Monitoring", "Audit Trail"],
    "INTEGRATIONS": ["Catalogs & AI", "Integration Guide", "Code Export", "Webhooks", "Federation"],
    "CONTRACT TOOLS": ["Import Rules", "Profiler", "CLI Guide"],
}

for _grp_label, _grp_items in _NAV_GROUPS.items():
    st.sidebar.markdown(f"<p style='font-size:0.7rem;font-weight:700;color:#888;letter-spacing:0.08em;margin:6px 0 0'>{_grp_label}</p>", unsafe_allow_html=True)
    for _item in _grp_items:
        if st.sidebar.button(
            _item,
            key=f"nav_btn_{_item}",
            use_container_width=True,
            type="primary" if st.session_state["section"] == _item else "secondary",
        ):
            st.session_state["section"] = _item
            st.rerun()

section = st.session_state["section"]

# ── Sidebar 3: Developer tools ────────────────────────────────────────
st.sidebar.markdown("---")
with st.sidebar.expander("Developer tools", expanded=False):
    st.markdown("**PAT Authentication**")
    token_input = st.text_input("PAT Token", value=st.session_state.token, type="password", key="pat_input")
    if st.button("Set Token", key="set_token_btn"):
        st.session_state.token = token_input.strip()
        headers = {"Authorization": f"Bearer {st.session_state.token}"} if st.session_state.token else {}
        st.success("Token set!")
    st.markdown("---")
    st.markdown("**Generate Test Token**")
    quick_user = st.text_input("Username", value="test", key="quick_token_user")
    if st.button("Generate Token", key="gen_token_btn"):
        try:
            r = requests.post(f"{API_URL}/api/v1/tokens/generate", params={"username": quick_user})
            if r.status_code == 200:
                token = r.json()["pat"]
                st.session_state.token = token
                st.success("Token generated and set!")
                st.code(token[:40] + "...")
            else:
                st.error(f"Failed: {r.status_code}")
        except requests.ConnectionError:
            st.error(f"Cannot connect to API at {API_URL}")

# ── Page header ──────────────────────────────────────────────────────
_hcol1, _hcol2 = st.columns([5, 1])
with _hcol1:
    _hires_logo = os.path.join(os.path.dirname(__file__), "OpenDQV-Logo-Hires.png")
    if os.path.exists(_hires_logo):
        st.image(_hires_logo, width=220)
    st.title("OpenDQV Workbench")
    st.caption("Open Data Quality Validation")
with _hcol2:
    st.write("")  # vertical align
    if st.button("↺ Reload", help="Pick up any changes made to contract YAML files", use_container_width=True):
        try:
            _rel = requests.post(f"{API_URL}/api/v1/contracts/reload", headers=headers, timeout=10)
            if _rel.status_code == 200:
                st.toast("Contracts reloaded", icon="✅")
                st.rerun()
            else:
                _rd = ""
                try:
                    _rd = _rel.json().get("detail", "")
                except Exception:
                    pass
                st.error(f"Reload failed {_rel.status_code}" + (f": {_rd}" if _rd else ""))
        except requests.ConnectionError:
            st.error(f"Cannot connect to API at {API_URL}")

# ACT-046-02 (enhanced): Full-width red DEV MODE banner.
try:
    _health_resp = requests.get(f"{API_URL}/health", timeout=5)
    _hdata = _health_resp.json() if _health_resp.status_code == 200 else {}
except Exception:
    _hdata = {}

_warn_open = _hdata.get("auth_mode", "open") == "open"
_warn_secret = _hdata.get("secret_key_insecure", False)

if _warn_open or _warn_secret:
    _issues_html = ""
    if _warn_open:
        _issues_html += (
            "<li><b>AUTH_MODE=open</b> — all callers have unrestricted admin access "
            "without a token.</li>"
        )
    if _warn_secret:
        _issues_html += (
            "<li><b>SECRET_KEY is the default</b> — any token issued by this node "
            "can be forged by anyone who reads config.py.</li>"
        )
    st.markdown(
        f"""
        <div style="background-color:#b91c1c;color:#fff;padding:18px 22px;
                    border-radius:6px;margin-bottom:18px;border:3px solid #7f1d1d;
                    font-family:monospace;">
          <p style="font-size:17px;font-weight:900;margin:0 0 6px 0;letter-spacing:1px;">
            ⛔ WARNING! DO NOT IGNORE THIS MESSAGE
          </p>
          <p style="font-size:15px;font-weight:700;margin:0 0 10px 0;">
            YOU ARE IN DEV MODE &mdash; DO NOT RUN IN PRODUCTION WITH THIS CONFIGURATION
          </p>
          <ul style="margin:0 0 10px 0;padding-left:18px;font-size:13px;">
            {_issues_html}
          </ul>
          <p style="font-size:13px;margin:0;font-weight:600;">
            See <span style="background:#7f1d1d;padding:2px 6px;border-radius:3px;
                             border:1px solid #fca5a5;letter-spacing:0.3px;">
              docs/production_deployment.md</span> for hardening steps.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ── Contracts ────────────────────────────────────────────────────────

# Domain label mapping — cleans up the prefix-derived industry names
_DOMAIN_LABELS = {
    "sf":         "Salesforce",
    "consumer":   "Consumer Goods",
    "financial":  "Financial Services",
    "hr":         "HR / People",
    "proof":      "Media / AdTech",
    "public":     "Public Sector",
    "real":       "Real Estate",
    "universal":  "Cross-Industry",
    "books":      "Publishing",
    "martyns":    "Martyn's Law",
    "allereasy":  "AllerEasy",
    "ppds":       "Natasha's Law / PPDS",
}

def _contract_domain(name: str) -> str:
    prefix = name.split("_")[0]
    if prefix in _DOMAIN_LABELS:
        return _DOMAIN_LABELS[prefix]
    titled = prefix.title()
    return _ACRONYMS.get(titled, titled)

# Known acronyms that Python's str.title() mangles (e.g. "qsr" → "Qsr" instead of "QSR")
_ACRONYMS = {
    "Qsr": "QSR", "Gdpr": "GDPR", "Dsar": "DSAR", "Fmcg": "FMCG",
    "Hipaa": "HIPAA", "Dora": "DORA", "Ict": "ICT", "Sox": "SOX",
    "Mifid": "MiFID", "Iot": "IoT", "Cdr": "CDR", "Hr": "HR",
    "Sf": "SF", "E2e": "E2E", "Eu": "EU", "Uk": "UK", "Ndc": "NDC",
    "Csv": "CSV", "Api": "API", "Nhs": "NHS",
}

def _display_name(contract_name: str) -> str:
    """Convert snake_case contract name to a human-readable display name, preserving known acronyms."""
    words = contract_name.replace("_", " ").title().split()
    return " ".join(_ACRONYMS.get(w, w) for w in words)

if section == "Contracts":
    st.header("Manage Data Contracts")
    st.markdown("Browse and manage validation contracts by status, owner, or industry.")

    # Fetch all contracts first so domain list is available for the filter multiselect
    r = api_get("/api/v1/contracts", params={"include_all": "true"})
    if r and r.status_code == 200:
        _all_contracts = r.json()
        _all_domains = sorted(set(_contract_domain(c["name"]) for c in _all_contracts))

        # ── Controls row ──────────────────────────────────────────────
        _ctl1, _ctl2, _ctl3 = st.columns([3, 2, 1])
        with _ctl1:
            _search = st.text_input("Search contracts", placeholder="Filter by name, status, or owner…",
                                    label_visibility="collapsed", key="contract_search")
        with _ctl2:
            _wizard_domains = st.session_state.get("keep_domains", [])
            _filter_default = _wizard_domains if _wizard_domains and set(_wizard_domains) < set(_all_domains) else []
            _industry_filter = st.multiselect("Industry", _all_domains, default=_filter_default,
                                              placeholder="All industries",
                                              label_visibility="collapsed", key="industry_filter")
        with _ctl3:
            show_all = st.checkbox("Show archived", value=False)

        _selected_domains = _industry_filter  # bound to session_state["industry_filter"]

        # Industry breadth banner
        _active_count  = sum(1 for c in _all_contracts if c.get("status") != "archived")
        _domain_count  = len(_all_domains)
        _dep_count     = sum(1 for c in _all_contracts if c.get("status") == "archived")
        st.caption(
            f"**{_active_count} active contracts** across **{_domain_count} industries**"
            + (f"  ·  {_dep_count} archived (hidden)" if _dep_count else "")
        )

        # Industry pills — visual breadth showcase
        _pill_md = "  ".join(
            f"`{'◉' if d in _selected_domains else '○'} {d}`"
            for d in _all_domains
        )
        st.markdown(_pill_md)

        # Now filter for display
        contracts = _all_contracts if show_all else [c for c in _all_contracts if c.get("status") != "archived"]

        if contracts:
            _names = set(c["name"] for c in _all_contracts)

            # Diff-based new-contract detection (works across Docker container boundaries)
            _new = _names - st.session_state["known_contracts"]
            if st.session_state["known_contracts"] and _new:
                for _n in sorted(_new):
                    st.success(f"New contract detected: **{_n}**")
            st.session_state["known_contracts"] = _names

            # Non-Docker host hint via session file
            _jump = st.session_state.pop("jump_to_contract", None)
            if _jump and _jump not in (_new or set()):
                st.info(f"Contract **{_jump}** was just created by the wizard.")

            # Apply industry filter
            if _selected_domains:
                contracts = [c for c in contracts if _contract_domain(c["name"]) in _selected_domains]
                if not contracts:
                    st.info("No contracts match the selected industries.")

            # Apply text search
            if _search:
                _q = _search.lower()
                contracts = [
                    c for c in contracts
                    if _q in c.get("name", "").lower()
                    or _q in c.get("status", "").lower()
                    or _q in (c.get("owner") or "").lower()
                ]
                if not contracts:
                    st.info(f"No contracts match '{_search}'.")

            # ── Industry setup: bulk archive ──────────────────────────
            with st.expander("⚙️ Industry setup — focus this workbench on your domain"):
                st.markdown(
                    "Select the industries relevant to your team. "
                    "All other contracts will be archived (hidden by default). "
                    "You can restore them at any time using **Show archived**."
                )
                _keep_domains = st.multiselect(
                    "Keep these industries active",
                    _all_domains,
                    default=[_contract_domain(c["name"]) for c in _all_contracts
                             if c.get("status") != "archived"],
                    key="keep_domains",
                )
                _to_deprecate = [
                    c for c in _all_contracts
                    if _contract_domain(c["name"]) not in _keep_domains
                    and c.get("status") != "archived"
                ]
                _to_restore = [
                    c for c in _all_contracts
                    if _contract_domain(c["name"]) in _keep_domains
                    and c.get("status") == "archived"
                ]
                _setup_col1, _setup_col2 = st.columns(2)
                with _setup_col1:
                    if _to_deprecate:
                        st.caption(f"{len(_to_deprecate)} contracts will be archived.")
                    else:
                        st.caption("All contracts in your selected industries are already active.")
                    if _to_deprecate and st.button("Apply — archive others", type="primary", key="bulk_deprecate"):
                        _errs = []
                        for _bc in _to_deprecate:
                            _br = api_post(f"/api/v1/contracts/{_bc['name']}/status", params={"status": "archived"})
                            if not (_br and _br.status_code == 200):
                                _errs.append(_bc["name"])
                        if _errs:
                            st.error(f"Failed for: {', '.join(_errs)}")
                        else:
                            st.success(f"Done — {len(_to_deprecate)} contracts archived.")
                            st.rerun()
                with _setup_col2:
                    if _to_restore:
                        st.caption(f"{len(_to_restore)} previously archived contracts will be restored.")
                        if st.button("Restore selected industries", key="bulk_restore"):
                            _errs = []
                            for _bc in _to_restore:
                                _br = api_post(f"/api/v1/contracts/{_bc['name']}/status", params={"status": "active"})
                                if not (_br and _br.status_code == 200):
                                    _errs.append(_bc["name"])
                            if _errs:
                                st.error(f"Failed for: {', '.join(_errs)}")
                            else:
                                st.success(f"Restored {len(_to_restore)} contracts.")
                                st.rerun()

            df = pd.DataFrame(contracts)
            df["status"] = df["status"].replace({"archived": "archived"})
            # Replace None/NaN with — for cleaner display
            df = df.fillna("—")
            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "name":        st.column_config.TextColumn("Name", width="medium"),
                    "version":     st.column_config.TextColumn("Version", width="small"),
                    "description": st.column_config.TextColumn("Description", width="large"),
                    "owner":       st.column_config.TextColumn("Owner", width="medium"),
                    "status":      st.column_config.TextColumn("Status", width="small"),
                    "rule_count":  st.column_config.NumberColumn("Rules", width="small"),
                    "asset_id":    st.column_config.TextColumn("Asset ID", width="medium"),
                },
            )

            # ACT-037-05: Draft-status banner — warn if any contract with recent
            # traffic is in DRAFT status (operator may have forgotten to reactivate)
            _draft_contracts = [c for c in _all_contracts if c.get("status") == "draft"]
            if _draft_contracts:
                for _dc in _draft_contracts:
                    st.warning(
                        f"⚠️ **{_display_name(_dc['name'])}** is in **DRAFT** status. "
                        f"Validation requests are being served using the current ruleset. "
                        f"Promote to Active when ready for production.",
                        icon=None,
                    )

            _name_list = sorted(c["name"] for c in contracts)
            # Pre-select: new contract > session file hint > active contract > first
            _preselect = (
                sorted(_new)[0] if len(_new) == 1 else
                _jump if (_jump and _jump in _name_list) else
                st.session_state.get("active_contract", "")
            )
            _default_idx = _name_list.index(_preselect) if _preselect in _name_list else 0
            selected = st.selectbox("View contract detail", _name_list, index=_default_idx,
                                    format_func=_display_name)
            if selected:
                st.session_state["active_contract"] = selected
                detail_r = api_get(f"/api/v1/contracts/{selected}")
                if detail_r and detail_r.status_code == 200:
                    detail = detail_r.json()
                    _status_val = detail.get("status", "active")
                    status_badge = {
                        "active":     "🟢 ACTIVE",
                        "draft":      "🔵 DRAFT",
                        "archived": "🔴 ARCHIVED",
                        "review":     "🟡 REVIEW",
                    }.get(_status_val, _status_val.upper())

                    st.markdown("---")
                    st.subheader(_display_name(detail["name"]))
                    st.caption(f"v{detail['version']}  ·  {detail['description']}  ·  owner: {detail.get('owner') or '—'}")
                    st.markdown(f"Status: {status_badge}")
                    if detail.get("contexts"):
                        st.markdown(f"Contexts: {', '.join(detail['contexts'])}")

                    st.markdown("**Rules:**")
                    st.dataframe(pd.DataFrame(detail["rules"]), width="stretch", hide_index=True)

                    # Lifecycle management
                    st.markdown("---")
                    current = detail.get("status", "active")

                    _lc_icon  = {"draft": "🔵", "review": "🟡", "active": "🟢", "archived": "🔴"}.get(current, "⚪")
                    _lc_steps = ["draft", "review", "active", "archived"]
                    _lc_display = {"draft": "draft", "review": "review", "active": "active", "archived": "archived"}
                    _lc_current_display = current
                    _lc_flow  = " → ".join(
                        f"**{_lc_icon} {s.upper()}**" if s == _lc_current_display else s
                        for s in _lc_steps
                    )
                    st.markdown(f"**Lifecycle: {detail['name']}** — {_lc_flow}")

                    if current == "draft":
                        st.caption(
                            "This contract is in draft — source systems cannot validate against it yet. "
                            "Promote it to Active when it's ready for production use."
                        )
                        _promote_confirm = st.checkbox(
                            f"I confirm **{selected}** is ready for production validation",
                            key=f"confirm_activate_{selected}",
                        )
                        st.caption(
                            "Active contracts are live — source systems can validate against them immediately. "
                            "Ensure all rules are correct and the contract has been reviewed before promoting."
                        )
                        _draft_col1, _draft_col2 = st.columns(2)
                        with _draft_col1:
                            if _promote_confirm and st.button("✅ Promote to Active", type="primary", key=f"activate_{selected}"):
                                _lr = api_post(f"/api/v1/contracts/{selected}/status", params={"status": "active"})
                                if _lr and _lr.status_code == 200:
                                    _ui_audit("status_change", f"contract={selected} new_status=active")
                                    st.success("Contract is now Active. Source systems can validate against it.")
                                    st.rerun()
                                elif _lr:
                                    try:    _ld = _lr.json().get("detail", "")
                                    except Exception: _ld = ""
                                    st.error(f"Could not activate: {_ld or _lr.text}")
                        with _draft_col2:
                            if st.button("📋 Submit for Review", key=f"review_{selected}"):
                                _lr = api_post(
                                    f"/api/v1/contracts/{selected}/{detail.get('version')}/submit-review",
                                    json={},
                                )
                                if _lr and _lr.status_code == 200:
                                    _ui_audit("status_change", f"contract={selected} new_status=review")
                                    st.success("Contract submitted for review.")
                                    st.rerun()
                                elif _lr:
                                    try: _ld = _lr.json().get("detail", "")
                                    except Exception: _ld = ""
                                    st.error(f"Failed: {_ld or _lr.text}")

                    elif current == "review":
                        st.caption(
                            "This contract is under review. An approver can promote it to Active or reject it back to Draft."
                        )
                        _rev_col1, _rev_col2 = st.columns(2)
                        with _rev_col1:
                            if st.button("✅ Approve", type="primary", key=f"approve_{selected}"):
                                _lr = api_post(
                                    f"/api/v1/contracts/{selected}/{detail.get('version')}/approve",
                                    json={},
                                )
                                if _lr and _lr.status_code == 200:
                                    _ui_audit("status_change", f"contract={selected} new_status=active")
                                    st.success("Contract approved and is now Active.")
                                    st.rerun()
                                elif _lr:
                                    try: _ld = _lr.json().get("detail", "")
                                    except Exception: _ld = ""
                                    st.error(f"Failed: {_ld or _lr.text}")
                        with _rev_col2:
                            if st.button("↩ Reject to Draft", key=f"reject_{selected}"):
                                _lr = api_post(
                                    f"/api/v1/contracts/{selected}/{detail.get('version')}/reject",
                                    json={},
                                )
                                if _lr and _lr.status_code == 200:
                                    _ui_audit("status_change", f"contract={selected} new_status=draft")
                                    st.success("Contract rejected back to Draft.")
                                    st.rerun()
                                elif _lr:
                                    try: _ld = _lr.json().get("detail", "")
                                    except Exception: _ld = ""
                                    st.error(f"Failed: {_ld or _lr.text}")

                    elif current == "active":
                        st.caption(
                            "This contract is live. Source systems can validate against it. "
                            "Move it back to Draft to make edits, or Archive it to retire it."
                        )
                        _lc_col1, _lc_col2 = st.columns(2)
                        with _lc_col1:
                            if st.button("↩ Move back to Draft", key=f"draft_{selected}"):
                                _lr = api_post(f"/api/v1/contracts/{selected}/status", params={"status": "draft"})
                                if _lr and _lr.status_code == 200:
                                    _ui_audit("status_change", f"contract={selected} new_status=draft")
                                    st.success("Contract moved back to Draft.")
                                    st.rerun()
                                elif _lr:
                                    try:    _ld = _lr.json().get("detail", "")
                                    except Exception: _ld = ""
                                    st.error(f"Failed: {_ld or _lr.text}")
                        with _lc_col2:
                            with st.expander("⚠️ Archive this contract"):
                                st.warning(
                                    "Archived contracts are hidden by default and cannot be used for new integrations. "
                                    "Existing source systems using this contract will no longer be able to validate."
                                )
                                _confirm = st.checkbox(
                                    f"I understand — archive **{selected}**",
                                    key=f"confirm_deprecate_{selected}"
                                )
                                if _confirm and st.button("Archive", key=f"deprecate_{selected}"):
                                    _lr = api_post(f"/api/v1/contracts/{selected}/status", params={"status": "archived"})
                                    if _lr and _lr.status_code == 200:
                                        _ui_audit("status_change", f"contract={selected} new_status=archived")
                                        st.success("Contract archived. It is now hidden from the default list.")
                                        st.rerun()
                                    elif _lr:
                                        try:    _ld = _lr.json().get("detail", "")
                                        except Exception: _ld = ""
                                        st.error(f"Failed: {_ld or _lr.text}")

                    elif current == "archived":
                        st.caption(
                            "This contract has been archived and is hidden from the default list. "
                            "Restore it to Draft to rework and re-activate it."
                        )
                        if st.button("↩ Restore to Draft", key=f"draft_{selected}"):
                            _lr = api_post(f"/api/v1/contracts/{selected}/status", params={"status": "draft"})
                            if _lr and _lr.status_code == 200:
                                _ui_audit("status_change", f"contract={selected} new_status=draft")
                                st.success("Contract restored to Draft.")
                                st.rerun()
                            elif _lr:
                                try:    _ld = _lr.json().get("detail", "")
                                except Exception: _ld = ""
                                st.error(f"Failed: {_ld or _lr.text}")

                    # ── Rule Editor (ACT-036-02/05/06) ───────────────────────────
                    st.markdown("---")
                    with st.expander("✏️ Edit Rules", expanded=False):
                        if current == "active":
                            st.warning(
                                "This contract is **ACTIVE** — rules are immutable. "
                                "Use **Fork to new version** below to create a new draft with modified rules."
                            )
                        _RULE_TYPES = [
                            "not_empty", "regex", "min_length", "max_length",
                            "range", "lookup", "date_format", "email", "url",
                        ]
                        _RULE_TYPE_HELP = {
                            "not_empty": "Field must not be null or empty",
                            "regex": "Field must match a regular expression",
                            "min_length": "Field must have at least N characters",
                            "max_length": "Field must have at most N characters",
                            "range": "Numeric field must be within min/max bounds",
                            "lookup": "Field must be one of a fixed set of values",
                            "date_format": "Field must be a date in a given format",
                            "email": "Field must be a valid email address",
                            "url": "Field must be a valid URL",
                        }

                        # ── Edit/delete per existing rule ─────────────────────
                        st.markdown("**Existing rules**" + (" — edit or delete:" if current != "active" else ":"))
                        for _ri, _rule in enumerate(detail.get("rules", [])):
                            _rcol1, _rcol2, _rcol3 = st.columns([4, 1, 1])
                            with _rcol1:
                                st.code(f"{_rule['name']}  ({_rule['type']})  field: {_rule['field']}", language=None)
                            with _rcol2:
                                if current != "active" and st.button("Edit", key=f"edit_rule_{selected}_{_ri}"):
                                    st.session_state[f"editing_rule_{selected}"] = _rule["name"]
                                    st.session_state[f"editing_rule_data_{selected}"] = dict(_rule)
                                    st.rerun()
                            with _rcol3:
                                if current != "active" and st.button("Delete", key=f"del_rule_{selected}_{_ri}"):
                                    st.session_state[f"confirm_del_rule_{selected}_{_ri}"] = True
                            # Confirm delete
                            if current != "active" and st.session_state.get(f"confirm_del_rule_{selected}_{_ri}"):
                                st.warning(f"Delete rule **{_rule['name']}**?")
                                _dc1, _dc2 = st.columns(2)
                                with _dc1:
                                    if st.button("Yes, delete", key=f"yes_del_{selected}_{_ri}", type="primary"):
                                        _dr = api_post(
                                            f"/api/v1/contracts/{selected}/rules/{_rule['name']}",
                                            method="DELETE",
                                        )
                                        if _dr and _dr.status_code == 200:
                                            st.success(f"Rule '{_rule['name']}' deleted.")
                                            st.session_state.pop(f"confirm_del_rule_{selected}_{_ri}", None)
                                            st.rerun()
                                        elif _dr:
                                            st.error(f"Delete failed: {_dr.text}")
                                with _dc2:
                                    if st.button("Cancel", key=f"cancel_del_{selected}_{_ri}"):
                                        st.session_state.pop(f"confirm_del_rule_{selected}_{_ri}", None)
                                        st.rerun()

                        if current != "active":
                            # ── Rule form (add new or edit existing) ─────────────
                            _editing_rule_name = st.session_state.get(f"editing_rule_{selected}")
                            _editing_rule_data = st.session_state.get(f"editing_rule_data_{selected}", {})
                            _is_editing = bool(_editing_rule_name)

                            st.markdown(f"**{'Edit rule: ' + _editing_rule_name if _is_editing else 'Add new rule'}**")
                            _form_key = f"rule_form_{selected}_{_editing_rule_name or 'new'}"
                            _fn_default = _editing_rule_data.get("name", "")
                            _ff_default = _editing_rule_data.get("field", "")
                            _ft_default = _editing_rule_data.get("type", "not_empty")
                            _ft_idx = _RULE_TYPES.index(_ft_default) if _ft_default in _RULE_TYPES else 0

                            _fc1, _fc2 = st.columns(2)
                            with _fc1:
                                _form_name = st.text_input("Rule name", value=_fn_default, key=f"{_form_key}_name")
                                _form_field = st.text_input("Field", value=_ff_default, key=f"{_form_key}_field")
                            with _fc2:
                                _form_type = st.selectbox(
                                    "Rule type",
                                    _RULE_TYPES,
                                    index=_ft_idx,
                                    format_func=lambda t: f"{t} — {_RULE_TYPE_HELP.get(t, '')}",
                                    key=f"{_form_key}_type",
                                )
                                _form_severity = st.selectbox(
                                    "Severity",
                                    ["error", "warning"],
                                    index=0 if _editing_rule_data.get("severity", "error") == "error" else 1,
                                    key=f"{_form_key}_severity",
                                )
                            _form_msg = st.text_input(
                                "Error message",
                                value=_editing_rule_data.get("error_message", ""),
                                key=f"{_form_key}_msg",
                            )
                            # Conditional fields based on type
                            _form_extra = {}
                            if _form_type == "regex":
                                _form_extra["pattern"] = st.text_input(
                                    "Pattern (regex)", value=_editing_rule_data.get("pattern", ""),
                                    key=f"{_form_key}_pattern",
                                )
                            elif _form_type in ("min_length", "max_length"):
                                _len_key = "min_length" if _form_type == "min_length" else "max_length"
                                _form_extra[_len_key] = st.number_input(
                                    _len_key, min_value=0,
                                    value=int(_editing_rule_data.get(_len_key, 1)),
                                    key=f"{_form_key}_{_len_key}",
                                )
                            elif _form_type == "range":
                                _rc1, _rc2 = st.columns(2)
                                with _rc1:
                                    _form_extra["min"] = st.number_input("Min", value=float(_editing_rule_data.get("min", 0)), key=f"{_form_key}_min")
                                with _rc2:
                                    _form_extra["max"] = st.number_input("Max", value=float(_editing_rule_data.get("max", 100)), key=f"{_form_key}_max")
                            elif _form_type == "lookup":
                                _vals_raw = st.text_input(
                                    "Allowed values (comma-separated)",
                                    value=", ".join(_editing_rule_data.get("values", [])),
                                    key=f"{_form_key}_values",
                                )
                                _form_extra["values"] = [v.strip() for v in _vals_raw.split(",") if v.strip()]
                            elif _form_type == "date_format":
                                _form_extra["format"] = st.text_input(
                                    "Date format (e.g. %Y-%m-%d)",
                                    value=_editing_rule_data.get("format", "%Y-%m-%d"),
                                    key=f"{_form_key}_fmt",
                                )

                            _rule_payload = {
                                "name": _form_name,
                                "field": _form_field,
                                "type": _form_type,
                                "severity": _form_severity,
                                "error_message": _form_msg,
                                **_form_extra,
                            }

                            _save_col1, _save_col2 = st.columns([2, 1])
                            with _save_col1:
                                if st.button(
                                    f"{'Update rule' if _is_editing else 'Add rule'}",
                                    type="primary",
                                    key=f"{_form_key}_save",
                                    disabled=not (_form_name and _form_field),
                                ):
                                    # ACT-037-06: Rule save calls rule endpoint only.
                                    # No POST /contracts/{name}/status call is made here.
                                    # Lifecycle status is managed separately via the Lifecycle section above.
                                    if _is_editing:
                                        _sr = api_post(
                                            f"/api/v1/contracts/{selected}/rules/{_editing_rule_name}",
                                            json=_rule_payload,
                                            method="PUT",
                                        )
                                    else:
                                        _sr = api_post(
                                            f"/api/v1/contracts/{selected}/rules",
                                            json=_rule_payload,
                                        )
                                    if _sr and _sr.status_code == 200:
                                        _srd = _sr.json()
                                        if _srd.get("breaking_change_warning"):
                                            st.warning(_srd["breaking_change_warning"])
                                        st.success(f"Rule {'updated' if _is_editing else 'added'}: {_form_name}")
                                        st.session_state.pop(f"editing_rule_{selected}", None)
                                        st.session_state.pop(f"editing_rule_data_{selected}", None)
                                        st.rerun()
                                    elif _sr:
                                        try:    _sd = _sr.json().get("detail", "")
                                        except Exception: _sd = _sr.text
                                        st.error(f"Failed: {_sd}")
                            with _save_col2:
                                if _is_editing and st.button("Cancel edit", key=f"{_form_key}_cancel"):
                                    st.session_state.pop(f"editing_rule_{selected}", None)
                                    st.session_state.pop(f"editing_rule_data_{selected}", None)
                                    st.rerun()

                    # ── Fork to new version (ACT-036-07) ─────────────────────
                    st.markdown("---")
                    with st.expander("🍴 Fork to new version", expanded=False):
                        st.markdown(
                            "Create a copy of this contract as a new major version. "
                            "The new version starts in **DRAFT** status and uses the same rules. "
                            "Use this for breaking rule changes — leave this version active for backwards compatibility."
                        )
                        _fv_col1, _fv_col2 = st.columns([2, 1])
                        with _fv_col1:
                            try:
                                _cur_parts = detail["version"].split(".")
                                _suggested_major = str(int(_cur_parts[0]) + 1) + ".0"
                            except Exception:
                                _suggested_major = "2.0"
                            _fork_version = st.text_input(
                                "New version", value=_suggested_major, key=f"fork_ver_{selected}"
                            )
                        with _fv_col2:
                            st.write("")  # vertical align
                            if st.button("Fork", type="primary", key=f"fork_btn_{selected}"):
                                _fvr = api_post(
                                    f"/api/v1/contracts/{selected}/version",
                                    params={"new_version": _fork_version},
                                )
                                if _fvr and _fvr.status_code == 200:
                                    _fvrd = _fvr.json()
                                    st.success(
                                        f"Forked to v{_fork_version} — now in DRAFT. "
                                        "Switch to it in the selector above."
                                    )
                                    st.session_state["active_contract"] = selected
                                    st.rerun()
                                elif _fvr:
                                    try:    _fd = _fvr.json().get("detail", "")
                                    except Exception: _fd = _fvr.text
                                    st.error(f"Fork failed: {_fd}")
        else:
            st.info("No contracts loaded. Add YAML files to the contracts/ directory.")
    elif r:
        st.error(f"Failed to load contracts: {r.status_code}")

# ── Sample record builder ─────────────────────────────────────────────
def _build_sample_record(rules: list) -> dict:
    """Generate a plausible valid sample record from a contract's rules."""
    # Longer/more-specific keys must appear before short ones — substring matching
    # uses first match, so "timestamp" must precede "time", "verified" before "id".
    _FIELD_HINTS = {
        "dob": "1990-01-01",
        "timestamp": "2026-01-15T09:00:00Z",
        "verified": "TRUE",
        "user_id": "USR-00001",
        "email": "user@example.com", "phone": "+447911123456",
        "date": "2024-01-15", "time": "12:00:00",
        "amount": 99.99, "price": 49.99, "balance": 1000.0, "total": 250.0,
        "age": 36, "score": 85, "count": 5, "quantity": 3,
        "id": "REF-001", "number": "ACC-123456", "code": "GBP",
        "type": "standard", "status": "active", "channel": "online",
        "currency": "GBP", "country": "GB", "language": "en",
        "name": "Alice Smith", "title": "The Cloud Report",
        "description": "Sample description", "notes": "Sample note",
        "url": "https://example.com", "ip": "192.168.1.1",
        "postcode": "EC1A 1BB", "zip": "10001",
    }
    sample = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        field = rule.get("field")
        if not field or field in sample:
            continue
        # required_if rules don't constrain the value format — skip so a later
        # lookup or regex rule can provide a representative valid value instead.
        if rule.get("type") == "required_if":
            continue
        # Lookup rule: use first allowed value (API populates values[] from the ref file)
        if rule.get("type") == "lookup" and rule.get("values"):
            sample[field] = rule["values"][0]
            continue
        # Date format rule
        if rule.get("type") == "date_format":
            sample[field] = "2024-01-15"
            continue
        # Range/min/max rules
        if rule.get("type") == "range":
            sample[field] = rule.get("min", 0)
            continue
        if rule.get("type") == "min":
            sample[field] = rule.get("min", 1)
            continue
        if rule.get("type") == "max":
            sample[field] = rule.get("max", 100)
            continue
        # Field name hint matching
        _fl = field.lower()
        matched = next((v for k, v in _FIELD_HINTS.items() if k in _fl), None)
        if matched is not None:
            sample[field] = matched
        else:
            sample[field] = f"sample_{field}"
    return sample


# ── Validate ─────────────────────────────────────────────────────────

if section == "Validate":
    st.header("Validate Data")
    st.markdown("Test records against a data contract — single record or batch.")

    # ── Shared inputs ──
    _val_contracts_r = api_get("/api/v1/contracts")
    _val_contract_names = sorted([c["name"] for c in _val_contracts_r.json()]) if _val_contracts_r and _val_contracts_r.status_code == 200 else []
    _val_default = st.session_state.get("active_contract", "customer")
    _val_default_idx = _val_contract_names.index(_val_default) if _val_default in _val_contract_names else 0

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        val_contract = st.selectbox("Contract", _val_contract_names, index=_val_default_idx, key="val_contract")
    with col2:
        val_version = st.text_input("Version", value="latest", key="val_version")
    with col3:
        val_context = st.text_input("Context (optional)", value="", key="val_context")

    val_record_id = st.text_input("Record ID (optional)", value="", key="val_record_id", help="Echoed in the response for traceability")

    # Auto-detect DRAFT status
    _val_cr = api_get(f"/api/v1/contracts/{val_contract}", params={"version": val_version or "latest"}) if val_contract else None
    _val_is_draft = _val_cr and _val_cr.status_code == 200 and _val_cr.json().get("status", "") == "draft"
    if _val_is_draft:
        st.info("Contract is in **DRAFT** status — allow_draft automatically enabled.")
        val_allow_draft = True
    else:
        val_allow_draft = st.checkbox("Allow DRAFT contracts (for testing)", value=False, key="val_allow_draft")

    st.markdown("---")

    # ── Mode toggle ──
    val_mode = st.radio("Validation mode", ["Single record", "Batch"], horizontal=True, key="val_mode")
    st.caption("Single record: validate one JSON object.  Batch: validate a JSON array — uses DuckDB for performance.")

    # ── JSON input — key tied to mode only, never to contract ──
    _textarea_key = "val_textarea_single" if val_mode == "Single record" else "val_textarea_batch"
    if _textarea_key not in st.session_state:
        st.session_state[_textarea_key] = "{}" if val_mode == "Single record" else "[]"

    if st.button("Generate sample for this contract", key="val_gen_sample"):
        if _val_cr and _val_cr.status_code == 200:
            _rules = _val_cr.json().get("rules", [])
            if val_mode == "Single record":
                st.session_state[_textarea_key] = json.dumps(_build_sample_record(_rules), indent=2)
            else:
                _rec = _build_sample_record(_rules)
                st.session_state[_textarea_key] = json.dumps([_rec, _rec], indent=2)
            st.rerun()
        else:
            st.warning("Could not load contract rules to generate sample.")

    _label = "Record (JSON)" if val_mode == "Single record" else "Records (JSON array)"
    val_json = st.text_area(_label, height=220, key=_textarea_key)

    # ── Validate button ──
    if st.button("Validate", key="val_submit", type="primary"):
        try:
            parsed = json.loads(val_json)
        except json.JSONDecodeError:
            st.error("Invalid JSON — check your input.")
            parsed = None

        if parsed is not None:
            _val_params = {"allow_draft": "true"} if val_allow_draft else {}

            if val_mode == "Single record":
                _body = {"record": parsed, "contract": val_contract, "version": val_version}
                if val_context:
                    _body["context"] = val_context
                if val_record_id:
                    _body["record_id"] = val_record_id
                _r = api_post("/api/v1/validate", json=_body, params=_val_params)
                if _r and _r.status_code == 200:
                    _res = _r.json()
                    _errs = _res.get("errors", [])
                    _warns = _res.get("warnings", [])
                    _ok = _res["valid"]
                    if _ok:
                        st.success(f"✓ PASS — valid against **{_res['contract']}** v{_res['version']}")
                    else:
                        st.error(f"✗ FAIL — {len(_errs)} error(s)  |  contract: **{_res['contract']}** v{_res['version']}")
                    _m1, _m2, _m3 = st.columns(3)
                    _m1.metric("Result", "PASS" if _ok else "FAIL")
                    _m2.metric("Errors", len(_errs))
                    _m3.metric("Warnings", len(_warns))
                    if _res.get("owner"):
                        st.caption(f"Contract owner: {_res['owner']}")
                    if _errs:
                        with st.expander(f"{len(_errs)} blocking error(s)", expanded=True):
                            st.dataframe(pd.DataFrame(_errs), hide_index=True, use_container_width=True)
                    if _warns:
                        with st.expander(f"{len(_warns)} warning(s)", expanded=False):
                            st.dataframe(pd.DataFrame(_warns), hide_index=True, use_container_width=True)
                elif _r:
                    st.error(f"API error: {_r.status_code} — {_r.text}")

            else:  # Batch
                _body = {"records": parsed, "contract": val_contract, "version": val_version}
                if val_context:
                    _body["context"] = val_context
                _r = api_post("/api/v1/validate/batch", json=_body, params=_val_params)
                if _r and _r.status_code == 200:
                    _res = _r.json()
                    _sum = _res["summary"]
                    if _res.get("owner"):
                        st.caption(f"Contract owner: {_res['owner']}")
                    _c1, _c2, _c3, _c4 = st.columns(4)
                    _c1.metric("Total", _sum["total"])
                    _c2.metric("Passed", _sum["passed"])
                    _c3.metric("Failed", _sum["failed"])
                    _c4.metric("Errors", _sum["error_count"])
                    _rule_counts = _sum.get("rule_failure_counts", {})
                    if _rule_counts:
                        st.markdown("**Rule failure breakdown** (most impactful first):")
                        st.table(pd.DataFrame(
                            sorted(_rule_counts.items(), key=lambda x: x[1], reverse=True),
                            columns=["Rule", "Records failing"],
                        ))
                    st.markdown("---")
                    for _item in _res["results"]:
                        _status = "PASS" if _item["valid"] else "FAIL"
                        with st.expander(f"Record {_item['index']}: {_status}"):
                            if _item["errors"]:
                                st.table(pd.DataFrame(_item["errors"]))
                            if _item["warnings"]:
                                st.table(pd.DataFrame(_item["warnings"]))
                            if not _item["errors"] and not _item["warnings"]:
                                st.success("All checks passed")
                elif _r:
                    st.error(f"API error: {_r.status_code} — {_r.text}")

# ── Integration Guide ────────────────────────────────────────────────

if section == "Integration Guide":
    st.header("Integration Guide")
    st.markdown(
        "Help source teams integrate with OpenDQV — generate tokens, customise sample payloads, and copy ready-to-use code snippets."
    )

    # Contract selection
    guide_r = api_get("/api/v1/contracts")
    contract_names = []
    if guide_r and guide_r.status_code == 200:
        contract_names = [c["name"] for c in guide_r.json()]

    if not contract_names:
        st.warning("No contracts available. Start the API and load contracts first.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            _guide_active = st.session_state.get("active_contract", "")
            _guide_idx = contract_names.index(_guide_active) if _guide_active in contract_names else 0
            guide_contract = st.selectbox("Contract", contract_names, index=_guide_idx, key="guide_contract")
        with col2:
            guide_context = st.text_input("Context (optional)", value="", key="guide_context")

        # ── Step 1: Generate a token for this source system ──
        st.markdown("---")
        st.markdown("### Step 1: Generate a token for the source system")
        st.markdown("Each source system gets its own named token for audit trail and revocation.")

        tcol1, tcol2, tcol3, tcol4 = st.columns([2, 1, 1, 1])
        with tcol1:
            system_name = st.text_input(
                "Source system name",
                value="salesforce-prod",
                key="guide_system_name",
                help="e.g. salesforce-prod, sap-hr, dynamics-crm, postgres-etl",
            )
        with tcol2:
            token_expiry = st.number_input(
                "Token lifetime (days)",
                min_value=1, max_value=3650, value=365,
                key="guide_token_expiry",
            )
        with tcol3:
            token_role = st.selectbox(
                "Role",
                ["validator", "reader", "auditor", "editor", "approver", "admin"],
                index=0,
                key="guide_token_role",
                help=(
                    "validator — validate records only (source systems, ETL pipelines)\n"
                    "reader — read contracts + validate (dashboards, monitoring)\n"
                    "auditor — reader + audit trail access (compliance officers)\n"
                    "editor — validate + author DRAFT contracts + submit for review\n"
                    "approver — validate + approve/reject (pure reviewer, cannot author)\n"
                    "admin — full access including token management"
                ),
            )
        with tcol4:
            st.markdown("<br>", unsafe_allow_html=True)
            generate_clicked = st.button("Generate Token", key="guide_generate_token")

        # Store generated token in session state
        if "guide_generated_token" not in st.session_state:
            st.session_state.guide_generated_token = None
            st.session_state.guide_generated_system = None

        if generate_clicked:
            r = api_post(
                "/api/v1/tokens/generate",
                params={"username": system_name, "expiry_days": token_expiry, "role": token_role},
            )
            if r and r.status_code == 200:
                data = r.json()
                st.session_state.guide_generated_token = data["pat"]
                st.session_state.guide_generated_system = system_name
                st.success(f"Token generated for **{system_name}** (role: {token_role}) — expires {data['expires_at'][:10]} ({data['expiry_days']} days)")
                st.warning("Copy this token now — it won't be shown again after you leave this page.")
                st.code(data["pat"], language="text")
            elif r:
                st.error(f"Failed to generate token: {r.status_code} — {r.text}")

        # Use the generated token in snippets, or placeholder
        snippet_token = st.session_state.guide_generated_token or "{snippet_token}"
        if st.session_state.guide_generated_token:
            st.info(f"Snippets below use the token generated for **{st.session_state.guide_generated_system}**")

        st.markdown("### Step 2: Copy the integration snippet")

        # Get contract detail for sample fields
        detail_r = api_get(f"/api/v1/contracts/{guide_contract}")
        sample_fields = {}
        if detail_r and detail_r.status_code == 200:
            detail = detail_r.json()
            # Build a sample record from the rule fields
            for rule in detail.get("rules", []):
                field = rule["field"]
                if field not in sample_fields:
                    rtype = rule["type"]
                    if rtype == "regex" and "email" in field:
                        sample_fields[field] = "user@example.com"
                    elif rtype == "regex" and "phone" in field:
                        sample_fields[field] = "+1234567890"
                    elif rtype == "regex":
                        sample_fields[field] = "sample_value"
                    elif rtype in ("min", "max", "range"):
                        sample_fields[field] = 25
                    elif rtype == "not_empty":
                        sample_fields[field] = "Sample Value"
                    elif rtype == "min_length":
                        sample_fields[field] = "abcdefgh"
                    elif rtype == "date_format":
                        sample_fields[field] = "2024-01-15"
                    elif rtype == "unique":
                        sample_fields[field] = "unique-id-001"
                    else:
                        sample_fields[field] = "value"

        sample_json = json.dumps(sample_fields, indent=2)
        sample_json_inline = json.dumps(sample_fields)

        st.markdown("**Sample payload** (edit to match your data):")
        edited_sample = st.text_area("Sample Record", value=sample_json, height=150, key="guide_sample")
        try:
            sample_obj = json.loads(edited_sample)
            sample_json = json.dumps(sample_obj, indent=2)
            sample_json_inline = json.dumps(sample_obj)
        except json.JSONDecodeError:
            st.warning("Invalid JSON — using default sample")

        api_base = st.text_input("API Base URL (for generated snippets)", value=API_URL, key="guide_api_url")
        context_param = f', "context": "{guide_context}"' if guide_context else ""
        context_query = f"&context={guide_context}" if guide_context else ""

        st.markdown("---")

        # ── MCP (Claude Desktop / Cursor) ──
        with st.expander("MCP — Claude Desktop / Cursor (AI agent integration)", expanded=True):
            st.markdown(
                "Connect any MCP-compatible AI agent (Claude Desktop, Cursor, or a custom agent) "
                "directly to OpenDQV. Agents can discover contracts, validate records, and create "
                "draft contracts for human review — without writing any integration code."
            )
            st.markdown("**Step 1 — Install the MCP extra (if running from source):**")
            st.code("pip install opendqv[mcp]", language="bash")

            st.markdown("**Step 2 — Register in Claude Desktop** (`~/.claude/claude_desktop_config.json`):")
            claude_config = f'''{{\n  "mcpServers": {{\n    "opendqv": {{\n      "command": "python",\n      "args": ["/path/to/OpenDQV/mcp_server.py"],\n      "env": {{\n        "OPENDQV_AGENT_IDENTITY": "your.email@example.com",\n        "OPENDQV_API_URL": "{api_base}",\n        "OPENDQV_TOKEN": "{snippet_token}"\n      }}\n    }}\n  }}\n}}'''
            st.code(claude_config, language="json")
            st.caption("Replace `/path/to/OpenDQV/mcp_server.py` with the absolute path on your machine.")

            st.markdown("**Step 2 (alternative) — Register in Cursor** (Settings → MCP → Add Server):")
            st.code(
                f'Name: opendqv\nCommand: python /path/to/OpenDQV/mcp_server.py\nEnv: OPENDQV_AGENT_IDENTITY=your.email@example.com\n     OPENDQV_API_URL={api_base}\n     OPENDQV_TOKEN={snippet_token}',
                language="text",
            )

            st.markdown("**Available MCP tools:**")
            st.markdown("""
| Tool | Type | What it does |
|---|---|---|
| `validate_record` | read | Validate a single record against a named contract |
| `validate_batch` | read | Validate up to 10,000 records in one call |
| `list_contracts` | read | Discover all active contracts |
| `get_contract` | read | Get full contract detail including all rules |
| `explain_error` | read | Get plain-English remediation guidance for a rule failure |
| `create_contract_draft` | write | Create a DRAFT contract for a novel domain (requires `OPENDQV_AGENT_IDENTITY`) |
""")

            st.markdown("**Agent review workflow:**")
            st.markdown(
                "Contracts created by `create_contract_draft` land in **DRAFT** status with `source: mcp`. "
                "They cannot be activated until a human approves them: go to **Contracts → [contract name] → Submit for Review**, "
                "then an approver uses **Approve** to make it active. "
                "This gate is enforced at the API level and cannot be bypassed."
            )
            st.info(
                "Use a `validator` role token for agents that only validate data. "
                "Use an `editor` token if the agent also creates contract drafts. "
                "Never give an agent an `approver` token — approval must be a human action."
            )

        # ── cURL ──
        with st.expander("cURL (any system)", expanded=True):
            st.markdown("Copy this into any terminal or HTTP client:")
            curl_snippet = f'''curl -X POST {api_base}/api/v1/validate \\
  -H "Authorization: Bearer {snippet_token}" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "record": {sample_json},
    "contract": "{guide_contract}"{context_param}
  }}'
'''
            st.code(curl_snippet, language="bash")

        # ── Python SDK ──
        with st.expander("Python SDK", expanded=True):
            st.markdown("Install: `pip install httpx` (the SDK uses httpx under the hood)")
            python_snippet = f'''from sdk import OpenDQVClient

client = OpenDQVClient("{api_base}", token="{snippet_token}")

# Single record validation
record = {sample_json}
result = client.validate(record, contract="{guide_contract}"{', context="' + guide_context + '"' if guide_context else ''})

if result["valid"]:
    print("Record passed all quality checks")
    # proceed with your write/insert
else:
    print(f"Validation failed: {{len(result['errors'])}} error(s)")
    for err in result["errors"]:
        print(f"  {{err['field']}}: {{err['message']}}")
    # reject or quarantine the record
'''
            st.code(python_snippet, language="python")

            st.markdown("**FastAPI decorator pattern:**")
            decorator_snippet = f'''from sdk import OpenDQVClient, ValidationError

client = OpenDQVClient("{api_base}", token="{snippet_token}")

@app.post("/customers")
@client.guard(contract="{guide_contract}"{', context="' + guide_context + '"' if guide_context else ''})
async def create_customer(data: dict):
    # This only runs if data passes OpenDQV validation
    db.insert(data)
    return {{"status": "created"}}
'''
            st.code(decorator_snippet, language="python")

        # ── Salesforce Apex ──
        with st.expander("Salesforce (Apex HTTP Callout)"):
            st.markdown("Add this to an Apex class. Requires a Named Credential or Remote Site Setting for the OpenDQV URL.")
            apex_snippet = f'''public class OpenDQVValidator {{
    private static final String OPENDQV_URL = '{api_base}/api/v1/validate';
    private static final String TOKEN = '{snippet_token}';

    /**
     * Call OpenDQV to validate a record before insert/update.
     * Returns true if valid, false if blocked.
     */
    public static Boolean validateRecord(Map<String, Object> record, String contractName) {{
        HttpRequest req = new HttpRequest();
        req.setEndpoint(OPENDQV_URL);
        req.setMethod('POST');
        req.setHeader('Authorization', 'Bearer ' + TOKEN);
        req.setHeader('Content-Type', 'application/json');

        Map<String, Object> body = new Map<String, Object>{{
            'record' => record,
            'contract' => contractName{", 'context' => '" + guide_context + "'" if guide_context else ""}
        }};
        req.setBody(JSON.serialize(body));

        Http http = new Http();
        HttpResponse res = http.send(req);

        if (res.getStatusCode() == 200) {{
            Map<String, Object> result = (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
            return (Boolean) result.get('valid');
        }}
        // API error — fail open or closed based on your policy
        return false;
    }}

    // Example usage in a trigger:
    // Map<String, Object> data = new Map<String, Object>{{
    //     'email' => contact.Email,
    //     'name' => contact.Name,
    //     'age' => contact.Age__c
    // }};
    // if (!OpenDQVValidator.validateRecord(data, '{guide_contract}')) {{
    //     contact.addError('Record failed data quality validation');
    // }}
}}'''
            st.code(apex_snippet, language="java")

        # ── JavaScript / Node.js ──
        with st.expander("JavaScript / Node.js (fetch)"):
            js_snippet = f'''async function validateRecord(record) {{
  const response = await fetch("{api_base}/api/v1/validate", {{
    method: "POST",
    headers: {{
      "Authorization": "Bearer {snippet_token}",
      "Content-Type": "application/json",
    }},
    body: JSON.stringify({{
      record: record,
      contract: "{guide_contract}"{', context: "' + guide_context + '"' if guide_context else ''}
    }}),
  }});

  const result = await response.json();

  if (result.valid) {{
    console.log("Record passed validation");
    return true;
  }} else {{
    console.error("Validation failed:");
    result.errors.forEach(err =>
      console.error(`  ${{err.field}}: ${{err.message}}`)
    );
    return false;
  }}
}}

// Usage:
const record = {sample_json};
const isValid = await validateRecord(record);
if (isValid) {{
  // proceed with save
}}
'''
            st.code(js_snippet, language="javascript")

        # ── Power Automate / HTTP connector ──
        with st.expander("Power Automate / HTTP Connector"):
            st.markdown("Use the **HTTP** action in Power Automate (or Logic Apps):")
            st.markdown(f"""
**Method:** `POST`

**URI:** `{api_base}/api/v1/validate`

**Headers:**
| Key | Value |
|---|---|
| Authorization | Bearer `{snippet_token}` |
| Content-Type | application/json |

**Body:**
""")
            power_body = json.dumps({
                "record": sample_obj if 'sample_obj' in locals() else sample_fields,
                "contract": guide_contract,
                **({"context": guide_context} if guide_context else {}),
            }, indent=2)
            st.code(power_body, language="json")
            st.markdown("""
**Then add a Condition:**
- `body('HTTP')?['valid']` is equal to `true` → proceed
- Otherwise → send notification / reject
""")

        # ── GraphQL ──
        with st.expander("GraphQL"):
            fields_str = ", ".join(
                f'{k}: "{v}"' if isinstance(v, str) else f'{k}: {v}'
                for k, v in (sample_obj if 'sample_obj' in locals() else sample_fields).items()
            )
            gql_snippet = f'''mutation {{
  validate(
    record: {{{fields_str}}},
    contract: "{guide_contract}",
    {f'context: "{guide_context}",' if guide_context else ''}
    recordId: "your-tracking-id"
  ) {{
    valid
    recordId
    errors {{ field rule message severity }}
    warnings {{ field rule message severity }}
  }}
}}

# Endpoint: {api_base}/graphql
'''
            st.code(gql_snippet, language="graphql")

# ── Code Export ──────────────────────────────────────────────────────

if section == "Code Export":
    st.header("Code Export (Push-Down Mode)")
    st.markdown("Embed validation rules directly into source systems as generated code — useful when the system can't make HTTP calls.")

    col1, col2, col3 = st.columns(3)
    with col1:
        gen_contract = st.text_input("Contract", value=st.session_state.get("active_contract", "customer"), key="gen_contract")
    with col2:
        target = st.selectbox("Target Platform", ["snowflake", "salesforce", "js"])
    with col3:
        gen_context = st.text_input("Context (optional)", value="salesforce", key="gen_context")

    if st.button("Generate Code"):
        gen_params = {"contract_name": gen_contract, "target": target}
        if gen_context:
            gen_params["context"] = gen_context
        r = api_post(
            "/api/v1/generate",
            params=gen_params,
        )
        if r and r.status_code == 200:
            lang = {"snowflake": "sql", "salesforce": "java", "js": "javascript"}.get(target, "text")
            st.code(r.json()["code"], language=lang)
        elif r:
            st.error(f"Generation failed: {r.status_code} — {r.text}")

# ── Monitoring ───────────────────────────────────────────────────────

if section == "Monitoring":
    st.header("Monitoring Dashboard")
    st.markdown("Track validation metrics and system health in real-time.")

    if st.button("Refresh", key="refresh_stats"):
        pass  # Streamlit reruns on button click

    # ── Contracts pending review ──────────────────────────────────────
    _mon_contracts_r = api_get("/api/v1/contracts", params={"include_all": "true"})
    if _mon_contracts_r and _mon_contracts_r.status_code == 200:
        _all_contracts = _mon_contracts_r.json()
        _review_pending = [c for c in _all_contracts if c.get("status") == "review"]
        _mcp_drafts = [
            c for c in _all_contracts
            if c.get("status") == "draft" and c.get("source") == "mcp"
        ]
        if _review_pending or _mcp_drafts:
            st.markdown("---")
            st.markdown("### Human Action Required")
            if _review_pending:
                st.warning(
                    f"**{len(_review_pending)} contract(s) pending approval** — "
                    + ", ".join(f"`{c['name']}`" for c in _review_pending)
                    + "  \nGo to **Contracts** to Approve or Reject."
                )
            if _mcp_drafts:
                st.info(
                    f"**{len(_mcp_drafts)} agent-created draft(s)** waiting to be submitted for review — "
                    + ", ".join(f"`{c['name']}`" for c in _mcp_drafts)
                    + "  \nThese were created by an MCP agent and require human review before activation."
                )

    r = api_get("/api/v1/stats")
    if r and r.status_code == 200:
        data = r.json()

        # ── Summary metrics ──
        total = data["total_validations"]
        if total == 0:
            st.info("No validations recorded yet. Run some validations and come back!")
        else:
            uptime_hrs = data["uptime_seconds"] / 3600

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Total Validations", f"{total:,}")
            c2.metric("Passed", f"{data['total_pass']:,}")
            c3.metric("Failed", f"{data['total_fail']:,}")
            c4.metric("Pass Rate", f"{data['pass_rate']}%")
            c5.metric("Uptime", f"{uptime_hrs:.1f}h")

            # ── Recent history chart (charts first for glanceability) ──
            history = data.get("recent_history", [])
            if history:
                st.markdown("---")
                st.markdown("### Recent Validations")
                hist_df = pd.DataFrame(history)
                hist_df["ts"] = pd.to_datetime(hist_df["ts"])
                hist_df["result"] = hist_df["valid"].map({True: "Pass", False: "Fail"})

                chart_data = hist_df.groupby(["result"]).size().reset_index(name="count")
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Pass vs Fail**")
                    _mon_counts = chart_data.set_index("result")["count"]
                    _mon_wide = pd.DataFrame({
                        "Fail": [_mon_counts.get("Fail", 0)],
                        "Pass": [_mon_counts.get("Pass", 0)],
                    }, index=[""])
                    st.bar_chart(_mon_wide, color=["#EF5350", "#2196F3"])
                with col2:
                    st.markdown("**Latency (ms)**")
                    st.line_chart(hist_df.set_index("ts")["latency_ms"])

                with st.expander("Raw validation log"):
                    st.caption(
                        "`mode` = `single` (one-record call) or `batch` (multi-record call). "
                        "Both MCP agents and direct API callers appear in this log — "
                        "caller identity is tracked in the API audit log, not here."
                    )
                    st.dataframe(
                        hist_df[["ts", "contract", "context", "result", "errors", "warnings", "latency_ms", "mode"]].sort_values("ts", ascending=False),
                        hide_index=True,
                    )

            # ── Per contract/context breakdown (detail — collapsed by default) ──
            st.markdown("---")
            by_contract = data.get("by_contract", {})
            if by_contract:
                with st.expander("By Contract & Context", expanded=False):
                    rows = []
                    for key, vals in by_contract.items():
                        contract, context = key.split(":", 1) if ":" in key else (key, "none")
                        t = vals["pass"] + vals["fail"]
                        rate = round(vals["pass"] / t * 100, 1) if t > 0 else 0
                        rows.append({
                            "Contract": contract,
                            "Context": context,
                            "Pass": vals["pass"],
                            "Fail": vals["fail"],
                            "Total": t,
                            "Pass Rate": f"{rate}%",
                            "Errors": vals["errors"],
                            "Warnings": vals["warnings"],
                        })
                    st.dataframe(pd.DataFrame(rows), hide_index=True)

            # ── Top failing fields (detail — collapsed by default) ──
            top_fields = data.get("top_failing_fields", [])
            if top_fields:
                with st.expander("Top Failing Fields", expanded=False):
                    st.dataframe(
                        pd.DataFrame(top_fields).rename(columns={
                            "contract": "Contract", "field": "Field", "rule": "Rule", "count": "Failures"
                        }),
                        hide_index=True,
                    )

            # Bottom Refresh button so users don't scroll back to top
            st.markdown("---")
            if st.button("Refresh", key="refresh_stats_bottom"):
                pass

    elif r:
        st.error(f"Failed to load stats: {r.status_code} — {r.text}")

# ── Catalogs & AI ─────────────────────────────────────────────────────

if section == "Catalogs & AI":
    st.header("Catalog Links & AI Prompts")
    st.markdown("Link contracts to data catalogs for traceability and generate AI agent prompts for faster governance.")

    _qc_tab3, _qc_tab4 = st.tabs(["Catalog Connections", "AI Prompt Builder"])

    with _qc_tab3:
        _qc_marmot_url = os.environ.get("MARMOT_URL", "")
        _qc_r3 = api_get("/api/v1/contracts")
        if _qc_r3 and _qc_r3.status_code == 200:
            _qc_catalog_contracts = [c for c in _qc_r3.json() if c.get("asset_id")]
            st.caption(
                "Only contracts with an `asset_id` field appear here — it's optional and only needed "
                "when a data asset for this contract exists in an external catalog (Marmot, DataHub, Atlan, etc.). "
                f"Showing {len(_qc_catalog_contracts)} of {len(_qc_r3.json())} contracts."
            )
            if not _qc_catalog_contracts:
                st.info("No external catalog links configured yet. Add an `asset_id` to your contract YAML to connect with data catalogs like Marmot or DataHub for better traceability.")
            else:
                for _qc_c in _qc_catalog_contracts:
                    st.markdown(f"**{_qc_c['name']}** v{_qc_c['version']} ({_qc_c['status']})")
                    _qc_asset_id = _qc_c["asset_id"]
                    if _qc_asset_id.startswith(("opendqv://marmot/", "marmot://")):
                        _qc_catalog_type = "Marmot"
                        if _qc_marmot_url:
                            _qc_asset_name = _qc_asset_id.rstrip("/").split("/")[-1]
                            st.link_button("Open in Marmot ↗", f"{_qc_marmot_url}/assets/{_qc_asset_name}")
                        else:
                            st.code(_qc_asset_id)
                            st.caption(f"Catalog: {_qc_catalog_type}")
                    elif _qc_asset_id.startswith("urn:li:"):
                        st.code(_qc_asset_id)
                        st.caption("Catalog: DataHub")
                    elif _qc_asset_id.startswith("alation://"):
                        st.code(_qc_asset_id)
                        st.caption("Catalog: Atlan")
                    elif _qc_asset_id.startswith("https://"):
                        st.link_button("Open ↗", _qc_asset_id)
                    elif _qc_asset_id.startswith("urn:opendqv:"):
                        st.code(_qc_asset_id)
                        st.caption("OpenDQV Internal ID — update asset_id in your contract YAML to link to an external catalog")
                    else:
                        st.code(_qc_asset_id)
                        st.caption("OpenDQV Internal ID")
                if not _qc_marmot_url:
                    st.info(
                        "Marmot is a lightweight, open-source data catalog built in Go, using Git for metadata management. "
                        "Connect it to OpenDQV for seamless one-click access to your data assets and lineage. "
                        "Set the `MARMOT_URL` environment variable to your Marmot instance URL to get started."
                    )
        else:
            st.error("Could not load contracts from API.")

    with _qc_tab4:
        st.markdown("### MCP Composition Prompts")
        st.markdown(
            "Copy-paste these into Claude Desktop or any MCP-compatible agent "
            "with both OpenDQV and Marmot servers registered."
        )
        st.markdown("**Daily quality health check:**")
        st.code(
            "Using the opendqv MCP server, call get_quality_metrics for all contracts.\n"
            "For any contract with pass_rate below 0.95:\n"
            "  1. Read the catalog_hint field.\n"
            "  2. Call the marmot MCP server's get_asset tool with the asset name from catalog_hint.\n"
            "  3. Report the asset owner, failing rule names, and how many downstream assets depend on it.\n"
            "Format the output as a markdown table sorted by pass_rate ascending.",
            language=None,
        )
        st.markdown("**Incident triage:**")
        st.code(
            'The "{contract_name}" contract is showing elevated rejections.\n'
            '1. Call opendqv:get_quality_metrics("{contract_name}") — get the top failing rules.\n'
            "2. Call opendqv:explain_error for each failing rule to understand the remediation.\n"
            "3. Call marmot:get_asset using catalog_hint to find who owns this asset.\n"
            "4. Draft a message to the owner summarising: rejection count, top rules, and fix suggestions.",
            language=None,
        )
        st.caption("Requires both OpenDQV and Marmot MCP servers registered in your agent client.")
        st.markdown(
            "See [docs/mcp.md](https://github.com/OpenDQV/OpenDQV/blob/main/docs/mcp.md) for setup instructions."
        )

# ── Import Rules ─────────────────────────────────────────────────────

if section == "Import Rules":
    st.header("Import Rules")
    st.markdown("Convert external validation rules from dbt, Great Expectations, Soda, and more into OpenDQV contracts.")

    with st.expander("Import from Great Expectations", expanded=False):
        st.markdown("Paste a Great Expectations expectation suite JSON.")
        gx_json = st.text_area(
            "GX Suite JSON",
            height=250,
            placeholder='{"expectation_suite_name": "my_suite", "expectations": [...]}',
            key="gx_json_input",
        )
        gx_contract_name = st.text_input("Contract name (leave blank to use suite name)", value="", key="gx_contract_name")
        gx_save = st.checkbox("Save as contract", value=True, key="gx_save")
        if st.button("Import GX Suite", key="btn_import_gx"):
            if gx_json.strip():
                try:
                    suite = json.loads(gx_json)
                    params = {"save": gx_save}
                    if gx_contract_name.strip():
                        params["contract_name"] = gx_contract_name.strip()
                    r = api_post("/api/v1/import/gx", json=suite, params=params)
                    if r and r.status_code == 200:
                        data = r.json()
                        st.success(
                            f"Imported {data['stats']['imported']} rules, "
                            f"skipped {data['stats']['skipped']}"
                        )
                        if data.get("saved_to"):
                            st.info(f"Saved to: {data['saved_to']}")
                        if data.get("skipped"):
                            st.warning(f"Skipped {len(data['skipped'])} unsupported expectation(s):")
                            st.json(data["skipped"])
                        st.markdown("**Generated contract YAML preview:**")
                        st.code(yaml.dump(data["contract"], allow_unicode=True), language="yaml")
                    elif r:
                        st.error(f"Import failed: {r.status_code} — {r.text}")
                except json.JSONDecodeError:
                    st.error("Invalid JSON — please check the pasted content")
            else:
                st.warning("Paste a GX suite JSON first")

    with st.expander("Import from dbt", expanded=False):
        st.markdown("Paste a dbt `schema.yml` file. Each model becomes a separate contract.")
        dbt_yaml_input = st.text_area(
            "dbt schema.yml",
            height=250,
            placeholder="version: 2\nmodels:\n  - name: customers\n    columns:\n      - name: email\n        tests:\n          - not_null\n          - unique",
            key="dbt_yaml_input",
        )
        dbt_save = st.checkbox("Save contracts", value=True, key="dbt_save")
        if st.button("Import dbt Schema", key="btn_import_dbt"):
            if dbt_yaml_input.strip():
                try:
                    schema = yaml.safe_load(dbt_yaml_input)
                    r = api_post("/api/v1/import/dbt", json=schema, params={"save": dbt_save})
                    if r and r.status_code == 200:
                        data = r.json()
                        for item in data.get("contracts", []):
                            cname = item["contract"]["name"]
                            imported = item["stats"]["imported"]
                            skipped = item["stats"]["skipped"]
                            st.success(f"Contract '{cname}': {imported} rules imported, {skipped} skipped")
                            st.code(yaml.dump(item["contract"], allow_unicode=True), language="yaml")
                    elif r:
                        st.error(f"Import failed: {r.status_code} — {r.text}")
                except Exception as e:
                    st.error(f"YAML parse error: {e}")
            else:
                st.warning("Paste a dbt schema.yml first")

    with st.expander("Import from Soda", expanded=False):
        st.markdown("Paste Soda Core checks YAML. Each dataset becomes a contract.")
        soda_yaml_input = st.text_area(
            "Soda Checks YAML",
            height=250,
            placeholder="checks for customers:\n  - missing_count(email) = 0\n  - duplicate_count(id) = 0",
            key="soda_yaml_input",
        )
        soda_save = st.checkbox("Save contracts", value=True, key="soda_save")
        if st.button("Import Soda Checks", key="btn_import_soda"):
            if soda_yaml_input.strip():
                try:
                    checks = yaml.safe_load(soda_yaml_input)
                    r = api_post("/api/v1/import/soda", json=checks, params={"save": soda_save})
                    if r and r.status_code == 200:
                        data = r.json()
                        for item in data.get("contracts", []):
                            cname = item["contract"]["name"]
                            imported = item["stats"]["imported"]
                            skipped = item["stats"]["skipped"]
                            st.success(f"Contract '{cname}': {imported} rules imported, {skipped} skipped")
                            st.code(yaml.dump(item["contract"], allow_unicode=True), language="yaml")
                    elif r:
                        st.error(f"Import failed: {r.status_code} — {r.text}")
                except Exception as e:
                    st.error(f"YAML parse error: {e}")
            else:
                st.warning("Paste Soda checks YAML first")

    with st.expander("Import from CSV", expanded=False):
        st.markdown(
            "Paste CSV rules with columns: `field, rule_type, value, severity, error_message`."
        )
        csv_text_input = st.text_area(
            "CSV Rules",
            height=200,
            placeholder="field,rule_type,value,severity,error_message\nemail,not_empty,,error,Email is required\nage,min,0,error,Age cannot be negative",
            key="csv_rules_input",
        )
        csv_contract_name = st.text_input("Contract name", value="csv_import", key="csv_contract_name")
        csv_save = st.checkbox("Save as contract", value=True, key="csv_save")
        if st.button("Import CSV Rules", key="btn_import_csv"):
            if csv_text_input.strip():
                r = api_post(
                    "/api/v1/import/csv",
                    data=csv_text_input,
                    params={"contract_name": csv_contract_name, "save": csv_save},
                    extra_headers={"Content-Type": "text/plain"},
                )
                if r and r.status_code == 200:
                    data = r.json()
                    st.success(
                        f"Imported {data['stats']['imported']} rules, "
                        f"skipped {data['stats']['skipped']}"
                    )
                    st.code(yaml.dump(data["contract"], allow_unicode=True), language="yaml")
                elif r:
                    st.error(f"Import failed: {r.status_code} — {r.text}")
            else:
                st.warning("Paste CSV content first")

    with st.expander("Import / Export ODCS 3.1 (Open Data Contract Standard)", expanded=False):
        st.markdown(
            "Import an [ODCS 3.1](https://bitol-io.github.io/open-data-contract-standard/) contract "
            "(OpenMetadata, Soda, Monte Carlo, Data Contract CLI) or export any OpenDQV contract to ODCS format."
        )

        odcs_mode = st.radio("Mode", ["Import", "Export"], horizontal=True, key="odcs_mode")

        if odcs_mode == "Import":
            st.markdown("Paste an ODCS 3.1 contract as YAML or JSON.")
            odcs_input = st.text_area(
                "ODCS contract (YAML or JSON)",
                height=300,
                placeholder=(
                    "apiVersion: v3.1.0\nkind: DataContract\ninfo:\n  title: My Contract\n"
                    "  version: '1.0'\nschema:\n  - name: customers\n    properties:\n"
                    "      - name: email\n        required: true\n        unique: true"
                ),
                key="odcs_import_input",
            )
            odcs_save = st.checkbox("Save as contract", value=True, key="odcs_save")
            if st.button("Import ODCS", key="btn_import_odcs"):
                if odcs_input.strip():
                    try:
                        # Accept both YAML and JSON input
                        contract_data = yaml.safe_load(odcs_input)
                        r = api_post("/api/v1/import/odcs", json=contract_data, params={"save": odcs_save})
                        if r and r.status_code == 200:
                            data = r.json()
                            rule_count = data.get("rule_count", 0)
                            skipped = data.get("skipped_checks", [])
                            st.success(f"Imported {rule_count} rules" + (f", skipped {len(skipped)}" if skipped else ""))
                            if skipped:
                                st.warning(f"Skipped unsupported checks: {', '.join(skipped)}")
                            if data.get("saved_to"):
                                st.info(f"Saved to: {data['saved_to']}")
                            st.markdown("**Generated contract YAML preview:**")
                            st.code(yaml.dump(data["contract"], allow_unicode=True), language="yaml")
                        elif r:
                            st.error(f"Import failed: {r.status_code} — {r.text}")
                    except Exception as e:
                        st.error(f"Parse error: {e}")
                else:
                    st.warning("Paste an ODCS contract first")

        else:  # Export
            st.markdown("Export an existing OpenDQV contract as ODCS 3.1 YAML.")
            export_contracts_r = api_get("/api/v1/contracts")
            export_contract_names = []
            if export_contracts_r and export_contracts_r.status_code == 200:
                export_contract_names = [c["name"] for c in export_contracts_r.json()]

            if not export_contract_names:
                st.info("No contracts available.")
            else:
                _odcs_active = st.session_state.get("active_contract", "")
                _odcs_idx = export_contract_names.index(_odcs_active) if _odcs_active in export_contract_names else 0
                odcs_export_name = st.selectbox("Contract to export", export_contract_names, index=_odcs_idx, key="odcs_export_contract")
                odcs_export_version = st.text_input("Version", value="latest", key="odcs_export_version")
                if st.button("Export as ODCS 3.1", key="btn_export_odcs"):
                    r = api_get(
                        f"/api/v1/export/odcs/{odcs_export_name}",
                        params={"version": odcs_export_version},
                    )
                    if r and r.status_code == 200:
                        _ui_audit("contract_export", f"contract={odcs_export_name} version={odcs_export_version} format=odcs")
                        st.success(f"Exported '{odcs_export_name}' as ODCS 3.1")
                        st.code(r.text, language="yaml")
                        st.download_button(
                            "Download YAML",
                            data=r.text,
                            file_name=f"{odcs_export_name}_odcs.yaml",
                            mime="application/yaml",
                            key="odcs_download",
                        )
                    elif r:
                        st.error(f"Export failed: {r.status_code} — {r.text}")


# ── Profiler ─────────────────────────────────────────────────────────

if section == "Profiler":
    st.header("Data Profiler")
    st.markdown("Analyse sample data to auto-generate a validation contract — paste a JSON array and OpenDQV infers the rules. Aim for 50–100 records for best results.")

    profile_input = st.text_area(
        "JSON records (list of dicts)",
        height=250,
        placeholder='[\n  {"email": "john@example.com", "age": 25, "name": "John"},\n  {"email": "jane@example.com", "age": 30, "name": "Jane"}\n]',
        key="profiler_input",
    )
    col1, col2 = st.columns(2)
    with col1:
        prof_contract_name = st.text_input("Contract name", value="profiled", key="prof_name")
    with col2:
        prof_save = st.checkbox("Save as contract", value=True, key="prof_save")

    if st.button("Analyze", key="btn_profile"):
        if profile_input.strip():
            try:
                records = json.loads(profile_input)
                if not isinstance(records, list):
                    st.error("Input must be a JSON array of records")
                else:
                    r = api_post(
                        "/api/v1/profile",
                        json=records,
                        params={"contract_name": prof_contract_name, "save": prof_save},
                    )
                    if r and r.status_code == 200:
                        data = r.json()
                        rule_count = len(data["contract"]["rules"])
                        record_count = data["profile"]["record_count"]
                        st.success(f"Generated {rule_count} rules from {record_count} records")

                        # Field profile table
                        st.subheader("Field Profiles")
                        profile_rows = []
                        for field_name, info in data["profile"]["fields"].items():
                            profile_rows.append({
                                "Field": field_name,
                                "Type": info.get("type", ""),
                                "Null %": f"{info.get('null_pct', 0):.1%}",
                                "Unique %": f"{info.get('unique_pct', 0):.1%}",
                                "Unique Count": info.get("unique_count", ""),
                                "Min": info.get("min", ""),
                                "Max": info.get("max", ""),
                                "Mean": info.get("mean", ""),
                                "Std Dev": info.get("stddev", ""),
                                "Median": info.get("p50", ""),
                            })
                        if profile_rows:
                            st.dataframe(pd.DataFrame(profile_rows), hide_index=True)

                        # Percentile detail expander (numeric fields only)
                        pct_rows = [
                            {
                                "Field": fn,
                                "p25": fi.get("p25", ""),
                                "p50": fi.get("p50", ""),
                                "p75": fi.get("p75", ""),
                                "p95": fi.get("p95", ""),
                            }
                            for fn, fi in data["profile"]["fields"].items()
                            if "p25" in fi
                        ]
                        if pct_rows:
                            with st.expander("Percentile Detail"):
                                st.dataframe(pd.DataFrame(pct_rows), hide_index=True)

                        # Value distributions expander (low-cardinality string fields)
                        dist_fields = {
                            fn: fi["top_values"]
                            for fn, fi in data["profile"]["fields"].items()
                            if "top_values" in fi
                        }
                        if dist_fields:
                            with st.expander("Value Distributions"):
                                for fn, top_values in dist_fields.items():
                                    st.caption(fn)
                                    st.bar_chart(pd.Series(top_values))

                        # Suggested rules list
                        st.subheader("Suggested Rules")
                        rules = data["contract"].get("rules", [])
                        if rules:
                            rules_rows = [
                                {
                                    "Name": r_item.get("name", ""),
                                    "Field": r_item.get("field", ""),
                                    "Type": r_item.get("type", ""),
                                    "Severity": r_item.get("severity", ""),
                                }
                                for r_item in rules
                            ]
                            st.dataframe(pd.DataFrame(rules_rows), hide_index=True)

                        # YAML preview
                        st.subheader("Contract YAML Preview")
                        st.code(yaml.dump(data["contract"], allow_unicode=True), language="yaml")

                        if prof_save and data.get("saved_to"):
                            st.info(f"Saved to: {data['saved_to']}")
                    elif r:
                        st.error(f"Profile failed: {r.status_code} — {r.text}")
            except json.JSONDecodeError:
                st.error("Invalid JSON — input must be a JSON array")
        else:
            st.warning("Paste JSON records first")

# ── Webhooks ─────────────────────────────────────────────────────────

if section == "Webhooks":
    st.header("Webhooks")
    st.markdown("Set up HTTP notifications so your systems are alerted instantly when validation events occur — failures, warnings, or batch errors.")

    if not st.session_state.get("token"):
        st.warning("Set a PAT token in the sidebar to manage webhooks.")

    # Register
    st.subheader("Register Webhook")
    wh_url = st.text_input("Webhook URL", placeholder="https://hooks.slack.com/services/...", key="wh_url")
    wh_events = st.multiselect(
        "Events",
        ["opendqv.validation.failed", "opendqv.validation.warning", "opendqv.batch.failed"],
        default=["opendqv.validation.failed"],
        key="wh_events",
    )
    wh_contract_filter = st.text_input(
        "Contract filter (optional — leave blank for all contracts)",
        placeholder="customer, sf_contact",
        key="wh_contract_filter",
    )

    if st.button("Register", key="btn_register_webhook"):
        if wh_url.strip():
            body = {"url": wh_url.strip(), "events": wh_events}
            if wh_contract_filter.strip():
                body["contracts"] = [c.strip() for c in wh_contract_filter.split(",") if c.strip()]
            r = api_post("/api/v1/webhooks", json=body)
            if r and r.status_code == 200:
                st.success("Webhook registered successfully")
            elif r:
                st.error(f"Registration failed: {r.status_code} — {r.text}")
        else:
            st.warning("Enter a webhook URL first")

    # List
    st.subheader("Active Webhooks")
    r_list = api_get("/api/v1/webhooks")
    if r_list and r_list.status_code == 200:
        hooks = r_list.json()
        if hooks:
            hook_rows = [
                {
                    "URL": h.get("url", ""),
                    "Events": ", ".join(h.get("events", [])),
                    "Contracts": ", ".join(h.get("contracts", [])) or "all",
                }
                for h in hooks
            ]
            st.dataframe(pd.DataFrame(hook_rows), hide_index=True)

            # Remove
            st.subheader("Remove Webhook")
            rm_url = st.text_input("URL to remove", key="wh_rm_url")
            if st.button("Remove", key="btn_remove_webhook"):
                if rm_url.strip():
                    try:
                        r_del = requests.delete(
                            f"{API_URL}/api/v1/webhooks",
                            json={"url": rm_url.strip()},
                            headers=headers,
                        )
                        if r_del.status_code == 200:
                            st.success("Webhook removed")
                            st.rerun()
                        else:
                            st.error(f"Remove failed: {r_del.status_code} — {r_del.text}")
                    except requests.ConnectionError:
                        st.error(f"Cannot connect to API at {API_URL}")
                else:
                    st.warning("Enter the URL to remove")
        else:
            st.info("No webhooks registered")
    elif r_list:
        st.error(f"Failed to list webhooks: {r_list.status_code} — {r_list.text}")

# ── Audit Trail ──────────────────────────────────────────────────────

if section == "Audit Trail":
    st.header("Contract Audit Trail")
    st.markdown("Track contract versions, approvals, and governance history — who proposed, approved, or rejected each change, and when.")

    if not st.session_state.get("token"):
        st.warning("Set a PAT token in the sidebar to use version history features.")

    # Contract selector
    vh_contracts_r = api_get("/api/v1/contracts", params={"include_all": "true"})
    if vh_contracts_r and vh_contracts_r.status_code == 200:
        vh_contract_names = [c["name"] for c in vh_contracts_r.json()]
        if not vh_contract_names:
            st.info("No contracts found.")
        else:
            _vh_active = st.session_state.get("active_contract", "")
            _vh_idx = vh_contract_names.index(_vh_active) if _vh_active in vh_contract_names else 0
            vh_selected = st.selectbox("Contract", vh_contract_names, index=_vh_idx, key="vh_contract")

            if vh_selected:
                # Clear stale diff state on contract switch
                if st.session_state.get("vh_history_contract") != vh_selected:
                    for _k in ["vh_history", "vh_history_contract", "vh_diff_result", "vh_diff_a", "vh_diff_b"]:
                        st.session_state.pop(_k, None)

                # Governance Audit Trail
                st.subheader("Governance Audit Trail")
                if st.button("Load Audit Trail", key="btn_view_history"):
                    r_hist = api_get(f"/api/v1/contracts/{vh_selected}/history")
                    if r_hist and r_hist.status_code == 200:
                        history = r_hist.json().get("history", [])
                        st.session_state["vh_history"] = history
                        st.session_state["vh_history_contract"] = vh_selected
                        if history:
                            # Status badge helper
                            def _status_badge(s):
                                return {"active": "🟢", "review": "🟡", "draft": "🔵", "archived": "🔴"}.get(s, "⚪") + f" {s.upper()}"

                            # Hash chain validation
                            def _chain_ok(entries):
                                prev = ""
                                for e in entries:
                                    if e.get("prev_hash", "") != prev:
                                        return False
                                    prev = e.get("entry_hash", "") or ""
                                return True

                            chain_valid = _chain_ok(history)
                            if chain_valid:
                                st.success("✅ Hash chain intact — audit trail has not been tampered with")
                            else:
                                st.error("❌ Hash chain BROKEN — audit trail integrity compromised")

                            st.markdown("---")

                            # Timeline view
                            for i, h in enumerate(reversed(history)):
                                status = h.get("status", "")
                                version = h.get("version", "")
                                updated = (h.get("updated_at") or "")[:19]
                                proposed_by = h.get("proposed_by") or None
                                proposed_at = (h.get("proposed_at") or "")[:19] or None
                                approved_by = h.get("approved_by") or None
                                rejected_by = h.get("rejected_by") or None
                                rejected_at = (h.get("rejected_at") or "")[:19] or None
                                rejection_reason = h.get("rejection_reason") or None
                                rules_count = len(h.get("rules", []))

                                with st.container():
                                    col_badge, col_detail = st.columns([1, 4])
                                    with col_badge:
                                        st.markdown(f"### {_status_badge(status)}")
                                        st.caption(f"v{version}")
                                    with col_detail:
                                        st.markdown(f"**Updated:** {updated} &nbsp;|&nbsp; **Rules:** {rules_count} &nbsp;|&nbsp; **Owner:** {h.get('owner') or '—'}")
                                        # Governance trail for this entry
                                        trail_parts = []
                                        if proposed_by:
                                            trail_parts.append(f"📤 **Proposed by** {proposed_by}" + (f" at {proposed_at}" if proposed_at else ""))
                                        if approved_by:
                                            trail_parts.append(f"✅ **Approved by** {approved_by}")
                                        if rejected_by:
                                            reason_str = f" — *{rejection_reason}*" if rejection_reason else ""
                                            trail_parts.append(f"↩ **Rejected by** {rejected_by}" + (f" at {rejected_at}" if rejected_at else "") + reason_str)
                                        if trail_parts:
                                            st.markdown("  \n".join(trail_parts))
                                        else:
                                            st.caption("No maker-checker action recorded for this entry")
                                        if h.get("description"):
                                            st.caption(h["description"][:120] + ("…" if len(h.get("description","")) > 120 else ""))
                                if i < len(history) - 1:
                                    st.markdown("---")

                            # Compact table view (collapsible)
                            with st.expander("Raw history table"):
                                hist_rows = [
                                    {
                                        "Version": h.get("version", ""),
                                        "Status": h.get("status", ""),
                                        "Proposed By": h.get("proposed_by") or "—",
                                        "Approved By": h.get("approved_by") or "—",
                                        "Rejected By": h.get("rejected_by") or "—",
                                        "Rejection Reason": h.get("rejection_reason") or "—",
                                        "Rules": len(h.get("rules", [])),
                                        "Updated At": (h.get("updated_at") or "")[:19],
                                    }
                                    for h in history
                                ]
                                st.dataframe(pd.DataFrame(hist_rows), hide_index=True)
                        else:
                            st.info("No version history recorded yet for this contract.")
                    elif r_hist:
                        st.error(f"Failed to load history: {r_hist.status_code} — {r_hist.text}")

                st.markdown("---")

                # Bump Version
                st.subheader("Bump Version")
                new_ver = st.text_input("New version (e.g. 1.1.0)", key="new_ver")
                if st.button("Bump Version", key="btn_bump_version"):
                    if new_ver.strip():
                        r_bump = api_post(
                            f"/api/v1/contracts/{vh_selected}/version",
                            params={"new_version": new_ver.strip()},
                        )
                        if r_bump and r_bump.status_code == 200:
                            st.success(f"Version bumped to {new_ver.strip()}")
                            st.code(
                                yaml.dump(r_bump.json(), allow_unicode=True),
                                language="yaml",
                            )
                        elif r_bump:
                            st.error(f"Bump failed: {r_bump.status_code} — {r_bump.text}")
                    else:
                        st.warning("Enter a new version string first")

                st.markdown("---")

                # Diff Versions
                st.subheader("Diff Versions")
                history_for_diff = (
                    st.session_state.get("vh_history", [])
                    if st.session_state.get("vh_history_contract") == vh_selected
                    else []
                )

                if not history_for_diff:
                    st.info("Load the Audit Trail above first to enable version comparison.")
                else:
                    version_labels = [
                        f"v{h['version']} — {h['status']} ({(h.get('updated_at') or '')[:10]})"
                        for h in history_for_diff
                    ]
                    version_values = [h["version"] for h in history_for_diff]

                    col1, col2 = st.columns(2)
                    with col1:
                        idx_a = st.selectbox(
                            "Version A (older)", range(len(version_labels)),
                            format_func=lambda i: version_labels[i], key="diff_ver_a_idx",
                        )
                    with col2:
                        idx_b = st.selectbox(
                            "Version B (newer)", range(len(version_labels)),
                            index=min(idx_a + 1, len(version_labels) - 1),
                            format_func=lambda i: version_labels[i], key="diff_ver_b_idx",
                        )

                    if st.button("Compare Versions", key="btn_compare_versions"):
                        ver_a, ver_b = version_values[idx_a], version_values[idx_b]
                        if ver_a == ver_b:
                            st.warning("Select two different versions to compare.")
                        else:
                            r_diff = api_get(
                                f"/api/v1/contracts/{vh_selected}/diff",
                                params={"version_a": ver_a, "version_b": ver_b},
                            )
                            if r_diff and r_diff.status_code == 200:
                                st.session_state["vh_diff_result"] = r_diff.json()
                                st.session_state["vh_diff_a"] = ver_a
                                st.session_state["vh_diff_b"] = ver_b
                            elif r_diff:
                                st.error(f"Diff failed: {r_diff.status_code} — {r_diff.text}")

                    # Render stored diff result
                    diff_result = st.session_state.get("vh_diff_result")
                    if diff_result and st.session_state.get("vh_diff_a"):
                        ver_a = st.session_state["vh_diff_a"]
                        ver_b = st.session_state["vh_diff_b"]
                        st.markdown(f"**v{ver_a} → v{ver_b}**")
                        changes = diff_result.get("changes", {})
                        added   = changes.get("rules_added", [])
                        removed = changes.get("rules_removed", [])
                        changed = changes.get("rules_changed", [])
                        meta    = changes.get("metadata_changed", {})

                        if not added and not removed and not changed and not meta:
                            st.success("No differences between these versions.")
                        else:
                            if added:
                                st.markdown(f"##### Rules Added ({len(added)})")
                                for rule_item in added:
                                    st.success(f"+ **{rule_item['name']}** — `{rule_item.get('type', '')}` on `{rule_item.get('field', '')}`")
                            if removed:
                                st.markdown(f"##### Rules Removed ({len(removed)})")
                                for rule_item in removed:
                                    st.error(f"- **{rule_item['name']}** — `{rule_item.get('type', '')}` on `{rule_item.get('field', '')}`")
                            if changed:
                                st.markdown(f"##### Rules Changed ({len(changed)})")
                                for rule_item in changed:
                                    with st.expander(f"~ **{rule_item['name']}** (field: `{rule_item.get('field', '')}`)", expanded=True):
                                        for field_nm, delta in rule_item.get("changes", {}).items():
                                            c_field, c_old, c_arr, c_new = st.columns([2, 3, 1, 3])
                                            c_field.markdown(f"`{field_nm}`")
                                            c_old.markdown(
                                                f'<span style="color:#e57373">{delta.get("old", "—")}</span>',
                                                unsafe_allow_html=True,
                                            )
                                            c_arr.markdown("→")
                                            c_new.markdown(
                                                f'<span style="color:#81c784">{delta.get("new", "—")}</span>',
                                                unsafe_allow_html=True,
                                            )
                            if meta:
                                st.markdown("##### Metadata Changed")
                                for field_nm, delta in meta.items():
                                    st.info(f"**{field_nm}**: `{delta.get('old', '—')}` → `{delta.get('new', '—')}`")
    elif vh_contracts_r:
        st.error(f"Failed to load contracts: {vh_contracts_r.status_code}")

# ── CLI Guide ────────────────────────────────────────────────────────

if section == "CLI Guide":
    st.header("CLI Guide")
    st.markdown("Manage contracts and run validations from the terminal — useful for CI/CD pipelines, pre-commit hooks, and scripting without the Workbench.")

    st.markdown("---")

    st.subheader("Installation")
    st.code("pip install opendqv", language="bash")
    st.markdown("Or, if running from source:")
    st.code("python -m opendqv.cli --help", language="bash")

    st.markdown("---")

    st.subheader("List contracts")
    st.markdown("Show all loaded contracts and their current status.")
    st.code("opendqv list", language="bash")
    st.code(
        "# With archived contracts included\nopendqv list --all",
        language="bash",
    )

    st.markdown("---")

    st.subheader("Show contract detail")
    st.markdown("Print the full rule set for a contract.")
    st.code("opendqv show customer", language="bash")
    st.code("opendqv show customer --version 1.2.0", language="bash")

    st.markdown("---")

    st.subheader("Validate a record")
    st.markdown("Validate a single JSON record against a contract.")
    st.code(
        'opendqv validate --contract customer --record \'{"email": "alice@example.com", "age": 25}\'',
        language="bash",
    )
    st.code(
        "# Pipe from a file\nopendqv validate --contract customer --record @record.json",
        language="bash",
    )
    st.code(
        "# With context filter\nopendqv validate --contract customer --context salesforce --record @record.json",
        language="bash",
    )

    st.markdown("---")

    st.subheader("Export to Great Expectations")
    st.markdown("Generate a GX expectation suite from an OpenDQV contract.")
    st.code("opendqv export-gx customer", language="bash")
    st.code(
        "# Write to a file\nopendqv export-gx customer --out customer_suite.json",
        language="bash",
    )

    st.markdown("---")

    st.subheader("Import from Great Expectations")
    st.markdown("Convert a GX expectation suite into an OpenDQV contract.")
    st.code(
        "opendqv import-gx customer_suite.json",
        language="bash",
    )
    st.code(
        "# Save immediately to the contracts directory\nopendqv import-gx customer_suite.json --save",
        language="bash",
    )

    st.markdown("---")

    st.subheader("Import from dbt")
    st.markdown("Convert a dbt `schema.yml` into one or more OpenDQV contracts.")
    st.code(
        "opendqv import-dbt models/schema.yml",
        language="bash",
    )
    st.code(
        "# Save all generated contracts\nopendqv import-dbt models/schema.yml --save",
        language="bash",
    )

    st.markdown("---")

    st.subheader("Generate push-down validation code")
    st.markdown(
        "Export validation logic as SQL (Snowflake), Apex (Salesforce), or JavaScript — "
        "for embedding in source systems that cannot make HTTP calls."
    )
    st.code(
        "opendqv generate --contract customer --target snowflake",
        language="bash",
    )
    st.code(
        "opendqv generate --contract customer --target salesforce --out validator.cls",
        language="bash",
    )
    st.code(
        "opendqv generate --contract customer --target js --context web",
        language="bash",
    )

    st.markdown("---")

    st.subheader("Contract review workflow")
    st.markdown(
        "Contracts created by MCP agents land in **DRAFT** with `source: mcp` and must go through "
        "a review workflow before they can be activated. The same workflow is available for any draft."
    )
    st.code(
        "# Submit a draft contract for review (editor or above)\nopendqv submit-review customer --version 1.0.0 --proposed-by alice@example.com",
        language="bash",
    )
    st.code(
        "# Approve a contract under review\nopendqv approve customer --version 1.0.0 --approved-by lead-architect@example.com",
        language="bash",
    )
    st.code(
        "# Reject a contract back to draft\nopendqv reject customer --version 1.0.0 --reason \"Missing address field rules\" --rejected-by lead-architect@example.com",
        language="bash",
    )
    st.markdown(
        "Approving a contract activates it immediately — source systems can validate against it from that point forward. "
        "MCP-sourced contracts that skip the review workflow will be blocked at the API level when "
        "promotion to Active is attempted."
    )

    st.markdown("---")

    st.subheader("Token roles")
    st.markdown("Use `--role` when generating tokens to scope permissions:")
    st.code(
        "# Validator — validation only (source systems, ETL pipelines)\n"
        "opendqv token-generate salesforce-prod --role validator\n\n"
        "# Editor — validate + author DRAFT contracts + submit for review\n"
        "opendqv token-generate ci-pipeline --role editor\n\n"
        "# Approver — validate + approve/reject (pure reviewer, cannot author)\n"
        "opendqv token-generate lead-architect --role approver\n\n"
        "# Auditor — validate + audit trail access (compliance officers)\n"
        "opendqv token-generate compliance-team --role auditor",
        language="bash",
    )
    st.caption(
        "In `AUTH_MODE=open`, all tokens are capped to `validator` regardless of `--role`. "
        "Elevated roles require `AUTH_MODE=token`."
    )

    st.markdown("---")

    st.subheader("CLI vs API vs MCP — when to use which")
    st.markdown("""
| Use case | Recommended tool |
|---|---|
| CI/CD pipeline checks | CLI |
| Pre-commit validation hooks | CLI |
| Bulk import of rules from dbt / GX | CLI |
| Submit / approve contracts in scripts | CLI |
| Source system integration (runtime) | API |
| Webhook management | API or Workbench |
| Browsing contracts interactively | Workbench |
| Generating integration snippets | Workbench (Integration Guide tab) |
| AI agent validation (Claude Desktop, Cursor) | MCP |
| AI agent contract drafting | MCP + human review via Workbench |
""")

    st.markdown("---")

    st.subheader("Environment variables")
    st.code(
        "# Point the CLI at a remote OpenDQV instance\nexport OPENDQV_API_URL=https://dq.internal.example.com\nexport OPENDQV_TOKEN=pat_xxxxxxxxxxxxxxxx",
        language="bash",
    )
    st.markdown(
        "When `OPENDQV_API_URL` is set, all CLI commands will call the remote API instead of "
        "the local one. The token is passed as a Bearer header automatically."
    )

# ── Federation ───────────────────────────────────────────────────────

if section == "Federation":
    st.header("Federation")
    st.markdown("Monitor node health and federation status across OpenDQV instances. In standalone mode only node health and isolation events are active.")

    if st.button("Refresh", key="refresh_federation"):
        pass  # Streamlit reruns on button click

    # ── Node Status ──
    st.subheader("Node Status")
    status_r = api_get("/api/v1/federation/status")
    if status_r and status_r.status_code == 200:
        s = status_r.json()

        state_color = {"online": "green", "degraded": "orange", "isolated": "red"}.get(
            s.get("opendqv_node_state", "online"), "gray"
        )
        st.markdown(
            f"**Node ID:** `{s.get('opendqv_node_id', '—')}`  \n"
            f"**State:** :{state_color}[{s.get('opendqv_node_state', '—').upper()}]  \n"
            f"**Mode:** {'Federated' if s.get('is_federated') else 'Standalone'}  \n"
            f"**Audit mode:** {'On' if s.get('audit_mode') else 'Off'}"
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Contracts loaded", s.get("contracts_loaded", 0))
        c2.metric("Time in state (s)", f"{s.get('time_in_state_seconds', 0):.1f}")
        c3.metric("Isolated since", s.get("isolated_since") or "—")

        if s.get("upstream_url"):
            st.info(f"Upstream: {s['upstream_url']}")
    elif status_r:
        st.error(f"Status check failed: {status_r.status_code} — {status_r.text}")

    st.markdown("---")

    # ── Node Health Log ──
    st.subheader("Node Health Log")
    health_r = api_get("/api/v1/federation/health", params={"log_limit": 20})
    if health_r and health_r.status_code == 200:
        h = health_r.json()

        open_iso = h.get("open_isolation_events", [])
        if open_iso:
            st.error(f"{len(open_iso)} open isolation event(s)")
            for evt in open_iso:
                st.write(f"  - Opened {evt.get('started_at', '')} — trigger: {evt.get('trigger', '')}")

        health_log = h.get("health_log", [])
        if health_log:
            log_rows = [
                {
                    "State": e.get("state", ""),
                    "Reason": e.get("reason", ""),
                    "At": e.get("transitioned_at", "")[:19],
                }
                for e in health_log
            ]
            st.dataframe(log_rows, hide_index=True)
        else:
            st.info("No health transitions recorded yet.")

        recent_iso = h.get("recent_isolation_events", [])
        if recent_iso:
            st.markdown("**Recent isolation events:**")
            iso_rows = [
                {
                    "Trigger": e.get("trigger", ""),
                    "Started": (e.get("started_at") or "")[:19],
                    "Ended": (e.get("ended_at") or "open")[:19],
                    "Duration (s)": e.get("duration_seconds") or "—",
                    "Exceeded threshold": "YES" if e.get("exceeded_threshold") else "no",
                }
                for e in recent_iso
            ]
            st.dataframe(iso_rows, hide_index=True)
    elif health_r:
        st.error(f"Health check failed: {health_r.status_code} — {health_r.text}")

    st.markdown("---")

    # ── Federation Event Log ──
    st.subheader("Federation Event Log")
    col1, col2 = st.columns(2)
    with col1:
        fed_since = st.number_input("Since LSN", min_value=0, value=0, step=1, key="fed_since")
    with col2:
        fed_contract = st.text_input("Filter by contract (optional)", value="", key="fed_contract")

    log_params = {"since": int(fed_since)}
    if fed_contract.strip():
        log_params["contract"] = fed_contract.strip()

    log_r = api_get("/api/v1/federation/log", params=log_params)
    if log_r and log_r.status_code == 200:
        log_data = log_r.json()
        events = log_data.get("events", [])
        st.caption(f"{log_data.get('count', 0)} event(s) since LSN {log_data.get('since', 0)}")
        if events:
            event_rows = [
                {
                    "LSN": e.get("lsn", ""),
                    "Type": e.get("event_type", ""),
                    "Contract": e.get("contract_name", ""),
                    "Version": e.get("contract_version", ""),
                    "Source": e.get("source_node", ""),
                    "Status": e.get("status", ""),
                    "At": (e.get("created_at") or "")[:19],
                }
                for e in events
            ]
            st.dataframe(event_rows, hide_index=True)
        else:
            st.info("No federation events recorded. Events appear when contracts are replicated across nodes.")
    elif log_r:
        st.error(f"Log failed: {log_r.status_code} — {log_r.text}")

    st.markdown("---")

    # ── Worker Heartbeats (from /health) ──
    st.subheader("Worker Heartbeats")
    hb_r = api_get("/health")
    if hb_r and hb_r.status_code == 200:
        hb = hb_r.json()
        c1, c2 = st.columns(2)
        c1.metric("Active workers", hb.get("worker_count", 0))
        c2.metric("Stale workers (>5 min)", hb.get("stale_worker_count", 0))
        if hb.get("stale_worker_count", 0) > 0:
            st.warning("Some workers have not validated recently. Check Gunicorn worker health.")
    elif hb_r:
        st.error(f"Health endpoint failed: {hb_r.status_code}")
