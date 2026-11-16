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
struct ScannedRoom: Identifiable, Codable {
    var id = UUID()
    var name: String
    var captured: CapturedRoom
    /// ARSession run this room was scanned in: rooms with the same id share one world frame
    var sessionId: String
    var frames: [FrameRecord]
    var zipPath: String? = nil
    var referenceMeasurements: [WallReferenceMeasurement]? = nil
    var editedRoom: PlanRoom? = nil
    var placement: PlanPlacement? = nil
    var alignmentGroup: String? = nil
    var originalSources: [OriginalScanRoom]? = nil
    var basePlan: PlanRoom { editedRoom ?? PlanRoom(captured) }
    var plan: PlanRoom { basePlan.placed(placement) }
    var isModified: Bool { editedRoom != nil || placement != nil || originalSources != nil }
    var worldFrameId: String { alignmentGroup ?? sessionId }
    var sources: [OriginalScanRoom] { originalSources ?? [OriginalScanRoom(name:name,captured:captured,sessionId:sessionId)] }
    func exportedFrames() throws -> [FrameRecord] {
        guard let placement else { return frames }
        _ = try placement.validated()
        return try frames.map { frame in
            guard frame.transform.count==16, frame.transform.allSatisfy(\.isFinite) else { throw CocoaError(.fileReadCorruptFile) }
            let a=frame.transform
            let matrix=simd_float4x4(columns:(SIMD4(a[0],a[1],a[2],a[3]),SIMD4(a[4],a[5],a[6],a[7]),
                                             SIMD4(a[8],a[9],a[10],a[11]),SIMD4(a[12],a[13],a[14],a[15])))
            var result=frame;result.transform=(placement.matrix*matrix).columnMajor;return result
        }
    }
    var stats: RoomStats { RoomStats(plan) }
}

struct OriginalScanRoom: Codable {
    var name:String
    var captured:CapturedRoom
    var sessionId:String
}

enum RoomPlanExport {
    private static func originalGeometry(_ rooms:[ScannedRoom], directory:URL) throws -> (name:String,url:URL)? {
        guard rooms.contains(where:{$0.isModified}) else { return nil }
        var seen=Set<UUID>()
        let sources=rooms.flatMap(\.sources).filter { seen.insert($0.captured.identifier).inserted }
        let named=sources.map { NamedRoom(name:$0.name,room:PlanRoom($0.captured),frames:[],sourceSessionId:$0.sessionId) }
        let url=directory.appendingPathComponent("original_capture.json")
        let json=captureJSON(rooms:named,merged:Set(sources.map(\.sessionId)).count==1,
                             sessionId:sources.first?.sessionId ?? "original").text
        try Data(json.utf8).write(to:url,options:.atomic)
        return ("original_capture.json",url)
    }
    /// One room → its own zip. `merged` is true: the room is in the world frame of its ARSession,
    /// shared by every room with the same session_id (the server joins them by that id).
    static func roomZip(_ r: ScannedRoom, to zipURL: URL) throws {
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let u = tmp.appendingPathComponent("room.usdz")
        var extras: [(name: String, url: URL)] = []
        if !r.isModified, (try? r.captured.export(to:u,exportOptions:.parametric)) != nil { extras.append(("room.usdz",u)) }
        if let original=try originalGeometry([r],directory:tmp) { extras.append(original) }
        try writeRoomscanZip(rooms: [NamedRoom(name: r.name, room: r.plan, frames: try r.exportedFrames(),
                                              referenceMeasurements: r.referenceMeasurements ?? [],manuallyEdited:r.isModified)],
                             merged: true, sessionId: r.worldFrameId, extras: extras, to: zipURL)
    }

    /// All rooms → one zip; merged with StructureBuilder when there are 2+ rooms and it succeeds.
    static func homeZip(_ rooms: [ScannedRoom], to zipURL: URL) async throws -> Bool {
        guard !rooms.isEmpty else { throw CocoaError(.fileWriteInvalidFileName) }
        let oneSession = Set(rooms.map(\.worldFrameId)).count == 1
        var merged = false
        var structure: CapturedStructure? = nil
        var named = try rooms.map { NamedRoom(name: $0.name, room: $0.plan, frames: try $0.exportedFrames(),
                                             referenceMeasurements: $0.referenceMeasurements ?? [],
                                             manuallyEdited:$0.isModified,sourceSessionId:$0.worldFrameId) }
        if rooms.count > 1 && oneSession && !rooms.contains(where:{$0.isModified}) {
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
        if let original=try originalGeometry(rooms,directory:tmp) { extras.append(original) }
        if let structure {
            let u = tmp.appendingPathComponent("room.usdz")
            if (try? structure.export(to: u, exportOptions: .parametric)) != nil { extras.append(("room.usdz", u)) }
        } else if rooms.count == 1 && !rooms[0].isModified {
            let u = tmp.appendingPathComponent("room.usdz")
            if (try? rooms[0].captured.export(to: u, exportOptions: .parametric)) != nil { extras.append(("room.usdz", u)) }
        } else {
            for (i, r) in rooms.enumerated() where !r.isModified {
                let u = tmp.appendingPathComponent("room_\(i).usdz")
                if (try? r.captured.export(to: u, exportOptions: .parametric)) != nil { extras.append(("room_\(i).usdz", u)) }
            }
        }
        let oneFrame = merged || oneSession
        try writeRoomscanZip(rooms: named, merged: oneFrame, sessionId: rooms[0].worldFrameId, extras: extras, to: zipURL)
        return merged
    }
}
