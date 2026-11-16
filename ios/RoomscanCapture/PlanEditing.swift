import Foundation
import simd

struct PlanPoint: Codable, Equatable, Identifiable {
    var id = UUID()
    var x: Double
    var z: Double
    var vector: SIMD2<Float> { SIMD2(Float(x), Float(z)) }
}

enum PlanEditError: LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case .invalid(let message) = self { return message }; return nil }
}

enum PlanPolygon {
    static func cross(_ a: PlanPoint, _ b: PlanPoint, _ c: PlanPoint) -> Double {
        (b.x-a.x)*(c.z-a.z) - (b.z-a.z)*(c.x-a.x)
    }
    static func signedArea(_ points: [PlanPoint]) -> Double {
        guard let origin = points.first else { return 0 }
        return points.indices.reduce(0) { sum, i in
            let a = points[i], b = points[(i+1)%points.count]
            return sum + ((a.x-origin.x)*(b.z-origin.z) - (b.x-origin.x)*(a.z-origin.z))/2
        }
    }
    static func onSegment(_ p: PlanPoint, _ a: PlanPoint, _ b: PlanPoint) -> Bool {
        abs(cross(a,b,p)) < 0.000001 && p.x >= min(a.x,b.x)-0.000001 && p.x <= max(a.x,b.x)+0.000001
        && p.z >= min(a.z,b.z)-0.000001 && p.z <= max(a.z,b.z)+0.000001
    }
    static func intersects(_ a: PlanPoint, _ b: PlanPoint, _ c: PlanPoint, _ d: PlanPoint) -> Bool {
        let ca = cross(a,b,c), cb = cross(a,b,d), cc = cross(c,d,a), cd = cross(c,d,b)
        if ca*cb < 0 && cc*cd < 0 { return true }
        return onSegment(c,a,b) || onSegment(d,a,b) || onSegment(a,c,d) || onSegment(b,c,d)
    }
    static func validated(_ points: [PlanPoint]) throws -> [PlanPoint] {
        guard (3...128).contains(points.count), points.allSatisfy({ $0.x.isFinite && $0.z.isFinite
            && abs($0.x) < 10_000_000 && abs($0.z) < 10_000_000 }) else {
            throw PlanEditError.invalid("Use 3–128 finite room corners.")
        }
        for i in points.indices {
            let j = (i+1)%points.count
            guard hypot(points[i].x-points[j].x, points[i].z-points[j].z) >= 0.02 else {
                throw PlanEditError.invalid("Two neighbouring corners are less than 2 cm apart.")
            }
            for k in points.indices where k > i && k != j && (k+1)%points.count != i {
                if intersects(points[i], points[j], points[k], points[(k+1)%points.count]) {
                    throw PlanEditError.invalid("The boundary crosses or touches itself. Move the highlighted corners apart.")
                }
            }
        }
        guard abs(signedArea(points)) >= 0.1 else { throw PlanEditError.invalid("A room must cover at least 0.1 m².") }
        return signedArea(points) < 0 ? Array(points.reversed()) : points
    }
    static func contains(_ point: PlanPoint, _ polygon: [PlanPoint]) -> Bool {
        guard polygon.count >= 3 else { return false }
        var inside = false
        for i in polygon.indices {
            let a=polygon[i], b=polygon[(i+1)%polygon.count]
            if onSegment(point,a,b) { return true }
            if (a.z > point.z) != (b.z > point.z),
               point.x < (b.x-a.x)*(point.z-a.z)/(b.z-a.z)+a.x { inside.toggle() }
        }
        return inside
    }
    static func split(_ points: [PlanPoint], alongX: Bool, at value: Double) throws -> [[PlanPoint]] {
        let polygon = try validated(points)
        guard value.isFinite else { throw PlanEditError.invalid("Enter a finite split position.") }
        func coordinate(_ p: PlanPoint) -> Double { alongX ? p.x : p.z }
        func clipped(_ positive: Bool) -> [PlanPoint] {
            var result: [PlanPoint] = []
            for i in polygon.indices {
                let a=polygon[i], b=polygon[(i+1)%polygon.count]
                let da=coordinate(a)-value, db=coordinate(b)-value
                let ina = positive ? da >= 0 : da <= 0
                let inb = positive ? db >= 0 : db <= 0
                if ina { result.append(a) }
                if ina != inb {
                    let t=da/(da-db)
                    let p=PlanPoint(x:a.x+t*(b.x-a.x), z:a.z+t*(b.z-a.z))
                    if !onSegment(p,a,a) && !onSegment(p,b,b) { result.append(p) }
                }
            }
            return result
        }
        let parts = try [validated(clipped(false)), validated(clipped(true))]
        guard abs(parts.reduce(0) { $0 + abs(signedArea($1)) } - abs(signedArea(polygon))) < 0.001 else {
            throw PlanEditError.invalid("This split produces disconnected regions. Choose a different line.")
        }
        return parts
    }
    static func merge(_ first: [PlanPoint], _ second: [PlanPoint]) throws -> [PlanPoint] {
        let a=try validated(first), b=try validated(second)
        // Split boundaries at every shared vertex so partial shared walls are handled too.
        func edges(_ ring: [PlanPoint], other: [PlanPoint]) -> [(PlanPoint,PlanPoint)] {
            var result: [(PlanPoint,PlanPoint)] = []
            for i in ring.indices {
                let p=ring[i], q=ring[(i+1)%ring.count]
                let dx=q.x-p.x, dz=q.z-p.z, l2=dx*dx+dz*dz
                let middle=other.filter { onSegment($0,p,q) }.sorted {
                    (($0.x-p.x)*dx+($0.z-p.z)*dz)/l2 < (($1.x-p.x)*dx+($1.z-p.z)*dz)/l2
                }
                var chain=[p]
                for point in middle where hypot(point.x-chain.last!.x,point.z-chain.last!.z) > 0.00001 { chain.append(point) }
                if hypot(q.x-chain.last!.x,q.z-chain.last!.z) > 0.00001 { chain.append(q) }
                for k in 0..<(chain.count-1) { result.append((chain[k],chain[k+1])) }
            }
            return result
        }
        func equal(_ p: PlanPoint,_ q: PlanPoint)->Bool { hypot(p.x-q.x,p.z-q.z) < 0.00001 }
        var left=edges(a,other:b), right=edges(b,other:a), remaining:[(PlanPoint,PlanPoint)]=[]
        var shared=0
        while !left.isEmpty {
            let edge=left.removeFirst()
            if let j=right.firstIndex(where:{equal(edge.0,$0.1) && equal(edge.1,$0.0)}) { right.remove(at:j);shared+=1 }
            else { remaining.append(edge) }
        }
        guard shared > 0 else { throw PlanEditError.invalid("Align the room boundaries to a shared wall before merging. Overlapping or disconnected rooms cannot be merged.") }
        remaining += right
        let start=remaining.removeFirst();var ring=[start.0];var end=start.1
        while !equal(end,ring[0]) {
            ring.append(end)
            guard let j=remaining.firstIndex(where:{equal($0.0,end)}) else {
                throw PlanEditError.invalid("The merged boundary is disconnected. Align its corners first.")
            }
            end=remaining.remove(at:j).1
        }
        guard remaining.isEmpty else { throw PlanEditError.invalid("Merging would create a hole or overlapping boundary.") }
        let result=try validated(ring)
        guard abs(signedArea(result)-signedArea(a)-signedArea(b)) < 0.001 else {
            throw PlanEditError.invalid("These rooms overlap. Adjust their corners before merging.")
        }
        return result
    }
}

