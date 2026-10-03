import Foundation
import simd

// A RoomPlan-free copy of what we keep from a CapturedRoom, so capture.json encoding and the
// room numbers can be unit-tested on the simulator (RoomPlan.CapturedRoom cannot be built by hand).

struct PlanSurface {
    var id: String
    var parentId: String? = nil
    var transform: simd_float4x4
    var dimensions: SIMD3<Float>      // x = width, y = height, z = thickness
    var confidence: String = "high"   // high / medium / low
    var isOpen: Bool? = nil           // doors only
    var polygon: [SIMD3<Float>] = []  // floors: corners in the surface's local frame
}

struct PlanObject {
    var category: String
    var transform: simd_float4x4
    var dimensions: SIMD3<Float>
}

struct PlanRoom {
    var walls: [PlanSurface] = []
    var doors: [PlanSurface] = []
    var windows: [PlanSurface] = []
    var openings: [PlanSurface] = []
    var floors: [PlanSurface] = []
    var objects: [PlanObject] = []
    var sections: [String] = []
    var story: Int = 0

    /// Wall endpoints in plan (x, z): transform * (±width/2, 0, 0, 1).
    static func ends(_ w: PlanSurface) -> (SIMD2<Float>, SIMD2<Float>) {
        let hw = w.dimensions.x / 2
        let a = w.transform.apply(SIMD3(-hw, 0, 0)), b = w.transform.apply(SIMD3(hw, 0, 0))
        return (SIMD2(a.x, a.z), SIMD2(b.x, b.z))
    }

    /// Floor corners in world coordinates, or nil if there is no usable floor.
    var floorWorld: [SIMD3<Float>]? {
        guard let f = floors.first, f.polygon.count >= 3 else { return nil }
        return f.polygon.map { f.transform.apply($0) }
    }
}

/// Live / final numbers for one room.
struct RoomStats: Equatable {
    var walls = 0, doors = 0, windows = 0, openings = 0
    var area: Double = 0, width: Double = 0, length: Double = 0, height: Double = 0

    init() {}

    init(_ r: PlanRoom) {
        walls = r.walls.count
        doors = r.doors.count
        windows = r.windows.count
        openings = r.openings.count
        height = Double(r.walls.map { $0.dimensions.y }.max() ?? 0)
        let ends = r.walls.map { PlanRoom.ends($0) }
        var pts: [SIMD2<Float>] = (r.floorWorld ?? []).map { SIMD2($0.x, $0.z) }
        let fromFloor = pts.count >= 3
        if !fromFloor { pts = ends.flatMap { [$0.0, $0.1] } }
        guard pts.count >= 2 else { return }
        // box aligned with the longest wall
        var ang: Float = 0
        if let w = ends.max(by: { simd_distance($0.0, $0.1) < simd_distance($1.0, $1.1) }) {
            let d = w.1 - w.0
            ang = atan2(d.y, d.x)
        }
        let c = cos(-ang), s = sin(-ang)
        let rot = pts.map { SIMD2<Float>(c * $0.x - s * $0.y, s * $0.x + c * $0.y) }
        let xs = rot.map(\.x), ys = rot.map(\.y)
        let a = Double(xs.max()! - xs.min()!), b = Double(ys.max()! - ys.min()!)
        width = min(a, b)
        length = max(a, b)
        if fromFloor {
            var sum: Float = 0
            for i in 0..<pts.count {
                let p = pts[i], q = pts[(i + 1) % pts.count]
                sum += p.x * q.y - q.x * p.y
            }
            area = Double(abs(sum) / 2)
        } else {
            area = width * length
        }
    }

    var summary: String {
        String(format: "%.2f × %.2f m · %.1f m² · ceiling %.2f m", width, length, area, height)
    }
    var counts: String { "\(walls) walls · \(doors) doors · \(windows) windows" + (openings > 0 ? " · \(openings) openings" : "") }
}

/// One saved keyframe (JPEG on disk + camera pose / intrinsics).
struct FrameRecord: Codable, Equatable {
    var file: String          // "frames/000001.jpg" (path inside the zip)
    var path: String          // path relative to Documents
    var t: Double
    var transform: [Float]    // 16, column-major
    var intrinsics: [Float]   // 9, column-major
    var width: Int
    var height: Int
}

struct NamedRoom {
    var name: String
    var room: PlanRoom
    var frames: [FrameRecord]
}

// MARK: - capture.json ("roomscan.roomplan/1")

