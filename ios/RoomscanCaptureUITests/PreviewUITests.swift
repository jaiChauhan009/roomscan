import XCTest
import UIKit

/// Entirely offline. Real camera/LiDAR accuracy is deliberately outside this fixture test.
final class PreviewUITests: XCTestCase {
    func testSyntheticRoomIn2DAnd3D() {
        let app = XCUIApplication()
        app.launchArguments = ["-uitesting", "-preview-testing"]
        app.launch()
        XCTAssertTrue(app.staticTexts["syntheticRoom"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["2D plan"].exists)
        capture(app, name: "synthetic-room-2d")
        app.buttons["3D room"].tap()
        // Give SceneKit one second to render the initial camera and lighting.
        sleep(1)
        XCTAssertEqual(app.state, .runningForeground)
        let label = app.staticTexts["syntheticRoom"]
        XCTAssertTrue(label.isHittable, "3D layout must keep the room header on screen")
        let scene = app.otherElements["room3DPreview"]
        XCTAssertTrue(scene.waitForExistence(timeout: 5))
        XCTAssertGreaterThan(scene.frame.minY, label.frame.maxY, "The 3D view must stay below the header")
        capture(app, name: "synthetic-room-3d")
        if UIDevice.current.userInterfaceIdiom == .pad {
            XCUIDevice.shared.orientation = .landscapeLeft
            defer { XCUIDevice.shared.orientation = .portrait }
            sleep(1)
            XCTAssertTrue(label.isHittable, "iPad landscape keeps the room header visible")
            XCTAssertGreaterThan(scene.frame.minY, label.frame.maxY)
            capture(app, name: "synthetic-room-3d-landscape")
        }
        app.buttons["2D plan"].tap()
        XCTAssertTrue(app.staticTexts["syntheticRoom"].exists)
    }

    private func capture(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
