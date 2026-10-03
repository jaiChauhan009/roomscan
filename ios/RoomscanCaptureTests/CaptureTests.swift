import XCTest
import simd
import UIKit
@testable import RoomscanCapture

/// A 4 m (x) × 3 m (z) room, walls 2.6 m high, one door, a floor polygon.
func syntheticRoom() -> PlanRoom {
    func wall(_ id: String, center: SIMD3<Float>, width: Float, turned: Bool) -> PlanSurface {
        var t = matrix_identity_float4x4
        if turned {  // 90° about y: local x runs along world -z
            t.columns.0 = SIMD4(0, 0, -1, 0)
            t.columns.2 = SIMD4(1, 0, 0, 0)
        }
        t.columns.3 = SIMD4(center.x, center.y, center.z, 1)
        return PlanSurface(id: id, transform: t, dimensions: SIMD3(width, 2.6, 0.1), confidence: "high")
    }
    var r = PlanRoom()
    r.walls = [
        wall("w0", center: SIMD3(0, 1.3, -1.5), width: 4, turned: false),
        wall("w1", center: SIMD3(2, 1.3, 0), width: 3, turned: true),
        wall("w2", center: SIMD3(0, 1.3, 1.5), width: 4, turned: false),
        wall("w3", center: SIMD3(-2, 1.3, 0), width: 3, turned: true),
    ]
    var dt = matrix_identity_float4x4
    dt.columns.3 = SIMD4(0.5, 1.05, -1.5, 1)
    r.doors = [PlanSurface(id: "d0", parentId: "w0", transform: dt, dimensions: SIMD3(0.9, 2.1, 0.05), confidence: "medium", isOpen: true)]
    r.floors = [PlanSurface(id: "f0", transform: matrix_identity_float4x4, dimensions: SIMD3(4, 3, 0),
                            polygon: [SIMD3(-2, 0, -1.5), SIMD3(2, 0, -1.5), SIMD3(2, 0, 1.5), SIMD3(-2, 0, 1.5)])]
    r.objects = [PlanObject(category: "bed", transform: matrix_identity_float4x4, dimensions: SIMD3(1.6, 0.5, 2.0))]
    r.sections = ["bedroom"]
    return r
}

final class CaptureTests: XCTestCase {

    func testRoomStats() {
        let s = RoomStats(syntheticRoom())
        XCTAssertEqual(s.walls, 4)
        XCTAssertEqual(s.doors, 1)
        XCTAssertEqual(s.area, 12, accuracy: 1e-4)
        XCTAssertEqual(s.width, 3, accuracy: 1e-4)
        XCTAssertEqual(s.length, 4, accuracy: 1e-4)
        XCTAssertEqual(s.height, 2.6, accuracy: 1e-4)
    }

