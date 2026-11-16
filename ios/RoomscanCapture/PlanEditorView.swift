import SwiftUI

struct RoomBoundaryEditor: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let roomId: UUID
    @State private var points:[PlanPoint]
    @State private var height:String
    @State private var splitAxis="x"
    @State private var splitValue=""
    @State private var message:String?

    init(room:ScannedRoom) {
        roomId=room.id
        _points=State(initialValue:room.plan.editableOutline)
        _height=State(initialValue:String(format:"%.2f",room.stats.height))
    }
    var body:some View {
        Form {
            Section("Room boundary") {
                PlanCornerCanvas(points:$points)
                    .frame(height:300).padding(.vertical,8)
                Text("Drag a corner to correct the outline. Coordinates are metres; measurements remain separate from scan accuracy.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(points.indices,id:\.self) { i in
                    HStack {
                        Text("Corner \(i+1)").frame(width:76,alignment:.leading)
                        TextField("X",value:$points[i].x,format:.number.precision(.fractionLength(3))).keyboardType(.decimalPad)
                        TextField("Z",value:$points[i].z,format:.number.precision(.fractionLength(3))).keyboardType(.decimalPad)
                        if points.count>3 { Button(role:.destructive) { points.remove(at:i) } label:{Image(systemName:"minus.circle")} }
                    }
                }
                Button("Add corner at end") {
                    guard let last=points.last,let first=points.first else{return}
                    points.insert(PlanPoint(x:(last.x+first.x)/2,z:(last.z+first.z)/2),at:points.count)
                }.disabled(points.count>=128)
                TextField("Ceiling height (m)",text:$height).keyboardType(.decimalPad)
            }
            Section("Split into two rooms") {
                Picker("Split axis",selection:$splitAxis) { Text("X").tag("x");Text("Z").tag("z") }.pickerStyle(.segmented)
                TextField("Split position in metres",text:$splitValue).keyboardType(.decimalPad)
                Button("Split room") {
                    guard let value=Double(splitValue) else { message="Enter a split coordinate in metres.";return }
                    do { try model.splitRoom(roomId,alongX:splitAxis=="x",at:value);dismiss() }
                    catch { message=error.localizedDescription }
                }
            }
            if let message { Section { Text(message).foregroundStyle(.red) } }
            Section { Button("Save boundary") {
                do { try model.editRoom(roomId,points:points,height:Double(height));dismiss() }
                catch { message=error.localizedDescription }
            }.bold() }
        }.navigationTitle("Edit room plan").navigationBarTitleDisplayMode(.inline)
    }
}

private struct PlanCornerCanvas:View {
    @Binding var points:[PlanPoint]
    @State private var dragIndex:Int?
    @State private var dragOrigin=PlanPoint(x:0,z:0)
    @State private var extent=CGRect(x:0,y:0,width:1,height:1)
    init(points:Binding<[PlanPoint]>) {
        _points=points
        let ps=points.wrappedValue
        let xs=ps.map(\.x),zs=ps.map(\.z)
        let loX=xs.min() ?? 0,loZ=zs.min() ?? 0
        _extent=State(initialValue:CGRect(x:loX,y:loZ,width:max((xs.max() ?? 1)-loX,0.1),height:max((zs.max() ?? 1)-loZ,0.1)))
    }
    var body:some View {
        GeometryReader { geo in
            let box=geo.size
            let scale=min((box.width-48)/extent.width,(box.height-48)/extent.height)
            func location(_ p:PlanPoint)->CGPoint { CGPoint(x:24+(p.x-extent.minX)*scale,y:box.height-24-(p.z-extent.minY)*scale) }
            ZStack {
                RoundedRectangle(cornerRadius:12).fill(Color(uiColor:.secondarySystemBackground))
                Path { path in
                    guard let first=points.first else{return};path.move(to:location(first));for p in points.dropFirst(){path.addLine(to:location(p))};path.closeSubpath()
                }.fill(Color.blue.opacity(0.13))
                Path { path in
                    guard let first=points.first else{return};path.move(to:location(first));for p in points.dropFirst(){path.addLine(to:location(p))};path.closeSubpath()
                }.stroke(Color.blue,lineWidth:3)
                ForEach(points.indices,id:\.self) { i in
                    Circle().fill(.blue).frame(width:22,height:22).overlay(Circle().stroke(.white,lineWidth:2))
                        .position(location(points[i])).gesture(DragGesture(minimumDistance:0).onChanged { value in
                            if dragIndex != i { dragIndex=i;dragOrigin=points[i] }
                            points[i].x=dragOrigin.x+value.translation.width/scale
                            points[i].z=dragOrigin.z-value.translation.height/scale
                        }.onEnded{_ in dragIndex=nil})
                }
            }.contentShape(Rectangle())
        }
    }
}

