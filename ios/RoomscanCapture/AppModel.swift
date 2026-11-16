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
    @Published private(set) var library = ScanLibrary()
    @Published var packingRooms = 0
    @Published var libraryError: String? = nil
    private var libraryReadable = true
    private var uploadsReadable = true
    @Published private(set) var connectingProject = false
    var localStorageReady: Bool { libraryReadable }
    static var libraryFile: URL { documentsDir.appendingPathComponent("scan-library.json") }
    private var libraryStore: AtomicJSONStore<ScanLibrary> { AtomicJSONStore(url: Self.libraryFile) }
    var lidarRooms: [ScannedRoom] {
        get { library.selectedIndex.map { library.properties[$0].rooms } ?? [] }
        set {
            guard let i = library.selectedIndex else { return }
            library.properties[i].rooms = newValue
            persistLibrary()
        }
    }
    var roomUpload: [UUID: UUID] {
        get { library.selectedIndex.map { library.properties[$0].roomUploads } ?? [:] }
        set {
            guard let i = library.selectedIndex else { return }
            library.properties[i].roomUploads = newValue
            persistLibrary()
        }
    }
    var hasUnuploadedEdits: Bool {
        guard let i=library.selectedIndex else { return false }
        return (library.properties[i].planRevision ?? 0) != (library.properties[i].uploadedPlanRevision ?? 0)
    }
    var canUndoPlanEdit: Bool { library.selectedIndex.map{!(library.properties[$0].editHistory ?? []).isEmpty} ?? false }
    @Published var verifyResult: JSONObject? = nil
    @Published var jobId: String? { didSet { UserDefaults.standard.set(jobId, forKey: "jobId") } }
    @Published var busy: String? = nil
    @Published var error: String? = nil

    let lidarSupported: Bool = RoomCaptureSession.isSupported
    let uiTesting = ProcessInfo.processInfo.arguments.contains("-uitesting")
    private(set) lazy var arSession = ARSession()
    private var frameCounter = 0
    let sessionDir: String = "sessions/\(stamp())-\(UUID().uuidString.lowercased())"
    /// One id per continuous ARSession (renewed when the app went to the background, since
    /// ARKit may restart its world frame then).
    private(set) var arSessionId = UUID().uuidString.lowercased()
    func appWentToBackground() { arSession.pause(); arSessionId = UUID().uuidString.lowercased() }

    var api: API { API(base: baseURL) }

    init() {
        let d = UserDefaults.standard
        baseURL = d.string(forKey: "baseURL") ?? AppModel.defaultBase
        email = d.string(forKey: "email") ?? ""
        projectId = d.string(forKey: "projectId")
        jobId = d.string(forKey: "jobId")
        do {
            if var items = try AtomicJSONStore<[UploadItem]>(url: Self.uploadsFile).load() {
                for i in items.indices where items[i].state == .uploading { items[i].state = .queued; items[i].progress = 0 }
                uploads = items
            }
        } catch {
            uploadsReadable = false
            self.error = "The upload queue could not be recovered. Its files have been preserved; saved room geometry remains separate."
        }
        do {
            library = try (libraryStore.load() ?? ScanLibrary(projectId: projectId)).validated()
            projectId = library.selectedIndex.flatMap { library.properties[$0].projectId }
        } catch {
            libraryReadable = false
            self.error = "Could not recover the local scan library. Its files have been preserved: \(error.localizedDescription)"
        }
    }

    // MARK: project

    func bootstrap() async {
        guard libraryReadable else { return }
        recoverPendingRoomExports()
        if projectId == nil { await connectProject() } else { await refresh() }
        queueSavedRooms()
        pump()
    }

    func newProject() async {
        guard libraryReadable, packingRooms == 0, !connectingProject else { return }
        let property = LocalScanProperty(name: "Property \(library.properties.count + 1)")
        library.properties.append(property)
        library.selectedId = property.id
        projectId = nil
        spaces = []; captureFiles = [:]; verifyResult = nil; jobId = nil
        arSession.pause(); arSessionId = UUID().uuidString.lowercased()
        persistLibrary()
        await connectProject()
    }

    func selectProperty(_ id: UUID) async {
        guard libraryReadable, packingRooms == 0, !connectingProject,
              library.properties.contains(where: { $0.id == id }) else { return }
        library.selectedId = id
        projectId = library.selectedIndex.flatMap { library.properties[$0].projectId }
        spaces = []; captureFiles = [:]; verifyResult = nil; jobId = nil
        arSession.pause(); arSessionId = UUID().uuidString.lowercased()
        persistLibrary()
        recoverPendingRoomExports()
        if projectId != nil { await refresh() }
        queueSavedRooms()
    }

    private func persistLibrary() {
        guard libraryReadable else { return }
        do { try libraryStore.save(library); libraryError = nil }
        catch {
            libraryError = "Could not save the local scan library: \(error.localizedDescription). Keep the app open and free storage."
            self.error = libraryError
        }
    }

    func connectProject() async {
        guard projectId == nil, libraryReadable, !connectingProject else { return }
        connectingProject = true
        // Connection runs independently of local capture; an offline server must not
        // cover the scan button with a blocking progress overlay.
        defer { connectingProject = false }
        do {
            let pid = try await api.createProject()
            projectId = pid
            if let i = library.selectedIndex { library.properties[i].projectId = pid }
            error = nil
            persistLibrary()
            queueSavedRooms()
        } catch { self.error = "Scanning and sharing are available offline. Could not connect this property: \(error.localizedDescription)" }
    }

    func refresh() async {
        guard let pid = projectId else { return }
        do {
            let p = try await api.project(pid)
            guard projectId == pid else { return }
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
            guard projectId == pid else { return }
            // Server expiry must never discard locally saved rooms or switch properties.
            projectId = nil
            if let i = library.selectedIndex {
                library.properties[i].projectId = nil
                library.properties[i].roomUploads = [:]
            }
            persistLibrary()
            await connectProject()
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

    @discardableResult
    func addLidarRoom(name: String, room: CapturedRoom, frames: [FrameRecord], sessionId: String? = nil) -> Bool {
        guard libraryReadable, let index = library.selectedIndex else { return false }
        let r = ScannedRoom(name: name, captured: room, sessionId: sessionId ?? arSessionId, frames: frames)
        var next = library
        next.properties[index].rooms.append(r)
        do {
            // Do not dismiss the capture review until its geometry is durably saved.
            try libraryStore.save(next)
            library = next
            libraryError = nil
        } catch {
            libraryError = "The room could not be saved. Keep this review open and free storage, then try Save again. \(error.localizedDescription)"
            return false
        }
        packRoom(r)
        return true
    }

    private func recoverPendingRoomExports() {
        guard packingRooms == 0 else { return }
        for room in lidarRooms where room.zipPath == nil { packRoom(room) }
    }

    func setWallReference(roomId: UUID, reference: WallReferenceMeasurement) {
        guard reference.isValid, let index = lidarRooms.firstIndex(where: { $0.id == roomId }),
              lidarRooms[index].plan.walls.contains(where: { $0.id == reference.wallId }) else { return }
        var rooms = lidarRooms
        var references = rooms[index].referenceMeasurements ?? []
        references.removeAll { $0.wallId == reference.wallId }
        references.append(reference)
        rooms[index].referenceMeasurements = references
        do { try commitPlanRooms(rooms) }
        catch { self.error = "The reference measurement could not be saved: \(error.localizedDescription)" }
    }

    private func commitPlanRooms(_ rooms:[ScannedRoom]) throws {
        guard libraryReadable, let i=library.selectedIndex, packingRooms==0 else {
            throw PlanEditError.invalid("Wait for the room export to finish before editing the property.")
        }
        var next=library
        var history=next.properties[i].editHistory ?? []
        history.append(LocalPlanRevision(rooms:next.properties[i].rooms,roomUploads:next.properties[i].roomUploads))
        next.properties[i].editHistory=Array(history.suffix(10))
        next.properties[i].rooms=rooms
        next.properties[i].planRevision=(next.properties[i].planRevision ?? 0)+1
        try libraryStore.save(next)
        library=next;jobId=nil;verifyResult=nil;libraryError=nil
    }

    func editRoom(_ id:UUID, points:[PlanPoint], height:Double?) throws {
        guard let i=lidarRooms.firstIndex(where:{$0.id==id}) else { return }
        var rooms=lidarRooms
        let localPoints:[PlanPoint]
        if let placement=rooms[i].placement {
            _ = try placement.validated()
            let angle=placement.degrees * .pi / 180,c=cos(angle),s=sin(angle)
            localPoints=points.map { point in
                let x=point.x-placement.x,z=point.z-placement.z
                return PlanPoint(x:c*x+s*z,z:-s*x+c*z)
            }
        } else { localPoints=points }
        rooms[i].editedRoom=try rooms[i].basePlan.replacingOutline(localPoints,height:height)
        try commitPlanRooms(rooms)
    }

    func splitRoom(_ id:UUID, alongX:Bool, at value:Double) throws {
        guard let i=lidarRooms.firstIndex(where:{$0.id==id}) else { return }
        let original=lidarRooms[i], base=original.basePlan
        guard original.frames.allSatisfy({ $0.transform.count == 16 && $0.transform.allSatisfy(\.isFinite) }) else {
            throw PlanEditError.invalid("This room has a damaged camera pose. Keep the original scan and repair its capture data first.")
        }
        for opening in base.doors+base.windows+base.openings {
            let ends=PlanRoom.ends(opening)
            let a=Double(alongX ? ends.0.x : ends.0.y)-value
            let b=Double(alongX ? ends.1.x : ends.1.y)-value
            if a*b <= 0 { throw PlanEditError.invalid("Move the split line away from doors and windows.") }
        }
        let polygons=try PlanPolygon.split(base.editableOutline,alongX:alongX,at:value)
        var children=try polygons.enumerated().map { index, points -> ScannedRoom in
            var room=original;room.id=UUID();room.name=original.name+(index==0 ? " A" : " B")
            room.editedRoom=try base.replacingOutline(points,partition:true)
            room.originalSources=original.sources;room.referenceMeasurements=[];room.zipPath=nil;room.frames=[]
            return room
        }
        for frame in original.frames {
            let point=PlanPoint(x:Double(frame.transform[12]),z:Double(frame.transform[14]))
            let side=PlanPolygon.contains(point,polygons[0]) ? 0 : 1
            children[side].frames.append(frame)
        }
        var rooms=lidarRooms;rooms.replaceSubrange(i...i,with:children)
        try commitPlanRooms(rooms)
    }

    func mergeRooms(_ first:UUID,_ second:UUID) throws {
        guard first != second,let a=lidarRooms.first(where:{$0.id==first}),let b=lidarRooms.first(where:{$0.id==second}) else { return }
        guard a.worldFrameId==b.worldFrameId, a.plan.story==b.plan.story else {
            throw PlanEditError.invalid("Align these scans into one property layout before merging rooms.")
        }
        func floorHeight(_ room:PlanRoom)->Float? {
            guard let floor=room.floorWorld,!floor.isEmpty else { return nil }
            return floor.map(\.y).reduce(0,+)/Float(floor.count)
        }
        if let fa=floorHeight(a.plan),let fb=floorHeight(b.plan),abs(fa-fb)>0.15 {
            throw PlanEditError.invalid("The room floors differ by more than 15 cm. Align the scans before merging.")
        }
        let outline=try PlanPolygon.merge(a.plan.editableOutline,b.plan.editableOutline)
        var combined=a.plan
        combined.walls += b.plan.walls;combined.doors += b.plan.doors;combined.windows += b.plan.windows
        combined.openings += b.plan.openings;combined.objects += b.plan.objects
        func touchesExterior(_ opening:PlanSurface)->Bool {
            let c=opening.transform.position
            let p=PlanPoint(x:Double(c.x),z:Double(c.z))
            return outline.indices.contains { PlanPolygon.onSegment(p,outline[$0],outline[($0+1)%outline.count]) }
        }
        combined.doors=combined.doors.filter(touchesExterior)
        combined.windows=combined.windows.filter(touchesExterior)
        combined.openings=combined.openings.filter(touchesExterior)
        combined=try combined.replacingOutline(outline)
        var room=a;room.id=UUID();room.name=a.name+" + "+b.name;room.editedRoom=combined;room.placement=nil
        room.sessionId=a.worldFrameId;room.alignmentGroup=a.alignmentGroup;room.zipPath=nil
        room.referenceMeasurements=[];room.originalSources=a.sources+b.sources
        room.frames=try (a.exportedFrames()+b.exportedFrames()).sorted{$0.t<$1.t}.map { frame in
            var copy=frame
            let stem=UUID().uuidString.lowercased()
            copy.file="frames/\(stem).jpg"
            if var depth=copy.depth {
                let depthExt=(depth.file as NSString).pathExtension
                depth.file="frames/\(stem).depth"+(depthExt.isEmpty ? ".bin" : ".\(depthExt)")
                if let confidence=depth.confidenceFile {
                    let ext=(confidence as NSString).pathExtension
                    depth.confidenceFile="frames/\(stem).confidence"+(ext.isEmpty ? ".bin" : ".\(ext)")
                }
                copy.depth=depth
            }
            return copy
        }
        var rooms=lidarRooms.filter{$0.id != first && $0.id != second};rooms.append(room)
        try commitPlanRooms(rooms)
    }

    func placeRooms(_ placements:[UUID:PlanPlacement]) throws {
        var rooms=lidarRooms
        let group="manual-\(library.selectedId.uuidString.lowercased())"
        for i in rooms.indices {
            guard let placement=placements[rooms[i].id] else { throw PlanEditError.invalid("Confirm a position for every room in this layout.") }
            rooms[i].placement=try placement.validated();rooms[i].alignmentGroup=group
        }
        try commitPlanRooms(rooms)
    }

    func undoPlanEdit() {
        guard let i=library.selectedIndex,var history=library.properties[i].editHistory,
              let revision=history.popLast(),packingRooms==0 else { return }
        var next=library
        next.properties[i].rooms=revision.rooms;next.properties[i].roomUploads=revision.roomUploads
        next.properties[i].editHistory=history;next.properties[i].planRevision=(next.properties[i].planRevision ?? 0)+1
        do { try libraryStore.save(next);library=next;jobId=nil;verifyResult=nil }
        catch { self.error="Could not restore the previous plan: \(error.localizedDescription)" }
    }

    @discardableResult
    func uploadCurrentPlan() async -> Bool {
        guard !lidarRooms.isEmpty,packingRooms==0 else { return false }
        let propertyID=library.selectedId,revision=library.selectedIndex.map{library.properties[$0].planRevision ?? 0} ?? 0
        guard let file=await homeZip() else { return false }
        busy="Uploading current property…"
        defer { busy=nil }
        do {
            // A new server revision keeps the old raw uploads and reports intact.
            let pid=try await api.createProject()
            try await api.setCapture(pid,kind:"lidar")
            let hash=try await Task.detached { try sha256Hex(of:file) }.value
            _ = try await api.upload(pid:pid,target:"captures/lidar",file:file,name:"property.roomscan.zip",sha256:hash){_ in}
            guard propertyID==library.selectedId,let i=library.selectedIndex else { return false }
            var next=library
            if let previous=next.properties[i].projectId {
                next.properties[i].previousProjectIds=(next.properties[i].previousProjectIds ?? [])+[previous]
            }
            next.properties[i].projectId=pid;next.properties[i].uploadedPlanRevision=revision
            try libraryStore.save(next)
            library=next;projectId=pid;jobId=nil;verifyResult=nil;spaces=[];error=nil
            await refresh()
            return true
        } catch { self.error="The current plan is saved locally, but upload failed: \(error.localizedDescription)";return false }
    }

    private func packRoom(_ r: ScannedRoom) {
        let propertyID = library.selectedId
        let zipName = "\(r.id.uuidString.lowercased()).roomscan.zip"
        let rel = "\(sessionDir)/\(zipName)"
        let dst = documentsDir.appendingPathComponent(rel)
        packingRooms += 1
        Task.detached(priority: .userInitiated) {
            do {
                try FileManager.default.createDirectory(at: dst.deletingLastPathComponent(), withIntermediateDirectories: true)
                try RoomPlanExport.roomZip(r, to: dst)
                await MainActor.run {
                    if let p = self.library.properties.firstIndex(where: { $0.id == propertyID }),
                       let i = self.library.properties[p].rooms.firstIndex(where: { $0.id == r.id }) {
                        self.library.properties[p].rooms[i].zipPath = rel
                        self.persistLibrary()
                    }
                    self.packingRooms -= 1
                    self.queueSavedRooms()
                }
            } catch {
                await MainActor.run {
                    self.packingRooms -= 1
                    self.error = "Room geometry is kept locally, but could not pack \(r.name): \(error.localizedDescription)"
                }
            }
        }
    }

    private func queueSavedRooms() {
        guard uploadsReadable, let pid = projectId else { return }
        for room in lidarRooms where roomUpload[room.id] == nil && !room.isModified {
            // The server accepts five capture files. Further rooms remain in the local
            // library and are included in the whole-property share export.
            guard roomUpload.count < 5 else { break }
            guard let path = room.zipPath else { continue }
            guard FileManager.default.fileExists(atPath: documentsDir.appendingPathComponent(path).path) else {
                self.error = "The saved ZIP for \(room.name) is missing. Its geometry remains available for sharing."
                continue
            }
            let item = UploadItem(pid: pid, target: "captures/lidar", path: path,
                                  name: (path as NSString).lastPathComponent, label: "LiDAR: \(room.name)")
            enqueue(item)
            roomUpload[room.id] = item.id
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
        guard uploadsReadable else { return }
        do { try AtomicJSONStore<[UploadItem]>(url: Self.uploadsFile).save(uploads) }
        catch { self.error = "Could not save the upload queue: \(error.localizedDescription)" }
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
        if hasUnuploadedEdits {
            let uploaded=await uploadCurrentPlan()
            guard uploaded else { return }
        }
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
        if hasUnuploadedEdits {
            let uploaded=await uploadCurrentPlan()
            guard uploaded else { return nil }
        }
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
