#!/usr/bin/env python3
"""Patch Hermes 0.13 computer_use for the installed cua-driver contract.

Hermes Agent 0.13's bundled computer_use wrapper expects an older
cua-driver tool surface:

* it calls `type_text_chars`, while cua-driver 0.1.9 exposes `type_text`
  with built-in AX/bulk insert plus character fallback;
* it omits `launch_app` from the public computer_use schema even though
  cua-driver exposes it;
* it stores the first launch window, which may be an off-screen accessory
  panel rather than the real document window.

This idempotent patch keeps yume's Hermes runtime aligned with the installed
cua-driver without adding app-specific shortcuts to yume itself.
"""
from __future__ import annotations

import sys
import sysconfig
from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str, present: str | None = None) -> bool:
    text = path.read_text(encoding="utf-8")
    if (present or new) in text:
        return False
    if old not in text:
        raise RuntimeError(f"{path}: could not find patch anchor for {label}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def ensure_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"missing Hermes file: {path}")


def patch_schema(base: Path) -> bool:
    path = base / "tools/computer_use/schema.py"
    ensure_file(path)
    changed = False
    changed |= replace_once(
        path,
        '                    "list_apps",\n                    "focus_app",\n',
        '                    "list_apps",\n                    "launch_app",\n                    "focus_app",\n',
        "launch_app enum",
    )
    changed |= replace_once(
        path,
        '            # \u2500\u2500 click / drag / scroll targeting \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n',
        '            "bundle_id": {\n'
        '                "type": "string",\n'
        '                "description": (\n'
        '                    "Optional. Bundle identifier for action=\'launch_app\', "\n'
        '                    "for example \'com.apple.TextEdit\'. Prefer this when "\n'
        '                    "list_apps returned an exact bundle_id."\n'
        '                ),\n'
        '            },\n'
        '            # \u2500\u2500 click / drag / scroll targeting \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n',
        "bundle_id property",
        present='            "bundle_id": {\n',
    )
    return changed


def patch_backend_interface(base: Path) -> bool:
    path = base / "tools/computer_use/backend.py"
    ensure_file(path)
    return replace_once(
        path,
        '    @abstractmethod\n'
        '    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:\n'
        '        """Route input to `app` (by name or bundle ID). Default: focus without raise."""\n',
        '    @abstractmethod\n'
        '    def launch_app(self, app: str = "", bundle_id: Optional[str] = None) -> ActionResult:\n'
        '        """Launch `app` (by name or bundle ID) without stealing focus when supported."""\n'
        '\n'
        '    @abstractmethod\n'
        '    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:\n'
        '        """Route input to `app` (by name or bundle ID). Default: focus without raise."""\n',
        "launch_app abstract method",
    )


def patch_tool(base: Path) -> bool:
    path = base / "tools/computer_use/tool.py"
    ensure_file(path)
    changed = False
    changed |= replace_once(
        path,
        '    "drag", "scroll", "type", "key", "set_value", "focus_app",\n',
        '    "drag", "scroll", "type", "key", "set_value", "launch_app", "focus_app",\n',
        "launch_app approval",
    )
    changed |= replace_once(
        path,
        '    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:\n'
        '        self.calls.append(("focus_app", {"app": app, "raise": raise_window}))\n'
        '        return ActionResult(ok=True, action="focus_app")\n',
        '    def launch_app(self, app: str = "", bundle_id: Optional[str] = None) -> ActionResult:\n'
        '        self.calls.append(("launch_app", {"app": app, "bundle_id": bundle_id}))\n'
        '        return ActionResult(ok=True, action="launch_app")\n'
        '\n'
        '    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:\n'
        '        self.calls.append(("focus_app", {"app": app, "raise": raise_window}))\n'
        '        return ActionResult(ok=True, action="focus_app")\n',
        "noop launch_app",
    )
    changed |= replace_once(
        path,
        '    if action == "focus_app":\n'
        '        return f"focus {args.get(\'app\', \'\')!r}" + (" (raise)" if args.get("raise_window") else "")\n'
        '    return action\n',
        '    if action == "focus_app":\n'
        '        return f"focus {args.get(\'app\', \'\')!r}" + (" (raise)" if args.get("raise_window") else "")\n'
        '    if action == "launch_app":\n'
        '        return f"launch {args.get(\'bundle_id\') or args.get(\'app\') or \'\'!r}"\n'
        '    return action\n',
        "launch_app summary",
    )
    changed |= replace_once(
        path,
        '    if action == "focus_app":\n'
        '        app = args.get("app")\n'
        '        if not app:\n'
        '            return json.dumps({"error": "focus_app requires `app`"})\n'
        '        res = backend.focus_app(app, raise_window=bool(args.get("raise_window")))\n'
        '        return _maybe_follow_capture(backend, res, capture_after)\n',
        '    if action == "launch_app":\n'
        '        app = args.get("app") or ""\n'
        '        bundle_id = args.get("bundle_id")\n'
        '        if not app and not bundle_id:\n'
        '            return json.dumps({"error": "launch_app requires `app` or `bundle_id`"})\n'
        '        res = backend.launch_app(app=str(app), bundle_id=str(bundle_id) if bundle_id else None)\n'
        '        return _maybe_follow_capture(backend, res, capture_after)\n'
        '\n'
        '    if action == "focus_app":\n'
        '        app = args.get("app")\n'
        '        if not app:\n'
        '            return json.dumps({"error": "focus_app requires `app`"})\n'
        '        res = backend.focus_app(app, raise_window=bool(args.get("raise_window")))\n'
        '        return _maybe_follow_capture(backend, res, capture_after)\n',
        "launch_app dispatch",
    )
    return changed


def patch_cua_backend(base: Path) -> bool:
    path = base / "tools/computer_use/cua_backend.py"
    ensure_file(path)
    changed = False
    changed |= replace_once(
        path,
        'def _parse_key_combo(keys: str) -> Tuple[Optional[str], List[str]]:\n'
        '    """Parse a key string like \'cmd+s\' into (key, modifiers).\n',
        'def _best_window(windows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:\n'
        '    """Pick the best automation target from cua-driver window records.\n'
        '\n'
        '    launch_app may return accessory/off-screen windows before the real document\n'
        '    or chooser window. Prefer a visible current-space window, then any visible\n'
        '    window, then fall back to the first record so later actions still have a pid.\n'
        '    """\n'
        '    if not windows:\n'
        '        return None\n'
        '    visible_current = [\n'
        '        w for w in windows\n'
        '        if w.get("is_on_screen", not w.get("off_screen", False))\n'
        '        and w.get("on_current_space", True)\n'
        '    ]\n'
        '    visible = [w for w in windows if w.get("is_on_screen", not w.get("off_screen", False))]\n'
        '    candidates = visible_current or visible or windows\n'
        '    return sorted(candidates, key=lambda w: int(w.get("z_index", 0) or 0))[0]\n'
        '\n'
        '\n'
        'def _parse_key_combo(keys: str) -> Tuple[Optional[str], List[str]]:\n'
        '    """Parse a key string like \'cmd+s\' into (key, modifiers).\n',
        "best window helper",
    )
    changed |= replace_once(
        path,
        '        # Safari WebKit AXTextField does not accept AX attribute writes (type_text),\n'
        '        # so use type_text_chars which synthesises individual key events instead.\n'
        '        # This works universally across all macOS apps in background mode.\n'
        '        return self._action("type_text_chars", {"pid": pid, "text": text})\n',
        '        args: Dict[str, Any] = {"pid": pid, "text": text}\n'
        '        if self._active_window_id is not None:\n'
        '            args["window_id"] = self._active_window_id\n'
        '        return self._action("type_text", args)\n',
        "type_text tool name",
    )
    changed |= replace_once(
        path,
        '            # hotkey requires at least one modifier + one key.\n'
        '            return self._action("hotkey", {"pid": pid, "keys": modifiers + [key_name]})\n',
        '            # hotkey requires at least one modifier + one key.\n'
        '            args: Dict[str, Any] = {"pid": pid, "keys": modifiers + [key_name]}\n'
        '            if self._active_window_id is not None:\n'
        '                args["window_id"] = self._active_window_id\n'
        '            return self._action("hotkey", args)\n',
        "hotkey window_id",
    )
    changed |= replace_once(
        path,
        '    def list_apps(self) -> List[Dict[str, Any]]:\n'
        '        out = self._session.call_tool("list_apps", {})\n'
        '        data = out["data"]\n',
        '    def list_apps(self) -> List[Dict[str, Any]]:\n'
        '        out = self._session.call_tool("list_apps", {})\n'
        '        sc = out.get("structuredContent") or {}\n'
        '        raw_apps = sc.get("apps") if isinstance(sc, dict) else None\n'
        '        if isinstance(raw_apps, list):\n'
        '            return raw_apps\n'
        '        data = out["data"]\n',
        "structured list_apps",
    )
    changed |= replace_once(
        path,
        '                    apps.append({"name": m.group(1).strip(), "pid": int(m.group(2))})\n',
        '                    name = m.group(1).strip().removeprefix("-").strip()\n'
        '                    apps.append({"name": name, "pid": int(m.group(2))})\n',
        "clean parsed app names",
    )
    changed |= replace_once(
        path,
        '    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:\n'
        '        """Target an app for subsequent actions without stealing system focus.\n',
        '    def launch_app(self, app: str = "", bundle_id: Optional[str] = None) -> ActionResult:\n'
        '        """Launch an installed app through cua-driver\'s native launch_app tool."""\n'
        '        target_bundle = bundle_id or self._resolve_bundle_id(app)\n'
        '        if not target_bundle:\n'
        '            return ActionResult(\n'
        '                ok=False,\n'
        '                action="launch_app",\n'
        '                message=f"No bundle_id found for app {app!r}. Call list_apps first.",\n'
        '            )\n'
        '\n'
        '        out = self._session.call_tool("launch_app", {"bundle_id": target_bundle})\n'
        '        ok = not out["isError"]\n'
        '        data = out.get("structuredContent") or out["data"]\n'
        '        meta = data if isinstance(data, dict) else {}\n'
        '        name = str(meta.get("name") or app or target_bundle)\n'
        '        pid = meta.get("pid")\n'
        '        windows = meta.get("windows") if isinstance(meta.get("windows"), list) else []\n'
        '        if pid:\n'
        '            try:\n'
        '                self._active_pid = int(pid)\n'
        '            except (TypeError, ValueError):\n'
        '                pass\n'
        '        target_window = _best_window(windows)\n'
        '        if target_window:\n'
        '            try:\n'
        '                self._active_window_id = int(target_window.get("window_id"))\n'
        '            except (AttributeError, TypeError, ValueError):\n'
        '                pass\n'
        '        return ActionResult(\n'
        '            ok=ok,\n'
        '            action="launch_app",\n'
        '            message=f"Launched {name}" + (f" (pid {pid})" if pid else ""),\n'
        '            meta=meta,\n'
        '        )\n'
        '\n'
        '    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:\n'
        '        """Target an app for subsequent actions without stealing system focus.\n',
        "launch_app backend method",
    )
    changed |= replace_once(
        path,
        '        return ActionResult(ok=False, action="focus_app",\n'
        '                            message=f"No on-screen window found for app \'{app}\'.")\n'
        '\n'
        '    # \u2500\u2500 Internal \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n',
        '        return ActionResult(ok=False, action="focus_app",\n'
        '                            message=f"No on-screen window found for app \'{app}\'.")\n'
        '\n'
        '    def _resolve_bundle_id(self, app: str) -> Optional[str]:\n'
        '        needle = app.strip().lower()\n'
        '        if not needle:\n'
        '            return None\n'
        '        apps = self.list_apps()\n'
        '        exact = [\n'
        '            a for a in apps\n'
        '            if str(a.get("name", "")).lower() == needle\n'
        '            or str(a.get("bundle_id", "")).lower() == needle\n'
        '        ]\n'
        '        if exact:\n'
        '            return str(exact[0].get("bundle_id") or "")\n'
        '        partial = [\n'
        '            a for a in apps\n'
        '            if needle in str(a.get("name", "")).lower()\n'
        '            or needle in str(a.get("bundle_id", "")).lower()\n'
        '        ]\n'
        '        if partial:\n'
        '            return str(partial[0].get("bundle_id") or "")\n'
        '        return None\n'
        '\n'
        '    # \u2500\u2500 Internal \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n',
        "bundle resolver",
        present="    def _resolve_bundle_id(self, app: str) -> Optional[str]:\n",
    )
    return changed


def main() -> int:
    base = Path(sysconfig.get_paths()["purelib"])
    try:
        changed = [
            patch_schema(base),
            patch_backend_interface(base),
            patch_tool(base),
            patch_cua_backend(base),
        ]
    except Exception as exc:  # noqa: BLE001
        print(f"hermes computer_use patch failed: {exc}", file=sys.stderr)
        return 1
    if any(changed):
        print("patched Hermes computer_use for installed cua-driver")
    else:
        print("Hermes computer_use patch already applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
