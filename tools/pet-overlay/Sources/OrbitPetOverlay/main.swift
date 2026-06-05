import AppKit
import Foundation

private let windowWidth: CGFloat = 340
private let minWindowHeight: CGFloat = 210
private let margin: CGFloat = 24
private let bubbleX: CGFloat = 18
private let bubbleY: CGFloat = 106
private let bubbleWidth: CGFloat = 304
private let minBubbleHeight: CGFloat = 86
private let bubbleTopPadding: CGFloat = 18
private let bubbleTextPaddingX: CGFloat = 16
private let bubbleTextPaddingY: CGFloat = 15
private let bubbleTextWidth: CGFloat = bubbleWidth - (bubbleTextPaddingX * 2)

private enum PetMode: String {
    case idle
    case thinking
    case speaking
    case waiting
    case notifying
    case error
}

private struct PetEvent: Decodable {
    let type: String
    let text: String?
    let state: String?
}

private final class PetPanel: NSPanel {
    override var canBecomeKey: Bool {
        true
    }

    override var canBecomeMain: Bool {
        true
    }
}

@main
final class OrbitPetApp: NSObject, NSApplicationDelegate {
    private var window: NSPanel?
    private var petView: PetView?

    static func main() {
        let app = NSApplication.shared
        let delegate = OrbitPetApp()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        createWindow()
        startInputReader()
    }

    private func createWindow() {
        let panel = PetPanel(
            contentRect: NSRect(x: 0, y: 0, width: windowWidth, height: minWindowHeight),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .transient, .ignoresCycle]
        if ProcessInfo.processInfo.environment["ORBIT_AI_PET_ALWAYS_ON_TOP"] != "0" {
            panel.level = .floating
        }

        let view = PetView(frame: NSRect(x: 0, y: 0, width: windowWidth, height: minWindowHeight)) { [weak self] text in
            self?.emitSubmit(text)
        }
        panel.contentView = view
        position(panel)
        panel.orderFrontRegardless()

        self.window = panel
        self.petView = view
    }

    private func position(_ panel: NSPanel) {
        guard let screenFrame = NSScreen.main?.visibleFrame else {
            panel.setFrameOrigin(NSPoint(x: 80, y: 80))
            return
        }
        let position = ProcessInfo.processInfo.environment["ORBIT_AI_PET_POSITION"] ?? "bottom_right"
        let x: CGFloat
        let y: CGFloat
        switch position {
        case "bottom_left":
            x = screenFrame.minX + margin
            y = screenFrame.minY + margin
        case "top_left":
            x = screenFrame.minX + margin
            y = screenFrame.maxY - panel.frame.height - margin
        case "top_right":
            x = screenFrame.maxX - windowWidth - margin
            y = screenFrame.maxY - panel.frame.height - margin
        default:
            x = screenFrame.maxX - windowWidth - margin
            y = screenFrame.minY + margin
        }
        panel.setFrameOrigin(NSPoint(x: max(screenFrame.minX, x), y: max(screenFrame.minY, y)))
    }

    private func startInputReader() {
        Thread.detachNewThread { [weak self] in
            while let line = readLine(strippingNewline: true) {
                guard let event = decodeEvent(line) else {
                    continue
                }
                DispatchQueue.main.async {
                    self?.handle(event)
                }
            }
            DispatchQueue.main.async {
                NSApp.terminate(nil)
            }
        }
    }

    private func handle(_ event: PetEvent) {
        switch event.type {
        case "quit":
            NSApp.terminate(nil)
        case "hide":
            window?.orderOut(nil)
        case "show":
            window?.orderFrontRegardless()
        case "say":
            petView?.update(mode: PetMode(rawValue: event.state ?? "") ?? .speaking, text: event.text)
            window?.orderFrontRegardless()
        case "progress":
            petView?.update(mode: .thinking, text: event.text)
            window?.orderFrontRegardless()
        case "state":
            petView?.update(mode: PetMode(rawValue: event.state ?? "") ?? .idle, text: event.text)
        default:
            break
        }
    }

