import SwiftUI

struct PermissionsView: View {
    @EnvironmentObject private var state: AppState
    @State private var refreshTrigger = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Permissions")
                .font(.system(size: 14, weight: .semibold))
                .padding(.bottom, 4)

            row("Microphone", status: state.permissions.microphone,
                why: "Voice input — yume listens when you hold Right Option.",
                pane: .microphone)
            row("Accessibility", status: state.permissions.accessibility,
                why: "Required by Hermes computer_use to drive Mac apps.",
                pane: .accessibility)
            row("Screen Recording", status: state.permissions.screenRecording,
                why: "Hermes computer_use captures app windows to find UI elements.",
                pane: .screenRecording)
            row("Input Monitoring", status: state.permissions.inputMonitoring,
                why: "Detect the global Right Option press reliably.",
                pane: .inputMonitoring)

            HStack {
                Spacer()
                Button("Recheck") {
                    state.permissionController.refresh { newState in
                        Task { @MainActor in
                            state.permissions = newState
                            refreshTrigger += 1
                        }
                    }
                }
            }
        }
        .padding(16)
        .frame(width: 420)
    }

    @ViewBuilder
    private func row(_ label: String,
                     status: PermissionState.Status,
                     why: String,
                     pane: PermissionController.SettingsPane) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(label).font(.system(size: 12, weight: .semibold))
                    statusBadge(status)
                }
                Text(why).font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Open") { state.permissionController.openSystemSettings(pane: pane) }
                .buttonStyle(.bordered)
        }
        .padding(.vertical, 4)
    }

    private func statusBadge(_ status: PermissionState.Status) -> some View {
        let (text, color): (String, Color) = {
            switch status {
            case .granted: return ("Granted", .green)
            case .denied: return ("Denied", .red)
            case .unknown: return ("Unknown", .gray)
            }
        }()
        return Text(text)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(color)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 4).fill(color.opacity(0.15)))
    }
}
