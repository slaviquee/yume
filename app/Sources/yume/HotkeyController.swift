import Foundation
import AppKit
import Carbon.HIToolbox

/// Watches for the **Right Option** key and Escape. Emits press/release/double-click
/// events on the main actor.
///
/// Right Option is a modifier key, not a regular character. We capture it by
/// installing an NSEvent global monitor for ``flagsChanged`` events and
/// inspecting the hardware key code. Spec docs/spec.md section 5.1.
final class HotkeyController {
    enum Event {
        case pressed, released, doubleClick, escape
    }

    /// Right Option hardware key code on macOS.
    private static let rightOptionKeyCode: UInt16 = 0x3D
    private static let escapeKeyCode: UInt16 = 0x35
    private static let doubleClickWindow: TimeInterval = 0.45

    var onEvent: ((Event) -> Void)?

    private var globalMonitor: Any?
    private var localMonitor: Any?
    private var pressed = false
    private var lastReleaseAt: TimeInterval = 0

    func start() {
        // Modifier events.
        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.flagsChanged, .keyDown]) { [weak self] event in
            self?.handle(event)
        }
        // Local monitor lets us react when the app itself is foregrounded.
        localMonitor = NSEvent.addLocalMonitorForEvents(matching: [.flagsChanged, .keyDown]) { [weak self] event in
            self?.handle(event)
            return event
        }
    }

    func stop() {
        if let m = globalMonitor { NSEvent.removeMonitor(m); globalMonitor = nil }
        if let m = localMonitor { NSEvent.removeMonitor(m); localMonitor = nil }
    }

    private func handle(_ event: NSEvent) {
        if event.type == .keyDown && event.keyCode == Self.escapeKeyCode {
            onEvent?(.escape)
            return
        }
        guard event.type == .flagsChanged, event.keyCode == Self.rightOptionKeyCode else { return }

        // The .option flag is shared between left and right; we already filtered
        // on the keyCode, so the flag toggle here tells us press vs release.
        let isPressed = event.modifierFlags.contains(.option)
        if isPressed && !pressed {
            pressed = true
            let now = ProcessInfo.processInfo.systemUptime
            if now - lastReleaseAt < Self.doubleClickWindow {
                onEvent?(.doubleClick)
            } else {
                onEvent?(.pressed)
            }
        } else if !isPressed && pressed {
            pressed = false
            lastReleaseAt = ProcessInfo.processInfo.systemUptime
            onEvent?(.released)
        }
    }
}
