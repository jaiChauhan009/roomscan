import SwiftUI
import SceneKit
import UIKit

/// Local previews use the captured metric geometry; no upload or USDZ export is required.
struct PlanPreview: View {
    let room: PlanRoom
    var interactive = true
    @State private var show3D = false

    var body: some View {
        VStack(spacing: 4) {
            if interactive {
                Picker("View", selection: $show3D) {
                    Text("2D plan").tag(false)
                    Text("3D room").tag(true)
                }.pickerStyle(.segmented)
            }
            if show3D {
                NativeRoomScene(room: room)
            } else {
                Canvas { context, size in
                    let segments = room.walls.map(PlanRoom.ends)
                    let points = segments.flatMap { [$0.0, $0.1] }
                    guard let x0 = points.map(\.x).min(), let x1 = points.map(\.x).max(),
                          let z0 = points.map(\.y).min(), let z1 = points.map(\.y).max() else { return }
                    let scale = min((size.width - 40) / CGFloat(max(x1 - x0, 0.1)),
                                    (size.height - 40) / CGFloat(max(z1 - z0, 0.1)))
                    func project(_ p: SIMD2<Float>) -> CGPoint {
                        CGPoint(x: size.width / 2 + CGFloat(p.x - (x0 + x1) / 2) * scale,
                                y: size.height / 2 + CGFloat(p.y - (z0 + z1) / 2) * scale)
                    }
                    if let floor = room.floorWorld {
                        let polygon = floor.map { project(SIMD2($0.x, $0.z)) }
                        var path = Path()
                        path.addLines(polygon); path.closeSubpath()
                        context.fill(path, with: .color(.blue.opacity(0.12)))
                    }
                    for (i, edge) in segments.enumerated() {
                        var line = Path()
                        line.move(to: project(edge.0)); line.addLine(to: project(edge.1))
                        context.stroke(line, with: .color(room.walls[i].confidence == "low" ? .orange : .primary), lineWidth: 3)
                    }
                    for surface in room.doors + room.openings + room.windows {
                        let edge = PlanRoom.ends(surface)
                        var line = Path()
                        line.move(to: project(edge.0)); line.addLine(to: project(edge.1))
                        context.stroke(line, with: .color(.cyan), lineWidth: 5)
                    }
                }
                .accessibilityLabel("Floor plan with \(room.walls.count) walls")
            }
        }
    }
}

private struct NativeRoomScene: UIViewRepresentable {
    let room: PlanRoom

    final class Coordinator {
        var geometry: [Float]? = nil
    }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.backgroundColor = .secondarySystemBackground
        view.isPlaying = false
        return view
    }

    func updateUIView(_ view: SCNView, context: Context) {
        let geometry = room.walls.flatMap { $0.transform.columnMajor + [$0.dimensions.x, $0.dimensions.y, $0.dimensions.z] }
        // Upload progress and other parent updates must not rebuild the scene or reset orbit.
        guard context.coordinator.geometry != geometry else { return }
        context.coordinator.geometry = geometry
        let scene = SCNScene()
        for wall in room.walls {
            let box = SCNBox(width: CGFloat(wall.dimensions.x), height: CGFloat(wall.dimensions.y),
                             length: CGFloat(max(wall.dimensions.z, 0.06)), chamferRadius: 0)
            box.firstMaterial?.diffuse.contents = UIColor.systemBlue.withAlphaComponent(0.65)
            let node = SCNNode(geometry: box)
            node.simdTransform = wall.transform
            scene.rootNode.addChildNode(node)
        }
        let points = room.walls.flatMap { wall -> [SIMD2<Float>] in
            let e = PlanRoom.ends(wall); return [e.0, e.1]
        }
        guard !points.isEmpty else { view.scene = scene; return }
        let x0 = points.map(\.x).min()!, x1 = points.map(\.x).max()!
        let z0 = points.map(\.y).min()!, z1 = points.map(\.y).max()!
        let span = max(max(x1 - x0, z1 - z0), 2)
        let target = SCNNode()
        target.position = SCNVector3((x0 + x1) / 2, 0, (z0 + z1) / 2)
        scene.rootNode.addChildNode(target)
        let camera = SCNNode()
        camera.camera = SCNCamera()
        camera.camera?.zFar = Double(span * 20)
        camera.position = SCNVector3(target.position.x + span, span * 1.5, target.position.z + span)
        let look = SCNLookAtConstraint(target: target)
        look.isGimbalLockEnabled = true
        camera.constraints = [look]
        scene.rootNode.addChildNode(camera)
        view.scene = scene
        view.pointOfView = camera
    }
}