struct PropertyPlanEditor:View {
    @EnvironmentObject private var model:AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var placements:[UUID:PlanPlacement]=[:]
    @State private var first:UUID?
    @State private var second:UUID?
    @State private var message:String?
    var body:some View {
        Form {
            Section("Property layout") {
                Text("Each scan starts in its own coordinate frame. Enter measured offsets and rotation to align the room plans before merging or uploading a combined layout.").font(.caption)
                Text("Offsets default to zero. Use shared measured corners; the preview shows overlaps but does not estimate alignment.").font(.caption).foregroundStyle(.orange)
                PropertyLayoutPreview(rooms:model.lidarRooms,placements:placements).frame(height:250)
                ForEach(model.lidarRooms) { room in
                    let binding=Binding(get:{placements[room.id] ?? PlanPlacement()},set:{placements[room.id]=$0})
                    VStack(alignment:.leading) {
                        Text(room.name).bold()
                        HStack {
                            TextField("X m",value:binding.x,format:.number.precision(.fractionLength(2))).keyboardType(.decimalPad)
                            TextField("Z m",value:binding.z,format:.number.precision(.fractionLength(2))).keyboardType(.decimalPad)
                            TextField("°",value:binding.degrees,format:.number.precision(.fractionLength(1))).keyboardType(.decimalPad)
                        }
                    }
                }
                Button("Save room positions") {
                    do { try model.placeRooms(Dictionary(uniqueKeysWithValues:model.lidarRooms.map{($0.id,placements[$0.id] ?? PlanPlacement())}));message=nil }
                    catch { message=error.localizedDescription }
                }
            }
            Section("Merge rooms") {
                Picker("First room",selection:$first) { Text("Choose room").tag(UUID?.none);ForEach(model.lidarRooms){Text($0.name).tag(Optional($0.id))} }
                Picker("Second room",selection:$second) { Text("Choose room").tag(UUID?.none);ForEach(model.lidarRooms){Text($0.name).tag(Optional($0.id))} }
                Button("Merge shared boundary") {
                    guard let first,let second else { message="Choose two rooms.";return }
                    do { try model.mergeRooms(first,second);message=nil }
                    catch { message=error.localizedDescription }
                }.disabled(first==nil || second==nil || first==second)
            }
            if let message { Section { Text(message).foregroundStyle(.red) } }
        }.navigationTitle("Property plan").toolbar { ToolbarItem(placement:.confirmationAction){Button("Done"){dismiss()}} }
    }
}

private struct PropertyLayoutPreview:View {
    let rooms:[ScannedRoom]
    let placements:[UUID:PlanPlacement]
    private let colors:[Color]=[.blue,.orange,.green,.purple,.pink,.teal]
    var body:some View {
        GeometryReader { geo in
            let rings=rooms.map { room -> [(Double,Double)] in
                let place=placements[room.id] ?? room.placement ?? PlanPlacement()
                let a=place.degrees * .pi / 180,c=cos(a),s=sin(a)
                return room.basePlan.editableOutline.map { p in (c*p.x-s*p.z+place.x,s*p.x+c*p.z+place.z) }
            }
            let all=rings.flatMap{$0}
            let loX=all.map{$0.0}.min() ?? 0,hiX=all.map{$0.0}.max() ?? 1
            let loZ=all.map{$0.1}.min() ?? 0,hiZ=all.map{$0.1}.max() ?? 1
            let scale=min((geo.size.width-40)/max(hiX-loX,0.1),(geo.size.height-40)/max(hiZ-loZ,0.1))
            func point(_ pair:(Double,Double))->CGPoint { CGPoint(x:20+(pair.0-loX)*scale,y:geo.size.height-20-(pair.1-loZ)*scale) }
            ZStack {
                RoundedRectangle(cornerRadius:12).fill(Color(uiColor:.secondarySystemBackground))
                ForEach(rings.indices,id:\.self) { i in
                    Path { path in
                        guard let first=rings[i].first else{return}
                        path.move(to:point(first));for p in rings[i].dropFirst(){path.addLine(to:point(p))};path.closeSubpath()
                    }.fill(colors[i%colors.count].opacity(0.18))
                    Path { path in
                        guard let first=rings[i].first else{return}
                        path.move(to:point(first));for p in rings[i].dropFirst(){path.addLine(to:point(p))};path.closeSubpath()
                    }.stroke(colors[i%colors.count],lineWidth:2)
                }
            }
        }
    }
}

private extension Binding where Value==PlanPlacement {
    var x:Binding<Double>{Binding(get:{wrappedValue.x},set:{wrappedValue.x=$0})}
    var z:Binding<Double>{Binding(get:{wrappedValue.z},set:{wrappedValue.z=$0})}
    var degrees:Binding<Double>{Binding(get:{wrappedValue.degrees},set:{wrappedValue.degrees=$0})}
}
