import SwiftUI

/// Small floating HUD shown over the user's workspace. States per docs/spec.md
/// section 12.2.
struct AssistantHUD: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                stateIndicator
                Text(stateLabel)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.primary)
                Spacer()
                if !state.workers.isEmpty {
                    Text("\(state.workers.filter { !["completed","failed","cancelled"].contains($0.state) }.count) agents")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.secondary)
                }
            }
            if !primaryText.isEmpty {
                Text(primaryText)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .truncationMode(.tail)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(.ultraThinMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(.white.opacity(0.08), lineWidth: 1)
        )
        .padding(8)
    }

    private var stateIndicator: some View {
        Circle()
            .fill(stateColor)
            .frame(width: 10, height: 10)
            .overlay(
                Circle()
                    .strokeBorder(stateColor.opacity(0.4), lineWidth: 4)
                    .scaleEffect(pulse ? 1.6 : 1.0)
                    .opacity(pulse ? 0 : 1)
                    .animation(.easeOut(duration: 1.0).repeatForever(autoreverses: false), value: pulse)
            )
    }

    private var pulse: Bool {
        switch state.voiceState {
        case .listeningPushToTalk, .listeningContinuous, .speaking, .thinking: return true
        default: return false
        }
    }

    private var stateColor: Color {
        switch state.voiceState {
        case .idle: return .secondary
        case .listeningPushToTalk, .listeningContinuous: return .red
        case .transcribing, .thinking: return .yellow
        case .speaking: return .green
        case .interrupted: return .orange
        case .error: return .red
        }
    }

    private var stateLabel: String {
        switch state.voiceState {
        case .idle: return state.continuousMode ? "Continuous on" : "yume"
        case .listeningPushToTalk: return "Listening…"
        case .listeningContinuous: return "Listening (continuous)"
        case .transcribing: return "Transcribing…"
        case .thinking: return "Thinking…"
        case .speaking: return "Speaking"
        case .interrupted: return "Stopped"
        case .error: return "Error"
        }
    }

    private var primaryText: String {
        if let err = state.lastError { return err }
        switch state.voiceState {
        case .listeningPushToTalk, .listeningContinuous, .transcribing:
            return state.liveTranscript
        case .speaking, .thinking:
            return state.lastAssistantText
        default:
            return ""
        }
    }
}
