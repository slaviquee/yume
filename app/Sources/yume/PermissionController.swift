import Foundation
import AVFoundation
import AppKit
import ApplicationServices

/// Checks macOS TCC-style permissions yume needs. Screen Recording and
/// Input Monitoring require the user to grant them in System Settings — we
/// can check status and deep-link, but cannot silently elevate.
final class PermissionController {
    func refresh(completion: @escaping (PermissionState) -> Void) {
        var state = PermissionState()
        state.microphone = micStatus()
        state.accessibility = AXIsProcessTrusted() ? .granted : .denied
        state.screenRecording = screenRecordingStatus()
        state.inputMonitoring = inputMonitoringStatus()
        state.camera = cameraStatus()
        completion(state)
    }

    func requestMicrophone(completion: @escaping (Bool) -> Void) {
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async { completion(granted) }
        }
    }

    func requestAccessibilityWithPrompt() {
        let prompt = "kAXTrustedCheckOptionPrompt" as CFString
        _ = AXIsProcessTrustedWithOptions([prompt: kCFBooleanTrue!] as CFDictionary)
    }

    func openSystemSettings(pane: SettingsPane) {
        if let url = URL(string: pane.urlString) {
            NSWorkspace.shared.open(url)
        }
    }

    enum SettingsPane {
        case microphone, accessibility, screenRecording, inputMonitoring, camera

        var urlString: String {
            switch self {
            case .microphone: return "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
            case .accessibility: return "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            case .screenRecording: return "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
            case .inputMonitoring: return "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
            case .camera: return "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
            }
        }
    }

    private func micStatus() -> PermissionState.Status {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return .granted
        case .denied, .restricted: return .denied
        default: return .unknown
        }
    }

    private func cameraStatus() -> PermissionState.Status {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return .granted
        case .denied, .restricted: return .denied
        default: return .unknown
        }
    }

    /// CGPreflightScreenCaptureAccess is the supported API; it does not prompt.
    private func screenRecordingStatus() -> PermissionState.Status {
        if CGPreflightScreenCaptureAccess() {
            return .granted
        }
        return .denied
    }

    /// Input Monitoring has no public API for checking status directly. As a
    /// best-effort, we infer from whether a CGEventTap can be created.
    private func inputMonitoringStatus() -> PermissionState.Status {
        let mask = (1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.flagsChanged.rawValue)
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: CGEventMask(mask),
            callback: { _, _, event, _ in Unmanaged.passRetained(event) },
            userInfo: nil
        ) else {
            return .denied
        }
        CFMachPortInvalidate(tap)
        return .granted
    }
}