struct PlanPlacement: Codable, Equatable {
    var x: Double = 0
    var z: Double = 0
    var degrees: Double = 0
    var matrix: simd_float4x4 {
        let angle=Float(degrees * .pi / 180), c=cos(angle), s=sin(angle)
        var matrix=matrix_identity_float4x4
        matrix.columns.0=SIMD4(c,0,s,0);matrix.columns.2=SIMD4(-s,0,c,0)
        matrix.columns.3=SIMD4(Float(x),0,Float(z),1)
        return matrix
    }
    func validated() throws -> PlanPlacement {
        guard x.isFinite && z.isFinite && degrees.isFinite && abs(x) <= 10_000 && abs(z) <= 10_000 else {
            throw PlanEditError.invalid("Enter finite room offsets within 10 km.")
        }
        return self
    }
}

extension PlanRoom {
    var editableOutline: [PlanPoint] {
        let points = floorWorld?.map { SIMD2($0.x,$0.z) } ?? wallOutline ?? []
        if points.count >= 3 { return points.map { PlanPoint(x:Double($0.x),z:Double($0.y)) } }
        let endpoints=walls.flatMap { [PlanRoom.ends($0).0,PlanRoom.ends($0).1] }
        guard let loX=endpoints.map(\.x).min(),let hiX=endpoints.map(\.x).max(),
              let loZ=endpoints.map(\.y).min(),let hiZ=endpoints.map(\.y).max(),hiX-loX>=0.1,hiZ-loZ>=0.1 else { return [] }
        return [PlanPoint(x:Double(loX),z:Double(loZ)),PlanPoint(x:Double(hiX),z:Double(loZ)),
                PlanPoint(x:Double(hiX),z:Double(hiZ)),PlanPoint(x:Double(loX),z:Double(hiZ))]
    }
    func placed(_ placement: PlanPlacement?) -> PlanRoom {
        guard let placement else { return self }
        var result=self
        func moved(_ surfaces:[PlanSurface])->[PlanSurface] { surfaces.map { surface in
            var copy=surface;copy.transform=placement.matrix * surface.transform;return copy
        }}
        result.walls=moved(walls);result.floors=moved(floors);result.doors=moved(doors)
        result.windows=moved(windows);result.openings=moved(openings)
        result.objects=objects.map { object in var copy=object;copy.transform=placement.matrix*object.transform;return copy }
        return result
    }
    func replacingOutline(_ points:[PlanPoint], height:Double?=nil, partition:Bool=false) throws -> PlanRoom {
        let ring=try PlanPolygon.validated(points)
        if let height, !height.isFinite || height < 0.5 || height > 20 {
            throw PlanEditError.invalid("Use a ceiling height from 0.5 to 20 m.")
        }
        let observedFloor=floorWorld
        let floorY:Float
        if let observedFloor,!observedFloor.isEmpty {
            let total=observedFloor.reduce(Float(0)) { partial,point in partial+point.y }
            floorY=total/Float(observedFloor.count)
        } else {
            floorY=walls.map{$0.transform.position.y-$0.dimensions.y/2}.min() ?? 0
        }
        var result=self, used=Set<String>()
        result.walls=ring.indices.map { i in
            let p=ring[i].vector, q=ring[(i+1)%ring.count].vector, delta=q-p, width=simd_length(delta)
            let direction=delta/width, midpoint=(p+q)/2
            let source=walls.filter { wall in
                let edge=PlanRoom.ends(wall), oldDirection=simd_normalize(edge.1-edge.0)
                let perpendicular=abs((midpoint.x-edge.0.x)*oldDirection.y-(midpoint.y-edge.0.y)*oldDirection.x)
                return !used.contains(wall.id) && abs(simd_dot(direction,oldDirection)) > 0.95 && perpendicular < 0.5
            }.min { a,b in
                let ea=PlanRoom.ends(a),eb=PlanRoom.ends(b)
                return simd_distance(midpoint,(ea.0+ea.1)/2) < simd_distance(midpoint,(eb.0+eb.1)/2)
            }
            let id=source?.id ?? "edited-\(ring[i].id.uuidString)";used.insert(id)
            let h=Float(height ?? Double(source?.dimensions.y ?? walls.map(\.dimensions.y).max() ?? 2.6))
            var transform=matrix_identity_float4x4
            transform.columns.0=SIMD4(direction.x,0,direction.y,0)
            transform.columns.2=SIMD4(-direction.y,0,direction.x,0)
            transform.columns.3=SIMD4(midpoint.x,floorY+h/2,midpoint.y,1)
            return PlanSurface(id:id,transform:transform,dimensions:SIMD3(width,h,source?.dimensions.z ?? 0.08),confidence:"low")
        }
        func relocated(_ surfaces:[PlanSurface]) throws -> [PlanSurface] {
            try surfaces.compactMap { surface in
                let position=surface.transform.position, p=SIMD2(position.x,position.z)
                if partition && !PlanPolygon.contains(PlanPoint(x:Double(p.x),z:Double(p.y)),ring) { return nil }
                let target=result.walls.first(where:{$0.id==surface.parentId}) ?? result.walls.min { a,b in
                    let ea=PlanRoom.ends(a),eb=PlanRoom.ends(b)
                    return simd_distance(p,(ea.0+ea.1)/2) < simd_distance(p,(eb.0+eb.1)/2)
                }
                guard let target else { return nil }
                guard surface.dimensions.x <= target.dimensions.x else {
                    throw PlanEditError.invalid("A door or window no longer fits its wall. Adjust the boundary or opening first.")
                }
                if let height, Double(position.y+surface.dimensions.y/2-floorY) > height + 0.02 {
                    throw PlanEditError.invalid("The new ceiling would cut through a door or window.")
                }
                let edge=PlanRoom.ends(target),d=edge.1-edge.0
                let t=max(surface.dimensions.x/(2*target.dimensions.x),min(1-surface.dimensions.x/(2*target.dimensions.x),simd_dot(p-edge.0,d)/simd_length_squared(d)))
                let centre=edge.0+t*d
                var copy=surface;copy.parentId=target.id;copy.transform=target.transform
                copy.transform.columns.3=SIMD4(centre.x,position.y,centre.y,1);copy.confidence="low"
                return copy
            }
        }
        result.doors=try relocated(doors);result.windows=try relocated(windows);result.openings=try relocated(openings)
        var floorTransform=matrix_identity_float4x4;floorTransform.columns.3.y=floorY
        result.floors=[PlanSurface(id:"edited-floor",transform:floorTransform,dimensions:SIMD3(0,0,0),confidence:"low",
                                  polygon:ring.map{SIMD3(Float($0.x),0,Float($0.z))})]
        if partition { result.objects=objects.filter { PlanPolygon.contains(PlanPoint(x:Double($0.transform.position.x),z:Double($0.transform.position.z)),ring) } }
        return result
    }
}
