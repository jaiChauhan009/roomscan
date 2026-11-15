#if DEBUG
import SwiftUI
import simd

/// Explicitly synthetic, offline geometry for reproducible simulator screenshots.
/// This entry point is compiled out of Release builds.
struct PreviewFixtureScreen: View {
    static var room: PlanRoom {
        let corners: [SIMD2<Float>] = [SIMD2(0, 0), SIMD2(4, 0), SIMD2(4, 1),
                                      SIMD2(1, 1), SIMD2(1, 4), SIMD2(0, 4)]
        var room = PlanRoom()
        room.walls = corners.indices.map { i in
            let a = corners[i], b = corners[(i + 1) % corners.count]
            let direction = b - a, length = simd_length(direction)
            var t = matrix_identity_float4x4
            t.columns.0 = SIMD4(direction.x / length, 0, direction.y / length, 0)
            t.columns.2 = SIMD4(-direction.y / length, 0, direction.x / length, 0)
            t.columns.3 = SIMD4((a.x + b.x) / 2, 1.3, (a.y + b.y) / 2, 1)
            return PlanSurface(id: "fixture-\(i)", transform: t, dimensions: SIMD3(length, 2.6, 0.08))
        }
        return room
    }

    var body: some View {
        NavigationStack {
            VStack {
                Text("Synthetic room · 7.0 m²")
                    .accessibilityIdentifier("syntheticRoom")
                PlanPreview(room: Self.room).padding()
            }.navigationTitle("Preview test")
        }
    }
}
#endif
