"""
End-to-end Playwright tests for the OpenDQV Workbench (Streamlit UI).

These tests verify that UI changes (rule editor, fork to version, draft banner)
are rendered correctly in the browser.

Requirements:
    pip install pytest-playwright
    playwright install chromium

Running:
    # With servers already running:
    pytest tests/test_e2e.py --headed  (visible browser)
    pytest tests/test_e2e.py           (headless)

    # The conftest below will auto-start servers if not running.
"""

import subprocess
import time
import socket
import pytest
import requests as _requests

STREAMLIT_URL = "http://localhost:8501"
API_URL = "http://localhost:8000"


def _port_open(host: str, port: int) -> bool:
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def api_server():
    """Tear down any existing stack, rebuild, and start fresh. Yields API URL when ready."""
    import os
    project_root = str(__file__).replace("/tests/test_e2e.py", "")

    # Ensure .env exists (docker-compose.yml requires it)
    env_file = os.path.join(project_root, ".env")
    env_example = os.path.join(project_root, ".env.example")
    if not os.path.exists(env_file) and os.path.exists(env_example):
        import shutil
        shutil.copy(env_example, env_file)

    # Kill any existing containers and wipe volumes for a clean slate
    subprocess.run(
        ["docker", "compose", "down", "-v", "--remove-orphans"],
        cwd=project_root,
        check=True,
    )

    # Release host ports that docker compose needs (8000, 8501).
    # A previous wizard run or dev session may have left host processes
    # bound to these ports, causing "address already in use" on docker up.
    for _port in (8000, 8501):
        subprocess.run(["fuser", "-k", f"{_port}/tcp"], capture_output=True)
    time.sleep(0.5)  # allow kernel to release the sockets

    # Rebuild images from current source
    subprocess.run(
        ["docker", "compose", "build"],
        cwd=project_root,
        check=True,
    )

    # Start api + ui (ui depends_on api service_healthy)
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=project_root,
        check=True,
    )

    # Poll until API is ready (up to 60s)
    for _ in range(60):
        if _port_open("localhost", 8000):
            break
        time.sleep(1)
    else:
        subprocess.run(["docker", "compose", "down", "-v", "--remove-orphans"], cwd=project_root)
        pytest.fail("API server failed to start within 60 seconds")

    yield API_URL

    # Ensure .env exists for teardown (docker compose down requires it)
    if not os.path.exists(env_file) and os.path.exists(env_example):
        import shutil
        shutil.copy(env_example, env_file)
    subprocess.run(
        ["docker", "compose", "down", "-v", "--remove-orphans"],
        cwd=project_root,
        check=False,
    )


@pytest.fixture(scope="session")
def streamlit_server(api_server):
    """Poll until Streamlit (started by api_server's docker compose up) is ready."""
    # Poll until Streamlit responds to HTTP (not just TCP port open) — up to 60s
    for _ in range(60):
        try:
            if _requests.get(STREAMLIT_URL, timeout=2).status_code < 500:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        pytest.fail("Streamlit server failed to respond within 60 seconds")

    yield STREAMLIT_URL
    # Teardown handled by api_server fixture


@pytest.fixture
def workbench(page, streamlit_server):
    """Navigate to the Streamlit workbench and wait for it to load."""
    page.goto(streamlit_server)
    # Wait for Streamlit to finish loading (the main title appears)
    page.wait_for_selector("h1", timeout=20000)
    return page


class TestWorkbenchLoads:
    """Basic smoke tests — workbench renders correctly."""

    def test_title_visible(self, workbench):
        """Workbench title is visible."""
        assert workbench.locator("h1").count() >= 1

    def test_sidebar_visible(self, workbench):
        """Sidebar navigation is rendered."""
        # Streamlit sidebar contains navigation buttons
        sidebar = workbench.locator("[data-testid='stSidebar']")
        assert sidebar.count() >= 1

    def test_reload_button_visible(self, workbench):
        """Reload button is present in the header area."""
        # networkidle fires before Streamlit's WebSocket-driven content renders;
        # wait explicitly for the app-rendered button text instead.
        workbench.wait_for_selector("button:has-text('↺')", timeout=20000)
        btns = workbench.locator("button").all()
        texts = [b.inner_text() for b in btns]
        assert any("Reload" in t or "↺" in t for t in texts), (
            f"Reload button not found. Button texts: {texts}"
        )


