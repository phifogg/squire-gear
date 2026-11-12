import SwiftUI
import SquireCore

/// Top view of the Keris II Origin (own neutral artwork, no ASUS marks) with its three light zones.
/// Same shapes and coordinates as mouse_svg() in linux/squire_settings.py (200 x 370).
struct MouseDrawing: View {
    let settings: MouseSettings

    static let size = CGSize(width: 200, height: 370)

    var body: some View {
        Canvas { context, _ in
            draw(&context)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .accessibilityLabel("Top view of the mouse with its three light zones")
    }

    private func draw(_ context: inout GraphicsContext) {
        let body = Self.body
        context.fill(body, with: .linearGradient(Gradient(colors: [Color(hex: 0x2A2F37), Color(hex: 0x1C2026)]),
                                                  startPoint: CGPoint(x: 0, y: 12), endPoint: CGPoint(x: 0, y: 358)))
        context.stroke(body, with: .color(Color(hex: 0x3A404A)), lineWidth: 1.5)

        // side strip: glow + line
        let strip = Self.strip
        if let side = paint(.side, gradientFrom: CGPoint(x: 0, y: 178), to: CGPoint(x: 0, y: 300)) {
            var glow = context
            glow.opacity = side.opacity
            glow.addFilter(.blur(radius: 6))
            glow.stroke(strip, with: side.shading, style: StrokeStyle(lineWidth: 12, lineCap: .round))
            var line = context
            line.opacity = side.opacity
            line.stroke(strip, with: side.shading, style: StrokeStyle(lineWidth: 4, lineCap: .round))
        }

        // button split and palm line
        var split = Path()
        split.move(to: CGPoint(x: 100, y: 14)); split.addLine(to: CGPoint(x: 100, y: 134))
        split.move(to: CGPoint(x: 14, y: 146))
        split.addCurve(to: CGPoint(x: 186, y: 146), control1: CGPoint(x: 60, y: 158), control2: CGPoint(x: 140, y: 158))
        context.stroke(split, with: .color(Color(hex: 0x0F1115)), lineWidth: 2)

        // scroll wheel
        let housing = Path(roundedRect: CGRect(x: 89, y: 46, width: 22, height: 46), cornerRadius: 11)
        context.fill(housing, with: .color(Color(hex: 0x14171B)))
        context.stroke(housing, with: .color(Color(hex: 0x3A404A)), lineWidth: 1)
        let wheelRect = CGRect(x: 93, y: 52, width: 14, height: 34)
        if let wheel = paint(.wheel, gradientFrom: wheelRect.origin, to: CGPoint(x: wheelRect.maxX, y: wheelRect.maxY)) {
            var c = context
            c.opacity = wheel.opacity
            c.fill(Path(roundedRect: wheelRect, cornerRadius: 7), with: wheel.shading)
        }

        // logo: the Squire shield in the LED colour, with glow; a dark imprint when off
        let shield = Self.shield.applying(CGAffineTransform(translationX: 76, y: 222).scaledBy(x: 3, y: 3))
        let box = shield.boundingRect
        let logo = paint(.logo, gradientFrom: box.origin, to: CGPoint(x: box.maxX, y: box.maxY))
        if let logo {
            var glow = context
            glow.opacity = logo.opacity * 0.7
            glow.addFilter(.blur(radius: 4.8))
            glow.fill(shield, with: logo.shading)
        }
        context.fill(shield, with: .color(Color(hex: 0x14171B)), style: FillStyle(eoFill: true))
        context.stroke(shield, with: .color(Color(hex: 0x3A404A)), lineWidth: 1)
        if let logo {
            var c = context
            c.opacity = logo.opacity
            c.fill(shield, with: logo.shading, style: FillStyle(eoFill: true))
        }
    }

    /// Shading and opacity for a zone, as the mouse would roughly look; nil = dark.
    private func paint(_ zone: LEDZone, gradientFrom start: CGPoint, to end: CGPoint)
        -> (shading: GraphicsContext.Shading, opacity: Double)? {
        guard let led = settings.leds[zone], led.mode != .off, led.brightness > 0 else { return nil }
        let opacity = 0.35 + Double(led.brightness) / 100 * 0.65
        switch led.mode {
        case .colorCycle, .rainbow:
            let colors = [Color(hex: 0xFF5A5A), Color(hex: 0x3CCF7A), Color(hex: 0x3D8BFF)]
            return (.linearGradient(Gradient(colors: colors), startPoint: start, endPoint: end), opacity)
        case .battery:
            return (.color(Color(hex: 0x3CCF7A)), opacity)
        default:
            return (.color(Color(led.color)), opacity)
        }
    }

    // MARK: shapes (from the SVG paths of the Linux window)

    static let body: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 100, y: 12))
        p.addCurve(to: CGPoint(x: 188, y: 114), control1: CGPoint(x: 150, y: 12), control2: CGPoint(x: 182, y: 44))
        p.addCurve(to: CGPoint(x: 186, y: 304), control1: CGPoint(x: 194, y: 184), control2: CGPoint(x: 196, y: 254))
        p.addCurve(to: CGPoint(x: 100, y: 358), control1: CGPoint(x: 176, y: 344), control2: CGPoint(x: 140, y: 358))
        p.addCurve(to: CGPoint(x: 14, y: 304), control1: CGPoint(x: 60, y: 358), control2: CGPoint(x: 24, y: 344))
        p.addCurve(to: CGPoint(x: 12, y: 114), control1: CGPoint(x: 4, y: 254), control2: CGPoint(x: 6, y: 184))
        p.addCurve(to: CGPoint(x: 100, y: 12), control1: CGPoint(x: 18, y: 44), control2: CGPoint(x: 50, y: 12))
        p.closeSubpath()
        return p
    }()

    static let strip: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 13, y: 178))
        p.addCurve(to: CGPoint(x: 15, y: 300), control1: CGPoint(x: 7, y: 220), control2: CGPoint(x: 7, y: 262))
        return p
    }()

    /// The Squire shield with a blade (linux/icons/squire-gear-symbolic.svg, 16 x 16); the blade is a hole (even-odd).
    static let shield: Path = {
        var p = Path()
        p.move(to: CGPoint(x: 8, y: 0.8))
        p.addLine(to: CGPoint(x: 14.2, y: 3))
        p.addLine(to: CGPoint(x: 14.2, y: 7.8))
        p.addCurve(to: CGPoint(x: 8, y: 15.2), control1: CGPoint(x: 14.2, y: 11.4), control2: CGPoint(x: 11.6, y: 13.9))
        p.addCurve(to: CGPoint(x: 1.8, y: 7.8), control1: CGPoint(x: 4.4, y: 13.9), control2: CGPoint(x: 1.8, y: 11.4))
        p.addLine(to: CGPoint(x: 1.8, y: 3))
        p.closeSubpath()
        let blade: [(CGFloat, CGFloat)] = [(8, 3), (7.1, 4.4), (7.1, 9.2), (5.4, 9.2), (5.4, 10.4), (7.1, 10.4),
                                           (7.1, 12.4), (8.9, 12.4), (8.9, 10.4), (10.6, 10.4), (10.6, 9.2),
                                           (8.9, 9.2), (8.9, 4.4)]
        p.addLines(blade.map { CGPoint(x: $0.0, y: $0.1) })
        p.closeSubpath()
        return p
    }()
}

extension Color {
    init(hex: UInt32) {
        self.init(.sRGB, red: Double(hex >> 16 & 0xFF) / 255, green: Double(hex >> 8 & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }

    init(_ rgb: RGB) {
        self.init(.sRGB, red: Double(rgb.r) / 255, green: Double(rgb.g) / 255, blue: Double(rgb.b) / 255)
    }
}

extension RGB {
    init(_ color: Color) { self.init(NSColor(color)) }

    /// The LED takes 8-bit RGB: convert to sRGB (colours outside sRGB are clamped).
    init(_ color: NSColor) {
        let c = color.usingColorSpace(.sRGB) ?? .black
        func byte(_ v: CGFloat) -> UInt8 { UInt8(max(0, min(255, (v * 255).rounded()))) }
        self.init(byte(c.redComponent), byte(c.greenComponent), byte(c.blueComponent))
    }

    var nsColor: NSColor {
        NSColor(srgbRed: CGFloat(r) / 255, green: CGFloat(g) / 255, blue: CGFloat(b) / 255, alpha: 1)
    }
}
