import Foundation
import CryptoKit
import simd

// MARK: - Minimal JSON value with 4-decimal float output

indirect enum J {
    case null
    case bool(Bool)
    case num(Double)
    case int(Int)
    case str(String)
    case arr([J])
    case obj([(String, J)])

    static func f(_ x: Float) -> J { .num(Double(x)) }
    static func f(_ x: Double) -> J { .num(x) }
    static func xs(_ v: [Float]) -> J { .arr(v.map { .num(Double($0)) }) }
    static func xs(_ v: [Double]) -> J { .arr(v.map { .num($0) }) }

    func render(into s: inout String, indent: Int = 0, pretty: Bool = true) {
        switch self {
        case .null: s += "null"
        case .bool(let b): s += b ? "true" : "false"
        case .int(let i): s += String(i)
        case .num(let d): s += J.fmt(d)
        case .str(let t): s += J.quote(t)
        case .arr(let a):
            // arrays of scalars on one line, arrays of objects one per line
            let nested = a.contains { if case .obj = $0 { return true }; if case .arr = $0 { return true }; return false }
            let objects = a.contains { if case .obj = $0 { return true }; return false }
            if a.isEmpty { s += "[]"; return }
            if !pretty || !objects {
                s += "["
                for (i, v) in a.enumerated() { if i > 0 { s += nested ? ", " : ", " }; v.render(into: &s, indent: indent, pretty: false) }
                s += "]"
            } else {
                s += "[\n"
                for (i, v) in a.enumerated() {
                    s += String(repeating: "  ", count: indent + 1)
                    v.render(into: &s, indent: indent + 1, pretty: true)
                    s += i < a.count - 1 ? ",\n" : "\n"
                }
                s += String(repeating: "  ", count: indent) + "]"
            }
        case .obj(let kv):
            if kv.isEmpty { s += "{}"; return }
            if !pretty {
                s += "{"
                for (i, (k, v)) in kv.enumerated() { if i > 0 { s += ", " }; s += J.quote(k) + ": "; v.render(into: &s, indent: indent, pretty: false) }
                s += "}"
                return
            }
            s += "{\n"
            for (i, (k, v)) in kv.enumerated() {
                s += String(repeating: "  ", count: indent + 1) + J.quote(k) + ": "
                v.render(into: &s, indent: indent + 1, pretty: true)
                s += i < kv.count - 1 ? ",\n" : "\n"
            }
            s += String(repeating: "  ", count: indent) + "}"
        }
    }

    var text: String { var s = ""; render(into: &s); return s + "\n" }

    static func fmt(_ d: Double) -> String {
        guard d.isFinite else { return "null" }
        var t = String(format: "%.4f", d)
        while t.hasSuffix("0") { t.removeLast() }
        if t.hasSuffix(".") { t.removeLast() }
        if t == "-0" { t = "0" }
        return t
    }

    static func quote(_ t: String) -> String {
        var o = "\""
        for u in t.unicodeScalars {
            switch u {
            case "\"": o += "\\\""
            case "\\": o += "\\\\"
            case "\n": o += "\\n"
            case "\r": o += "\\r"
            case "\t": o += "\\t"
            default:
                if u.value < 0x20 { o += String(format: "\\u%04x", u.value) } else { o.unicodeScalars.append(u) }
            }
        }
        return o + "\""
    }
}

// MARK: - simd helpers

extension simd_float4x4 {
    /// 16 floats, column-major
    var columnMajor: [Float] {
        [columns.0, columns.1, columns.2, columns.3].flatMap { [$0.x, $0.y, $0.z, $0.w] }
    }
    var position: SIMD3<Float> { SIMD3(columns.3.x, columns.3.y, columns.3.z) }
    func apply(_ p: SIMD3<Float>) -> SIMD3<Float> {
        let r = self * SIMD4<Float>(p.x, p.y, p.z, 1)
        return SIMD3(r.x, r.y, r.z)
    }
}

extension simd_float3x3 {
    var columnMajor: [Float] {
        [columns.0, columns.1, columns.2].flatMap { [$0.x, $0.y, $0.z] }
    }
}

