import SwiftUI
import UIKit

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

struct ShareTarget: Identifiable { let id = UUID(); let url: URL }

struct Measure {
    var value: Double?
    var lo: Double?
    var hi: Double?
    init(_ o: Any?) {
        let d = o as? JSONObject ?? [:]
        value = d["value"] as? Double
        if let ci = d["ci90"] as? [Double], ci.count == 2 { lo = ci[0]; hi = ci[1] }
    }
    func text(_ unit: String, _ digits: Int = 2) -> String {
        guard let v = value else { return "–" }
        var s = String(format: "%.\(digits)f \(unit)", v)
        if let lo, let hi { s += String(format: "  (90 %%: %.\(digits)f–%.\(digits)f)", lo, hi) }
        return s
    }
}

@MainActor
final class JobModel: ObservableObject {
    @Published var job: JobState? = nil
    @Published var error: String? = nil
    @Published var results: [String: JSONObject] = [:]   // run prefix -> result.json
    @Published var plans: [String: UIImage] = [:]
    let api: API
    let jid: String
    private var loading: Set<String> = []

    struct JobState {
        var status: String
        var error: String?
        var stages: [(name: String, status: String, seconds: Double?, note: String?)]
        var runs: [JSONObject]
    }

    init(api: API, jid: String) { self.api = api; self.jid = jid }

    func poll() async {
        while !Task.isCancelled {
            do {
                let j = try await api.job(jid)
                let st = (j["stages"] as? [JSONObject] ?? []).map {
                    (name: $0["name"] as? String ?? "", status: $0["status"] as? String ?? "",
                     seconds: $0["seconds"] as? Double, note: $0["note"] as? String)
                }
                let runs = j["runs"] as? [JSONObject] ?? []
                job = JobState(status: j["status"] as? String ?? "?", error: j["error"] as? String, stages: st, runs: runs)
                error = nil
                for r in runs where (r["status"] as? String) == "done" { await load(r) }
                if let s = job?.status, s == "done" || s == "failed" { return }
            } catch { self.error = error.localizedDescription }
            try? await Task.sleep(nanoseconds: 2_500_000_000)
        }
    }

    func path(_ r: JSONObject, _ key: String, _ file: String) -> String {
        if let o = r["outputs"] as? JSONObject, let p = o[key] as? String { return p }
        return (try? api.jobFileURL(jid, (r["prefix"] as? String ?? "") + file).absoluteString) ?? ""
    }

    private func load(_ r: JSONObject) async {
        let prefix = r["prefix"] as? String ?? ""
        guard results[prefix] == nil, !loading.contains(prefix) else { return }
        loading.insert(prefix)
        defer { loading.remove(prefix) }
        do {
            let data = try await api.download(path(r, "result_json", "result.json"))
            results[prefix] = (try JSONSerialization.jsonObject(with: data) as? JSONObject) ?? [:]
            if let png = try? await api.download(path(r, "plan_png", "plan.png")), let img = UIImage(data: png) { plans[prefix] = img }
        } catch { self.error = "result.json: \(error.localizedDescription)" }
    }

    func fetchToFile(_ r: JSONObject, key: String, file: String) async -> URL? {
        do {
            let data = try await api.download(path(r, key, file))
            let title = (r["label"] as? String ?? "result").replacingOccurrences(of: "/", with: "_")
            let u = FileManager.default.temporaryDirectory.appendingPathComponent("\(title)-\(file)")
            try data.write(to: u)
            return u
        } catch { self.error = error.localizedDescription; return nil }
    }
}

struct JobView: View {
    @StateObject private var jm: JobModel
    @State private var share: ShareTarget? = nil
    @State private var bigPlan: UIImage? = nil

    init(api: API, jid: String) { _jm = StateObject(wrappedValue: JobModel(api: api, jid: jid)) }

    var body: some View {
        List {
            if let e = jm.error { Section { Text(e).foregroundStyle(.red) } }
            if let j = jm.job {
                Section("Job \(jm.jid.prefix(8)) · \(j.status)") {
                    if let e = j.error { Text(e).foregroundStyle(.red) }
                    ForEach(Array(j.stages.enumerated()), id: \.offset) { _, s in
                        HStack {
                            icon(s.status)
                            VStack(alignment: .leading) {
                                Text(s.name)
                                if let n = s.note, !n.isEmpty { Text(n).font(.caption).foregroundStyle(.secondary) }
                            }
                            Spacer()
                            if let sec = s.seconds { Text(String(format: "%.0f s", sec)).font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
                        }
                    }
                }
                ForEach(Array(j.runs.enumerated()), id: \.offset) { _, r in runSection(r) }
            } else {
                Section { HStack { ProgressView(); Text("Loading job…") } }
            }
        }
        .navigationTitle("Results")
        .task { await jm.poll() }
        .sheet(item: $share) { ShareSheet(items: [$0.url]) }
        .sheet(isPresented: Binding(get: { bigPlan != nil }, set: { if !$0 { bigPlan = nil } })) {
            if let img = bigPlan {
                ScrollView([.horizontal, .vertical]) { Image(uiImage: img) }
            }
        }
    }

    @ViewBuilder func icon(_ status: String) -> some View {
        switch status {
        case "done": Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case "running": ProgressView()
        case "failed": Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
        case "skipped": Image(systemName: "minus.circle").foregroundStyle(.secondary)
        default: Image(systemName: "circle").foregroundStyle(.secondary)
        }
    }

    @ViewBuilder func runSection(_ r: JSONObject) -> some View {
        let prefix = r["prefix"] as? String ?? ""
        let status = r["status"] as? String ?? ""
        Section(r["title"] as? String ?? "Run") {
            HStack { icon(status); Text(status) }
            if let e = r["error"] as? String { Text(e).foregroundStyle(.red) }
            if let res = jm.results[prefix] {
                let rooms = res["rooms"] as? [JSONObject] ?? []
                let prop = res["property"] as? JSONObject ?? [:]
                Text("Footprint: " + Measure(prop["footprint_area"]).text("m²", 1))
                Text("Damage found: \((res["damage"] as? [Any])?.count ?? 0)")
                ForEach(Array(rooms.enumerated()), id: \.offset) { _, room in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(room["label"] as? String ?? room["id"] as? String ?? "Room").font(.headline)
                        Text("Floor: " + Measure(room["floor_area"]).text("m²"))
                        Text("Ceiling: " + Measure(room["ceiling_height"]).text("m"))
                        Text("Perimeter: " + Measure(room["perimeter"]).text("m"))
                        Text("Walls: \((room["walls"] as? [Any])?.count ?? 0)")
                    }.font(.callout)
                }
                if let img = jm.plans[prefix] {
                    Button { bigPlan = img } label: {
                        Image(uiImage: img).resizable().scaledToFit().frame(maxHeight: 260)
                    }
                }
                HStack {
                    Button("result.xlsx") { Task { if let u = await jm.fetchToFile(r, key: "result_xlsx", file: "result.xlsx") { share = ShareTarget(url: u) } } }
                    Spacer()
                    Button("result.json") { Task { if let u = await jm.fetchToFile(r, key: "result_json", file: "result.json") { share = ShareTarget(url: u) } } }
                    Spacer()
                    Button("plan.png") { Task { if let u = await jm.fetchToFile(r, key: "plan_png", file: "plan.png") { share = ShareTarget(url: u) } } }
                }.buttonStyle(.bordered)
            } else if status == "done" {
                HStack { ProgressView(); Text("Loading results…") }
            }
        }
    }
}
