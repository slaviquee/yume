import Foundation

struct WorkerSummary: Codable, Identifiable, Hashable {
    let taskId: String
    let title: String
    var state: String
    var riskLevel: String
    var lastMessage: String
    var lastAction: String
    var needsUser: Bool
    var targetApps: [String]
    var summary: String
    var elapsedSec: Double

    var id: String { taskId }

    init(taskId: String, title: String, state: String, riskLevel: String,
         lastMessage: String = "", lastAction: String = "",
         needsUser: Bool = false, targetApps: [String] = [],
         summary: String = "", elapsedSec: Double = 0) {
        self.taskId = taskId
        self.title = title
        self.state = state
        self.riskLevel = riskLevel
        self.lastMessage = lastMessage
        self.lastAction = lastAction
        self.needsUser = needsUser
        self.targetApps = targetApps
        self.summary = summary
        self.elapsedSec = elapsedSec
    }
}

struct ConfirmationRequest: Codable, Identifiable, Hashable {
    let confirmationId: String
    let taskId: String
    let prompt: String
    let riskLevel: String
    let choices: [String]

    var id: String { confirmationId }
}

struct PermissionState: Equatable {
    enum Status: String { case unknown, granted, denied }

    var microphone: Status = .unknown
    var accessibility: Status = .unknown
    var screenRecording: Status = .unknown
    var inputMonitoring: Status = .unknown
    var camera: Status = .unknown

    var allRequiredGranted: Bool {
        microphone == .granted && accessibility == .granted && screenRecording == .granted
    }
}
