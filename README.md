# yume

Native macOS voice assistant. Hold **Right Option**, talk to your Mac. One foreground agent stays with you while background agents do real work in your apps.

> Status: hackathon MVP scaffold. Spec is in [`docs/spec.md`](docs/spec.md). This stage delivers the architecture and runnable scaffolding minus the gaze stretch layer.

## Architecture

```
┌─────────────────────────── macOS App (SwiftUI + AppKit) ────────────────────────────┐
│  Hotkey · Audio · HUD · Task drawer · Confirmation overlay                          │
└────────────────────────────────────┬────────────────────────────────────────────────┘
                                     │  localhost JSON-RPC + WebSocket events
              ┌──────────────────────┴──────────────────────┐
              ▼                                             ▼
   ┌────────────────────┐                       ┌──────────────────────────┐
   │  voice_service     │                       │  agent_service           │
   │  Gradium STT + TTS │                       │  Orchestrator            │
   │  (Python helper)   │                       │  Worker Manager          │
   └────────────────────┘                       │  Hermes Bridge           │
                                                │  Safety Policy           │
                                                └────────────┬─────────────┘
                                                             │ spawns
                                                             ▼
                                                ┌────────────────────────┐
                                                │  Hermes Agent          │
                                                │  computer_use          │
                                                └────────────────────────┘
```

* **Foreground agent** stays conversational, classifies turns, dispatches background work.
* **Background workers** run through Hermes Agent's `computer_use` tool — one per app, per-app locks, monotonic cancellation.
* **Gradium** drives speech-to-text (24 kHz PCM) and text-to-speech (48 kHz PCM streaming).

## Repo layout

```
app/                  Swift macOS app (SwiftUI + AppKit)
voice_service/        Python helper: Gradium STT/TTS + WS server for the app
agent_service/        Python: orchestrator, worker manager, Hermes bridge, safety
tests/                Pytest suites for agent + safety + worker invariants
docs/spec.md          Source of truth
```

## Prerequisites

* macOS 14 or later
* Xcode 16 (for the Swift app)
* `xcodegen` — `brew install xcodegen`
* Python 3.11+
* Hermes Agent with `computer_use` installed: `hermes computer-use install`
* A Gradium API key

## Quickstart

```bash
# 1. Install Python deps for both services
python3 -m venv .venv && source .venv/bin/activate
pip install -r voice_service/requirements.txt -r agent_service/requirements.txt

# 2. Configure
cp .env.example .env
# edit .env with GRADIUM_API_KEY, ANTHROPIC_API_KEY, etc.

# 3. Generate + build the Mac app
cd app && xcodegen generate && cd ..
make app

# 4. Run the helper services (two terminals or use the Makefile target)
make services
# or, for demo runs that keep helpers in the background:
make services-detached

# 5. Launch the Mac app
open app/build/Build/Products/Debug/yume.app
```

See [`docs/spec.md`](docs/spec.md) section 18 for the full implementation milestones.

## Safety

yume requires explicit confirmation before any of:

* sending messages or emails, submitting forms, deleting/moving files
* installing software, running terminal commands, editing system settings
* anything touching credentials, 2FA, payments, or secrets
* low-confidence UI targets

Screen content is treated as untrusted context, never as instructions. See `agent_service/safety_policy.py`.

## What's not here yet

The **gaze stretch layer** (section 19 of the spec) is intentionally not included in this stage. It will be added in a follow-up.

## License

MIT — see [LICENSE](LICENSE).
