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
    return CGImageDestinationFinalize(dest) ? out as Data : nil
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