// MARK: - Stored (uncompressed) zip writer, streaming to disk

final class ZipWriter {
    private let fh: FileHandle
    private var offset: UInt32 = 0
    private var central = Data()
    private var count: UInt16 = 0

    init(url: URL) throws {
        FileManager.default.createFile(atPath: url.path, contents: nil)
        fh = try FileHandle(forWritingTo: url)
    }

    func add(name: String, data: Data) throws {
        let crc = CRC32.checksum(data)
        let nameData = Data(name.utf8)
        let (time, date) = ZipWriter.dosNow()
        var h = Data()
        h.le32(0x04034b50); h.le16(20); h.le16(0x0800); h.le16(0) // version, flags (utf8), stored
        h.le16(time); h.le16(date); h.le32(crc)
        h.le32(UInt32(data.count)); h.le32(UInt32(data.count))
        h.le16(UInt16(nameData.count)); h.le16(0)
        h.append(nameData)
        try fh.write(contentsOf: h)
        try fh.write(contentsOf: data)

        var c = Data()
        c.le32(0x02014b50); c.le16(20); c.le16(20); c.le16(0x0800); c.le16(0)
        c.le16(time); c.le16(date); c.le32(crc)
        c.le32(UInt32(data.count)); c.le32(UInt32(data.count))
        c.le16(UInt16(nameData.count)); c.le16(0); c.le16(0); c.le16(0); c.le16(0)
        c.le32(0); c.le32(offset)
        c.append(nameData)
        central.append(c)
        offset += UInt32(h.count + data.count)
        count += 1
    }

    func addFile(name: String, url: URL) throws {
        try add(name: name, data: try Data(contentsOf: url))
    }

    func finish() throws {
        var e = Data()
        e.le32(0x06054b50); e.le16(0); e.le16(0); e.le16(count); e.le16(count)
        e.le32(UInt32(central.count)); e.le32(offset); e.le16(0)
        try fh.write(contentsOf: central)
        try fh.write(contentsOf: e)
        try fh.close()
    }

    private static func dosNow() -> (UInt16, UInt16) {
        let c = Calendar(identifier: .gregorian).dateComponents([.year, .month, .day, .hour, .minute, .second], from: Date())
        let t = UInt16((c.hour ?? 0) << 11 | (c.minute ?? 0) << 5 | (c.second ?? 0) / 2)
        let d = UInt16(max(0, (c.year ?? 1980) - 1980) << 9 | (c.month ?? 1) << 5 | (c.day ?? 1))
        return (t, d)
    }
}

private extension Data {
    mutating func le16(_ v: UInt16) { var x = v.littleEndian; Swift.withUnsafeBytes(of: &x) { append(contentsOf: $0) } }
    mutating func le32(_ v: UInt32) { var x = v.littleEndian; Swift.withUnsafeBytes(of: &x) { append(contentsOf: $0) } }
}

enum CRC32 {
    static let table: [UInt32] = (0..<256).map { i -> UInt32 in
        var c = UInt32(i)
        for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1 }
        return c
    }
    static func checksum(_ data: Data) -> UInt32 {
        var c: UInt32 = 0xFFFFFFFF
        data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
            for b in buf { c = table[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
        }
        return c ^ 0xFFFFFFFF
    }
}

// MARK: - misc

func sha256Hex(of url: URL) throws -> String {
    let fh = try FileHandle(forReadingFrom: url)
    defer { try? fh.close() }
    var h = SHA256()
    while let chunk = try fh.read(upToCount: 1 << 20), !chunk.isEmpty { h.update(data: chunk) }
    return h.finalize().map { String(format: "%02x", $0) }.joined()
}

func deviceModel() -> String {
    var u = utsname()
    uname(&u)
    return withUnsafeBytes(of: &u.machine) { raw in
        String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
    }
}

func isoNow() -> String {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f.string(from: Date())
}

func stamp() -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = "yyyyMMdd-HHmmss"
    return f.string(from: Date())
}

var documentsDir: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0] }
