import SwiftUI
import PhotosUI

/// "Rooms" section (photo spaces, as in the web app).
struct RoomsSection: View {
    @EnvironmentObject var model: AppModel
    @State private var newName = ""

    var body: some View {
        Section {
            HStack {
                TextField("Room name, e.g. Kitchen", text: $newName)
                    .accessibilityIdentifier("addRoomName")
                    .onSubmit(add)
                Button("Add", action: add)
                    .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty || model.projectId == nil)
                    .accessibilityIdentifier("addRoom")
            }
            ForEach(model.spaces) { s in
                NavigationLink {
                    SpaceView(spaceId: s.id)
                } label: {
                    HStack {
                        Text(s.name)
                        Spacer()
                        let pending = model.uploads.filter { $0.target == "spaces/\(s.id)" && $0.state != .done }.count
                        Text("\(s.files) photo\(s.files == 1 ? "" : "s")" + (pending > 0 ? " (+\(pending))" : ""))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .accessibilityIdentifier("room-\(s.name)")
            }
            .onDelete { idx in
                let ids = idx.map { model.spaces[$0].id }
                Task { for id in ids { await model.deleteSpace(id) } }
            }
        } header: { Text("Rooms (photos)") } footer: {
            Text("From the doorway: 5-6 photos turning left to right, overlapping, phone upright at chest height, 1x. From the 2nd room on, add 1 photo looking back into the room you came from. Add any sizes you know.")
        }
    }

    func add() {
        let n = newName
        newName = ""
        Task { await model.addSpace(n) }
    }
}

struct SpaceView: View {
    @EnvironmentObject var model: AppModel
    let spaceId: String
    @State private var length = ""
    @State private var width = ""
    @State private var height = ""
    @State private var camera = false
    @State private var picks: [PhotosPickerItem] = []
    @State private var note: String? = nil

    var space: Space? { model.spaces.first { $0.id == spaceId } }

    var body: some View {
        Form {
            if let s = space {
                Section("Sizes (optional, metres)") {
                    sizeField("Length", $length)
                    sizeField("Breadth", $width)
                    sizeField("Height", $height)
                    Button("Save sizes") {
                        Task { await model.setSizes(s.id, length: num(length), width: num(width), height: num(height)) }
                    }
                }
                Section {
                    Button { if CameraPicker.available { camera = true } else { note = "The camera is not available on this device. Choose photos from the library instead." } } label: {
                        Label("Take a photo", systemImage: "camera")
                    }.accessibilityIdentifier("takePhoto")
                    PhotosPicker(selection: $picks, maxSelectionCount: 8, matching: .images) {
                        Label("Choose from library", systemImage: "photo.on.rectangle")
                    }.accessibilityIdentifier("pickPhotos")
                    if let n = note { Text(n).font(.caption).foregroundStyle(.orange) }
                    Text("\(s.files) photo(s) on the server").font(.caption)
                    ForEach(model.uploads.filter { $0.target == "spaces/\(s.id)" }) { UploadRow(item: $0) }
                } header: { Text("Photos") } footer: {
                    Text("From the doorway: 5-6 photos turning left to right, overlapping, phone upright at chest height, 1x. From the 2nd room on, add 1 photo looking back into the room you came from.")
                }
            } else {
                Text("This room no longer exists.")
            }
        }
        .navigationTitle(space?.name ?? "Room")
        .onAppear {
            if let s = space {
                length = s.length.map { String($0) } ?? ""
                width = s.width.map { String($0) } ?? ""
                height = s.height.map { String($0) } ?? ""
            }
        }
        .fullScreenCover(isPresented: $camera) {
            CameraPicker(mode: .photo, onPhoto: { img, meta in
                guard let s = space else { return }
                Task.detached {
                    guard let jpeg = shrinkCameraPhoto(img, metadata: meta) else { return }
                    await MainActor.run { model.addPhoto(jpeg, space: s) }
                }
            }).ignoresSafeArea()
        }
        .onChange(of: picks) { _, items in
            guard let s = space, !items.isEmpty else { return }
            picks = []
            Task {
                for it in items {
                    if let d = try? await it.loadTransferable(type: Data.self),
                       let jpeg = await Task.detached(operation: { shrinkPhotoData(d) }).value {
                        model.addPhoto(jpeg, space: s)
                    }
                }
            }
        }
    }

    func sizeField(_ label: String, _ v: Binding<String>) -> some View {
        HStack { Text(label); Spacer(); TextField("m", text: v).keyboardType(.decimalPad).multilineTextAlignment(.trailing).frame(width: 100) }
    }
    func num(_ s: String) -> Double? { Double(s.replacingOccurrences(of: ",", with: ".")).flatMap { $0 > 0 ? $0 : nil } }
}

/// "Video walkthrough" (whole-home video capture).
struct VideoSection: View {
    @EnvironmentObject var model: AppModel
    @State private var camera = false
    @State private var pick: PhotosPickerItem? = nil
    @State private var note: String? = nil

    var body: some View {
        Section {
            Button { if CameraPicker.available { camera = true } else { note = "The camera is not available on this device. Pick a video from the library instead." } } label: {
                Label("Record a walkthrough", systemImage: "video")
            }
            .disabled(model.projectId == nil)
            .accessibilityIdentifier("videoRecord")
            PhotosPicker(selection: $pick, matching: .videos) {
                Label("Choose a video", systemImage: "film")
            }
            .disabled(model.projectId == nil)
            .accessibilityIdentifier("videoPick")
            if let n = note { Text(n).font(.caption).foregroundStyle(.orange).accessibilityIdentifier("videoNote") }
            ForEach(model.captureFiles["video"] ?? [], id: \.self) { Text($0).font(.caption) }
            ForEach(model.uploads.filter { $0.target == "captures/video" && $0.state != .done }) { UploadRow(item: $0) }
        } header: { Text("Whole home: video walkthrough") } footer: {
            Text("Walk slowly through every room, up to 3 minutes, 1080p. Up to 5 videos.")
        }
        .fullScreenCover(isPresented: $camera) {
            CameraPicker(mode: .video, onVideo: { url in model.addVideo(from: url) }).ignoresSafeArea()
        }
        .onChange(of: pick) { _, it in
            guard let it else { return }
            pick = nil
            Task {
                model.busy = "Importing the video…"
                defer { model.busy = nil }
                if let m = try? await it.loadTransferable(type: PickedMovie.self) {
                    model.addVideo(from: m.url)
                    try? FileManager.default.removeItem(at: m.url)
                } else { model.error = "Could not import that video." }
            }
        }
    }
}
