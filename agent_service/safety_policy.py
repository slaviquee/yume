"""Safety policy. See docs/spec.md section 14.

This module is the authoritative gate. yume's own safety policy is checked
*before* a worker spec is sent to Hermes; Hermes safety is defense-in-depth.

The rules are intentionally explicit and easy to read so they can be reviewed
in a code review and audited at runtime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RiskLevel = Literal["low", "medium", "high", "blocked"]


# Always-block patterns — these tasks cannot run, even with confirmation.
BLOCKED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(password|passcode|api[\s-]?key|secret|2fa|two[\s-]?factor)\b", re.I),
    re.compile(r"\b(credit[\s-]?card|bank|wire[\s-]?transfer|payment[\s-]?details|cvv)\b", re.I),
    re.compile(r"\b(disable|turn off)\b.*\b(firewall|gatekeeper|sip|filevault)\b", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\brm\s+-rf\b", re.I),
)

# Patterns that always require user confirmation regardless of context.
CONFIRM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(send|reply|forward)\s+(an?\s+)?(email|message|imessage|sms|slack|dm)\b", re.I),
    re.compile(r"\b(submit|post)\b.*\b(form|application|review)\b", re.I),
    re.compile(r"\b(delete|trash|remove|erase|wipe)\b.*\b(files?|folders?|photos?|notes?)\b", re.I),
    re.compile(r"\b(install|uninstall|update)\b.*\b(app|software|extension|package)\b", re.I),
    re.compile(r"\brun\b.*\b(terminal|shell|command|script)\b", re.I),
    re.compile(r"\bchange\b.*\b(system|settings|preferences|configuration)\b", re.I),
    re.compile(r"\b(buy|purchase|order|checkout|pay)\b", re.I),
    re.compile(r"\bsign\s+(in|up|out)\b", re.I),
)

# Apps that are off-limits to background workers unless explicitly opted in
# (and even then a high-risk confirmation is required).
RESTRICTED_APPS: frozenset[str] = frozenset(
    {
        "System Settings",
        "System Preferences",
        "Keychain Access",
        "Terminal",
        "iTerm",
        "iTerm2",
        "1Password",
        "Bitwarden",
        "Wallet",
        "Banking",
    }
)


@dataclass(frozen=True)
class SafetyDecision:
    risk: RiskLevel
    require_confirmation: bool
    reasons: tuple[str, ...]
    blocked_apps: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return self.risk == "blocked"


def evaluate(instruction: str, allowed_apps: tuple[str, ...] = ()) -> SafetyDecision:
    """Classify a task instruction against yume's safety policy.

    Returns a decision the orchestrator must obey before dispatching work.
    Screen-quoted text fed back into instructions is treated as untrusted —
    callers must label any quoted content as such; this function inspects only
    the raw instruction it was given.
    """
    reasons: list[str] = []
    blocked_app_hits: list[str] = []
    risk: RiskLevel = "low"

    # Blocked patterns first.
    for pat in BLOCKED_PATTERNS:
        if pat.search(instruction):
            reasons.append(f"matches blocked pattern: {pat.pattern}")
            risk = "blocked"

    # Restricted app intersection.
    for app in allowed_apps:
        if app in RESTRICTED_APPS:
            blocked_app_hits.append(app)
            reasons.append(f"restricted app requested: {app}")
            if risk != "blocked":
                risk = "high"

    # Confirmation-required patterns.
    for pat in CONFIRM_PATTERNS:
        if pat.search(instruction):
            reasons.append(f"matches confirm pattern: {pat.pattern}")
            if risk == "low":
                risk = "medium"

    require_confirmation = risk in ("medium", "high")
    return SafetyDecision(
        risk=risk,
        require_confirmation=require_confirmation,
        reasons=tuple(reasons),
        blocked_apps=tuple(blocked_app_hits),
    )


def redact_secrets(text: str) -> str:
    """Conservative redaction for logs. Catches obvious shapes; not exhaustive."""
    redactions = [
        # API keys / tokens
        (re.compile(r"\b(sk|pk|key|token|secret|bearer)[-_]?[A-Za-z0-9]{20,}\b", re.I), "[redacted]"),
        # Credit card numbers
        (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[redacted-card]"),
        # Emails (preserve domain hint)
        (re.compile(r"\b[\w.+-]+@([\w-]+\.[\w.-]+)\b"), r"[redacted]@\1"),
    ]
    out = text
    for pat, repl in redactions:
        out = pat.sub(repl, out)
    return out
