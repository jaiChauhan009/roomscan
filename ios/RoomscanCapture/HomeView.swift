import SwiftUI

struct HomeView: View {
    @EnvironmentObject var model: AppModel
    @State private var roomName = ""
    @State private var scanning = false
    @State private var scanName = ""
    @State private var share: ShareTarget? = nil
    @State private var confirmForce = false
    @State private var openJob: String? = nil
    @State private var showLidarHelp = false

    var body: some View {
        NavigationStack {
            Form {
                if let e = model.error {
                    Section { Text(e).foregroundStyle(.red).accessibilityIdentifier("error") }
                }
                Section {
                    TextField("Email me the report (optional)", text: $model.email)
                        .keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .accessibilityIdentifier("email")
                } footer: { Text("Results are always shown here too.") }

                Section("Your property") {
                    LabeledContent("Project", value: model.projectId.map { String($0.prefix(12)) } ?? "–")
                    Button("Start a new project") { Task { await model.newProject() } }
                }

                lidarSection
                extraSections
                uploadsSection
                verifySection

                Section {
                    Button("Check captures") { Task { await model.verify() } }
                        .disabled(model.projectId == nil || model.pendingUploads > 0)
                        .accessibilityIdentifier("verify")
                    Button("Start computing") {
                        if model.verifyHasRetake { confirmForce = true } else { Task { await start(force: false) } }
                    }
                    .bold()
                    .disabled(model.projectId == nil || model.pendingUploads > 0)
                    .accessibilityIdentifier("run")
                    if model.pendingUploads > 0 { Text("Waiting for \(model.pendingUploads) upload(s)…").font(.caption) }
                    if let j = model.jobId {
                        NavigationLink("Last results (job \(j.prefix(8)))") { JobView(api: model.api, jid: j) }
                    }
                }

                Section("Server") {
                    TextField("Backend URL", text: $model.baseURL)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Reset to default") { model.baseURL = AppModel.defaultBase }
                }
            }
            .navigationTitle("roomscan")
            .overlay {
                if let b = model.busy {
                    VStack(spacing: 10) { ProgressView(); Text(b) }
                        .padding(20).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
                }
            }
            .refreshable { await model.refresh() }
            .fullScreenCover(isPresented: $scanning) { ScanScreen(roomName: scanName).environmentObject(model) }
            .sheet(item: $share) { ShareSheet(items: [$0.url]) }
            .navigationDestination(item: $openJob) { jid in JobView(api: model.api, jid: jid) }
            .confirmationDialog("Some captures need a retake. Results for them may be wrong or missing. Compute anyway?",
                                isPresented: $confirmForce, titleVisibility: .visible) {
                Button("Compute anyway") { Task { await start(force: true) } }
                Button("Cancel", role: .cancel) {}
            }
            .alert("This iPhone has no LiDAR scanner", isPresented: $showLidarHelp) {
                Button("OK", role: .cancel) {}
            } message: {
                Text("RoomPlan scanning needs an iPhone Pro (12 Pro or newer) or an iPad Pro with LiDAR. You can still add rooms with photos or a video walkthrough.")
            }
        }
    }

    func start(force: Bool) async {
        if let j = await model.run(force: force) { openJob = j }
    }

    var nextRoomName: String { "Room \(model.lidarRooms.count + 1)" }

