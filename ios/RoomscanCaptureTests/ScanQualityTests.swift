import XCTest
@testable import RoomscanCapture

final class ScanQualityTests: XCTestCase {
    func testTrackingMustRecoverBeforeFinish() {
        var health = ScanHealth()
        let states: [ScanTracking] = [.initializing, .movingTooFast, .insufficientFeatures, .relocalizing, .unavailable]
        for tracking in states {
            health.tracking = tracking
            XCTAssertFalse(health.canFinish)
            XCTAssertFalse(health.canRecordPhoto)
            XCTAssertNotNil(health.guidance)
        }
        health.tracking = .normal
        XCTAssertTrue(health.canFinish)
        XCTAssertTrue(health.canRecordPhoto)
        XCTAssertNil(health.guidance)
    }

    func testHeatReducesPhotoWorkWithoutPreventingSave() {
        var health = ScanHealth(tracking: .normal, thermal: .serious)
        XCTAssertEqual(health.photoInterval, 2.1)
        XCTAssertTrue(health.canFinish)
        health.thermal = .critical
        XCTAssertFalse(health.canRecordPhoto)
        XCTAssertTrue(health.canFinish)
        XCTAssertNotNil(health.guidance)
    }

    func testTrackingGuidanceTakesPriorityOverLowLight() {
        var health = ScanHealth(tracking: .relocalizing, ambientIntensity: 20)
        XCTAssertEqual(health.guidance, ScanTracking.relocalizing.guidance)
        health.tracking = .normal
        XCTAssertNotNil(health.guidance)
        health.ambientIntensity = 1000
        XCTAssertNil(health.guidance)
    }
}
