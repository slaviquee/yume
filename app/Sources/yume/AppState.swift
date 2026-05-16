import Foundation
import Combine
import AppKit

/// Top-level observable state for the app. The HUD, task drawer, and overlays
/// all read from this object. State mutations happen on the main actor.
@MainActor
final class AppState: ObservableObject {
    enum VoiceState: String, Codable {
        case idle, listeningPushToTalk, listeningContinuous, transcribing, thinking, speaking, interrupted, error
    }

    // Voice + activation
    @Published var voiceState: VoiceState = .idle
    @Published var continuousMode: Bool = false
    @Published var liveTranscript: String = ""
    @Published var lastAssistantText: String = ""
    @Published var activeTurnId: String?
    @Published var activeUtteranceId: String?

    // Workers
    @Published var workers: [WorkerSummary] = []
    @Published var pendingConfirmation: ConfirmationRequest?

    // Permissions
    @Published var permissions = PermissionState()

    // Errors
    @Published var lastError: String?

    // Services
    let voiceClient: VoiceClient
    let agentClient: AgentClient
    let audioCapture: AudioCapture
    let audioPlayback: AudioPlayback
    let hotkey: HotkeyController
    let permissionController: PermissionController

    private var cancellables = Set<AnyCancellable>()
    private var voiceTask: Task<Void, Never>?
    private var agentTask: Task<Void, Never>?

    init() {
        self.voiceClient = VoiceClient()
        self.agentClient = AgentClient()
        self.audioCapture = AudioCapture()
        self.audioPlayback = AudioPlayback()
        self.hotkey = HotkeyController()
        self.permissionController = PermissionController()
    }

    func bootstrap() {
        permissionController.refresh { [weak self] state in
            guard let self else { return }
            Task { @MainActor in self.permissions = state }
        }

        hotkey.onEvent = { [weak self] event in
            Task { @MainActor in self?.handleHotkey(event) }
        }
        hotkey.start()

        voiceTask = Task { [weak self] in
            guard let self else { return }
            for await message in self.voiceClient.connect() {
                await self.handleVoiceMessage(message)
            }
        }
        agentTask = Task { [weak self] in
            guard let self else { return }
            for await message in self.agentClient.connect() {
                await self.handleAgentMessage(message)
            }
        }

        audioCapture.onPCMFrame = { [weak self] pcm in
            Task { @MainActor in self?.voiceClient.sendAudioFrame(turnId: self?.activeTurnId ?? "", pcm: pcm) }
        }
    }

    func shutdown() {
        voiceTask?.cancel()
        agentTask?.cancel()
        hotkey.stop()
        audioCapture.stop()
        audioPlayback.stop()
        voiceClient.disconnect()
        agentClient.disconnect()
    }

    // MARK: - Hotkey

    private func handleHotkey(_ event: HotkeyController.Event) {
        switch event {
        case .pressed:
            if voiceState == .speaking {
                // Barge-in: stop TTS and start listening.
                stopSpeechAndCancelUtterance()
            }
            beginListening(mode: continuousMode ? .continuous : .pushToTalk)
        case .released:
            if !continuousMode {
                endListeningPushToTalk()
            }
        case .doubleClick:
            toggleContinuousMode()
        case .escape:
            handleStop()
        }
    }

    // MARK: - Voice

    enum ListeningMode { case pushToTalk, continuous }

    private func beginListening(mode: ListeningMode) {
        guard permissions.microphone == .granted else {
            lastError = "Microphone permission is required."
            return
        }
        let turnId = "turn_\(UUID().uuidString.prefix(8))"
        activeTurnId = String(turnId)
        liveTranscript = ""
        voiceState = mode == .continuous ? .listeningContinuous : .listeningPushToTalk
        voiceClient.sendSttStart(turnId: String(turnId), mode: mode == .continuous ? "continuous" : "push_to_talk")
        audioCapture.start()
    }

    private func endListeningPushToTalk() {
        guard voiceState == .listeningPushToTalk, let turnId = activeTurnId else { return }
        audioCapture.stop()
        voiceClient.sendSttFlush(turnId: turnId)
        voiceState = .transcribing
    }

