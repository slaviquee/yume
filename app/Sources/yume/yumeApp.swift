import SwiftUI
import AppKit

@main
struct AppMain: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        Settings {
            SettingsView()
                .environmentObject(appDelegate.appState)
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let appState = AppState()
    private var menuBarController: MenuBarController?
    private var hudWindow: NSWindow?
    private var taskDrawerWindow: NSWindow?
    private var confirmationWindow: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        // Bring the menu bar item up first so the app is always reachable.
        menuBarController = MenuBarController(appState: appState)

        // Floating HUD that follows the user's active workspace.
        hudWindow = makeFloatingWindow(rootView: AssistantHUD().environmentObject(appState),
                                       size: CGSize(width: 360, height: 96),
                                       anchor: .topRight)
        hudWindow?.orderFrontRegardless()

        appState.bootstrap()
    }

    func applicationWillTerminate(_ notification: Notification) {
        appState.shutdown()
    }

    private func makeFloatingWindow<Content: View>(rootView: Content,
                                                   size: CGSize,
                                                   anchor: WindowAnchor) -> NSWindow {
        let hosting = NSHostingView(rootView: rootView)
        let window = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                             styleMask: [.borderless, .nonactivatingPanel, .hudWindow],
                             backing: .buffered,
                             defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.level = .statusBar
        window.collectionBehavior = [.canJoinAllSpaces, .stationary]
        window.hasShadow = true
        window.contentView = hosting
        window.isReleasedWhenClosed = false
        window.ignoresMouseEvents = false

        if let screen = NSScreen.main {
            let frame = screen.visibleFrame
            switch anchor {
            case .topRight:
                window.setFrameOrigin(NSPoint(x: frame.maxX - size.width - 20,
                                              y: frame.maxY - size.height - 20))
            case .bottomCenter:
                window.setFrameOrigin(NSPoint(x: frame.midX - size.width / 2,
                                              y: frame.minY + 20))
            }
        }
        return window
    }
}

enum WindowAnchor {
    case topRight
    case bottomCenter
}
