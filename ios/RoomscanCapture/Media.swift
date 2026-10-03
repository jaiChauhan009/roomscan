import SwiftUI
import UIKit
import PhotosUI
import ImageIO
import UniformTypeIdentifiers
import CoreTransferable

/// Long side of an uploaded photo, as web/js/shrink.js (the engine works at 960 px; the capture
/// checker calls anything under 1600 px a chat-app copy).
let shrinkLong: CGFloat = 2048

/// JPEG with the camera's metadata (EXIF focal length sets the depth scale) and orientation 1,
/// because the pixels are already upright.
func jpegWithMetadata(_ cg: CGImage, metadata: [String: Any], quality: Double = 0.9) -> Data? {
    var props = metadata
    props[kCGImagePropertyOrientation as String] = 1
    if var tiff = props[kCGImagePropertyTIFFDictionary as String] as? [String: Any] {
        tiff[kCGImagePropertyTIFFOrientation as String] = 1
        props[kCGImagePropertyTIFFDictionary as String] = tiff
    }
    if var exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any] {
        if exif[kCGImagePropertyExifVersion as String] == nil { exif[kCGImagePropertyExifVersion as String] = [2, 3, 2] }
        exif[kCGImagePropertyExifPixelXDimension as String] = cg.width
        exif[kCGImagePropertyExifPixelYDimension as String] = cg.height
        props[kCGImagePropertyExifDictionary as String] = exif
    }
    props[kCGImagePropertyPixelWidth as String] = nil
    props[kCGImagePropertyPixelHeight as String] = nil
    props[kCGImageDestinationLossyCompressionQuality as String] = quality
    let out = NSMutableData()
    guard let dest = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
    CGImageDestinationAddImage(dest, cg, props as CFDictionary)
    guard CGImageDestinationFinalize(dest) else { return nil }
    let jpeg = out as Data

    // ImageIO silently drops some tags on write (FocalLenIn35mmFilm among them, which the engine
    // needs for the scale): check, and if one is missing write our own EXIF block instead.
    let exifIn = metadata[kCGImagePropertyExifDictionary as String] as? [String: Any] ?? [:]
    let want35 = (exifIn[kCGImagePropertyExifFocalLenIn35mmFilm as String] as? NSNumber)?.intValue
    let wantF = (exifIn[kCGImagePropertyExifFocalLength as String] as? NSNumber)?.doubleValue
    if want35 == nil && wantF == nil { return jpeg }
    let got = exifOf(jpeg)
    let ok35 = want35 == nil || (got[kCGImagePropertyExifFocalLenIn35mmFilm as String] as? NSNumber)?.intValue == want35
    let okF = wantF == nil || got[kCGImagePropertyExifFocalLength as String] != nil
    if ok35 && okF { return jpeg }
    let tiffIn = metadata[kCGImagePropertyTIFFDictionary as String] as? [String: Any] ?? [:]
    let block = ExifBlock.make(make: tiffIn[kCGImagePropertyTIFFMake as String] as? String,
                               model: tiffIn[kCGImagePropertyTIFFModel as String] as? String,
                               focalLength: wantF, focal35: want35, width: cg.width, height: cg.height)
    return ExifBlock.splice(block, into: jpeg) ?? jpeg
}

func exifOf(_ jpeg: Data) -> [String: Any] {
    guard let src = CGImageSourceCreateWithData(jpeg as CFData, nil),
          let p = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [String: Any] else { return [:] }
    return p[kCGImagePropertyExifDictionary as String] as? [String: Any] ?? [:]
}

/// A minimal EXIF (APP1) block: IFD0 Make / Model / Orientation=1, Exif IFD ExifVersion,
/// FocalLength, PixelX/YDimension, FocalLenIn35mmFilm (0xA405). Big endian.
enum ExifBlock {
    private struct Entry { var tag: UInt16; var type: UInt16; var count: UInt32; var bytes: [UInt8] }

    private static func be16(_ v: UInt16) -> [UInt8] { [UInt8(v >> 8), UInt8(v & 0xFF)] }
    private static func be32(_ v: UInt32) -> [UInt8] { [UInt8(v >> 24), UInt8((v >> 16) & 0xFF), UInt8((v >> 8) & 0xFF), UInt8(v & 0xFF)] }
    private static func ascii(_ tag: UInt16, _ s: String) -> Entry {
        let b = Array(s.utf8.filter { $0 >= 0x20 && $0 < 0x7F }) + [0]
        return Entry(tag: tag, type: 2, count: UInt32(b.count), bytes: b)
    }
    private static func short(_ tag: UInt16, _ v: Int) -> Entry { Entry(tag: tag, type: 3, count: 1, bytes: be16(UInt16(clamping: v))) }
    private static func long(_ tag: UInt16, _ v: Int) -> Entry { Entry(tag: tag, type: 4, count: 1, bytes: be32(UInt32(clamping: v))) }

    /// IFD at `start` (offset from the TIFF header); returns its bytes, data area included.
    private static func ifd(_ entries: [Entry], start: Int, next: UInt32 = 0) -> [UInt8] {
        let es = entries.sorted { $0.tag < $1.tag }
        var head = be16(UInt16(es.count))
        var data: [UInt8] = []
        let dataStart = start + 2 + 12 * es.count + 4
        for e in es {
            head += be16(e.tag) + be16(e.type) + be32(e.count)
            if e.bytes.count <= 4 {
                head += e.bytes + Array(repeating: 0, count: 4 - e.bytes.count)
            } else {
                head += be32(UInt32(dataStart + data.count))
                data += e.bytes
                if data.count % 2 == 1 { data.append(0) }
            }
        }
        return head + be32(next) + data
    }

