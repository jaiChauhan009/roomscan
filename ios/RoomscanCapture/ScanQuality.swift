import Foundation

/// Device-independent decisions kept separate from ARKit for simulator unit tests.
enum ScanTracking: Equatable {
    case normal, initializing, movingTooFast, insufficientFeatures, relocalizing, unavailable

    var guidance: String? {
        switch self {
        case .normal: return nil
        case .initializing: return "Move slowly and show a well-lit wall to start tracking."
        case .movingTooFast: return "Slow down and keep the phone steady."
        case .insufficientFeatures: return "Point toward furniture or a textured wall in better light."
        case .relocalizing: return "Return to the last scanned area and move slowly."
        case .unavailable: return "Camera tracking is unavailable. Cancel and start a new scan."
        }
    }
}

struct ScanHealth: Equatable {
    var tracking: ScanTracking = .initializing
    var thermal: ProcessInfo.ThermalState = .nominal
    var ambientIntensity: Double? = nil
    var depthAvailable = false

    var canFinish: Bool { tracking == .normal }
    var photoInterval: TimeInterval { thermal == .serious ? 2.1 : 0.7 }
    var canRecordPhoto: Bool { tracking == .normal && thermal != .critical }

    var guidance: String? {
        if thermal == .critical { return "Device too hot. Save this room now, or cancel and let the device cool down." }
        if let trackingMessage = tracking.guidance { return trackingMessage }
        if thermal == .serious { return "Device warming up. Photo sampling is reduced; finish this room soon." }
        if let light = ambientIntensity, light < 100 { return "Turn on more lights for better tracking and room photos." }
        return nil
    }
}