class TestContractsSection:
    """Verify the Contracts section renders the rule editor and fork button."""

    @pytest.fixture(autouse=True)
    def navigate_to_contracts(self, workbench):
        """Ensure we are in the Contracts section."""
        self.page = workbench
        # The Contracts section is the default — check we are there
        # by looking for the "Data Contracts" heading or filter input
        self.page.wait_for_load_state("networkidle", timeout=15000)

    def test_contracts_table_visible(self, workbench):
        """Contract list table is rendered."""
        # Wait for any dataframe or table to appear
        workbench.wait_for_selector("[data-testid='stDataFrame'], [data-testid='stTable'], .dataframe", timeout=15000)
        frames = workbench.locator("[data-testid='stDataFrame']").count()
        assert frames >= 1, "Expected at least one dataframe (contract list)"

    def test_search_filter_visible(self, workbench):
        """Search filter input is present."""
        # Wait for the input to appear — Streamlit renders dynamically via WebSocket
        workbench.wait_for_selector("input[placeholder*='Filter']", timeout=20000)
        search = workbench.locator("input[placeholder*='Filter']")
        assert search.count() >= 1, "Search filter input not found"

    def test_rule_editor_expander_exists(self, workbench):
        """Edit Rules expander is visible in the contract detail."""
        # Look for an expander with "Edit Rules" text
        # Streamlit expanders use data-testid="stExpander"
        workbench.wait_for_timeout(2000)  # let dynamic content load
        expanders = workbench.locator("[data-testid='stExpander']").all_text_contents()
        rule_editor_found = any("Edit Rules" in e or "edit" in e.lower() for e in expanders)
        assert rule_editor_found, (
            f"Rule editor expander not found. Expanders: {expanders}"
        )

    def test_fork_to_version_expander_exists(self, workbench):
        """Fork to new version expander is visible."""
        workbench.wait_for_timeout(2000)
        expanders = workbench.locator("[data-testid='stExpander']").all_text_contents()
        fork_found = any("Fork" in e or "fork" in e.lower() for e in expanders)
        assert fork_found, (
            f"Fork to new version expander not found. Expanders: {expanders}"
        )

    def test_lifecycle_section_visible(self, workbench):
        """Lifecycle management section is rendered."""
        workbench.wait_for_timeout(2000)
        # Look for the lifecycle buttons
        buttons = workbench.locator("button").all_text_contents()
        lifecycle_found = any(
            kw in b for b in buttons
            for kw in ["Active", "Draft", "Archive", "Restore", "Promote"]
        )
        assert lifecycle_found, f"Lifecycle buttons not found. Buttons: {buttons}"


class TestRuleEditorForm:
    """Verify the rule editor form is functional (ACT-036-02/05/09)."""

    def test_edit_rules_expander_opens(self, workbench):
        """Clicking the Edit Rules expander opens the form."""
        workbench.wait_for_timeout(2000)
        expanders = workbench.locator("[data-testid='stExpander']")
        for i in range(expanders.count()):
            text = expanders.nth(i).text_content()
            if "Edit Rules" in text:
                expanders.nth(i).click()
                workbench.wait_for_timeout(1000)
                # After clicking, form elements should appear
                # Look for any text input (rule name, field inputs)
                inputs = workbench.locator("input[type='text']").count()
                assert inputs >= 1, "Form inputs not found after opening Edit Rules expander"
                return
        pytest.skip("Edit Rules expander not found — may need contract selected")

    def test_rule_type_dropdown_has_options(self, workbench):
        """Rule type selectbox contains expected rule types."""
        workbench.wait_for_timeout(2000)
        # Open Edit Rules expander first
        expanders = workbench.locator("[data-testid='stExpander']")
        for i in range(expanders.count()):
            if "Edit Rules" in (expanders.nth(i).text_content() or ""):
                expanders.nth(i).click()
                workbench.wait_for_timeout(1000)
                break
        # Look for selectbox elements
        selects = workbench.locator("[data-testid='stSelectbox']").count()
        assert selects >= 1, "No selectbox found in rule editor form"


