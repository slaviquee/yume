import Foundation

/// WebSocket client to the voice_service (localhost). Sends mic frames as
/// base64 PCM, receives transcripts and TTS audio.
final class VoiceClient {
    enum Message {
        case transcript(turnId: String, text: String, isFinal: Bool, finalizedBy: String?)
        case ttsAudio(utteranceId: String, pcm: Data)
        case ttsDone(utteranceId: String)
        case error(code: String, message: String)
    }

    private var socket: URLSessionWebSocketTask?
    private var continuation: AsyncStream<Message>.Continuation?
    private var connected = false
    private var pending: [String] = []
    private var reconnectScheduled = false

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
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        connected = false
    }

    private func openSocket() {
        socket?.cancel(with: .goingAway, reason: nil)
        let port = ProcessInfo.processInfo.environment["YUME_VOICE_PORT"] ?? "7421"
        guard let url = URL(string: "ws://127.0.0.1:\(port)") else { return }
        let ws = URLSession.shared.webSocketTask(with: url)
        socket = ws
        connected = true
        reconnectScheduled = false
        ws.resume()
        flushPending()
        receiveNext()
    }

    // MARK: - Outbound API

    func sendSttStart(turnId: String, mode: String) {
        send(["type": "stt.start", "turnId": turnId, "mode": mode])
    }

    func sendSttFlush(turnId: String) {
        send(["type": "stt.flush", "turnId": turnId])
    }

    func sendSttStop(turnId: String) {
        send(["type": "stt.stop", "turnId": turnId])
    }

    func sendAudioFrame(turnId: String, pcm: Data) {
        guard !turnId.isEmpty else { return }
        let b64 = pcm.base64EncodedString()
        send(["type": "stt.audio", "turnId": turnId, "pcm_b64": b64])
    }

    func sendTtsSpeak(utteranceId: String, text: String) {
        send(["type": "tts.speak", "utteranceId": utteranceId, "text": text, "interruptible": true])
    }

    func sendTtsAppend(utteranceId: String, text: String, flush: Bool) {
        send(["type": "tts.append", "utteranceId": utteranceId, "text": text, "flush": flush])
    }

    func sendTtsStop(utteranceId: String) {
        send(["type": "tts.stop", "utteranceId": utteranceId])
    }

    private func send(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let str = String(data: data, encoding: .utf8) else { return }
        if connected {
            socket?.send(.string(str)) { [weak self] error in
                if error != nil { self?.scheduleReconnect() }
            }
        } else {
            pending.append(str)
        }
    }

    private func flushPending() {
        for s in pending {
            socket?.send(.string(s)) { [weak self] error in
                if error != nil { self?.scheduleReconnect() }
            }
        }
        pending.removeAll()
    }

    private func receiveNext() {
        socket?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(.string(let text)):
                self.handle(text)
                self.receiveNext()
            case .success(.data):
                self.receiveNext()
            case .failure:
                self.scheduleReconnect()
            @unknown default:
                self.receiveNext()
            }
        }
    }

    private func scheduleReconnect() {
        guard !reconnectScheduled else { return }
        connected = false
        reconnectScheduled = true
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.openSocket()
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let kind = obj["type"] as? String ?? ""
        switch kind {
        case "stt.transcript":
            continuation?.yield(.transcript(
                turnId: obj["turnId"] as? String ?? "",
                text: obj["text"] as? String ?? "",
                isFinal: obj["isFinal"] as? Bool ?? false,
                finalizedBy: obj["finalizedBy"] as? String
            ))
        case "tts.audio":
            if let b64 = obj["pcm_b64"] as? String, let pcm = Data(base64Encoded: b64) {
                continuation?.yield(.ttsAudio(utteranceId: obj["utteranceId"] as? String ?? "", pcm: pcm))
            }
        case "tts.done":
            continuation?.yield(.ttsDone(utteranceId: obj["utteranceId"] as? String ?? ""))
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
