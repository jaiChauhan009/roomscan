import XCTest

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
        capture(app, name: "synthetic-room-3d")
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
