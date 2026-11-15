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

struct RoomReviewIssue: Identifiable, Equatable {
    var id: String
    var message: String
}

extension PlanRoom {
    var reviewIssues: [RoomReviewIssue] {
        var issues: [RoomReviewIssue] = []
        if wallOutline == nil {
            issues.append(RoomReviewIssue(id: "boundary", message:
                "The wall boundary does not close. Check the room corners and any missing wall, even if a floor is visible."))
        }
        let weak = walls.indices.filter { walls[$0].confidence != "high" }
        if !weak.isEmpty {
            issues.append(RoomReviewIssue(id: "confidence", message:
                "Check walls \(weak.map { String($0 + 1) }.joined(separator: ", ")). Their scan confidence is limited."))
        }
        if floorWorld == nil {
            issues.append(RoomReviewIssue(id: "floor", message:
                "No floor boundary was captured. Include the floor edges when rescanning."))
        }
        if walls.contains(where: { !$0.dimensions.x.isFinite || !$0.dimensions.y.isFinite
            || $0.dimensions.x <= 0 || $0.dimensions.y <= 0 }) {
            issues.append(RoomReviewIssue(id: "dimensions", message: "A wall has invalid dimensions. Rescan this room."))
        }
        return issues
    }
}
