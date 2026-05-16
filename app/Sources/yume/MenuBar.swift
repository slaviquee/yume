import AppKit
import SwiftUI

@MainActor
final class MenuBarController: NSObject, NSMenuDelegate {
    private let statusItem: NSStatusItem
    private let appState: AppState
    private var taskDrawerPopover: NSPopover?

    init(appState: AppState) {
        self.appState = appState
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()
        if let button = statusItem.button {
            button.title = "yume"
            button.toolTip = "yume — hold Right Option to talk"
            button.target = self
            button.action = #selector(handleClick(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
        rebuildMenu()
    }

    private func rebuildMenu() {
        let menu = NSMenu()
        menu.delegate = self

        menu.addItem(NSMenuItem.sectionHeader(title: "yume"))
        menu.addItem(.separator())

        let listeningTitle = appState.voiceState == .listeningPushToTalk ? "Stop Listening" : "Start Listening"
        let startItem = NSMenuItem(title: listeningTitle, action: #selector(startListening), keyEquivalent: " ")
        startItem.keyEquivalentModifierMask = [.option]
        startItem.target = self
        menu.addItem(startItem)

        let continuousItem = NSMenuItem(title: appState.continuousMode ? "Stop Continuous Conversation" : "Toggle Continuous Conversation",
                                        action: #selector(toggleContinuous),
                                        keyEquivalent: "c")
        continuousItem.target = self
        menu.addItem(continuousItem)

        menu.addItem(.separator())

        let tasksItem = NSMenuItem(title: "Show Tasks (\(appState.workers.count))", action: #selector(showTasks), keyEquivalent: "t")
        tasksItem.target = self
        menu.addItem(tasksItem)

        let permsItem = NSMenuItem(title: "Permissions…", action: #selector(showPermissions), keyEquivalent: "")
        permsItem.target = self
        menu.addItem(permsItem)

        let settingsItem = NSMenuItem(title: "Settings…", action: #selector(showSettings), keyEquivalent: ",")
        settingsItem.target = self
        menu.addItem(settingsItem)

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit yume", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    @objc private func handleClick(_ sender: AnyObject?) {
        rebuildMenu()
    }

    @objc private func startListening() {
        appState.toggleMenuListening()
    }

    @objc private func toggleContinuous() {
        appState.hotkey.onEvent?(.doubleClick)
    }

    @objc private func showTasks() {
        showPopover(view: TaskDrawer().environmentObject(appState),
                    size: CGSize(width: 360, height: 420))
    }

    @objc private func showPermissions() {
        showPopover(view: PermissionsView().environmentObject(appState),
                    size: CGSize(width: 420, height: 320))
    }

    @objc private func showSettings() {
        NSApp.activate(ignoringOtherApps: true)
        if #available(macOS 14.0, *) {
            NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
        } else {
            NSApp.sendAction(Selector(("showPreferencesWindow:")), to: nil, from: nil)
        }
    }

    private func showPopover<V: View>(view: V, size: CGSize) {
        let popover = NSPopover()
        popover.contentSize = size
        popover.behavior = .transient
        popover.contentViewController = NSHostingController(rootView: view)
        if let button = statusItem.button {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        }
        taskDrawerPopover = popover
    }

    nonisolated func menuNeedsUpdate(_ menu: NSMenu) {
        Task { @MainActor in self.rebuildMenu() }
    }
}

private extension NSMenuItem {
    static func sectionHeader(title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }
}
