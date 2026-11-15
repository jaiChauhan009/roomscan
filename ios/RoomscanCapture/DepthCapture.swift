import ARKit
import Foundation

/// Optional diagnostic sensor data. RGB/parametric capture continues when sceneDepth is absent.
/// The RoomPlan-owned ARSession configuration and delegate are never replaced.
struct DepthAttachment: Codable, Equatable {
    var file: String
    var path: String
    var confidenceFile: String?
    var confidencePath: String?
    var width: Int
    var height: Int
    var intrinsics: [Float]
    var validFraction: Double
}

struct DepthSnapshot {
    let values: Data                 // row-major float32 little-endian, metres; zero = invalid
    let confidence: Data?            // row-major UInt8, ARConfidenceLevel raw values
    let width: Int
    let height: Int
    let intrinsics: [Float]
    let validFraction: Double

    /// Called on the bounded encoding queue. Never assumes CVPixelBuffer rows are packed.
    static func copy(_ depth: ARDepthData, cameraIntrinsics: simd_float3x3,
                     imageWidth: Int, imageHeight: Int) -> DepthSnapshot? {
        copy(buffer: depth.depthMap, confidenceMap: depth.confidenceMap,
             cameraIntrinsics: cameraIntrinsics, imageWidth: imageWidth, imageHeight: imageHeight)
    }

    static func copy(buffer: CVPixelBuffer, confidenceMap: CVPixelBuffer?,
                     cameraIntrinsics: simd_float3x3, imageWidth: Int, imageHeight: Int) -> DepthSnapshot? {
        guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_DepthFloat32,
              imageWidth > 0, imageHeight > 0,
              CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return nil }
        let width = CVPixelBufferGetWidth(buffer), height = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        guard width > 0, height > 0 else { return nil }
        var confidence: Data? = nil
        if let map = confidenceMap,
           CVPixelBufferGetWidth(map) == width, CVPixelBufferGetHeight(map) == height,
           CVPixelBufferGetPixelFormatType(map) == kCVPixelFormatType_OneComponent8,
           CVPixelBufferLockBaseAddress(map, .readOnly) == kCVReturnSuccess {
            defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
            if let pixels = CVPixelBufferGetBaseAddress(map) {
                var packed = Data(capacity: width * height)
                for y in 0..<height {
                    packed.append(pixels.advanced(by: y * CVPixelBufferGetBytesPerRow(map)).assumingMemoryBound(to: UInt8.self), count: width)
                }
                confidence = packed
            }
        }
        var values = Data(capacity: width * height * 4)
        var valid = 0
        for y in 0..<height {
            let row = base.advanced(by: y * stride).assumingMemoryBound(to: Float.self)
            for x in 0..<width {
                let value = row[x]
                let finite = value.isFinite && value > 0
                if finite && (confidence.map { $0[y * width + x] >= 1 } ?? true) { valid += 1 }
                var bits = (finite ? value : Float(0)).bitPattern.littleEndian
                withUnsafeBytes(of: &bits) { values.append(contentsOf: $0) }
            }
        }
        var k = cameraIntrinsics
        let sx = Float(width) / Float(imageWidth), sy = Float(height) / Float(imageHeight)
        for column in 0..<3 { k[column][0] *= sx; k[column][1] *= sy }
        return DepthSnapshot(values: values, confidence: confidence, width: width, height: height,
                             intrinsics: k.columnMajor, validFraction: Double(valid) / Double(width * height))
    }

    func write(directory: String, stem: String) throws -> DepthAttachment {
        let path = "\(directory)/\(stem).depth.f32"
        let confidencePath = confidence.map { _ in "\(directory)/\(stem).confidence.u8" }
        do {
            try values.write(to: documentsDir.appendingPathComponent(path), options: .atomic)
            if let confidence, let confidencePath {
                try confidence.write(to: documentsDir.appendingPathComponent(confidencePath), options: .atomic)
            }
        } catch {
            try? FileManager.default.removeItem(at: documentsDir.appendingPathComponent(path))
            if let confidencePath { try? FileManager.default.removeItem(at: documentsDir.appendingPathComponent(confidencePath)) }
            throw error
        }
        return DepthAttachment(file: "depth/\(stem).depth.f32", path: path,
                               confidenceFile: confidence.map { _ in "depth/\(stem).confidence.u8" },
                               confidencePath: confidencePath, width: width, height: height,
                               intrinsics: intrinsics, validFraction: validFraction)
    }
}