class TestContractDisplayQuality:
    """Contract name humanisation and clean initial load."""

    def test_contract_name_humanized(self, workbench):
        """Subheader for any contract with underscores in name is humanized (no raw underscores)."""
        workbench.wait_for_timeout(3000)
        # Verify the subheader text doesn't contain raw underscored names.
        # The default selected contract will be rendered humanized.
        headings = workbench.locator("h3, h2").all_text_contents()
        underscore_headings = [h for h in headings if "_" in h and h != "Data Contracts"]
        assert len(underscore_headings) == 0, \
            f"Raw underscored contract name found in subheader: {underscore_headings}"
        # Also confirm at least one contract detail heading is visible
        assert len(headings) >= 1, "No h2/h3 headings found on page"

    def test_no_draft_warning_on_clean_load(self, workbench):
        """No DRAFT status alert on initial page load — no draft contracts in contracts dir."""
        workbench.wait_for_timeout(3000)
        alerts = workbench.locator("[data-testid='stAlert']").all_text_contents()
        draft_alerts = [a for a in alerts if "DRAFT" in a]
        assert len(draft_alerts) == 0, f"Unexpected DRAFT warnings on load: {draft_alerts}"

    def test_industry_breadth_caption_visible(self, workbench):
        """Caption showing active contract count and industry count is present."""
        import re
        workbench.wait_for_timeout(3000)
        captions = workbench.locator("[data-testid='stCaptionContainer']").all_text_contents()
        pattern = re.compile(r"\d+ active contracts across \d+ industries")
        assert any(pattern.search(c) for c in captions), \
            f"Industry breadth caption not found. Captions: {captions}"


class TestIndustryFilter:
    """Industry multiselect filter behaves correctly."""

    def test_filter_multiselect_exists(self, workbench):
        """Industry multiselect renders on the contracts page."""
        workbench.wait_for_timeout(3000)
        # Look for multiselect with aria-label "Industry" or placeholder "All industries"
        multiselects = workbench.locator("[data-testid='stMultiSelect']")
        assert multiselects.count() >= 1, "No multiselect found on contracts page"

    def test_filter_reduces_contract_list(self, workbench):
        """Selecting an industry in the filter reduces the contract selectbox options."""
        workbench.wait_for_timeout(3000)
        # Count initial selectbox options
        contract_box = workbench.locator("[data-testid='stSelectbox']").first
        contract_box.click()
        workbench.wait_for_timeout(500)
        dropdown = workbench.locator("[data-testid='stSelectboxVirtualDropdown'] li")
        total_count = dropdown.count()
        # Close dropdown
        workbench.keyboard.press("Escape")
        workbench.wait_for_timeout(300)

        if total_count <= 1:
            pytest.skip("Not enough contracts to test filter reduction")

        # Click the industry multiselect and pick "Financial Services"
        multiselect = workbench.locator("[data-testid='stMultiSelect']").first
        multiselect.click()
        workbench.wait_for_timeout(500)
        option = workbench.locator("li[role='option']", has_text="financial")
        if option.count() == 0:
            pytest.skip("No industry option matching 'financial' found")
        option.first.click()
        workbench.wait_for_timeout(1500)

        # Count options again
        contract_box.click()
        workbench.wait_for_timeout(500)
        filtered_count = workbench.locator("[data-testid='stSelectboxVirtualDropdown'] li").count()
        workbench.keyboard.press("Escape")
        assert filtered_count < total_count, \
            f"Filter did not reduce options: {filtered_count} >= {total_count}"


