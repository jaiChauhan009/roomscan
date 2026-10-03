import Foundation
import SwiftUI
import ARKit
import RoomPlan

struct Space: Identifiable, Equatable {
    var id: String
    var name: String
    var length: Double?
    var width: Double?
    var height: Double?
    var files: Int
}

struct UploadItem: Identifiable, Codable, Equatable {
    enum State: String, Codable { case queued, uploading, done, failed }
    var id = UUID()
    var pid: String
    var target: String      // "spaces/<sid>" or "captures/video" / "captures/lidar"
    var path: String        // file, relative to Documents
    var name: String        // name sent to the server
    var label: String
    var state: State = .queued
    var progress: Double = 0
    var error: String? = nil
}

@MainActor
final class AppModel: ObservableObject {
    static let defaultBase = "https://34-14-174-240.sslip.io"
    static let maxParallel = 3

    @Published var baseURL: String { didSet { UserDefaults.standard.set(baseURL, forKey: "baseURL") } }
    @Published var email: String { didSet { UserDefaults.standard.set(email, forKey: "email") } }
    @Published var projectId: String? { didSet { UserDefaults.standard.set(projectId, forKey: "projectId") } }
    @Published var spaces: [Space] = []
    @Published var captureFiles: [String: [String]] = [:]     // kind -> file names on the server
    @Published var uploads: [UploadItem] = [] { didSet { saveUploads() } }
    @Published var lidarRooms: [ScannedRoom] = []
    @Published var roomUpload: [UUID: UUID] = [:]             // scanned room -> upload item
    @Published var verifyResult: JSONObject? = nil
    @Published var jobId: String? { didSet { UserDefaults.standard.set(jobId, forKey: "jobId") } }
    @Published var busy: String? = nil
    @Published var error: String? = nil

    let lidarSupported: Bool = RoomCaptureSession.isSupported
    let uiTesting = ProcessInfo.processInfo.arguments.contains("-uitesting")
    private(set) lazy var arSession = ARSession()
    private var frameCounter = 0
    let sessionDir: String = "sessions/\(stamp())"
    /// One id per continuous ARSession (renewed when the app went to the background, since
    /// ARKit may restart its world frame then).
    private(set) var arSessionId = UUID().uuidString.lowercased()
    func appWentToBackground() { arSessionId = UUID().uuidString.lowercased() }

    var api: API { API(base: baseURL) }

    init() {
        let d = UserDefaults.standard
        baseURL = d.string(forKey: "baseURL") ?? AppModel.defaultBase
        email = d.string(forKey: "email") ?? ""
        projectId = d.string(forKey: "projectId")
        jobId = d.string(forKey: "jobId")
        if let data = try? Data(contentsOf: AppModel.uploadsFile),
           var items = try? JSONDecoder().decode([UploadItem].self, from: data) {
            for i in items.indices where items[i].state == .uploading { items[i].state = .queued; items[i].progress = 0 }
            uploads = items
        }
    }

    // MARK: project

    func bootstrap() async {
        if projectId == nil { await newProject() } else { await refresh() }
        pump()
    }

    func newProject() async {
        busy = "Creating project…"
        defer { busy = nil }
        do {
            let pid = try await api.createProject()
            projectId = pid
            spaces = []; captureFiles = [:]; lidarRooms = []; roomUpload = [:]
            verifyResult = nil; jobId = nil
            uploads.removeAll { $0.state == .done || $0.pid != pid }
            error = nil
        } catch { self.error = "Could not create a project: \(error.localizedDescription)" }
    }

    func refresh() async {
        guard let pid = projectId else { return }
        do {
            let p = try await api.project(pid)
            spaces = (p["spaces"] as? [JSONObject] ?? []).map { s in
                let sz = s["sizes"] as? JSONObject ?? [:]
                return Space(id: s["space_id"] as? String ?? "", name: s["name"] as? String ?? "",
                             length: sz["length"] as? Double, width: sz["width"] as? Double, height: sz["height"] as? Double,
                             files: (s["files"] as? [Any])?.count ?? 0)
            }
            var cf: [String: [String]] = [:]
            for (k, v) in p["captures"] as? JSONObject ?? [:] {
                if let c = v as? JSONObject { cf[k] = (c["files"] as? [JSONObject] ?? []).compactMap { $0["name"] as? String } }
            }
            captureFiles = cf
            error = nil
        } catch let e as APIError where e.status == 404 {
            await newProject()  // the server forgot the project (results expire): start a new one
        } catch { self.error = error.localizedDescription }
    }

