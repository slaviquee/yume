from __future__ import annotations

import os

import pytest

from agent_service.hermes_bridge import HermesTaskSpec, RealHermesBridge


def test_calculator_prompt_prefers_element_clicks() -> None:
    bridge = RealHermesBridge()
    prompt = bridge._build_prompt(
        HermesTaskSpec(
            task_id="task_test",
            title="Calculate 2+2",
            instruction="Open Calculator and add two plus two.",
            allowed_apps=("Calculator",),
        )
    )

    assert "For Calculator arithmetic" in prompt
    assert "numbered element indices" in prompt
    assert "Do not use computer_use(action='type'" in prompt


def test_clip_for_log_redacts_known_api_key_shapes() -> None:
    text = (
        "gradium=gsk_test_fake_secret_value "
        "anthropic=sk-ant-api03-secretvalue api_key=secret"
    )

    clipped = RealHermesBridge._clip_for_log(text)

    assert "2965c5" not in clipped
    assert "secretvalue" not in clipped
    assert "api_key=secret" not in clipped
    assert "gsk_[redacted]" in clipped
    assert "sk-ant-[redacted]" in clipped


@pytest.mark.asyncio
async def test_preflight_reports_blind_cuadriver(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "cua-driver"
    fake.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"structuredContent\":{\"windows\":[]}}'\n",
        encoding="utf-8",
    )
    os.chmod(fake, 0o755)
    monkeypatch.setattr("agent_service.hermes_bridge.shutil.which", lambda name: str(fake))

    message = await RealHermesBridge()._preflight_computer_use()

    assert message is not None
    assert "cannot see any macOS windows" in message


@pytest.mark.asyncio
async def test_preflight_accepts_visible_windows(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "cua-driver"
    fake.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"structuredContent\":{\"windows\":[{\"app_name\":\"Calculator\",\"pid\":123,\"window_id\":456}]}}'\n",
        encoding="utf-8",
    )
    os.chmod(fake, 0o755)
    monkeypatch.setattr("agent_service.hermes_bridge.shutil.which", lambda name: str(fake))

    message = await RealHermesBridge()._preflight_computer_use()

    assert message is None


@pytest.mark.asyncio
async def test_preflight_reports_accessibility_denied_after_window_list(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "cua-driver"
    fake.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"status\" ] || [ \"$1\" = \"serve\" ]; then\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$2\" = \"check_permissions\" ]; then\n"
        "  printf '%s\\n' '{\"content\":[{\"text\":\"Accessibility: NOT granted. Screen Recording: granted.\"}]}'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$2\" = \"list_windows\" ]; then\n"
        "  printf '%s\\n' '{\"structuredContent\":{\"windows\":[{\"app_name\":\"Calculator\",\"pid\":123,\"window_id\":456}]}}'\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\n' '{\"content\":[{\"text\":\"Accessibility permission not granted.\"}],\"isError\":true}'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    os.chmod(fake, 0o755)
    monkeypatch.setattr("agent_service.hermes_bridge.shutil.which", lambda name: str(fake))

    message = await RealHermesBridge()._preflight_computer_use()

    assert message is not None
    assert "macOS denied Accessibility access for CuaDriver.app" in message