    @ViewBuilder var lidarSection: some View {
        Section {
            if !model.lidarSupported {
                Label("This device has no LiDAR scanner: RoomPlan scanning is not available. Use photos or a video instead.",
                      systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
                    .accessibilityIdentifier("noLidar")
            }
            TextField(nextRoomName, text: $roomName).accessibilityIdentifier("scanRoomName")
            Button {
                guard model.lidarSupported else { showLidarHelp = true; return }
                scanName = roomName.trimmingCharacters(in: .whitespaces).isEmpty ? nextRoomName : roomName
                roomName = ""
                scanning = true
            } label: { Label("Scan a room", systemImage: "camera.metering.matrix") }
            .disabled(model.projectId == nil || (model.captureFiles["lidar"]?.count ?? 0) >= 5)
            .accessibilityIdentifier("scan")
            ForEach(model.lidarRooms) { r in
                let s = r.stats
                VStack(alignment: .leading, spacing: 2) {
                    Text(r.name).font(.headline)
                    Text(s.summary).font(.callout.monospacedDigit())
                    Text(s.counts + " · \(r.frames.count) photos").font(.caption).foregroundStyle(.secondary)
                    if let uid = model.roomUpload[r.id], let u = model.uploads.first(where: { $0.id == uid }) {
                        UploadRow(item: u)
                    } else {
                        Text("Packing…").font(.caption)
                    }
                }
            }
            if !model.lidarRooms.isEmpty {
                Button("Share the whole scan (.zip)") { Task { if let u = await model.homeZip() { share = ShareTarget(url: u) } } }
            }
        } header: { Text("Whole home: LiDAR scan") } footer: {
            Text("One room at a time: follow the on-screen guidance, open the doors, good light. Each room uploads as soon as you tap Done (up to 5).")
        }
    }

    @ViewBuilder var extraSections: some View {
        RoomsSection()
        VideoSection()
    }

    @ViewBuilder var uploadsSection: some View {
        if !model.uploads.isEmpty {
            Section("Uploads") {
                ForEach(model.uploads) { u in UploadRow(item: u) }
                if model.uploads.contains(where: { $0.state == .done }) {
                    Button("Clear finished") { model.uploads.removeAll { $0.state == .done } }
                }
            }
        }
    }

    @ViewBuilder var verifySection: some View {
        if let v = model.verifyResult {
            Section("Check") {
                let ok = v["ok"] as? Bool ?? false
                Text(ok ? "Ready to compute." : "Some captures need attention.").bold()
                ForEach(Array((v["spaces"] as? [JSONObject] ?? []).enumerated()), id: \.offset) { _, s in
                    FindingsBlock(title: s["name"] as? String ?? "Room", status: s["status"] as? String ?? "", findings: s["findings"] as? [JSONObject] ?? [], advice: nil)
                }
                ForEach(Array((v["captures"] as? [JSONObject] ?? []).enumerated()), id: \.offset) { _, c in
                    FindingsBlock(title: c["name"] as? String ?? "Whole home", status: c["status"] as? String ?? "", findings: c["findings"] as? [JSONObject] ?? [], advice: c["advice"] as? String)
                }
                let pf = v["project_findings"] as? [JSONObject] ?? []
                if !pf.isEmpty { FindingsBlock(title: "Project", status: "", findings: pf, advice: nil) }
            }
        }
    }
}

struct StatusBadge: View {
    let status: String
    var body: some View {
        let (t, c): (String, Color) = status == "ok" ? ("OK", .green) : status == "warn" ? ("Check", .orange) : status == "retake" ? ("Retake", .red) : ("Note", .gray)
        Text(t).font(.caption.bold()).padding(.horizontal, 6).padding(.vertical, 2)
            .background(c.opacity(0.2), in: Capsule()).foregroundStyle(c)
    }
}

struct FindingsBlock: View {
    let title: String
    let status: String
    let findings: [JSONObject]
    let advice: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack { Text(title).bold(); Spacer(); if !status.isEmpty { StatusBadge(status: status) } }
            ForEach(Array(findings.filter { let l = $0["level"] as? String; return l != "ok" && l != "info" }.enumerated()), id: \.offset) { _, f in
                HStack(alignment: .top) {
                    StatusBadge(status: f["level"] as? String ?? "")
                    Text(f["message"] as? String ?? "").font(.callout)
                }
            }
            if let a = advice, status != "ok" { Text((status == "retake" ? "Retake: " : "Tip: ") + a).font(.caption) }
        }
    }
}

struct UploadRow: View {
    @EnvironmentObject var model: AppModel
    let item: UploadItem
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(item.label).font(.caption)
                Spacer()
                switch item.state {
                case .queued: Text("waiting").font(.caption).foregroundStyle(.secondary)
                case .uploading: Text("\(Int(item.progress * 100)) %").font(.caption.monospacedDigit())
                case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                case .failed: Button("Retry") { model.retry(item.id) }.font(.caption).buttonStyle(.bordered)
                }
            }
            if item.state == .uploading { ProgressView(value: item.progress) }
            if let e = item.error { Text(e).font(.caption).foregroundStyle(.red) }
        }
    }
}