    // MARK: rooms (photo spaces)

    func addSpace(_ name: String) async {
        guard let pid = projectId else { return }
        let n = name.trimmingCharacters(in: .whitespaces)
        guard !n.isEmpty else { return }
        do { _ = try await api.createSpace(pid, name: n); await refresh() } catch { self.error = error.localizedDescription }
    }

    func setSizes(_ sid: String, length: Double?, width: Double?, height: Double?) async {
        guard let pid = projectId else { return }
        let v: (Double?) -> Any = { $0.map { $0 as Any } ?? NSNull() }
        do {
            _ = try await api.patchSpace(pid, sid, body: ["sizes": ["length": v(length), "width": v(width), "height": v(height)]])
            await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func deleteSpace(_ sid: String) async {
        guard let pid = projectId else { return }
        do { try await api.deleteSpace(pid, sid); await refresh() } catch { self.error = error.localizedDescription }
    }

    func addPhoto(_ jpeg: Data, space: Space) {
        guard let pid = projectId else { return }
        let rel = "photos/\(space.id)/\(stamp())-\(UUID().uuidString.prefix(6)).jpg"
        do {
            try save(jpeg, rel)
            enqueue(UploadItem(pid: pid, target: "spaces/\(space.id)", path: rel, name: (rel as NSString).lastPathComponent,
                               label: "\(space.name): photo"))
        } catch { self.error = error.localizedDescription }
    }

    // MARK: whole-home video

    func addVideo(from url: URL) {
        guard let pid = projectId else { return }
        let ext = url.pathExtension.isEmpty ? "mov" : url.pathExtension.lowercased()
        let rel = "videos/walkthrough-\(stamp()).\(ext)"
        do {
            let dst = documentsDir.appendingPathComponent(rel)
            try FileManager.default.createDirectory(at: dst.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: url, to: dst)
            enqueue(UploadItem(pid: pid, target: "captures/video", path: rel, name: (rel as NSString).lastPathComponent,
                               label: "Video walkthrough"))
        } catch { self.error = "Could not keep the video: \(error.localizedDescription)" }
    }

    // MARK: whole-home LiDAR (RoomPlan)

    func nextFrameIndex() -> Int { frameCounter += 1; return frameCounter }
    var framesDir: String { sessionDir + "/frames" }

    func addLidarRoom(name: String, room: CapturedRoom, frames: [FrameRecord]) {
        let r = ScannedRoom(name: name, captured: room, sessionId: arSessionId, frames: frames)
        lidarRooms.append(r)
        guard let pid = projectId else { return }
        let n = lidarRooms.count
        let zipName = "\(stamp())-room\(n).roomscan.zip"
        let rel = "\(sessionDir)/\(zipName)"
        let dst = documentsDir.appendingPathComponent(rel)
        Task.detached(priority: .userInitiated) {
            do {
                try FileManager.default.createDirectory(at: dst.deletingLastPathComponent(), withIntermediateDirectories: true)
                try RoomPlanExport.roomZip(r, to: dst)
                await MainActor.run {
                    let item = UploadItem(pid: pid, target: "captures/lidar", path: rel, name: zipName, label: "LiDAR: \(name)")
                    self.roomUpload[r.id] = item.id
                    self.enqueue(item)
                }
            } catch {
                await MainActor.run { self.error = "Could not pack \(name): \(error.localizedDescription)" }
            }
        }
    }

    /// Whole-home zip for Share (AirDrop / Files), merged with StructureBuilder when possible.
    func homeZip() async -> URL? {
        guard !lidarRooms.isEmpty else { return nil }
        busy = "Packing the scan…"
        defer { busy = nil }
        let rooms = lidarRooms
        let url = documentsDir.appendingPathComponent("\(sessionDir)/\(stamp()).roomscan.zip")
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            _ = try await RoomPlanExport.homeZip(rooms, to: url)
            return url
        } catch {
            self.error = "Could not pack the scan: \(error.localizedDescription)"
            return nil
        }
    }

    // MARK: uploads (up to 3 in parallel, retry on failure, resumed at next launch)

    static var uploadsFile: URL { documentsDir.appendingPathComponent("uploads.json") }
    private func saveUploads() {
        if let d = try? JSONEncoder().encode(uploads) { try? d.write(to: AppModel.uploadsFile) }
    }

    private func save(_ data: Data, _ rel: String) throws {
        let u = documentsDir.appendingPathComponent(rel)
        try FileManager.default.createDirectory(at: u.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: u)
    }

    func enqueue(_ item: UploadItem) {
        uploads.append(item)
        verifyResult = nil
        pump()
    }

    func retry(_ id: UUID) {
        guard let i = uploads.firstIndex(where: { $0.id == id }) else { return }
        uploads[i].state = .queued; uploads[i].error = nil; uploads[i].progress = 0
        pump()
    }

    func removeUpload(_ id: UUID) { uploads.removeAll { $0.id == id } }

    var pendingUploads: Int { uploads.filter { $0.state == .queued || $0.state == .uploading }.count }
    var failedUploads: Int { uploads.filter { $0.state == .failed }.count }

    func pump() {
        guard !uiTesting else { return }
        var running = uploads.filter { $0.state == .uploading }.count
        for i in uploads.indices where running < AppModel.maxParallel && uploads[i].state == .queued {
            uploads[i].state = .uploading
            running += 1
            let item = uploads[i]
            Task { await self.perform(item) }
        }
    }

    private func update(_ id: UUID, _ f: (inout UploadItem) -> Void) {
        if let i = uploads.firstIndex(where: { $0.id == id }) { f(&uploads[i]) }
    }

    private func perform(_ item: UploadItem) async {
        let api = self.api
        let file = documentsDir.appendingPathComponent(item.path)
        do {
            if item.target.hasPrefix("captures/") {
                try await api.setCapture(item.pid, kind: String(item.target.dropFirst("captures/".count)))
            }
            let sha = try await Task.detached { try sha256Hex(of: file) }.value
            var last = Date.distantPast
            _ = try await api.upload(pid: item.pid, target: item.target, file: file, name: item.name, sha256: sha) { p in
                let now = Date()
                guard now.timeIntervalSince(last) > 0.2 || p >= 1 else { return }
                last = now
                Task { @MainActor in self.update(item.id) { $0.progress = p } }
            }
            update(item.id) { $0.state = .done; $0.progress = 1 }
            await refresh()
        } catch {
            update(item.id) { $0.state = .failed; $0.error = error.localizedDescription }
        }
        pump()
    }

    // MARK: verify / run

    func verify() async {
        guard let pid = projectId else { return }
        busy = "Checking captures…"
        defer { busy = nil }
        do { verifyResult = try await api.verify(pid); error = nil } catch { self.error = error.localizedDescription }
    }

    var verifyHasRetake: Bool {
        guard let v = verifyResult else { return false }
        let sp = (v["spaces"] as? [JSONObject] ?? []).contains { ($0["status"] as? String) == "retake" }
        let cp = (v["captures"] as? [JSONObject] ?? []).contains { ($0["status"] as? String) == "retake" }
        let pf = (v["project_findings"] as? [JSONObject] ?? []).contains { ($0["level"] as? String) == "retake" }
        return sp || cp || pf
    }

    /// Start computing; returns the job id.
    func run(force: Bool) async -> String? {
        guard let pid = projectId else { return nil }
        busy = "Starting…"
        defer { busy = nil }
        do {
            let r = try await api.run(pid, force: force, email: email.trimmingCharacters(in: .whitespaces))
            jobId = r["job_id"] as? String
            error = nil
            return jobId
        } catch { self.error = error.localizedDescription; return nil }
    }
}
