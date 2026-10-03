import Foundation

struct APIError: LocalizedError {
    var message: String
    var status: Int = 0
    var errorDescription: String? { message }
}

typealias JSONObject = [String: Any]

/// Thin client for the roomscan backend (mirrors web/js/api.js).
struct API {
    var base: String

    init(base: String) {
        var b = base.trimmingCharacters(in: .whitespacesAndNewlines)
        while b.hasSuffix("/") { b.removeLast() }
        self.base = b
    }

    func url(_ path: String) throws -> URL {
        if path.hasPrefix("http://") || path.hasPrefix("https://"), let u = URL(string: path) { return u }
        guard let u = URL(string: base + (path.hasPrefix("/") ? path : "/" + path)) else {
            throw APIError(message: "Bad server address: \(base)")
        }
        return u
    }

    static func enc(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? s
    }

    static func detail(_ data: Data, status: Int) -> String {
        if let o = try? JSONSerialization.jsonObject(with: data) as? JSONObject {
            if let d = o["detail"] as? String { return d }
            if let d = o["detail"] as? [JSONObject] { return d.compactMap { $0["msg"] as? String }.joined(separator: "; ") }
            if let d = o["error"] as? String { return d }
        }
        let t = String(decoding: data.prefix(300), as: UTF8.self)
        return t.isEmpty ? "HTTP \(status)" : "HTTP \(status): \(t)"
    }

    @discardableResult
    func request(_ method: String, _ path: String, body: JSONObject? = nil, timeout: TimeInterval = 30) async throws -> Any {
        var req = URLRequest(url: try url(path))
        req.httpMethod = method
        req.timeoutInterval = timeout
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let data: Data, resp: URLResponse
        do {
            (data, resp) = try await URLSession.shared.data(for: req)
        } catch {
            throw APIError(message: "Cannot reach the server (\(error.localizedDescription)). Check your connection or the server address.")
        }
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw APIError(message: API.detail(data, status: status), status: status) }
        if data.isEmpty { return JSONObject() }
        return (try? JSONSerialization.jsonObject(with: data)) ?? JSONObject()
    }

    func obj(_ method: String, _ path: String, body: JSONObject? = nil, timeout: TimeInterval = 30) async throws -> JSONObject {
        (try await request(method, path, body: body, timeout: timeout) as? JSONObject) ?? [:]
    }

    // endpoints (server/app.py)
    func health() async throws -> JSONObject { try await obj("GET", "/api/health", timeout: 8) }
    func createProject() async throws -> String {
        let o = try await obj("POST", "/api/projects", body: [:])
        guard let pid = o["project_id"] as? String else { throw APIError(message: "No project_id in the answer") }
        return pid
    }
    func project(_ pid: String) async throws -> JSONObject { try await obj("GET", "/api/projects/\(API.enc(pid))") }
    func createSpace(_ pid: String, name: String, sizes: JSONObject = [:]) async throws -> JSONObject {
        try await obj("POST", "/api/projects/\(API.enc(pid))/spaces", body: ["name": name, "kind": "photos", "sizes": sizes])
    }
    func patchSpace(_ pid: String, _ sid: String, body: JSONObject) async throws -> JSONObject {
        try await obj("PATCH", "/api/projects/\(API.enc(pid))/spaces/\(API.enc(sid))", body: body)
    }
    func deleteSpace(_ pid: String, _ sid: String) async throws {
        try await request("DELETE", "/api/projects/\(API.enc(pid))/spaces/\(API.enc(sid))")
    }
    /// Create the whole-home capture container ("video" / "lidar"); idempotent.
    func setCapture(_ pid: String, kind: String) async throws {
        try await request("PUT", "/api/projects/\(API.enc(pid))/captures/\(kind)", body: [:])
    }
    func deleteFile(_ pid: String, target: String, sha: String) async throws {
        try await request("DELETE", "/api/projects/\(API.enc(pid))/\(target)/files/\(sha)")
    }
    func verify(_ pid: String) async throws -> JSONObject {
        try await obj("POST", "/api/projects/\(API.enc(pid))/verify", body: [:], timeout: 180)
    }
    func run(_ pid: String, force: Bool, email: String?, damage: Bool = true) async throws -> JSONObject {
        var b: JSONObject = ["damage": damage]
        if force { b["force"] = true }
        if let email, !email.isEmpty { b["email"] = email }
        return try await obj("POST", "/api/projects/\(API.enc(pid))/run", body: b)
    }
    func job(_ jid: String) async throws -> JSONObject { try await obj("GET", "/api/jobs/\(API.enc(jid))") }
    func jobFileURL(_ jid: String, _ name: String) throws -> URL {
        try url("/api/jobs/\(API.enc(jid))/files/\(name.split(separator: "/").map { API.enc(String($0)) }.joined(separator: "/"))")
    }
    func download(_ pathOrURL: String) async throws -> Data {
        let (data, resp) = try await URLSession.shared.data(from: try url(pathOrURL))
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw APIError(message: API.detail(data, status: status), status: status) }
        return data
    }

    /// Multipart PUT /api/projects/{pid}/{target}/files with fields sha256, name, file
    /// (target: "spaces/<sid>" or "captures/<kind>"), same as web/js/api.js uploadFile.
    func upload(pid: String, target: String, file: URL, name: String, sha256: String,
                progress: @escaping (Double) -> Void) async throws -> JSONObject {
        let boundary = "roomscan-\(UUID().uuidString)"
        let body = FileManager.default.temporaryDirectory.appendingPathComponent("upload-\(UUID().uuidString).multipart")
        defer { try? FileManager.default.removeItem(at: body) }
        FileManager.default.createFile(atPath: body.path, contents: nil)
        let out = try FileHandle(forWritingTo: body)
        func field(_ k: String, _ v: String) -> Data {
            Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(k)\"\r\n\r\n\(v)\r\n".utf8)
        }
        try out.write(contentsOf: field("sha256", sha256))
        try out.write(contentsOf: field("name", name))
        let safe = name.replacingOccurrences(of: "\"", with: "_")
        try out.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safe)\"\r\nContent-Type: \(API.mime(name))\r\n\r\n".utf8))
        let inp = try FileHandle(forReadingFrom: file)
        while let chunk = try inp.read(upToCount: 1 << 20), !chunk.isEmpty { try out.write(contentsOf: chunk) }
        try inp.close()
        try out.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
        try out.close()

        var req = URLRequest(url: try url("/api/projects/\(API.enc(pid))/\(target)/files"))
        req.httpMethod = "PUT"
        req.timeoutInterval = 300
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let data: Data, resp: URLResponse
        do {
            (data, resp) = try await URLSession.shared.upload(for: req, fromFile: body, delegate: ProgressDelegate(progress))
        } catch {
            throw APIError(message: "Upload interrupted (\(error.localizedDescription))")
        }
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw APIError(message: API.detail(data, status: status), status: status) }
        return (try? JSONSerialization.jsonObject(with: data) as? JSONObject) ?? [:]
    }

    static func mime(_ name: String) -> String {
        switch (name as NSString).pathExtension.lowercased() {
        case "jpg", "jpeg": return "image/jpeg"
        case "zip": return "application/zip"
        case "mov": return "video/quicktime"
        case "mp4", "m4v": return "video/mp4"
        default: return "application/octet-stream"
        }
    }
}

final class ProgressDelegate: NSObject, URLSessionTaskDelegate {
    let cb: (Double) -> Void
    init(_ cb: @escaping (Double) -> Void) { self.cb = cb }
    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        if totalBytesExpectedToSend > 0 { cb(Double(totalBytesSent) / Double(totalBytesExpectedToSend)) }
    }
}