class TestIndustrySetupWizard:
    """Industry setup wizard expander renders and contains expected controls."""

    def test_wizard_expander_visible(self, workbench):
        """An expander containing 'Industry setup' text exists."""
        workbench.wait_for_timeout(3000)
        expanders = workbench.locator("[data-testid='stExpander']")
        texts = [expanders.nth(i).text_content() or "" for i in range(expanders.count())]
        assert any("Industry setup" in t or "industry setup" in t.lower() for t in texts), \
            f"'Industry setup' expander not found. Expanders: {texts}"

    def test_wizard_opens_and_has_multiselect(self, workbench):
        """Clicking the Industry setup expander reveals a multiselect for industries."""
        workbench.wait_for_timeout(3000)
        expanders = workbench.locator("[data-testid='stExpander']")
        for i in range(expanders.count()):
            text = expanders.nth(i).text_content() or ""
            if "industry setup" in text.lower() or "Industry setup" in text:
                expanders.nth(i).click()
                workbench.wait_for_timeout(1000)
                multiselects = workbench.locator("[data-testid='stMultiSelect']")
                labels = [multiselects.nth(j).get_attribute("aria-label") or
                          multiselects.nth(j).text_content() or ""
                          for j in range(multiselects.count())]
                assert any("Keep" in lbl or "industr" in lbl.lower() for lbl in labels), \
                    f"Expected 'Keep these industries active' multiselect. Labels: {labels}"
                return
        pytest.skip("Industry setup expander not found")


