import XCTest
import UIKit
@testable import RoomscanCapture

/// Talks to the real backend; skipped when it cannot be reached.
final class BackendTests: XCTestCase {
    let api = API(base: ProcessInfo.processInfo.environment["ROOMSCAN_BACKEND"] ?? AppModel.defaultBase)

    override func setUp() async throws {
        do { _ = try await api.health() } catch { throw XCTSkip("backend unreachable: \(error.localizedDescription)") }
    }

    func tmpFile(_ name: String, _ data: Data) throws -> URL {
        let u = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        try data.write(to: u)
        return u
    }

    func testRoomWithPhotoUpload() async throws {
        let pid = try await api.createProject()
        let space = try await api.createSpace(pid, name: "Kitchen", sizes: ["length": 4.0])
        let sid = try XCTUnwrap(space["space_id"] as? String)
        let img = UIGraphicsImageRenderer(size: CGSize(width: 64, height: 48)).image { c in
            UIColor.orange.setFill(); c.fill(CGRect(x: 0, y: 0, width: 64, height: 48))
        }
        let file = try tmpFile("p.jpg", try XCTUnwrap(img.jpegData(compressionQuality: 0.8)))
        let sha = try sha256Hex(of: file)
        let rec = try await api.upload(pid: pid, target: "spaces/\(sid)", file: file, name: "p.jpg", sha256: sha) { _ in }
        XCTAssertEqual(rec["sha256"] as? String, sha)
        let p = try await api.project(pid)
        let spaces = try XCTUnwrap(p["spaces"] as? [[String: Any]])
        XCTAssertEqual(spaces.first?["name"] as? String, "Kitchen")
        XCTAssertEqual((spaces.first?["files"] as? [Any])?.count, 1)
    }

    func testRoomPlanZipEndToEnd() async throws {
        let pid = try await api.createProject()
        try await api.setCapture(pid, kind: "lidar")
        let zip = FileManager.default.temporaryDirectory.appendingPathComponent("synthetic-room1.roomscan.zip")
        try? FileManager.default.removeItem(at: zip)
        try writeRoomscanZip(rooms: [NamedRoom(name: "Kitchen", room: syntheticRoom(), frames: [])], merged: false, extras: [], to: zip)
        let sha = try sha256Hex(of: zip)
        _ = try await api.upload(pid: pid, target: "captures/lidar", file: zip, name: zip.lastPathComponent, sha256: sha) { _ in }
        let ver = try await api.verify(pid)
        print("verify:", ver)
        let run = try await api.run(pid, force: true, email: nil, damage: false)
        let jid = try XCTUnwrap(run["job_id"] as? String)
        print("job:", jid)

        var job: [String: Any] = [:]
        let deadline = Date().addingTimeInterval(240)
        while Date() < deadline {
            job = try await api.job(jid)
            if let s = job["status"] as? String, s == "done" || s == "failed" { break }
            try await Task.sleep(nanoseconds: 3_000_000_000)
        }
        print("job state:", job)
        let runs = job["runs"] as? [[String: Any]] ?? []
        let lidar = runs.first { ($0["tier"] as? String) == "lidar" } ?? runs.first ?? [:]
        var rooms: [[String: Any]] = []
        if (lidar["status"] as? String) == "done" {
            let path = (lidar["outputs"] as? [String: Any])?["result_json"] as? String
                ?? (try api.jobFileURL(jid, (lidar["prefix"] as? String ?? "") + "result.json").absoluteString)
            let data = try await api.download(path)
            rooms = (try JSONSerialization.jsonObject(with: data) as? [String: Any])?["rooms"] as? [[String: Any]] ?? []
        }
        if rooms.isEmpty {
            // The backend's roomscan.roomplan/1 reader is being added in parallel: until it lands
            // the server treats the zip as a broken Stray Scanner export.
            let opts = XCTExpectedFailure.Options()
            opts.isStrict = false
            XCTExpectFailure("backend does not read roomscan.roomplan/1 zips yet (run: \(lidar["status"] ?? "?"), \(lidar["error"] ?? job["error"] ?? "no error"))", options: opts)
            XCTFail("no rooms in the LiDAR run result (job \(job["status"] ?? "?"))")
            return
        }
        XCTAssertEqual(rooms.count, 1)
        let area = (rooms[0]["floor_area"] as? [String: Any])?["value"] as? Double
        XCTAssertEqual(try XCTUnwrap(area), 12, accuracy: 0.6)
    }
}
