import SwiftUI
import SquireCore

/// Top view of the Falchion RX LP (own neutral artwork, no ASUS marks): a 65 % board shaped by the layout the
/// keyboard reports (ISO or ANSI). Same shapes and coordinates as keyboard_svg() in linux/squire_settings.py.
/// The LED colour cannot be read yet: a lit effect shows as a neutral glow.
struct KeyboardDrawing: View {
    let settings: KeyboardSettings

    static let unit: CGFloat = 36, gap: CGFloat = 4, pad: CGFloat = 18
    static let size = CGSize(width: 16 * unit + 2 * pad, height: 5 * unit + 2 * pad)   // 612 x 216

    var body: some View {
        Canvas { context, _ in
            draw(&context)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .accessibilityLabel("Top view of the keyboard")
    }

    private func draw(_ context: inout GraphicsContext) {
        let u = Self.unit, g = Self.gap, pad = Self.pad
        let frame = Path(roundedRect: CGRect(x: 1, y: 1, width: Self.size.width - 2, height: Self.size.height - 2),
                         cornerRadius: 12)
        context.fill(frame, with: .linearGradient(Gradient(colors: [Color(hex: 0x2A2F37), Color(hex: 0x1C2026)]),
                                                   startPoint: .zero, endPoint: CGPoint(x: 0, y: Self.size.height)))
        context.stroke(frame, with: .color(Color(hex: 0x3A404A)), lineWidth: 1.5)
        // touch panel on the left edge
        context.fill(Path(roundedRect: CGRect(x: 5, y: pad + u, width: 4, height: 3 * u), cornerRadius: 2),
                     with: .color(Color(hex: 0x3A404A)))

        if let mode = settings.lightMode, mode != "off" {
            var glow = context
            glow.opacity = 0.22
            glow.addFilter(.blur(radius: 7))
            glow.fill(Path(roundedRect: CGRect(x: pad - 4, y: pad - 4, width: 16 * u - g + 8, height: 5 * u - g + 8),
                           cornerRadius: 8), with: .color(Color(hex: 0xCFD6E0)))
        }

        let keyFill = GraphicsContext.Shading.color(Color(hex: 0x14171B))
        let keyStroke = GraphicsContext.Shading.color(Color(hex: 0x3A404A))
        let legendColor = Color(hex: 0x9AA3AE)
        for (r, row) in KeyboardLayout.rows(for: settings).enumerated() {
            var x = pad
            for key in row {
                let y = pad + CGFloat(r) * u, w = CGFloat(key.width) * u - g, h = u - g
                if key.legend == KeyboardLayout.isoEnter {
                    // one key over two rows: 1.5 units on top, 1.25 below (right-aligned)
                    let x2 = x + 0.25 * u, bottom = y + 2 * u - g
                    var p = Path()
                    p.addLines([CGPoint(x: x, y: y), CGPoint(x: x + w, y: y), CGPoint(x: x + w, y: bottom),
                                CGPoint(x: x2, y: bottom), CGPoint(x: x2, y: y + h), CGPoint(x: x, y: y + h)])
                    p.closeSubpath()
                    context.fill(p, with: keyFill)
                    context.stroke(p, with: keyStroke, lineWidth: 1)
                    context.draw(Text("↵").font(.system(size: 11)).foregroundColor(legendColor),
                                 at: CGPoint(x: x + w / 2 + 4, y: y + u - 6))
                } else if key.legend != KeyboardLayout.isoEnterLow {
                    let rect = Path(roundedRect: CGRect(x: x, y: y, width: w, height: h), cornerRadius: 4)
                    context.fill(rect, with: keyFill)
                    context.stroke(rect, with: keyStroke, lineWidth: 1)
                    if !key.legend.isEmpty {
                        context.draw(Text(key.legend).font(.system(size: key.legend.count > 2 ? 8 : 11))
                                        .foregroundColor(legendColor),
                                     at: CGPoint(x: x + w / 2, y: y + h / 2))
                    }
                }
                x += CGFloat(key.width) * u
            }
        }
    }
}
