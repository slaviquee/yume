import SwiftUI

struct TaskDrawer: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            if state.workers.isEmpty {
                emptyState
            } else {
                ScrollView {
                    VStack(spacing: 8) {
                        ForEach(state.workers) { worker in
                            WorkerRow(worker: worker) {
                                state.cancelWorker(taskId: worker.taskId)
                            }
                        }
                    }
                    .padding(12)
                }
            }
            if let req = state.pendingConfirmation {
                Divider()
                ConfirmationBanner(request: req,
                                   onConfirm: { state.respondToConfirmation("confirm") },
                                   onCancel: { state.respondToConfirmation("cancel") })
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Background tasks")
                .font(.system(size: 13, weight: .semibold))
            Spacer()
            if !state.workers.isEmpty {
                Button("Stop all") { state.cancelAllWorkers() }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.red)
            }
        }
        .padding(12)
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Text("No background workers")
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
            Text("Hold Right Option and ask yume to do something in the background.")
                .font(.system(size: 11))
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(24)
    }
}

struct WorkerRow: View {
    let worker: WorkerSummary
    let onCancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                stateDot
                Text(worker.title)
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(1)
                Spacer()
                if !isTerminal {
                    Button("Cancel", action: onCancel)
                        .buttonStyle(.borderless)
                        .foregroundStyle(.red)
                }
            }
            if !worker.lastMessage.isEmpty {
                Text(worker.lastMessage)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            HStack(spacing: 6) {
                Text(worker.state)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.tertiary)
                if !worker.targetApps.isEmpty {
                    Text("· \(worker.targetApps.joined(separator: ", "))")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
                Spacer()
                Text(String(format: "%.0fs", worker.elapsedSec))
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color.gray.opacity(0.08))
        )
    }

    private var isTerminal: Bool {
        ["completed", "failed", "cancelled"].contains(worker.state)
    }

    private var stateDot: some View {
        Circle()
            .fill(stateColor)
            .frame(width: 8, height: 8)
    }

    private var stateColor: Color {
        switch worker.state {
        case "running", "starting": return .green
        case "waiting_for_user_confirmation": return .orange
        case "paused": return .yellow
        case "completed": return .gray
        case "failed": return .red
        case "cancelled": return .gray
        default: return .blue
        }
    }
}

struct ConfirmationBanner: View {
    let request: ConfirmationRequest
    let onConfirm: () -> Void
    let onCancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(request.prompt)
                .font(.system(size: 12, weight: .semibold))
            HStack {
                Text(request.riskLevel.uppercased())
                    .font(.system(size: 10, weight: .bold))
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 4).fill(.orange.opacity(0.2)))
                    .foregroundStyle(.orange)
                Spacer()
                Button("Cancel", action: onCancel)
                Button("Confirm", action: onConfirm)
                    .keyboardShortcut(.return)
            }
        }
        .padding(12)
        .background(Color.orange.opacity(0.08))
    }
}