    static func make(make: String?, model: String?, focalLength: Double?, focal35: Int?, width: Int, height: Int) -> Data {
        var exif: [Entry] = [Entry(tag: 0x9000, type: 7, count: 4, bytes: Array("0232".utf8)),
                             long(0xA002, width), long(0xA003, height)]
        if let f = focalLength, f > 0 {
            exif.append(Entry(tag: 0x920A, type: 5, count: 1, bytes: be32(UInt32((f * 1000).rounded())) + be32(1000)))
        }
        if let f = focal35, f > 0 { exif.append(short(0xA405, f)) }
        var ifd0: [Entry] = [short(0x0112, 1)]
        if let m = make, !m.isEmpty { ifd0.append(ascii(0x010F, m)) }
        if let m = model, !m.isEmpty { ifd0.append(ascii(0x0110, m)) }
        // IFD0 size is known once the Exif pointer entry is in: compute it with a placeholder
        let probe = ifd(ifd0 + [long(0x8769, 0)], start: 8)
        let exifStart = 8 + probe.count
        let tiff: [UInt8] = Array("MM".utf8) + be16(42) + be32(8)
            + ifd(ifd0 + [long(0x8769, exifStart)], start: 8) + ifd(exif, start: exifStart)
        let payload: [UInt8] = Array("Exif".utf8) + [0, 0] + tiff
        return Data([0xFF, 0xE1] + be16(UInt16(payload.count + 2)) + payload)
    }

    /// JPEG with `app1` right after SOI and any existing Exif APP1 removed.
    static func splice(_ app1: Data, into jpeg: Data) -> Data? {
        let b = [UInt8](jpeg)
        guard b.count > 4, b[0] == 0xFF, b[1] == 0xD8 else { return nil }
        var out: [UInt8] = [0xFF, 0xD8] + [UInt8](app1)
        var i = 2
        while i + 4 <= b.count, b[i] == 0xFF {
            let marker = b[i + 1]
            if marker == 0xDA { break }  // start of scan: the rest is image data
            let len = Int(b[i + 2]) << 8 | Int(b[i + 3])
            let isExif = marker == 0xE1 && i + 10 <= b.count && Array(b[(i + 4)..<(i + 8)]) == Array("Exif".utf8)
            if !isExif { out += b[i..<min(b.count, i + 2 + len)] }
            i += 2 + len
        }
        out += b[min(i, b.count)...]
        return Data(out)
    }
}

/// Camera result: draw upright at ≤ 2048 px, keep the metadata the picker gives.
func shrinkCameraPhoto(_ image: UIImage, metadata: [String: Any]) -> Data? {
    let long = max(image.size.width, image.size.height)
    let s = min(1, shrinkLong / long)
    let size = CGSize(width: (image.size.width * s).rounded(), height: (image.size.height * s).rounded())
    let fmt = UIGraphicsImageRendererFormat()
    fmt.scale = 1
    let img = UIGraphicsImageRenderer(size: size, format: fmt).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
    guard let cg = img.cgImage else { return nil }
    return jpegWithMetadata(cg, metadata: metadata)
}

/// Library photo (JPEG or HEIC): upright thumbnail ≤ 2048 px with the original metadata.
func shrinkPhotoData(_ data: Data) -> Data? {
    guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
    let meta = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [String: Any] ?? [:]
    let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                 kCGImageSourceCreateThumbnailWithTransform: true,
                                 kCGImageSourceThumbnailMaxPixelSize: shrinkLong]
    guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
    return jpegWithMetadata(cg, metadata: meta)
}

/// UIImagePickerController (camera) for a photo or a video.
struct CameraPicker: UIViewControllerRepresentable {
    enum Mode { case photo, video }
    let mode: Mode
    var onPhoto: (UIImage, [String: Any]) -> Void = { _, _ in }
    var onVideo: (URL) -> Void = { _ in }
    @Environment(\.dismiss) private var dismiss

    static var available: Bool { UIImagePickerController.isSourceTypeAvailable(.camera) }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.sourceType = .camera
        if mode == .video {
            p.mediaTypes = [UTType.movie.identifier]
            p.cameraCaptureMode = .video
            p.videoQuality = .typeHigh          // 1080p on current iPhones
            p.videoMaximumDuration = 180
        } else {
            p.mediaTypes = [UTType.image.identifier]
            p.cameraCaptureMode = .photo
        }
        p.delegate = context.coordinator
        return p
    }
    func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker
        init(_ p: CameraPicker) { parent = p }
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let url = info[.mediaURL] as? URL {
                parent.onVideo(url)
            } else if let img = info[.originalImage] as? UIImage {
                parent.onPhoto(img, info[.mediaMetadata] as? [String: Any] ?? [:])
            }
            parent.dismiss()
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
    }
}

/// A movie picked from the library, copied into our temporary folder.
struct PickedMovie: Transferable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { SentTransferredFile($0.url) } importing: { received in
            let dst = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).\(received.file.pathExtension.isEmpty ? "mov" : received.file.pathExtension)")
            try FileManager.default.copyItem(at: received.file, to: dst)
            return PickedMovie(url: dst)
        }
    }
}
