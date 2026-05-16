import Foundation
import Starscream

/// WebSocket client to the agent_service (localhost). Submits user turns,
/// receives streamed assistant speech text + worker events.
final class AgentClient: WebSocketDelegate {
    enum Message {
        case thinking(turnId: String, active: Bool)
        case sayStart(turnId: String, utteranceId: String, interruptible: Bool)
        case sayChunk(utteranceId: String, text: String)
        case sayEnd(utteranceId: String, interrupted: Bool)
        case workerStarted(WorkerSummary)
        case workerProgress(taskId: String, state: String, message: String, lastAction: String, needsUser: Bool)
        case workerResult(taskId: String, status: String, summary: String)
        case workerNeedsConfirmation(ConfirmationRequest)
        case workersSnapshot([WorkerSummary])
        case error(code: String, message: String)
    }

    private var socket: WebSocket?
    private var continuation: AsyncStream<Message>.Continuation?
    private var connected = false
    private var pending: [Data] = []

    func connect() -> AsyncStream<Message> {
        AsyncStream { continuation in
            self.continuation = continuation
            self.openSocket()
            continuation.onTermination = { @Sendable _ in
                self.disconnect()
            }
        }
    }

    func disconnect() {
        socket?.disconnect()
        socket = nil
        connected = false
    }

    private func openSocket() {
        let port = ProcessInfo.processInfo.environment["YUME_AGENT_PORT"] ?? "7422"
        guard let url = URL(string: "ws://127.0.0.1:\(port)") else { return }
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        let ws = WebSocket(request: request)
        ws.delegate = self
        ws.connect()
        socket = ws

        DispatchQueue.global().asyncAfter(deadline: .now() + 3) { [weak self] in
            guard let self, !self.connected else { return }
            self.openSocket()
        }
    }

    // MARK: - Outbound

    func submitTurn(turnId: String, text: String) {
        send(["type": "turn.submit", "turnId": turnId, "text": text])
    }

    func confirmationResponse(confirmationId: String, decision: String) {
        send(["type": "confirmation.response", "confirmationId": confirmationId, "decision": decision])
    }

    func cancelWorker(taskId: String) {
        send(["type": "worker.cancel", "taskId": taskId])
    }

    func cancelAllWorkers() {
        send(["type": "worker.cancel_all"])
    }

    func listWorkers() {
        send(["type": "workers.list"])
    }

    func sendStop() {
        send(["type": "stop"])
    }

    private func send(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let str = String(data: data, encoding: .utf8) else { return }
        if connected {
            socket?.write(string: str)
        } else {
            pending.append(data)
        }
    }

    private func flushPending() {
        for d in pending {
            if let s = String(data: d, encoding: .utf8) { socket?.write(string: s) }
        }
        pending.removeAll()
    }

    func didReceive(event: WebSocketEvent, client: WebSocketClient) {
        switch event {
        case .connected:
            connected = true
            flushPending()
        case .disconnected, .cancelled, .error:
            connected = false
            DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in self?.openSocket() }
        case .text(let text):
            handle(text)
        case .binary, .ping, .pong, .viabilityChanged, .reconnectSuggested, .peerClosed:
            break
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let kind = obj["type"] as? String ?? ""
        switch kind {
        case "agent.thinking":
            continuation?.yield(.thinking(
                turnId: obj["turnId"] as? String ?? "",
                active: obj["active"] as? Bool ?? false
            ))
        case "agent.say_start":
            continuation?.yield(.sayStart(
                turnId: obj["turnId"] as? String ?? "",
                utteranceId: obj["utteranceId"] as? String ?? "",
                interruptible: obj["interruptible"] as? Bool ?? true
            ))
        case "agent.say_chunk":
            continuation?.yield(.sayChunk(
                utteranceId: obj["utteranceId"] as? String ?? "",
                text: obj["text"] as? String ?? ""
            ))
        case "agent.say_end":
            continuation?.yield(.sayEnd(
                utteranceId: obj["utteranceId"] as? String ?? "",
                interrupted: obj["interrupted"] as? Bool ?? false
            ))
        case "worker.started":
            let summary = WorkerSummary(
                taskId: obj["taskId"] as? String ?? "",
                title: obj["title"] as? String ?? "Task",
                state: "queued",
                riskLevel: obj["riskLevel"] as? String ?? "low",
                targetApps: obj["targetApps"] as? [String] ?? []
            )
            continuation?.yield(.workerStarted(summary))
        case "worker.progress":
            continuation?.yield(.workerProgress(
                taskId: obj["taskId"] as? String ?? "",
                state: obj["state"] as? String ?? "running",
                message: obj["message"] as? String ?? "",
                lastAction: obj["lastAction"] as? String ?? "",
                needsUser: obj["needsUser"] as? Bool ?? false
            ))
        case "worker.needs_confirmation":
            let req = ConfirmationRequest(
                confirmationId: obj["confirmationId"] as? String ?? "",
                taskId: obj["taskId"] as? String ?? "",
                prompt: obj["prompt"] as? String ?? "Proceed?",
                riskLevel: obj["riskLevel"] as? String ?? "medium",
                choices: obj["choices"] as? [String] ?? ["confirm", "cancel"]
            )
            continuation?.yield(.workerNeedsConfirmation(req))
        case "worker.result":
            continuation?.yield(.workerResult(
                taskId: obj["taskId"] as? String ?? "",
                status: obj["status"] as? String ?? "completed",
                summary: obj["summary"] as? String ?? ""
            ))
        case "workers.snapshot":
            let raw = obj["workers"] as? [[String: Any]] ?? []
            let summaries = raw.map { WorkerSummary(
                taskId: $0["taskId"] as? String ?? "",
                title: $0["title"] as? String ?? "",
                state: $0["state"] as? String ?? "queued",
                riskLevel: $0["riskLevel"] as? String ?? "low",
                lastMessage: $0["lastMessage"] as? String ?? "",
                lastAction: $0["lastAction"] as? String ?? "",
                needsUser: $0["needsUser"] as? Bool ?? false,
                targetApps: $0["targetApps"] as? [String] ?? [],
                summary: $0["summary"] as? String ?? "",
                elapsedSec: $0["elapsedSec"] as? Double ?? 0
            ) }
            continuation?.yield(.workersSnapshot(summaries))
        case "error":
            continuation?.yield(.error(
                code: obj["code"] as? String ?? "unknown",
                message: obj["message"] as? String ?? ""
            ))
        default:
            break
        }
    }
}
