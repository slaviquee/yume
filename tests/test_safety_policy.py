"""Safety policy tests. See docs/spec.md section 14 + 20.1."""
from __future__ import annotations

import pytest

from agent_service.safety_policy import evaluate, redact_secrets


class TestEvaluate:
    def test_low_risk_default(self):
        d = evaluate("Open TextEdit and draft a checklist", allowed_apps=("TextEdit",))
        assert d.risk == "low"
        assert not d.require_confirmation
        assert not d.is_blocked

    def test_blocked_on_secrets(self):
        d = evaluate("Type my password into 1Password", allowed_apps=("1Password",))
        assert d.is_blocked
        # Restricted app should also surface in reasons.
        assert any("1Password" in r for r in d.reasons)

    def test_blocked_on_2fa(self):
        d = evaluate("Enter the 2FA code")
        assert d.is_blocked

    def test_blocked_on_payment(self):
        d = evaluate("Type the credit card number into the checkout page")
        assert d.is_blocked

    def test_blocked_on_sudo(self):
        d = evaluate("Run sudo apt install something")
        assert d.is_blocked

    def test_confirm_on_send_email(self):
        d = evaluate("Send an email to Alice about the deadline")
        assert d.require_confirmation
        assert d.risk == "medium"

    def test_confirm_on_delete_files(self):
        d = evaluate("Delete the old files in Downloads")
        assert d.require_confirmation

    def test_confirm_on_install(self):
        d = evaluate("Install the new app from the website")
        assert d.require_confirmation

    def test_restricted_app_is_high_risk(self):
        d = evaluate("Open Terminal and check disk usage", allowed_apps=("Terminal",))
        assert d.risk in ("high", "blocked")
        assert d.require_confirmation or d.is_blocked
        assert "Terminal" in d.blocked_apps

    def test_safe_summary_task(self):
        d = evaluate("Summarize the current Safari tab", allowed_apps=("Safari",))
        assert d.risk == "low"
        assert not d.require_confirmation


class TestRedactSecrets:
    def test_redacts_long_token(self):
        s = "key_abcdefghijklmnopqrstuvwxyz1234"
        assert "[redacted]" in redact_secrets(s)

    def test_redacts_card_number(self):
        s = "card 4111 1111 1111 1111 used"
        assert "[redacted-card]" in redact_secrets(s)

    def test_keeps_domain(self):
        s = "email alice@example.com"
        out = redact_secrets(s)
        assert "example.com" in out
        assert "alice" not in out

    def test_does_not_break_normal_text(self):
        s = "open Safari and search for hackathon"
        assert redact_secrets(s) == s
