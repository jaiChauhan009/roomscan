import Foundation
import simd

private func decodedMatrix(_ values:[Float]) throws -> simd_float4x4 {
    guard values.count == 16, values.allSatisfy(\.isFinite) else { throw CocoaError(.fileReadCorruptFile) }
    return simd_float4x4(columns:(SIMD4(values[0],values[1],values[2],values[3]),
        SIMD4(values[4],values[5],values[6],values[7]),SIMD4(values[8],values[9],values[10],values[11]),
        SIMD4(values[12],values[13],values[14],values[15])))
}

extension PlanSurface: Codable {
    private struct Record: Codable {
        var id:String;var parentId:String?;var transform:[Float];var dimensions:[Float]
        var confidence:String;var isOpen:Bool?;var polygon:[[Float]]
    }
    init(from decoder:Decoder) throws {
        let record=try Record(from:decoder)
        guard record.dimensions.count==3,record.dimensions.allSatisfy(\.isFinite),
              record.polygon.allSatisfy({$0.count==3 && $0.allSatisfy(\.isFinite)}) else {
            throw CocoaError(.fileReadCorruptFile)
        }
        self.init(id:record.id,parentId:record.parentId,transform:try decodedMatrix(record.transform),
                  dimensions:SIMD3(record.dimensions[0],record.dimensions[1],record.dimensions[2]),
                  confidence:record.confidence,isOpen:record.isOpen,
                  polygon:record.polygon.map{SIMD3($0[0],$0[1],$0[2])})
    }
    func encode(to encoder:Encoder) throws {
        try Record(id:id,parentId:parentId,transform:transform.columnMajor,
                   dimensions:[dimensions.x,dimensions.y,dimensions.z],confidence:confidence,isOpen:isOpen,
                   polygon:polygon.map{[$0.x,$0.y,$0.z]}).encode(to:encoder)
    }
}

extension PlanObject: Codable {
    private struct Record: Codable {var category:String;var transform:[Float];var dimensions:[Float]}
    init(from decoder:Decoder) throws {
        let record=try Record(from:decoder)
        guard record.dimensions.count==3,record.dimensions.allSatisfy(\.isFinite) else { throw CocoaError(.fileReadCorruptFile) }
        self.init(category:record.category,transform:try decodedMatrix(record.transform),
                  dimensions:SIMD3(record.dimensions[0],record.dimensions[1],record.dimensions[2]))
    }
    func encode(to encoder:Encoder) throws {
        try Record(category:category,transform:transform.columnMajor,dimensions:[dimensions.x,dimensions.y,dimensions.z]).encode(to:encoder)
    }
}
