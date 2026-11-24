import AppKit

/// The standard About panel with version + build from the bundle, the GitHub link, licence and credits.
/// Same text as the Linux tray's About dialog (linux/squire_tray.py about_info()).
@MainActor
func showAboutPanel() {
    let repo = "https://github.com/phifogg/squire-gear"
    let small = NSFont.systemFont(ofSize: NSFont.smallSystemFontSize)
    let centred = NSMutableParagraphStyle()
    centred.alignment = .center
    let plain: [NSAttributedString.Key: Any] = [.font: small, .foregroundColor: NSColor.secondaryLabelColor,
                                                .paragraphStyle: centred]
    let credits = NSMutableAttributedString(string: "Battery and settings for ASUS ROG keyboards and mice.\n\n",
                                            attributes: plain)
    var link = plain
    link[.link] = URL(string: repo)
    credits.append(NSAttributedString(string: "github.com/phifogg/squire-gear", attributes: link))
    credits.append(NSAttributedString(string: "\nMIT License\n\n"
        + "Protocol facts from G-Helper (GPL-3.0), libratbag and captures of Armoury Crate; no code copied.\n"
        + "Not affiliated with or endorsed by ASUS. ROG is a trademark of ASUSTeK Computer Inc.",
        attributes: plain))
    // A menu bar app is never active on its own: bring it forward, or the panel opens behind other windows.
    NSApp.activate(ignoringOtherApps: true)
    NSApp.orderFrontStandardAboutPanel(options: [
        .applicationName: "Squire",
        .credits: credits,
        NSApplication.AboutPanelOptionKey(rawValue: "Copyright"): "© Daniel Draes",
    ])
}
