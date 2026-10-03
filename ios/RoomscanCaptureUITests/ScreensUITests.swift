import XCTest

/// Walks the screens on the simulator (no LiDAR, no camera) and keeps a screenshot of each.
final class ScreensUITests: XCTestCase {
    var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = true
        app = XCUIApplication()
        app.launchArguments = ["-uitesting"]
        app.launch()
    }

    func shot(_ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }

    /// Scroll the form until the element is on screen.
    @discardableResult
    func reveal(_ e: XCUIElement, maxSwipes: Int = 8) -> Bool {
        var n = 0
        while !(e.exists && e.isHittable) && n < maxSwipes {
            app.swipeUp()
            n += 1
        }
        return e.exists && e.isHittable
    }

    func testScreens() {
        XCTAssertTrue(app.navigationBars["roomscan"].waitForExistence(timeout: 15))
        sleep(3)  // project creation against the backend
        shot("01-home")

        // no LiDAR on the simulator: a clear message, and the scan button explains instead of crashing
        let noLidar = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS[c] 'no LiDAR'")).firstMatch
        XCTAssertTrue(noLidar.waitForExistence(timeout: 5), "the 'needs LiDAR' message is shown")
        let scan = app.buttons["scan"]
        if scan.waitForExistence(timeout: 5), scan.isEnabled {
            scan.tap()
            let alert = app.alerts.firstMatch
            XCTAssertTrue(alert.waitForExistence(timeout: 5), "tapping Scan explains that LiDAR is needed")
            if alert.exists {
                XCTAssertTrue(alert.label.localizedCaseInsensitiveContains("LiDAR"), alert.label)
                shot("02-no-lidar-alert")
                alert.buttons["OK"].tap()
                XCTAssertTrue(alert.waitForNonExistence(timeout: 5))
            }
        }
        shot("03-lidar-section")

        // add a room "Kitchen"
        let name = app.textFields["addRoomName"]
        XCTAssertTrue(reveal(name))
        name.tap()
        name.typeText("Kitchen")
        app.buttons["addRoom"].tap()
        let kitchen = app.buttons["room-Kitchen"]
        if kitchen.waitForExistence(timeout: 20) {
            shot("04-rooms")
            reveal(kitchen)
            kitchen.tap()
            sleep(1)
            shot("05-room-kitchen")
            let take = app.buttons["takePhoto"]
            if reveal(take) {
                take.tap()
                sleep(1)
                shot("06-room-take-photo")
            }
            app.navigationBars.buttons.firstMatch.tap()
        } else {
            shot("04-rooms-not-added")
            XCTFail("room Kitchen did not appear (backend unreachable?)")
        }

        // video section
        let record = app.buttons["videoRecord"]
        if reveal(record) {
            if record.isEnabled { record.tap(); sleep(1) }
            shot("07-video")
        } else {
            XCTFail("video section not found")
        }
        let run = app.buttons["run"]
        reveal(run)
        shot("08-check-and-compute")
        XCTAssertEqual(app.state, .runningForeground, "the app is still running")
    }
}
