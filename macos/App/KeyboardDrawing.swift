import SwiftUI
import SquireCore

/// Top view of the Falchion RX LP (own neutral artwork, no ASUS marks): a 65 % board shaped by the layout the
/// keyboard reports (ISO or ANSI). Same shapes and coordinates as keyboard_svg() in linux/squire_settings.py.
/// The keyboard cannot report its colour: with `light` (what Squire saved, or an edit not sent yet) the keys glow in
/// it, otherwise a lit effect shows as a neutral glow. Same as keyboard_glow() on Linux.
struct KeyboardDrawing: View {
    let settings: KeyboardSettings
    var light: KeyboardLight? = nil

    static let unit: CGFloat = 36, gap: CGFloat = 4, pad: CGFloat = 18
    static let size = CGSize(width: 16 * unit + 2 * pad, height: 5 * unit + 2 * pad)   // 612 x 216

    var body: some View {
        Canvas { context, _ in
            draw(&context)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .accessibilityLabel("Top view of the keyboard")
    }

    /// Fill and opacity of the glow under the keys.
    private func glow(_ mode: String) -> (GraphicsContext.Shading, Double) {
        guard let light, KeyboardEditing.sameEffect(light, mode) else { return (.color(Color(hex: 0xCFD6E0)), 0.22) }
        if light.effect == KeyboardLighting.off || light.brightness == 0 { return (.color(.clear), 0) }
        let opacity = 0.2 + Double(light.brightness) / 100 * 0.4
        if !KeyboardLighting.colourless.contains(light.effect), let rgb = RGB(hex: light.color ?? "") {
            return (.color(Color(rgb)), opacity)
        }
        var stops: [(Double, String)]
        if KeyboardLighting.gradientEffects.contains(light.effect),
           let gradient = try? KeyboardLighting.stops(light.gradient) {
            stops = gradient.map { (Double($0.0) / 100, $0.1) }
            if ["left", "up-left", "down-left", "right-to-left"].contains(light.direction ?? "") {
                stops = stops.reversed().map { (1 - $0.0, $0.1) }
            }
        } else if light.effect == "quicksand" {
            let scale = KeyboardLighting.quicksandScale
            stops = scale.enumerated().map { (Double($0.offset) / Double(scale.count - 1), $0.element) }
        } else {
            stops = [(0, "ff5a5a"), (0.5, "3ccf7a"), (1, "3d8bff")]
        }
        let gradient = Gradient(stops: stops.compactMap { stop in
            RGB(hex: stop.1).map { Gradient.Stop(color: Color($0), location: stop.0) }
        })
        return (.linearGradient(gradient, startPoint: CGPoint(x: Self.pad, y: 0),
                                endPoint: CGPoint(x: Self.size.width - Self.pad, y: 0)), opacity)
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

        if let mode = settings.lightMode, mode != KeyboardLighting.off {
            let (shading, opacity) = glow(mode)
            if opacity > 0 {
                var glow = context
                glow.opacity = opacity
                glow.addFilter(.blur(radius: 7))
                glow.fill(Path(roundedRect: CGRect(x: pad - 4, y: pad - 4, width: 16 * u - g + 8, height: 5 * u - g + 8),
                               cornerRadius: 8), with: shading)
            }
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
