import XCTest
import CoreVideo
import simd
@testable import RoomscanCapture

final class DepthCaptureTests: XCTestCase {
    func testDepthPackingAndIntrinsics() throws {
        var created: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, 2, 2, kCVPixelFormatType_DepthFloat32,
                                           nil, &created), kCVReturnSuccess)
        let buffer = try XCTUnwrap(created)
        CVPixelBufferLockBaseAddress(buffer, [])
        let base = try XCTUnwrap(CVPixelBufferGetBaseAddress(buffer))
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let a = base.assumingMemoryBound(to: Float.self)
        let b = base.advanced(by: stride).assumingMemoryBound(to: Float.self)
        a[0] = 1.5; a[1] = .nan; b[0] = -1; b[1] = 3
        CVPixelBufferUnlockBaseAddress(buffer, [])
        let k = simd_float3x3(SIMD3(100, 0, 0), SIMD3(0, 120, 0), SIMD3(50, 40, 1))
        let snapshot = try XCTUnwrap(DepthSnapshot.copy(buffer: buffer, confidenceMap: nil,
                                                      cameraIntrinsics: k, imageWidth: 100, imageHeight: 80))
        XCTAssertEqual(snapshot.values.count, 16) // excludes CVPixelBuffer row padding
        XCTAssertEqual(snapshot.validFraction, 0.5)
        XCTAssertEqual(snapshot.intrinsics[0], 2, accuracy: 1e-5)
        XCTAssertEqual(snapshot.intrinsics[4], 3, accuracy: 1e-5)
        XCTAssertEqual(snapshot.intrinsics[6], 1, accuracy: 1e-5)
        XCTAssertEqual(snapshot.intrinsics[7], 1, accuracy: 1e-5)
        let expected: [Float] = [1.5, 0, 0, 3]
        var packed = Data()
        for value in expected {
            var bits = value.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { packed.append(contentsOf: $0) }
        }
        XCTAssertEqual(snapshot.values, packed)
        XCTAssertNil(snapshot.confidence)
    }

    func testDepthZipAndMetadata() throws {
        let directory = "depth-tests/\(UUID().uuidString)"
        let folder = documentsDir.appendingPathComponent(directory)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let snapshot = DepthSnapshot(values: Data([0, 0, 128, 63]), confidence: Data([2]),
                                     width: 1, height: 1, intrinsics: [1, 0, 0, 0, 1, 0, 0, 0, 1], validFraction: 1)
        let depth = try snapshot.write(directory: directory, stem: "000001")
        let image = folder.appendingPathComponent("000001.jpg")
        try Data([1, 2, 3]).write(to: image)
        let frame = FrameRecord(file: "frames/000001.jpg", path: "\(directory)/000001.jpg", t: 0,
                                transform: matrix_identity_float4x4.columnMajor,
                                intrinsics: depth.intrinsics, width: 1, height: 1, depth: depth)
        let zip = folder.appendingPathComponent("capture.zip")
        try writeRoomscanZip(rooms: [NamedRoom(name: "Depth", room: syntheticRoom(), frames: [frame])],
                             merged: true, sessionId: "test", extras: [], to: zip)
        let entries = try TinyZipReader.read(Data(contentsOf: zip))
        XCTAssertEqual(Set(entries.map(\.name)), ["capture.json", "frames/000001.jpg", "depth/000001.depth.f32", "depth/000001.confidence.u8"])
        let json = try XCTUnwrap(entries.first { $0.name == "capture.json" })
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: json.data) as? [String: Any])
        let frames = try XCTUnwrap(object["frames"] as? [[String: Any]])
        let metadata = try XCTUnwrap(frames[0]["depth"] as? [String: Any])
        XCTAssertEqual(metadata["format"] as? String, "float32_le_metres")
        XCTAssertNil(metadata["path"], "Export must not contain local device paths")
    }
}
