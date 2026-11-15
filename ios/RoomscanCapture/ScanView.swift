import SwiftUI
import RoomPlan
import ARKit
import CoreImage
import UIKit
import AVFoundation

/// Saves keyframes from the RoomPlan ARSession: every ~0.7 s if the camera moved > 0.25 m
/// or turned > 15° since the last saved frame, at most 120 per room. JPEG encoding runs on a
/// background queue; the ARSession delegate (owned by RoomPlan) is never touched.
final class KeyframeRecorder {
    static let interval: TimeInterval = 0.7
    static let maxFrames = 120
    static let minMove: Float = 0.25
    static let minTurnDeg: Float = 15

    private weak var session: ARSession?
    private var timer: Timer?
    private var lastPose: simd_float4x4?
    private var startT: TimeInterval?
    private var count = 0
    private var lastSampleTime: TimeInterval?
    private let queue = DispatchQueue(label: "keyframes", qos: .utility)
    private let lock = NSLock()
    private var records: [FrameRecord] = []
    // Never retain a queue of AR camera buffers while JPEG encoding falls behind.
    private let encodeSlot = DispatchSemaphore(value: 1)
    private let ctx = CIContext(options: [.cacheIntermediates: false])
    private let framesDir: String
    private let nextIndex: () -> Int

    init(framesDir: String, nextIndex: @escaping () -> Int) {
        self.framesDir = framesDir
        self.nextIndex = nextIndex
        try? FileManager.default.createDirectory(at: documentsDir.appendingPathComponent(framesDir), withIntermediateDirectories: true)
    }

    var saved: Int { lock.lock(); defer { lock.unlock() }; return records.count }

    func start(_ session: ARSession) {
        self.session = session
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: KeyframeRecorder.interval, repeats: true) { [weak self] _ in self?.tick() }
    }

    func stop() { timer?.invalidate(); timer = nil }

    /// Waits for pending encodes, returns the frames in order.
    func finish() async -> [FrameRecord] {
        stop()
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in queue.async { c.resume() } }
        lock.lock(); defer { lock.unlock() }
        return records.sorted { $0.file < $1.file }
    }

    func discard() {
        stop()
        queue.async { [self] in
            lock.lock(); let rs = records; records = []; lock.unlock()
            for r in rs {
                let paths = [r.path, r.depth?.path, r.depth?.confidencePath].compactMap { $0 }
                for path in paths { try? FileManager.default.removeItem(at: documentsDir.appendingPathComponent(path)) }
            }
        }
    }

    private func tick() {
        guard count < KeyframeRecorder.maxFrames, let frame = session?.currentFrame else { return }
        guard case .normal = frame.camera.trackingState else { return }
        let policy = ScanHealth(tracking: .normal, thermal: ProcessInfo.processInfo.thermalState)
        guard policy.canRecordPhoto else { return }
        let samplingInterval = policy.photoInterval
        if let lastSampleTime, frame.timestamp - lastSampleTime < samplingInterval { return }
        let pose = frame.camera.transform
        if let last = lastPose {
            let moved = simd_distance(pose.position, last.position)
            let r0 = simd_float3x3(SIMD3(last.columns.0.x, last.columns.0.y, last.columns.0.z),
                                   SIMD3(last.columns.1.x, last.columns.1.y, last.columns.1.z),
                                   SIMD3(last.columns.2.x, last.columns.2.y, last.columns.2.z))
            let r1 = simd_float3x3(SIMD3(pose.columns.0.x, pose.columns.0.y, pose.columns.0.z),
                                   SIMD3(pose.columns.1.x, pose.columns.1.y, pose.columns.1.z),
                                   SIMD3(pose.columns.2.x, pose.columns.2.y, pose.columns.2.z))
            let rel = r0.transpose * r1
            let tr = rel.columns.0.x + rel.columns.1.y + rel.columns.2.z
            let angle = acos(max(-1, min(1, (tr - 1) / 2))) * 180 / .pi
            if moved < KeyframeRecorder.minMove && angle < KeyframeRecorder.minTurnDeg { return }
        }
        guard encodeSlot.wait(timeout: .now()) == .success else { return }
        lastSampleTime = frame.timestamp
        lastPose = pose
        count += 1
        if startT == nil { startT = frame.timestamp }
        let t = frame.timestamp - (startT ?? frame.timestamp)
        let pixels = frame.capturedImage
        let depth = frame.sceneDepth
        let K = frame.camera.intrinsics
        let idx = nextIndex()
        let name = String(format: "%06d.jpg", idx)
        let rel = "\(framesDir)/\(name)"
        queue.async { [self] in
            defer { encodeSlot.signal() }
            autoreleasepool {
                let w = CVPixelBufferGetWidth(pixels), h = CVPixelBufferGetHeight(pixels)
                let s = min(1, 1920 / CGFloat(max(w, h)))
                var img = CIImage(cvPixelBuffer: pixels)  // sensor orientation (landscape), not rotated
                if s < 1 { img = img.transformed(by: CGAffineTransform(scaleX: s, y: s)) }
                guard let cg = ctx.createCGImage(img, from: img.extent),
                      let jpeg = UIImage(cgImage: cg).jpegData(compressionQuality: 0.8) else { return }
                do { try jpeg.write(to: documentsDir.appendingPathComponent(rel)) } catch { return }
                var k = K
                let sx = Float(cg.width) / Float(w), sy = Float(cg.height) / Float(h)
                for column in 0..<3 { k[column][0] *= sx; k[column][1] *= sy }
                let attachment = depth.flatMap { DepthSnapshot.copy($0, cameraIntrinsics: K, imageWidth: w, imageHeight: h) }
                    .flatMap { try? $0.write(directory: framesDir, stem: String(format: "%06d", idx)) }
                let rec = FrameRecord(file: "frames/\(name)", path: rel, t: t, transform: pose.columnMajor,
                                      intrinsics: k.columnMajor, width: cg.width, height: cg.height, depth: attachment)
                lock.lock(); records.append(rec); lock.unlock()
            }
        }
    }
}

