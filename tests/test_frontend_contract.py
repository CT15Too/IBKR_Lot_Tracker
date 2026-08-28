from pathlib import Path


HTML = (Path(__file__).parents[1] / "frontend/index.html").read_text()


def test_settings_panel_contains_required_accessible_controls():
    for element_id in [
        "settingsBtn",
        "settingsPanel",
        "settingsTitle",
        "flexQueryId",
        "flexToken",
        "autoCheckUpdates",
        "appVersion",
        "lastUpdateCheck",
        "checkUpdatesBtn",
        "downloadUpdateBtn",
        "cancelUpdateBtn",
        "restartUpdateBtn",
        "deferUpdateBtn",
    ]:
        assert f'id="{element_id}"' in HTML
    assert 'role="dialog"' in HTML
    assert 'aria-labelledby="settingsTitle"' in HTML
    assert 'type="password"' in HTML


def test_frontend_uses_settings_and_update_contracts_without_storing_secret():
    for route in [
        "/api/settings",
        "/api/updates/status",
        "/api/updates/check",
        "/api/updates/download",
        "/api/updates/cancel",
        "/api/updates/restart",
        "/api/updates/defer",
    ]:
        assert route in HTML
    assert "savedToken" not in HTML
    assert "localStorage.setItem('flexToken'" not in HTML


def test_untrusted_update_text_is_rendered_with_text_content():
    assert "updateReleaseNotes.textContent" in HTML
    assert "updateMessage.textContent" in HTML
    assert "updateReleaseNotes.innerHTML" not in HTML
    assert "updateMessage.innerHTML" not in HTML


def test_all_update_states_and_browser_guidance_are_explicit():
    for status in [
        "idle",
        "checking",
        "update_available",
        "downloading",
        "ready_to_restart",
        "up_to_date",
        "failed",
    ]:
        assert f"case '{status}'" in HTML
    assert "Updates are installed through Git in browser mode" in HTML
