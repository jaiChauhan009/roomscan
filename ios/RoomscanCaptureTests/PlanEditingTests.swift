import XCTest
import simd
@testable import RoomscanCapture

final class PlanEditingTests:XCTestCase {
    private let rectangle=[PlanPoint(x:0,z:0),PlanPoint(x:4,z:0),PlanPoint(x:4,z:3),PlanPoint(x:0,z:3)]

    func testSplitConservesAreaAndRejectsOutsideLine() throws {
        let parts=try PlanPolygon.split(rectangle,alongX:true,at:1.5)
        XCTAssertEqual(parts.count,2)
        XCTAssertEqual(parts.reduce(0){$0+abs(PlanPolygon.signedArea($1))},12,accuracy:1e-8)
        XCTAssertThrowsError(try PlanPolygon.split(rectangle,alongX:true,at:8))
    }

    func testRejectsSelfCrossingBoundary() {
        let bow=[PlanPoint(x:0,z:0),PlanPoint(x:3,z:3),PlanPoint(x:0,z:3),PlanPoint(x:3,z:0)]
        XCTAssertThrowsError(try PlanPolygon.validated(bow))
    }

    func testMergeRemovesSharedWallAndConservesArea() throws {
        let left=[PlanPoint(x:0,z:0),PlanPoint(x:2,z:0),PlanPoint(x:2,z:3),PlanPoint(x:0,z:3)]
        let right=[PlanPoint(x:2,z:0),PlanPoint(x:4,z:0),PlanPoint(x:4,z:3),PlanPoint(x:2,z:3)]
        let merged=try PlanPolygon.merge(left,right)
        XCTAssertEqual(merged.count,4)
        XCTAssertEqual(PlanPolygon.signedArea(merged),12,accuracy:1e-8)
        XCTAssertThrowsError(try PlanPolygon.merge(left,[PlanPoint(x:3,z:0),PlanPoint(x:5,z:0),PlanPoint(x:5,z:3),PlanPoint(x:3,z:3)]))
    }

    func testBoundaryEditKeepsMeasuredOpeningAndCreatesLowConfidenceWalls() throws {
        let room=try syntheticRoom().replacingOutline(rectangle.map{PlanPoint(x:$0.x+1,z:$0.z+2)})
        XCTAssertEqual(room.walls.count,4)
        XCTAssertTrue(room.walls.allSatisfy{$0.confidence=="low"})
        XCTAssertEqual(room.doors.count,1)
        XCTAssertEqual(room.doors[0].parentId,room.walls[0].id)
    }

    func testPlacementMovesPlanButNotSource() throws {
        let source=try syntheticRoom().replacingOutline(rectangle)
        let moved=source.placed(try PlanPlacement(x:10,z:-2,degrees:90).validated())
        let p=moved.walls[0].transform.position
        XCTAssertEqual(p.x,10,accuracy:1e-5)
        XCTAssertEqual(p.z,0,accuracy:1e-5)
        XCTAssertEqual(source.walls[0].transform.position.x,2,accuracy:1e-5)
    }
}
