import Foundation

/// An atomic primary file plus the last readable revision. A corrupt file is never
/// silently treated as an empty library, and a corrupt primary cannot replace a backup.
struct AtomicJSONStore<Value: Codable> {
    let url: URL
    var backupURL: URL { url.appendingPathExtension("backup") }

    func load() throws -> Value? {
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) || fm.fileExists(atPath: backupURL.path) else { return nil }
        do { return try JSONDecoder().decode(Value.self, from: Data(contentsOf: url)) }
        catch {
            if let data = try? Data(contentsOf: backupURL),
               let recovered = try? JSONDecoder().decode(Value.self, from: data) { return recovered }
            throw error
        }
    }

    func save(_ value: Value) throws {
        let data = try JSONEncoder().encode(value)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let previous = try? Data(contentsOf: url),
           (try? JSONDecoder().decode(Value.self, from: previous)) != nil {
            try previous.write(to: backupURL, options: .atomic)
        }
        try data.write(to: url, options: .atomic)
    }
}

struct LocalScanProperty: Identifiable, Codable {
    var id = UUID()
    var name: String = "Property"
    var projectId: String? = nil
    var rooms: [ScannedRoom] = []
    var roomUploads: [UUID: UUID] = [:]
    var editHistory: [LocalPlanRevision]? = nil
    var previousProjectIds: [String]? = nil
    var planRevision: Int? = nil
    var uploadedPlanRevision: Int? = nil
}

struct LocalPlanRevision: Codable {
    var rooms:[ScannedRoom]
    var roomUploads:[UUID:UUID]
}

struct ScanLibrary: Codable {
    var version = 1
    var properties: [LocalScanProperty]
    var selectedId: UUID

    init(projectId: String? = nil) {
        let property = LocalScanProperty(projectId: projectId)
        properties = [property]
        selectedId = property.id
    }

    var selectedIndex: Int? { properties.firstIndex { $0.id == selectedId } }

    func validated() throws -> ScanLibrary {
        guard version == 1, selectedIndex != nil,
              Set(properties.map(\.id)).count == properties.count else {
            throw CocoaError(.fileReadCorruptFile)
        }
        return self
    }
}
