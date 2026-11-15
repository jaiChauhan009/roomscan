import XCTest
@testable import RoomscanCapture

final class ScanLibraryTests: XCTestCase {
    private func store() -> AtomicJSONStore<ScanLibrary> {
        AtomicJSONStore(url: FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString).appendingPathComponent("library.json"))
    }

    func testPropertiesSurviveRelaunchWithoutACloudProject() throws {
        let store = store()
        defer { try? FileManager.default.removeItem(at: store.url.deletingLastPathComponent()) }
        var library = ScanLibrary()
        let first = library.selectedId
        let second = LocalScanProperty(name: "Second property")
        library.properties.append(second)
        library.selectedId = second.id
        try store.save(library)
        let recovered = try XCTUnwrap(store.load()).validated()
        XCTAssertEqual(recovered.selectedId, second.id)
        XCTAssertEqual(recovered.properties.map(\.id), [first, second.id])
        XCTAssertNil(recovered.properties[0].projectId)
    }

    func testCorruptPrimaryRecoversLastReadableRevision() throws {
        let store = store()
        defer { try? FileManager.default.removeItem(at: store.url.deletingLastPathComponent()) }
        var library = ScanLibrary(projectId: "cloud-a")
        try store.save(library)
        library.properties[0].projectId = "cloud-b"
        try store.save(library)
        try Data("partial write".utf8).write(to: store.url)
        XCTAssertEqual(try store.load()?.properties[0].projectId, "cloud-a")
        // Re-saving recovery must not replace the readable backup with corrupt data.
        try store.save(try XCTUnwrap(store.load()))
        XCTAssertEqual(try store.load()?.properties[0].projectId, "cloud-a")
    }

    func testUnreadableLibraryThrowsAndPreservesItsBytes() throws {
        let store = store()
        defer { try? FileManager.default.removeItem(at: store.url.deletingLastPathComponent()) }
        try FileManager.default.createDirectory(at: store.url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let corrupt = Data("not a library".utf8)
        try corrupt.write(to: store.url)
        XCTAssertThrowsError(try store.load())
        XCTAssertEqual(try Data(contentsOf: store.url), corrupt)
    }

    func testUnknownSchemaAndInvalidSelectionAreRejected() throws {
        var library = ScanLibrary()
        library.version = 100
        XCTAssertThrowsError(try library.validated())
        library.version = 1
        library.selectedId = UUID()
        XCTAssertThrowsError(try library.validated())
    }

    func testReviewFindsMissingWallEvenWhenFloorIsPresent() {
        var room = syntheticRoom()
        room.walls.removeLast()
        XCTAssertNotNil(room.floorWorld)
        XCTAssertTrue(room.reviewIssues.contains { $0.id == "boundary" })
    }

    func testReviewNamesMediumAndLowConfidenceWalls() {
        var room = syntheticRoom()
        room.walls[1].confidence = "medium"
        room.walls[2].confidence = "low"
        XCTAssertTrue(room.reviewIssues.contains { $0.id == "confidence" && $0.message.contains("2, 3") })
    }

    func testMeasuredReferencesAreValidatedAndExportedSeparately() throws {
        let room = syntheticRoom()
        var measurement = WallReferenceMeasurement(wallId: room.walls[0].id, metres: 4.2,
                                                   uncertaintyMetres: 0.005, source: "laser")
        XCTAssertTrue(measurement.isValid)
        let json = captureJSON(rooms: [NamedRoom(name: "Test", room: room, frames: [],
                                                referenceMeasurements: [measurement])], merged: true, sessionId: "s").text
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
        let rooms = try XCTUnwrap(root["rooms"] as? [[String: Any]])
        let references = try XCTUnwrap(rooms[0]["reference_measurements"] as? [[String: Any]])
        XCTAssertEqual(references[0]["metres"] as? Double, 4.2)
        XCTAssertEqual(room.walls[0].dimensions.x, 4) // Recording a reference never changes geometry.
        measurement.metres = .nan
        XCTAssertFalse(measurement.isValid)
        measurement.metres = 4.2
        measurement.uncertaintyMetres = 0
        XCTAssertFalse(measurement.isValid)
    }
}
