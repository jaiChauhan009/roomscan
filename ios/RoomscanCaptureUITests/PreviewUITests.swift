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
            let landscapeReady = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
                let bounds = app.frame
                return bounds.width > bounds.height && label.isHittable
                    && scene.frame.maxX <= bounds.maxX + 1 && scene.frame.maxY <= bounds.maxY + 1
                    && scene.frame.minY > label.frame.maxY
            }, object: nil)
            XCTAssertEqual(XCTWaiter.wait(for: [landscapeReady], timeout: 15), .completed,
                           "Wait for the actual landscape window and preview to fit inside it")
            // A settled accessibility frame can precede the compositor's final rotation frame.
            sleep(2)
            XCTAssertTrue(label.isHittable, "iPad landscape keeps the room header visible")
            XCTAssertGreaterThan(scene.frame.minY, label.frame.maxY)
            XCTAssertLessThanOrEqual(scene.frame.maxX, app.frame.maxX + 1)
            XCTAssertLessThanOrEqual(scene.frame.maxY, app.frame.maxY + 1)
            print("Landscape bounds: app=\(app.frame), header=\(label.frame), preview=\(scene.frame)")
            capture(app, name: "synthetic-room-3d-landscape")
        }
        app.buttons["2D plan"].tap()
        XCTAssertTrue(app.staticTexts["syntheticRoom"].exists)
    }

    private func capture(_ app: XCUIApplication, name: String) {
        // Full-screen screenshots avoid application-rectangle cropping during rotation.
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
