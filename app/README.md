# yume Mac app

SwiftUI + AppKit menu-bar app. Generates an Xcode project from `project.yml`
via [XcodeGen](https://github.com/yonaskolb/XcodeGen).

## Build

```bash
brew install xcodegen     # one-time
cd app
xcodegen generate          # produces yume.xcodeproj
xcodebuild -project yume.xcodeproj -scheme yume \
  -configuration Debug -derivedDataPath build \
  CODE_SIGN_IDENTITY="-" CODE_SIGNING_REQUIRED=NO build
open build/Build/Products/Debug/yume.app
```

The Makefile at the repo root wraps both steps: `make app`.

## Modules

| File | Purpose |
| --- | --- |
| `yumeApp.swift` | `@main`, menu bar setup, floating HUD window. |
| `AppState.swift` | `@MainActor` observable that wires voice ↔ agent flows. |
| `HotkeyController.swift` | Global Right Option detection via `NSEvent` flags monitor. |
| `AudioCapture.swift` | AVAudioEngine tap → 24 kHz s16le PCM frames for Gradium. |
| `AudioPlayback.swift` | AVAudioEngine player for 48 kHz s16le PCM from Gradium. |
| `VoiceClient.swift` | localhost WS to `voice_service`. |
| `AgentClient.swift` | localhost WS to `agent_service`. |
| `MenuBar.swift` | NSStatusItem menu + popover. |
| `AssistantHUD.swift` | Floating capsule that shows voice state. |
| `TaskDrawer.swift` | Background worker list + inline confirmation banner. |
| `PermissionsView.swift` | TCC permission preflight UI. |
| `SettingsView.swift` | Settings tabs. |

## Permissions you'll need

The first time you run, macOS will prompt for:

1. **Microphone** — granted automatically the first time `AVAudioEngine.start()`
   tries to read input. Description in Info.plist.
2. **Accessibility** — required by Hermes' `computer_use`. Open System Settings
   from the permissions popover and toggle yume on.
3. **Screen Recording** — required by Hermes captures. Same flow.
4. **Input Monitoring** — required for reliable global Right Option capture.
   yume infers this from a CGEventTap test; if it reads "Denied" or
   "Unknown", grant it in System Settings.

## Right Option

Right Option is a modifier key. We detect it by watching `flagsChanged` events
and matching the hardware key code `0x3D`. Fallback `Option+Space` is handled
through the menu's "Start Listening" item.

## Dependencies

The app uses Apple's built-in `URLSessionWebSocketTask` for localhost helper
connections, so the stage-1 build has no Swift package dependency fetch.