    func testCaptureJSONContract() throws {
        var t = matrix_identity_float4x4
        t.columns.3 = SIMD4(1.23456789, 2, 3, 1)
        let K = simd_float3x3(SIMD3(1500, 0, 0), SIMD3(0, 1501, 0), SIMD3(960, 720, 1))
        let frame = FrameRecord(file: "frames/000001.jpg", path: "x", t: 12.345678, transform: t.columnMajor,
                                intrinsics: K.columnMajor, width: 1920, height: 1440)
        let j = captureJSON(rooms: [NamedRoom(name: "Kitchen", room: syntheticRoom(), frames: [frame])], merged: false,
                            capturedAt: "2026-10-03T14:00:00Z")
        let text = j.text
        // 4-decimal rounding in the text itself
        XCTAssertTrue(text.contains("1.2346"), text)
        XCTAssertFalse(text.contains("1.23456"))
        XCTAssertTrue(text.contains("12.3457"))

        let o = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
        XCTAssertEqual(Set(o.keys), ["format", "app_version", "device", "captured_at", "units", "coordinate_frame", "merged", "rooms", "frames"])
        XCTAssertEqual(o["format"] as? String, "roomscan.roomplan/1")
        XCTAssertEqual(o["units"] as? String, "m")
        XCTAssertEqual(o["coordinate_frame"] as? String, "arkit_world_y_up")
        XCTAssertEqual(o["merged"] as? Bool, false)
        XCTAssertEqual(o["captured_at"] as? String, "2026-10-03T14:00:00Z")
        let dev = try XCTUnwrap(o["device"] as? [String: Any])
        XCTAssertEqual(Set(dev.keys), ["model", "system"])
        XCTAssertTrue((dev["system"] as? String ?? "").hasPrefix("iOS "))

        let rooms = try XCTUnwrap(o["rooms"] as? [[String: Any]])
        XCTAssertEqual(rooms.count, 1)
        let r = rooms[0]
        XCTAssertEqual(Set(r.keys), ["name", "index", "story", "walls", "doors", "windows", "openings", "floor", "objects", "section_labels"])
        XCTAssertEqual(r["name"] as? String, "Kitchen")
        XCTAssertEqual(r["index"] as? Int, 0)
        let walls = try XCTUnwrap(r["walls"] as? [[String: Any]])
        XCTAssertEqual(walls.count, 4)
        XCTAssertEqual(Set(walls[0].keys), ["id", "start", "end", "height", "thickness", "confidence"])
        XCTAssertEqual(walls[0]["start"] as? [Double], [-2, -1.5])
        XCTAssertEqual(walls[0]["end"] as? [Double], [2, -1.5])
        XCTAssertEqual(walls[0]["height"] as? Double, 2.6)
        // turned wall: local x is world -z
        XCTAssertEqual(walls[1]["start"] as? [Double], [2, 1.5])
        XCTAssertEqual(walls[1]["end"] as? [Double], [2, -1.5])
        let doors = try XCTUnwrap(r["doors"] as? [[String: Any]])
        XCTAssertEqual(Set(doors[0].keys), ["id", "wall_id", "center", "width", "height", "confidence", "is_open"])
        XCTAssertEqual(doors[0]["wall_id"] as? String, "w0")
        XCTAssertEqual(doors[0]["center"] as? [Double], [0.5, 1.05, -1.5])
        XCTAssertEqual(doors[0]["width"] as? Double, 0.9)
        XCTAssertEqual(doors[0]["confidence"] as? String, "medium")
        XCTAssertEqual(doors[0]["is_open"] as? Bool, true)
        let floor = try XCTUnwrap(r["floor"] as? [String: Any])
        XCTAssertEqual((floor["polygon"] as? [[Double]])?.count, 4)
        XCTAssertEqual(floor["y"] as? Double, 0)
        let obj = try XCTUnwrap((r["objects"] as? [[String: Any]])?.first)
        XCTAssertEqual(obj["category"] as? String, "bed")
        XCTAssertEqual(obj["dimensions"] as? [Double], [1.6, 0.5, 2])
        XCTAssertEqual(r["section_labels"] as? [String], ["bedroom"])

        let frames = try XCTUnwrap(o["frames"] as? [[String: Any]])
        XCTAssertEqual(Set(frames[0].keys), ["file", "room_index", "t", "transform", "intrinsics", "width", "height"])
        let tr = try XCTUnwrap(frames[0]["transform"] as? [Double])
        XCTAssertEqual(tr.count, 16)
        XCTAssertEqual(Array(tr[12...14]), [1.2346, 2, 3])   // column-major: translation is the last column
        let k = try XCTUnwrap(frames[0]["intrinsics"] as? [Double])
        XCTAssertEqual(k, [1500, 0, 0, 0, 1501, 0, 960, 720, 1])
        XCTAssertEqual(frames[0]["width"] as? Int, 1920)
    }

