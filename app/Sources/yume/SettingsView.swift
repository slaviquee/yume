import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        TabView {
            general
                .tabItem { Label("General", systemImage: "gearshape") }
            voice
                .tabItem { Label("Voice", systemImage: "waveform") }
            agents
                .tabItem { Label("Agents", systemImage: "bolt.horizontal") }
            permissions
                .tabItem { Label("Permissions", systemImage: "lock.shield") }
        }
        .frame(width: 480, height: 360)
    }

    private var general: some View {
        Form {
            Toggle("Continuous mode", isOn: $state.continuousMode)
            HStack {
                Text("Activation key")
                Spacer()
                Text("Right Option")
                    .foregroundStyle(.secondary)
            }
            HStack {
                Text("Voice provider")
                Spacer()
                Text("Gradium")
                    .foregroundStyle(.secondary)
            }
        }
        .padding(20)
    }

    private var voice: some View {
        Form {
            HStack {
                Text("Voice service port")
                Spacer()
                Text(ProcessInfo.processInfo.environment["YUME_VOICE_PORT"] ?? "7421")
                    .foregroundStyle(.secondary)
                    .monospaced()
            }
            Text("STT: 24 kHz · 16-bit mono · 80 ms chunks")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("TTS: 48 kHz · 16-bit mono · whitespace-only chunking")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(20)
    }

    private var agents: some View {
        Form {
            HStack {
                Text("Agent service port")
                Spacer()
                Text(ProcessInfo.processInfo.environment["YUME_AGENT_PORT"] ?? "7422")
                    .foregroundStyle(.secondary)
                    .monospaced()
            }
            HStack {
                Text("Foreground model")
                Spacer()
                Text(ProcessInfo.processInfo.environment["YUME_FOREGROUND_MODEL"] ?? "claude-sonnet-4-20250514")
                    .foregroundStyle(.secondary)
                    .monospaced()
            }
            HStack {
                Text("Max concurrent workers")
                Spacer()
                Text("2")
                    .foregroundStyle(.secondary)
            }
        }
        .padding(20)
    }

    private var permissions: some View {
        PermissionsView()
            .padding(0)
    }
}