    private func toggleContinuousMode() {
        continuousMode.toggle()
        if continuousMode {
            beginListening(mode: .continuous)
        } else {
            audioCapture.stop()
            if let turnId = activeTurnId {
                voiceClient.sendSttStop(turnId: turnId)
            }
            voiceState = .idle
        }
    }

    private func handleStop() {
        stopSpeechAndCancelUtterance()
        agentClient.sendStop()
        if pendingConfirmation != nil {
            pendingConfirmation = nil
        }
    }

    private func stopSpeechAndCancelUtterance() {
        audioPlayback.stop()
        if let utt = activeUtteranceId {
            voiceClient.sendTtsStop(utteranceId: utt)
        }
        activeUtteranceId = nil
        voiceState = .idle
    }

    // MARK: - Voice service messages

    private func handleVoiceMessage(_ message: VoiceClient.Message) async {
        switch message {
        case .transcript(let turnId, let text, let isFinal, _):
            liveTranscript = text
            if isFinal {
                voiceState = .thinking
                agentClient.submitTurn(turnId: turnId, text: text)
            }
        case .ttsAudio(_, let pcm):
            audioPlayback.enqueue(pcm: pcm)
            if voiceState != .speaking {
                voiceState = .speaking
            }
        case .ttsDone:
            // Player drains its queue naturally; transition handled by AudioPlayback.
            break
        case .error(let code, let message):
            lastError = "[voice/\(code)] \(message)"
        }
    }

    // MARK: - Agent service messages

    private func handleAgentMessage(_ message: AgentClient.Message) async {
        switch message {
        case .thinking(_, let active):
            if active {
                voiceState = .thinking
            }
        case .sayStart(_, let utteranceId, _):
            activeUtteranceId = utteranceId
            voiceState = .speaking
            voiceClient.sendTtsSpeak(utteranceId: utteranceId, text: "")
            lastAssistantText = ""
        case .sayChunk(let utteranceId, let text):
            lastAssistantText += text
            voiceClient.sendTtsAppend(utteranceId: utteranceId, text: text, flush: false)
        case .sayEnd(let utteranceId, _):
            voiceClient.sendTtsAppend(utteranceId: utteranceId, text: "", flush: true)
        case .workerStarted(let summary):
            insertOrUpdateWorker(summary)
        case .workerProgress(let taskId, let state, let message, let lastAction, let needsUser):
            updateWorker(taskId: taskId, state: state, message: message, lastAction: lastAction, needsUser: needsUser)
        case .workerResult(let taskId, let status, let summary):
            updateWorker(taskId: taskId, state: status, message: summary, lastAction: "", needsUser: false)
        case .workerNeedsConfirmation(let req):
            pendingConfirmation = req
            NSSound(named: "Funk")?.play()
        case .workersSnapshot(let list):
            workers = list
        case .error(let code, let message):
            lastError = "[agent/\(code)] \(message)"
        }
    }

    func respondToConfirmation(_ decision: String) {
        guard let req = pendingConfirmation else { return }
        agentClient.confirmationResponse(confirmationId: req.confirmationId, decision: decision)
        pendingConfirmation = nil
    }

    func cancelWorker(taskId: String) {
        agentClient.cancelWorker(taskId: taskId)
    }

    func cancelAllWorkers() {
        agentClient.cancelAllWorkers()
    }

    // MARK: - Worker list helpers

    private func insertOrUpdateWorker(_ summary: WorkerSummary) {
        if let i = workers.firstIndex(where: { $0.taskId == summary.taskId }) {
            workers[i] = summary
        } else {
            workers.append(summary)
        }
    }

    private func updateWorker(taskId: String, state: String, message: String, lastAction: String, needsUser: Bool) {
        guard let i = workers.firstIndex(where: { $0.taskId == taskId }) else { return }
        workers[i].state = state
        if !message.isEmpty { workers[i].lastMessage = message }
        if !lastAction.isEmpty { workers[i].lastAction = lastAction }
        workers[i].needsUser = needsUser
    }
}