private func surfaceJSON(_ s: PlanSurface, door: Bool) -> J {
    let c = s.transform.position
    var kv: [(String, J)] = [
        ("id", .str(s.id)),
        ("wall_id", s.parentId.map { .str($0) } ?? .null),
        ("center", .xs([c.x, c.y, c.z])),
        ("width", .f(s.dimensions.x)),
        ("height", .f(s.dimensions.y)),
        ("confidence", .str(s.confidence)),
    ]
    if door { kv.append(("is_open", s.isOpen.map { .bool($0) } ?? .null)) }
    return .obj(kv)
}

func roomJSON(_ r: PlanRoom, name: String, index: Int) -> J {
    let walls: [J] = r.walls.map { w in
        let (a, b) = PlanRoom.ends(w)
        return .obj([
            ("id", .str(w.id)),
            ("start", .xs([a.x, a.y])),
            ("end", .xs([b.x, b.y])),
            ("height", .f(w.dimensions.y)),
            ("thickness", .f(w.dimensions.z)),
            ("confidence", .str(w.confidence)),
        ])
    }
    var floor: J = .null
    if let pts = r.floorWorld {
        let y = pts.map(\.y).reduce(0, +) / Float(pts.count)
        floor = .obj([("polygon", .arr(pts.map { .xs([$0.x, $0.z]) })), ("y", .f(y))])
    }
    let objects: [J] = r.objects.map { o in
        let c = o.transform.position
        let c0 = o.transform.columns.0
        return .obj([
            ("category", .str(o.category)),
            ("center", .xs([c.x, c.y, c.z])),
            ("dimensions", .xs([o.dimensions.x, o.dimensions.y, o.dimensions.z])),
            ("yaw", .f(atan2(-c0.z, c0.x))),
        ])
    }
    return .obj([
        ("name", .str(name)),
        ("index", .int(index)),
        ("story", .int(r.story)),
        ("walls", .arr(walls)),
        ("doors", .arr(r.doors.map { surfaceJSON($0, door: true) })),
        ("windows", .arr(r.windows.map { surfaceJSON($0, door: false) })),
        ("openings", .arr(r.openings.map { surfaceJSON($0, door: false) })),
        ("floor", floor),
        ("objects", .arr(objects)),
        ("section_labels", .arr(r.sections.map { .str($0) })),
    ])
}

func systemVersionString() -> String {
    let v = ProcessInfo.processInfo.operatingSystemVersion
    return "iOS \(v.majorVersion).\(v.minorVersion)" + (v.patchVersion > 0 ? ".\(v.patchVersion)" : "")
}

func captureJSON(rooms: [NamedRoom], merged: Bool, capturedAt: String = isoNow()) -> J {
    var frames: [J] = []
    for (i, r) in rooms.enumerated() {
        for f in r.frames {
            frames.append(.obj([
                ("file", .str(f.file)), ("room_index", .int(i)), ("t", .f(f.t)),
                ("transform", .xs(f.transform)), ("intrinsics", .xs(f.intrinsics)),
                ("width", .int(f.width)), ("height", .int(f.height)),
            ]))
        }
    }
    let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0"
    return .obj([
        ("format", .str("roomscan.roomplan/1")),
        ("app_version", .str(version)),
        ("device", .obj([("model", .str(deviceModel())), ("system", .str(systemVersionString()))])),
        ("captured_at", .str(capturedAt)),
        ("units", .str("m")),
        ("coordinate_frame", .str("arkit_world_y_up")),
        ("merged", .bool(merged)),
        ("rooms", .arr(rooms.enumerated().map { roomJSON($0.element.room, name: $0.element.name, index: $0.offset) })),
        ("frames", .arr(frames)),
    ])
}

/// Write a .roomscan.zip: capture.json, frames/*.jpg, and the given extra files (usdz).
func writeRoomscanZip(rooms: [NamedRoom], merged: Bool, extras: [(name: String, url: URL)], to zipURL: URL) throws {
    let zw = try ZipWriter(url: zipURL)
    try zw.add(name: "capture.json", data: Data(captureJSON(rooms: rooms, merged: merged).text.utf8))
    for r in rooms {
        for f in r.frames {
            try zw.addFile(name: f.file, url: documentsDir.appendingPathComponent(f.path))
        }
    }
    for e in extras { try zw.addFile(name: e.name, url: e.url) }
    try zw.finish()
}