    private func emitSubmit(_ text: String) {
        let payload = ["type": "submit", "text": text]
        guard
            let data = try? JSONSerialization.data(withJSONObject: payload),
            let newline = "\n".data(using: .utf8)
        else {
            return
        }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(newline)
    }
}

private func decodeEvent(_ line: String) -> PetEvent? {
    guard let data = line.data(using: .utf8) else {
        return nil
    }
    return try? JSONDecoder().decode(PetEvent.self, from: data)
}

private final class PetView: NSView {
    private let onSubmit: (String) -> Void
    private var mode: PetMode = .idle
    private var message: String = "Idle."
    private var pulse: CGFloat = 0
    private var timer: Timer?
    private var dragStartScreenPoint: NSPoint?
    private var dragStartWindowOrigin: NSPoint?
    private var dragStartedOnCharacter = false
    private var didDrag = false
    private var inputField: NSTextField?

    init(frame frameRect: NSRect, onSubmit: @escaping (String) -> Void) {
        self.onSubmit = onSubmit
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.pulse = self.pulse == 0 ? 1 : 0
            self.needsDisplay = true
        }
    }

    required init?(coder: NSCoder) {
        nil
    }

    func update(mode: PetMode, text: String?) {
        self.mode = mode
        if let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.message = text
        }
        resizeWindowToFitMessage()
        needsDisplay = true
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }

    override func mouseDown(with event: NSEvent) {
        guard let window else {
            return
        }
        didDrag = false
        dragStartedOnCharacter = characterHitRect.contains(event.locationInWindow)
        dragStartScreenPoint = window.convertPoint(toScreen: event.locationInWindow)
        dragStartWindowOrigin = window.frame.origin
    }

    override func mouseDragged(with event: NSEvent) {
        guard
            let window,
            let dragStartScreenPoint,
            let dragStartWindowOrigin
        else {
            return
        }
        let currentScreenPoint = window.convertPoint(toScreen: event.locationInWindow)
        let deltaX = currentScreenPoint.x - dragStartScreenPoint.x
        let deltaY = currentScreenPoint.y - dragStartScreenPoint.y
        if abs(deltaX) > 3 || abs(deltaY) > 3 {
            didDrag = true
        }
        window.setFrameOrigin(NSPoint(x: dragStartWindowOrigin.x + deltaX, y: dragStartWindowOrigin.y + deltaY))
    }

    override func mouseUp(with event: NSEvent) {
        if dragStartedOnCharacter && !didDrag {
            showPromptInput()
        }
        dragStartScreenPoint = nil
        dragStartWindowOrigin = nil
        dragStartedOnCharacter = false
        didDrag = false
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let accent = color(for: mode)
        drawBubble(accent: accent)
        drawCharacter(accent: accent)
        drawStateLabel(accent: accent)
    }

    private func drawBubble(accent: NSColor) {
        let bubble = bubbleRect
        let path = NSBezierPath(roundedRect: bubble, xRadius: 14, yRadius: 14)
        NSColor.white.withAlphaComponent(0.96).setFill()
        path.fill()
        accent.setStroke()
        path.lineWidth = 2
        path.stroke()

        let tail = NSBezierPath()
        tail.move(to: NSPoint(x: 82, y: 106))
        tail.line(to: NSPoint(x: 104, y: 106))
        tail.line(to: NSPoint(x: 92, y: 91))
        tail.close()
        NSColor.white.withAlphaComponent(0.96).setFill()
        tail.fill()
        accent.setStroke()
        tail.lineWidth = 2
        tail.stroke()

        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byWordWrapping
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 14, weight: .medium),
            .foregroundColor: NSColor(calibratedRed: 0.12, green: 0.16, blue: 0.23, alpha: 1),
            .paragraphStyle: paragraph,
        ]
        let textRect = NSRect(
            x: bubble.minX + bubbleTextPaddingX,
            y: bubble.minY + bubbleTextPaddingY,
            width: bubbleTextWidth,
            height: bubble.height - (bubbleTextPaddingY * 2)
        )
        NSString(string: displayMessage(for: textRect.height)).draw(in: textRect, withAttributes: attributes)
    }

    private func drawCharacter(accent: NSColor) {
        let center = NSPoint(x: 124, y: 54 + pulse)
        let head = NSRect(x: center.x - 45, y: center.y - 40, width: 90, height: 82)
        let headPath = NSBezierPath(ovalIn: head)
        NSColor.white.withAlphaComponent(0.98).setFill()
        headPath.fill()
        accent.setStroke()
        headPath.lineWidth = 3
        headPath.stroke()

        drawOval(NSRect(x: center.x - 23, y: center.y + 1, width: 12, height: 12), color: accent)
        drawOval(NSRect(x: center.x + 11, y: center.y + 1, width: 12, height: 12), color: accent)

        let mouth: String
        switch mode {
        case .thinking:
            mouth = "..."
        case .notifying:
            mouth = "!"
        case .error:
            mouth = "x"
        default:
            mouth = "u"
        }
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: 18),
            .foregroundColor: accent,
        ]
        NSString(string: mouth).draw(at: NSPoint(x: center.x - 8, y: center.y - 26), withAttributes: attributes)

        accent.setStroke()
        let leftAntenna = NSBezierPath()
        leftAntenna.move(to: NSPoint(x: center.x - 34, y: center.y + 36))
        leftAntenna.curve(
            to: NSPoint(x: center.x - 58, y: center.y + 56),
            controlPoint1: NSPoint(x: center.x - 48, y: center.y + 42),
            controlPoint2: NSPoint(x: center.x - 58, y: center.y + 48)
        )
        leftAntenna.lineWidth = 3
        leftAntenna.stroke()

        let rightAntenna = NSBezierPath()
        rightAntenna.move(to: NSPoint(x: center.x + 34, y: center.y + 36))
        rightAntenna.curve(
            to: NSPoint(x: center.x + 58, y: center.y + 56),
            controlPoint1: NSPoint(x: center.x + 48, y: center.y + 42),
            controlPoint2: NSPoint(x: center.x + 58, y: center.y + 48)
        )
        rightAntenna.lineWidth = 3
        rightAntenna.stroke()
    }

    private var characterHitRect: NSRect {
        NSRect(x: 56, y: 4, width: 136, height: 112)
    }

    private func showPromptInput() {
        let field = ensureInputField()
        field.stringValue = ""
        field.isHidden = false
        window?.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        window?.makeFirstResponder(field)
    }

    private func ensureInputField() -> NSTextField {
        if let inputField {
            inputField.frame = inputFieldFrame
            return inputField
        }
        let field = NSTextField(frame: inputFieldFrame)
        field.placeholderString = "Orbitへ入力..."
        field.font = NSFont.systemFont(ofSize: 14)
        field.isBezeled = true
        field.bezelStyle = .roundedBezel
        field.drawsBackground = true
        field.target = self
        field.action = #selector(submitPrompt)
        addSubview(field)
        inputField = field
        return field
    }

    @objc private func submitPrompt() {
        guard let inputField else {
            return
        }
        let text = inputField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        inputField.isHidden = true
        window?.makeFirstResponder(nil)
        guard !text.isEmpty else {
            return
        }
        message = text
        needsDisplay = true
        onSubmit(text)
    }

    private func drawStateLabel(accent: NSColor) {
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .semibold),
            .foregroundColor: accent,
        ]
        NSString(string: mode.rawValue).draw(at: NSPoint(x: 230, y: 38), withAttributes: attributes)
    }

    private func drawOval(_ rect: NSRect, color: NSColor) {
        let path = NSBezierPath(ovalIn: rect)
        color.setFill()
        path.fill()
    }

    private var bubbleRect: NSRect {
        NSRect(x: bubbleX, y: bubbleY, width: bubbleWidth, height: max(minBubbleHeight, bounds.height - bubbleY - bubbleTopPadding))
    }

    private var inputFieldFrame: NSRect {
        let bubble = bubbleRect
        return NSRect(x: bubble.minX + 16, y: bubble.maxY - 43, width: bubble.width - 32, height: 30)
    }

    private func resizeWindowToFitMessage() {
        guard let window else {
            return
        }
        let currentFrame = window.frame
        let targetBubbleHeight = min(maxBubbleHeight(), requiredBubbleHeight(for: message))
        let targetHeight = max(minWindowHeight, bubbleY + targetBubbleHeight + bubbleTopPadding)
        guard abs(currentFrame.height - targetHeight) > 1 else {
            updateInputFieldFrame()
            return
        }
        var nextFrame = NSRect(x: currentFrame.minX, y: currentFrame.minY, width: windowWidth, height: targetHeight)
        if let screenFrame = window.screen?.visibleFrame ?? NSScreen.main?.visibleFrame {
            if nextFrame.maxY > screenFrame.maxY {
                nextFrame.origin.y = screenFrame.maxY - nextFrame.height
            }
            if nextFrame.minY < screenFrame.minY {
                nextFrame.origin.y = screenFrame.minY
            }
            if nextFrame.maxX > screenFrame.maxX {
                nextFrame.origin.x = screenFrame.maxX - nextFrame.width
            }
            if nextFrame.minX < screenFrame.minX {
                nextFrame.origin.x = screenFrame.minX
            }
        }
        window.setFrame(nextFrame, display: true)
        frame = NSRect(x: 0, y: 0, width: nextFrame.width, height: nextFrame.height)
        updateInputFieldFrame()
    }

    private func updateInputFieldFrame() {
        inputField?.frame = inputFieldFrame
    }

    private func requiredBubbleHeight(for text: String) -> CGFloat {
        let measured = textHeight(for: text, width: bubbleTextWidth)
        return max(minBubbleHeight, ceil(measured + (bubbleTextPaddingY * 2)))
    }

    private func maxBubbleHeight() -> CGFloat {
        guard let screenFrame = window?.screen?.visibleFrame ?? NSScreen.main?.visibleFrame else {
            return 320
        }
        return max(minBubbleHeight, screenFrame.height - (margin * 2) - bubbleY - bubbleTopPadding)
    }

    private func displayMessage(for availableHeight: CGFloat) -> String {
        if textHeight(for: message, width: bubbleTextWidth) <= availableHeight {
            return message
        }
        var candidate = message
        while candidate.count > 12 {
            candidate.removeLast(max(1, candidate.count / 10))
            let clipped = candidate.trimmingCharacters(in: .whitespacesAndNewlines) + "..."
            if textHeight(for: clipped, width: bubbleTextWidth) <= availableHeight {
                return clipped
            }
        }
        return String(message.prefix(12)) + "..."
    }

    private func textHeight(for text: String, width: CGFloat) -> CGFloat {
        let attributes = bubbleTextAttributes()
        let rect = NSString(string: text).boundingRect(
            with: NSSize(width: width, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: attributes
        )
        return ceil(rect.height)
    }

    private func bubbleTextAttributes() -> [NSAttributedString.Key: Any] {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byWordWrapping
        return [
            .font: NSFont.systemFont(ofSize: 14, weight: .medium),
            .foregroundColor: NSColor(calibratedRed: 0.12, green: 0.16, blue: 0.23, alpha: 1),
            .paragraphStyle: paragraph,
        ]
    }
}

private func color(for mode: PetMode) -> NSColor {
    switch mode {
    case .idle:
        return NSColor(calibratedRed: 0.25, green: 0.42, blue: 0.92, alpha: 1)
    case .thinking:
        return NSColor(calibratedRed: 0.78, green: 0.48, blue: 0.04, alpha: 1)
    case .speaking:
        return NSColor(calibratedRed: 0.05, green: 0.55, blue: 0.29, alpha: 1)
    case .waiting:
        return NSColor(calibratedRed: 0.32, green: 0.32, blue: 0.37, alpha: 1)
    case .notifying:
        return NSColor(calibratedRed: 0.83, green: 0.18, blue: 0.38, alpha: 1)
    case .error:
        return NSColor(calibratedRed: 0.78, green: 0.12, blue: 0.16, alpha: 1)
    }
}
