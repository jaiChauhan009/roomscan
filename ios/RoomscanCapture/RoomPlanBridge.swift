import Foundation
import RoomPlan
import simd

private func confidenceString(_ c: CapturedRoom.Confidence) -> String {
    switch c {
    case .high: return "high"
    case .medium: return "medium"
    case .low: return "low"
    @unknown default: return "low"
    }
}

extension PlanSurface {
    init(_ s: CapturedRoom.Surface) {
        var open: Bool? = nil
        if case .door(let o) = s.category { open = o }
        self.init(id: s.identifier.uuidString, parentId: s.parentIdentifier?.uuidString,
                  transform: s.transform, dimensions: s.dimensions, confidence: confidenceString(s.confidence),
                  isOpen: open, polygon: s.polygonCorners)
    }
}

extension PlanRoom {
    init(_ r: CapturedRoom) {
        self.init()
        walls = r.walls.map(PlanSurface.init)
        doors = r.doors.map(PlanSurface.init)
        windows = r.windows.map(PlanSurface.init)
        openings = r.openings.map(PlanSurface.init)
        floors = r.floors.map(PlanSurface.init)
        objects = r.objects.map { PlanObject(category: String(describing: $0.category), transform: $0.transform, dimensions: $0.dimensions) }
        sections = r.sections.map { String(describing: $0.label) }
        story = r.story
    }
}

/// A finished RoomPlan room kept for the session.
struct ScannedRoom: Identifiable {
    let id = UUID()
    var name: String
    var captured: CapturedRoom
    /// ARSession run this room was scanned in: rooms with the same id share one world frame
    var sessionId: String
    var frames: [FrameRecord]
    var plan: PlanRoom { PlanRoom(captured) }
    var stats: RoomStats { RoomStats(plan) }
}

enum RoomPlanExport {
    /// One room → its own zip. `merged` is true: the room is in the world frame of its ARSession,
    /// shared by every room with the same session_id (the server joins them by that id).
    static func roomZip(_ r: ScannedRoom, to zipURL: URL) throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let u = tmp.appendingPathComponent("room.usdz")
        let extras: [(name: String, url: URL)] = (try? r.captured.export(to: u, exportOptions: .parametric)) != nil ? [("room.usdz", u)] : []
        try writeRoomscanZip(rooms: [NamedRoom(name: r.name, room: r.plan, frames: r.frames)], merged: true, sessionId: r.sessionId, extras: extras, to: zipURL)
    }

    /// All rooms → one zip; merged with StructureBuilder when there are 2+ rooms and it succeeds.
    static func homeZip(_ rooms: [ScannedRoom], to zipURL: URL) async throws -> Bool {
        guard !rooms.isEmpty else { throw CocoaError(.fileWriteInvalidFileName) }
        let oneSession = Set(rooms.map(\.sessionId)).count == 1
        var merged = false
        var structure: CapturedStructure? = nil
        var named = rooms.map { NamedRoom(name: $0.name, room: $0.plan, frames: $0.frames) }
        if rooms.count > 1 && oneSession {
            if let s = try? await StructureBuilder(options: [.beautifyObjects]).capturedStructure(from: rooms.map(\.captured)),
               s.rooms.count == rooms.count {
                structure = s
                merged = true
                // StructureBuilder need not preserve input order. Keep names and photos
                // attached to their original room by identifier, never by array position.
                let byID = Dictionary(uniqueKeysWithValues: s.rooms.map { ($0.identifier, $0) })
                if rooms.allSatisfy({ byID[$0.captured.identifier] != nil }) {
                    for i in named.indices { named[i].room = PlanRoom(byID[rooms[i].captured.identifier]!) }
                } else {
                    structure = nil
                    merged = false
                }
            }
        }
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        var extras: [(name: String, url: URL)] = []
        if let structure {
            let u = tmp.appendingPathComponent("room.usdz")
            if (try? structure.export(to: u, exportOptions: .parametric)) != nil { extras.append(("room.usdz", u)) }
        } else if rooms.count == 1 {
            let u = tmp.appendingPathComponent("room.usdz")
            if (try? rooms[0].captured.export(to: u, exportOptions: .parametric)) != nil { extras.append(("room.usdz", u)) }
        } else {
            for (i, r) in rooms.enumerated() {
                let u = tmp.appendingPathComponent("room_\(i).usdz")
                if (try? r.captured.export(to: u, exportOptions: .parametric)) != nil { extras.append(("room_\(i).usdz", u)) }
            }
        }
        let oneFrame = merged || Set(rooms.map(\.sessionId)).count == 1
        try writeRoomscanZip(rooms: named, merged: oneFrame, sessionId: rooms[0].sessionId, extras: extras, to: zipURL)
        return merged
    }
}