/// Owns the RoomCaptureView, the live numbers and the end of a room scan.
final class ScanController: NSObject, ObservableObject, RoomCaptureViewDelegate, RoomCaptureSessionDelegate {
    @Published var stats = RoomStats()
    @Published var frames = 0
    @Published var livePlan = PlanRoom()
    @Published var processing = false
    @Published var message: String? = nil
    @Published var result: (CapturedRoom, [FrameRecord])? = nil
    @Published var closed = false
    @Published var health = ScanHealth()
    @Published var coaching: String? = nil
    private var healthTimer: Timer?
    var canFinish: Bool { health.canFinish && stats.walls >= 3 }

    private func updateHealth(_ arSession: ARSession) {
        var value = ScanHealth()
        value.thermal = ProcessInfo.processInfo.thermalState
        if let frame = arSession.currentFrame {
            switch frame.camera.trackingState {
            case .normal: value.tracking = .normal
            case .notAvailable: value.tracking = .unavailable
            case .limited(let reason):
                switch reason {
                case .initializing: value.tracking = .initializing
                case .excessiveMotion: value.tracking = .movingTooFast
                case .insufficientFeatures: value.tracking = .insufficientFeatures
                case .relocalizing: value.tracking = .relocalizing
                @unknown default: value.tracking = .unavailable
                }
            }
            value.ambientIntensity = frame.lightEstimate.map { Double($0.ambientIntensity) }
            value.depthAvailable = frame.sceneDepth != nil
        }
        if health != value { health = value }
    }

    private func stopMonitoring() { healthTimer?.invalidate(); healthTimer = nil }

    func leave() {
        stopMonitoring()
        if result == nil && !cancelled { cancel() }
    }

    func accept() { stopMonitoring(); closed = true }

    private(set) var view: RoomCaptureView?
    private var recorder: KeyframeRecorder?
    private var lastUpdate = Date.distantPast
    private var cancelled = false
    private var building = false

    override init() { super.init() }
    required init?(coder: NSCoder) { super.init() }
    func encode(with coder: NSCoder) {}

    func makeView(arSession: ARSession, recorder: KeyframeRecorder) -> RoomCaptureView {
        let v = RoomCaptureView(frame: .zero, arSession: arSession)
        v.delegate = self
        v.captureSession.delegate = self
        view = v
        self.recorder = recorder
        v.captureSession.run(configuration: RoomCaptureSession.Configuration())
        recorder.start(arSession)
        healthTimer?.invalidate()
        healthTimer = Timer.scheduledTimer(withTimeInterval: 0.7, repeats: true) { [weak self, weak arSession] _ in
            guard let self, let arSession else { return }
            self.updateHealth(arSession)
        }
        return v
    }

