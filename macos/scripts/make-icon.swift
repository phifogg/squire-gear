// Generates the placeholder app icon set. Run: swift scripts/make-icon.swift
// Replace App/Assets.xcassets/AppIcon.appiconset with real artwork whenever you have it.
import AppKit

func render(pixels: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(pixels)
    func c(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> NSColor { NSColor(red: r, green: g, blue: b, alpha: a) }

    // Body: macOS icon grid, 824 of 1024.
    let inset = s * 100 / 1024
    let body = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let shape = NSBezierPath(roundedRect: body, xRadius: s * 0.185, yRadius: s * 0.185)

    NSGraphicsContext.saveGraphicsState()
    shape.addClip()

    // Deep navy to electric blue, glow rising from the bottom centre.
    NSGradient(colors: [c(0.02, 0.03, 0.16), c(0.03, 0.08, 0.42)])!.draw(in: body, angle: 90)
    NSGradient(colors: [c(0.10, 0.30, 1.00, 0.85), c(0.10, 0.30, 1.00, 0)])!
        .draw(fromCenter: NSPoint(x: s / 2, y: body.minY + s * 0.04), radius: 0,
              toCenter: NSPoint(x: s / 2, y: body.minY + s * 0.04), radius: s * 0.55, options: [])

    // Concentric HUD rings on the "floor", in perspective.
    let cx = s / 2, cy = body.minY + s * 0.10
    for (i, radius) in [0.16, 0.26, 0.37, 0.49, 0.62].enumerated() {
        let rx = s * radius, ry = rx * 0.27
        let ring = NSBezierPath(ovalIn: NSRect(x: cx - rx, y: cy - ry, width: 2 * rx, height: 2 * ry))
        ring.lineWidth = max(1, s * (i % 2 == 0 ? 0.005 : 0.003))
        c(0.45, 0.75, 1.0, 0.55 - 0.07 * CGFloat(i)).setStroke()
        ring.stroke()
    }

    // Thin vertical light streaks.
    for (x, h, alpha) in [(0.22, 0.45, 0.10), (0.36, 0.62, 0.14), (0.64, 0.58, 0.14), (0.78, 0.40, 0.10)] as [(CGFloat, CGFloat, CGFloat)] {
        let rect = NSRect(x: s * x, y: body.minY + s * 0.10, width: max(1, s * 0.004), height: s * h)
        NSGradient(colors: [c(0.6, 0.85, 1, alpha), c(0.6, 0.85, 1, 0)])!.draw(in: rect, angle: 90)
    }
    NSGraphicsContext.restoreGraphicsState()

    // Shield with a blade cut out of it (Falchion and Keris are both blades): silver-white with a blue glow.
    let w = s * 0.46, h = s * 0.54
    let mid = NSPoint(x: s / 2, y: s / 2 + s * 0.035)
    func p(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: mid.x + x * w, y: mid.y + y * h) }
    let shield = NSBezierPath()
    shield.move(to: p(-0.5, 0.40))
    shield.curve(to: p(-0.40, 0.5), controlPoint1: p(-0.5, 0.46), controlPoint2: p(-0.46, 0.5))
    shield.line(to: p(0.40, 0.5))
    shield.curve(to: p(0.5, 0.40), controlPoint1: p(0.46, 0.5), controlPoint2: p(0.5, 0.46))
    shield.line(to: p(0.5, 0.06))
    shield.curve(to: p(0, -0.5), controlPoint1: p(0.5, -0.26), controlPoint2: p(0.2, -0.42))
    shield.curve(to: p(-0.5, 0.06), controlPoint1: p(-0.2, -0.42), controlPoint2: p(-0.5, -0.26))
    shield.close()

    let tinted = NSImage(size: NSSize(width: s, height: s), flipped: false) { rect in
        NSGradient(colors: [c(1, 1, 1), c(0.70, 0.70, 1.0)])!.draw(in: shield, angle: -90)
        return true
    }
    let glow = NSShadow()
    glow.shadowColor = c(0.25, 0.55, 1.0, 0.95)
    glow.shadowBlurRadius = s * 0.05
    glow.shadowOffset = .zero
    NSGraphicsContext.saveGraphicsState()
    glow.set()
    tinted.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    tinted.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)

    // Blade, crossguard, grip and pommel in the background navy.
    let blade = NSBezierPath()
    blade.move(to: p(0, 0.40))
    blade.line(to: p(0.075, 0.28))
    blade.line(to: p(0.075, -0.02))
    blade.line(to: p(-0.075, -0.02))
    blade.line(to: p(-0.075, 0.28))
    blade.close()
    blade.append(NSBezierPath(rect: NSRect(x: p(-0.20, 0).x, y: p(0, -0.065).y, width: 0.40 * w, height: 0.05 * h)))
    blade.append(NSBezierPath(rect: NSRect(x: p(-0.03, 0).x, y: p(0, -0.24).y, width: 0.06 * w, height: 0.18 * h)))
    blade.append(NSBezierPath(ovalIn: NSRect(x: p(-0.055, 0).x, y: p(0, -0.30).y, width: 0.11 * w, height: 0.07 * h)))
    c(0.03, 0.08, 0.38).setFill()
    blade.fill()

    // Thin light edge for a HUD feel.
    shape.lineWidth = max(1, s * 0.004)
    c(0.5, 0.75, 1.0, 0.5).setStroke()
    shape.stroke()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let dir = URL(fileURLWithPath: "App/Assets.xcassets/AppIcon.appiconset")
var images: [[String: String]] = []
for (points, scale) in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)] {
    let pixels = points * scale
    let name = "icon_\(pixels).png"
    try render(pixels: pixels).write(to: dir.appendingPathComponent(name))
    images.append(["idiom": "mac", "size": "\(points)x\(points)", "scale": "\(scale)x", "filename": name])
}
let contents: [String: Any] = ["images": images, "info": ["version": 1, "author": "xcode"]]
try JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys])
    .write(to: dir.appendingPathComponent("Contents.json"))
print("wrote \(images.count) icons")