class TestContractAuditLifecycle:
    """Tests for the Audit Trail tab."""

    def _navigate_to_version_history(self, page):
        """Navigate to the Audit Trail section via sidebar button."""
        sidebar = page.locator("[data-testid='stSidebar']")
        sidebar.locator("button", has_text="Audit Trail").click()
        page.wait_for_timeout(2000)

    def _load_audit_trail(self, page):
        """Navigate to Audit Trail, then click Load Audit Trail for the default contract."""
        self._navigate_to_version_history(page)
        page.locator("button", has_text="Load Audit Trail").click()
        page.wait_for_timeout(3000)

    def test_audit_header_visible(self, workbench):
        """Contract Audit Trail heading appears after navigating to Audit Trail."""
        self._navigate_to_version_history(workbench)
        heading = workbench.locator("text=Contract Audit Trail")
        assert heading.count() >= 1, "Contract Audit Trail heading not found"

    def test_contract_selector_exists(self, workbench):
        """A selectbox for choosing a contract is visible on the audit tab."""
        self._navigate_to_version_history(workbench)
        selectboxes = workbench.locator("[data-testid='stSelectbox']")
        assert selectboxes.count() >= 1, "No contract selector selectbox found on Audit Trail tab"

    def test_load_audit_trail_button_exists(self, workbench):
        """The Load Audit Trail button is present on the audit tab."""
        self._navigate_to_version_history(workbench)
        btn = workbench.locator("button", has_text="Load Audit Trail")
        assert btn.count() >= 1, "Load Audit Trail button not found"

    def test_load_audit_trail_responds(self, workbench):
        """Clicking Load Audit Trail produces a response — banner or info message.

        A fresh stack has no history (history is written when lifecycle operations
        occur). On empty history the UI shows st.info; on populated history it shows
        the hash-chain banner (st.success or st.error). Either proves the button
        works and the API is reachable.
        """
        self._load_audit_trail(workbench)
        success = workbench.locator("[data-testid='stSuccess']").count()
        error = workbench.locator("[data-testid='stError']").count()
        info = workbench.locator("[data-testid='stAlert']").count()
        assert (success + error + info) >= 1, (
            "Expected any response after clicking Load Audit Trail "
            "(hash chain banner, error, or no-history info message)"
        )

    @staticmethod
    def _container_token(project_root: str, role: str = "editor") -> str:
        """Generate a token with the given role inside the running API container."""
        import json as _json
        username = f"e2e-{role}"
        result = subprocess.run(
            ["docker", "compose", "exec", "api", "python", "-c",
             f"from security.auth import create_pat; import json; "
             f"print(json.dumps(create_pat('{username}', role='{role}')))"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return _json.loads(result.stdout.strip())["token"]

    @staticmethod
    def _set_workbench_token(page, token: str) -> None:
        """Expand the Developer tools sidebar expander and set the PAT token."""
        sidebar = page.locator("[data-testid='stSidebar']")
        # Expand the Developer tools expander (collapsed by default)
        dev_expander = sidebar.locator("[data-testid='stExpander']").filter(has_text="Developer tools")
        if dev_expander.count() > 0:
            dev_expander.click()
            page.wait_for_timeout(500)
        # Fill the PAT token input (first text input in Developer tools expander)
        token_input = sidebar.locator("[data-testid='stTextInput']").first
        token_input.locator("input").fill(token)
        sidebar.locator("button", has_text="Set Token").click()
        page.wait_for_timeout(1000)

    @staticmethod
    def _seed_history(base: str, project_root: str) -> tuple:
        """Seed audit history for agriculture_batch (default contract at index 0).

        Forks to a new draft version (99.0) and submits for review, generating
        a history entry. Returns (contract_name, editor_token).
        """
        import requests as _req
        import json as _json

        def _container_token(role):
            r = subprocess.run(
                ["docker", "compose", "exec", "api", "python", "-c",
                 f"from security.auth import create_pat; import json; "
                 f"print(json.dumps(create_pat('e2e-{role}2', role='{role}')))"],
                cwd=project_root, capture_output=True, text=True, check=True,
            )
            return _json.loads(r.stdout.strip())["token"]

        admin_h = {"Authorization": f"Bearer {_container_token('admin')}"}
        editor_token = _container_token("editor")
        editor_h = {"Authorization": f"Bearer {editor_token}"}

        contract = "agriculture_batch"
        # Bump to draft version 99.0 (idempotent — 409 if already exists)
        _req.post(
            f"{base}/api/v1/contracts/{contract}/version",
            params={"new_version": "99.0"},
            headers=admin_h,
        )
        # Submit draft for review (writes a history entry)
        _req.post(
            f"{base}/api/v1/contracts/{contract}/99.0/submit-review",
            json={"proposed_by": "e2e-test"},
            headers=editor_h,
        )
        return contract, editor_token

    def _load_audit_trail_authenticated(self, page, token: str) -> None:
        """Set token, navigate to Version History, and click Load Audit Trail.

        More robust than _load_audit_trail: waits for the button to be visible
        after each navigation step to handle Streamlit reruns from token setting.
        """
        self._set_workbench_token(page, token)
        page.wait_for_timeout(2000)  # allow Streamlit rerun from token set to finish
        self._navigate_to_version_history(page)
        # Wait explicitly for the button to appear after navigation
        btn = page.locator("button", has_text="Load Audit Trail")
        btn.wait_for(state="visible", timeout=10000)
        btn.click()
        page.wait_for_timeout(5000)  # allow API call + Streamlit rerender

    def test_audit_trail_with_history(self, workbench, api_server):
        """When history exists the hash-chain banner and status badges are visible."""
        project_root = str(__file__).replace("/tests/test_e2e.py", "")
        _, editor_token = self._seed_history(api_server, project_root)

        self._load_audit_trail_authenticated(workbench, editor_token)

        content = workbench.locator("[data-testid='stMainBlockContainer']").inner_text() or ""
        # Streamlit 1.40+ uses stAlert for all alert types (success, error, info, warning)
        alerts = workbench.locator("[data-testid='stAlert']").count()
        assert alerts >= 1, (
            f"Hash chain banner (stAlert) not shown. Visible text: {content[:400]}"
        )
        badges = [b for b in ["\U0001f7e2", "\U0001f535", "\U0001f7e1", "\U0001f534"] if b in content]
        assert len(badges) >= 1, f"No status badges in: {content[:400]}"

    def test_raw_history_expander_exists_with_history(self, workbench, api_server):
        """Raw history table expander is present when history exists."""
        project_root = str(__file__).replace("/tests/test_e2e.py", "")
        _, editor_token = self._seed_history(api_server, project_root)

        self._load_audit_trail_authenticated(workbench, editor_token)

        expanders = workbench.locator("[data-testid='stExpander']").all_text_contents()
        raw_found = any("Raw history table" in e for e in expanders)
        assert raw_found, (
            f"'Raw history table' expander not found. Expanders: {expanders}"
        )

    def test_audit_tab_does_not_crash(self, workbench):
        """Audit Trail section loads without a Streamlit Python exception."""
        self._navigate_to_version_history(workbench)
        # Streamlit renders exceptions with a heading containing "Error"
        # and a data-testid="stException" element
        exceptions = workbench.locator("[data-testid='stException']").count()
        assert exceptions == 0, "Streamlit rendered a Python exception on the Audit Trail tab"