    func done() {
        guard let v = view, !processing, canFinish else { return }
        processing = true
        stopMonitoring()
        recorder?.stop()
        v.captureSession.stop(pauseARSession: false)
    }

    func cancel() {
        guard !cancelled else { return }
        cancelled = true
        stopMonitoring()
        recorder?.discard()
        view?.captureSession.stop(pauseARSession: false)
        closed = true
    }

    // live numbers, ~4 Hz
    func captureSession(_ session: RoomCaptureSession, didUpdate room: CapturedRoom) {
        let now = Date()
        guard now.timeIntervalSince(lastUpdate) >= 0.25 else { return }
        lastUpdate = now
        let plan = PlanRoom(room)
        let s = RoomStats(plan)
        let n = recorder?.saved ?? 0
        DispatchQueue.main.async {
            guard !self.cancelled, !self.processing else { return }
            self.stats = s; self.frames = n; self.livePlan = plan
        }
    }

    func captureSession(_ session: RoomCaptureSession, didProvide instruction: RoomCaptureSession.Instruction) {
        let guidance: String?
        switch instruction {
        case .normal: guidance = nil
        case .moveCloseToWall: guidance = "Move closer to the wall."
        case .moveAwayFromWall: guidance = "Step back so the camera can see more of the wall."
        case .turnOnLight: guidance = "Turn on the lights for better tracking."
        case .slowDown: guidance = "Move slowly and keep the phone steady."
        case .lowTexture: guidance = "Show a textured surface, corner, or piece of furniture."
        @unknown default: guidance = "Move slowly around the room and show every wall."
        }
        DispatchQueue.main.async {
            guard !self.cancelled, !self.processing else { return }
            self.coaching = guidance
        }
    }

    // Either path may report the end of the scan, depending on who RoomPlan tells first.
    func captureSession(_ session: RoomCaptureSession, didEndWith data: CapturedRoomData, error: Error?) {
        finish(data, error)
    }

    func captureView(shouldPresent roomDataForProcessing: CapturedRoomData, error: Error?) -> Bool {
        finish(roomDataForProcessing, error)
        return false
    }

    func captureView(didPresent processedResult: CapturedRoom, error: Error?) {}

    private func finish(_ data: CapturedRoomData, _ error: Error?) {
        DispatchQueue.main.async {
            guard !self.cancelled, !self.building else { return }
            self.building = true
            self.stopMonitoring()
            self.recorder?.stop()
            self.processing = true
            if let error { self.message = "RoomPlan reported: \(error.localizedDescription)" }
            Task { @MainActor in
                do {
                    let room = try await RoomBuilder(options: [.beautifyObjects]).capturedRoom(from: data)
                    let frames = await self.recorder?.finish() ?? []
                    guard !self.cancelled else { return }
                    guard !room.walls.isEmpty else {
                        self.processing = false
                        self.message = "No walls were captured. Cancel and scan again with more light."
                        return
                    }
                    self.stats = RoomStats(PlanRoom(room))
                    self.processing = false
                    self.result = (room, frames)
                } catch {
                    self.processing = false
                    self.building = false
                    self.message = "Could not build the room: \(error.localizedDescription). Cancel and scan it again."
                }
            }
        }
    }
}

struct RoomCaptureRep: UIViewRepresentable {
    let controller: ScanController
    let arSession: ARSession
    let recorder: KeyframeRecorder
    func makeUIView(context: Context) -> RoomCaptureView { controller.makeView(arSession: arSession, recorder: recorder) }
    func updateUIView(_ uiView: RoomCaptureView, context: Context) {}
}