    func testNoFloorGivesNull() throws {
        var r = syntheticRoom()
        r.floors = []
        let text = captureJSON(rooms: [NamedRoom(name: "A", room: r, frames: [])], merged: true).text
        let o = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any])
        let room = try XCTUnwrap((o["rooms"] as? [[String: Any]])?.first)
        XCTAssertTrue(room["floor"] is NSNull)
        XCTAssertEqual(o["merged"] as? Bool, true)
        XCTAssertEqual(RoomStats(r).area, 12, accuracy: 1e-3)  // from the wall box
    }

    func testZipWriterProducesValidZip() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("t.zip")
        let a = Data("hello roomscan\n".utf8)
        let b = Data((0..<100_000).map { UInt8(truncatingIfNeeded: $0 * 7) })
        let zw = try ZipWriter(url: url)
        try zw.add(name: "capture.json", data: a)
        try zw.add(name: "frames/000001.jpg", data: b)
        try zw.finish()
        let entries = try TinyZipReader.read(try Data(contentsOf: url))
        XCTAssertEqual(entries.map(\.name), ["capture.json", "frames/000001.jpg"])
        XCTAssertEqual(entries[0].data, a)
        XCTAssertEqual(entries[1].data, b)
        XCTAssertEqual(CRC32.checksum(Data("123456789".utf8)), 0xCBF43926)
    }

    func testRoomscanZip() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).roomscan.zip")
        try writeRoomscanZip(rooms: [NamedRoom(name: "Kitchen", room: syntheticRoom(), frames: [])], merged: false, extras: [], to: url)
        let entries = try TinyZipReader.read(try Data(contentsOf: url))
        XCTAssertEqual(entries.map(\.name), ["capture.json"])
        let o = try JSONSerialization.jsonObject(with: entries[0].data) as? [String: Any]
        XCTAssertEqual(o?["format"] as? String, "roomscan.roomplan/1")
    }

    func testPhotoShrinkKeepsExif() throws {
        let img = UIGraphicsImageRenderer(size: CGSize(width: 4032, height: 3024)).image { ctx in
            UIColor.gray.setFill(); ctx.fill(CGRect(x: 0, y: 0, width: 4032, height: 3024))
        }
        let meta: [String: Any] = [kCGImagePropertyExifDictionary as String: [kCGImagePropertyExifFocalLength as String: 5.96,
                                                                                kCGImagePropertyExifFocalLenIn35mmFilm as String: 24]]
        let out = try XCTUnwrap(shrinkCameraPhoto(img, metadata: meta))
        let src = try XCTUnwrap(CGImageSourceCreateWithData(out as CFData, nil))
        let props = try XCTUnwrap(CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [String: Any])
        XCTAssertEqual(props[kCGImagePropertyPixelWidth as String] as? Int, 2048)
        let exif = try XCTUnwrap(props[kCGImagePropertyExifDictionary as String] as? [String: Any])
        XCTAssertEqual(exif[kCGImagePropertyExifFocalLenIn35mmFilm as String] as? Int, 24)
    }
}

/// Reads a stored (method 0) zip through its central directory and checks every CRC.
enum TinyZipReader {
    struct Entry { var name: String; var data: Data }
    struct Bad: Error { var why: String }

    static func u16(_ d: Data, _ o: Int) -> Int { Int(d[d.startIndex + o]) | Int(d[d.startIndex + o + 1]) << 8 }
    static func u32(_ d: Data, _ o: Int) -> Int { u16(d, o) | u16(d, o + 2) << 16 }

    static func read(_ d: Data) throws -> [Entry] {
        guard d.count >= 22 else { throw Bad(why: "too short") }
        let eocd = d.count - 22
        guard u32(d, eocd) == 0x06054b50 else { throw Bad(why: "no end of central directory") }
        let n = u16(d, eocd + 10), cdSize = u32(d, eocd + 12), cdOff = u32(d, eocd + 16)
        guard cdOff + cdSize == eocd else { throw Bad(why: "central directory offset") }
        var out: [Entry] = []
        var p = cdOff
        for _ in 0..<n {
            guard u32(d, p) == 0x02014b50 else { throw Bad(why: "central header") }
            let method = u16(d, p + 10), crc = u32(d, p + 16), size = u32(d, p + 20)
            let nl = u16(d, p + 28), el = u16(d, p + 30), cl = u16(d, p + 32), local = u32(d, p + 42)
            let name = String(decoding: d.subdata(in: p + 46 ..< p + 46 + nl), as: UTF8.self)
            guard method == 0, u32(d, local) == 0x04034b50 else { throw Bad(why: "local header of \(name)") }
            let start = local + 30 + u16(d, local + 26) + u16(d, local + 28)
            let data = d.subdata(in: start ..< start + size)
            guard Int(CRC32.checksum(data)) == crc else { throw Bad(why: "crc of \(name)") }
            out.append(Entry(name: name, data: data))
            p += 46 + nl + el + cl
        }
        return out
    }
}