struct ScanScreen: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    let roomName: String
    @StateObject private var ctl = ScanController()
    @State private var recorder: KeyframeRecorder? = nil
    @State private var startupMessage: String? = nil
    @State private var captureSessionID: String? = nil
    @State private var reviewing = false

    var body: some View {
        ZStack {
            if let recorder {
                RoomCaptureRep(controller: ctl, arSession: model.arSession, recorder: recorder).ignoresSafeArea()
            } else {
                Color.black.ignoresSafeArea()
            }
            VStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(roomName).font(.headline)
                    Text(ctl.stats.counts).font(.subheadline)
                    Text(String(format: "≈ %.1f m² · %.2f × %.2f m · ceiling %.2f m",
                                ctl.stats.area, ctl.stats.width, ctl.stats.length, ctl.stats.height)).font(.subheadline.monospacedDigit())
                    Text("\(ctl.frames) photos kept for damage detection").font(.caption)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                if let guidance = startupMessage ?? ctl.health.guidance ?? ctl.coaching {
                    Text(guidance).font(.callout).padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
                        .accessibilityIdentifier("scanGuidance")
                }
                if let m = ctl.message {
                    Text(m).font(.callout).padding(8).background(.red.opacity(0.85), in: RoundedRectangle(cornerRadius: 8)).foregroundStyle(.white)
                }
                Spacer()
                if !ctl.livePlan.walls.isEmpty {
                    PlanPreview(room: ctl.livePlan, interactive: false)
                        .frame(height: 180)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
                HStack {
                    Button("Cancel", role: .cancel) { ctl.cancel() }
                        .buttonStyle(.bordered).tint(.white)
                    Spacer()
                    Button { ctl.done() } label: { Text("Done").bold().padding(.horizontal, 20) }
                        .buttonStyle(.borderedProminent).disabled(ctl.processing || !ctl.canFinish)
                }
            }
            .padding()
            if ctl.processing {
                VStack(spacing: 12) { ProgressView(); Text("Building the room…") }
                    .padding(24).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            }
        }
        .task {
            guard RoomCaptureSession.isSupported else {
                startupMessage = "Room scanning needs a LiDAR-equipped iPhone Pro or iPad Pro."
                return
            }
            let authorized: Bool
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized: authorized = true
            case .notDetermined: authorized = await AVCaptureDevice.requestAccess(for: .video)
            default: authorized = false
            }
            guard !Task.isCancelled, !ctl.closed else { return }
            guard authorized else {
                startupMessage = "Allow Camera access in Settings to scan this room."
                return
            }
            if recorder == nil {
                captureSessionID = model.arSessionId
                recorder = KeyframeRecorder(framesDir: model.framesDir, nextIndex: { [weak model] in
                    MainActor.assumeIsolated { model?.nextFrameIndex() ?? 0 }
                })
            }
            UIApplication.shared.isIdleTimerDisabled = true
        }
        .onDisappear {
            ctl.leave()
            recorder?.stop()
            UIApplication.shared.isIdleTimerDisabled = false
        }
        .onChange(of: phase) { _, value in
            if value == .background && !ctl.closed {
                model.error = "The room scan was interrupted. Start a new scan after returning to the app."
                ctl.cancel()
            }
        }
        .onChange(of: ctl.closed) { _, c in if c { dismiss() } }
        .onChange(of: ctl.result != nil) { _, has in reviewing = has }
        .sheet(isPresented: $reviewing, onDismiss: {
            if !ctl.closed { ctl.cancel() }
        }) {
            if let (room, frames) = ctl.result {
                ScanReview(room: PlanRoom(room), frameCount: frames.count,
                           depthCount: frames.filter { $0.depth != nil }.count,
                           save: {
                    model.addLidarRoom(name: roomName, room: room, frames: frames,
                                       sessionId: captureSessionID ?? model.arSessionId)
                    ctl.accept()
                    reviewing = false
                }, discard: { ctl.cancel(); reviewing = false })
            }
        }
    }
}

private struct ScanReview: View {
    let room: PlanRoom
    let frameCount: Int
    let depthCount: Int
    let save: () -> Void
    let discard: () -> Void
    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Text(RoomStats(room).summary).font(.headline)
                if room.floorWorld == nil && room.wallOutline == nil {
                    Text("The wall boundary is incomplete. The area is approximate; rescan if a wall is missing.")
                        .foregroundStyle(.orange)
                }
                let low = room.walls.filter { $0.confidence == "low" }.count
                if low > 0 { Text("\(low) wall(s) have low confidence. Check their shape before saving.").foregroundStyle(.orange) }
                PlanPreview(room: room)
                Text("\(frameCount) photos · \(depthCount) LiDAR depth frames").font(.caption)
                Button("Save room", action: save).buttonStyle(.borderedProminent)
                Button("Discard and scan again", role: .destructive, action: discard)
            }
            .padding().navigationTitle("Review room")
        }
        .interactiveDismissDisabled()
    }
}
