import Foundation
import SwiftUI

struct WallReferenceMeasurement: Identifiable, Codable, Equatable {
    var id = UUID()
    var wallId: String
    var metres: Double
    var uncertaintyMetres: Double
    var source: String
    var recordedAt: Date = Date()

    var isValid: Bool {
        metres.isFinite && metres >= 0.05 && metres <= 100
        && uncertaintyMetres.isFinite && uncertaintyMetres > 0 && uncertaintyMetres < metres
        && ["tape", "laser"].contains(source)
    }

    var json: J {
        .obj([("wall_id", .str(wallId)), ("metres", .f(metres)),
              ("uncertainty_metres", .f(uncertaintyMetres)), ("source", .str(source)),
              ("recorded_at", .str(ISO8601DateFormatter().string(from: recordedAt)))])
    }
}

struct SavedRoomView: View {
    @EnvironmentObject var model: AppModel
    let roomId: UUID
    @State private var selectedWall = ""
    @State private var length = ""
    @State private var uncertainty = "0.005"
    @State private var source = "laser"
    @State private var validationMessage: String? = nil

    private func number(_ text: String) -> Double? {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        return formatter.number(from: text)?.doubleValue
    }

    var body: some View {
        if let saved = model.lidarRooms.first(where: { $0.id == roomId }) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let error = model.libraryError { Text(error).foregroundStyle(.red) }
                    PlanPreview(room: saved.plan).frame(height: 350)
                    NavigationLink("Edit room boundary") { RoomBoundaryEditor(room:saved).environmentObject(model) }
                        .buttonStyle(.borderedProminent)
                    ForEach(saved.plan.reviewIssues) { issue in
                        Text(issue.message).font(.callout).foregroundStyle(.orange)
                    }
                    Text("Check a wall against a measured length").font(.headline)
                    Picker("Wall", selection: $selectedWall) {
                        Text("Choose a wall").tag("")
                        ForEach(Array(saved.plan.walls.enumerated()), id: \.element.id) { i, wall in
                            Text("Wall \(i + 1) · \(Double(wall.dimensions.x), specifier: "%.3f") m scanned").tag(wall.id)
                        }
                    }
                    TextField("Measured length in metres", text: $length).keyboardType(.decimalPad)
                        .textFieldStyle(.roundedBorder)
                    TextField("Measurement uncertainty in metres", text: $uncertainty).keyboardType(.decimalPad)
                        .textFieldStyle(.roundedBorder)
                    Picker("Measured using", selection: $source) {
                        Text("Laser").tag("laser")
                        Text("Tape").tag("tape")
                    }.pickerStyle(.segmented)
                    if let message = validationMessage { Text(message).foregroundStyle(.orange) }
                    Button("Save reference measurement") {
                        guard let metres = number(length), let tolerance = number(uncertainty), !selectedWall.isEmpty else {
                            validationMessage = "Choose a wall and enter its measured length and uncertainty in metres."
                            return
                        }
                        let reference = WallReferenceMeasurement(wallId: selectedWall, metres: metres,
                                                                 uncertaintyMetres: tolerance, source: source)
                        guard reference.isValid else {
                            validationMessage = "Use a length from 0.05 to 100 m and a positive uncertainty smaller than the length."
                            return
                        }
                        model.setWallReference(roomId: roomId, reference: reference)
                        validationMessage = nil
                    }.buttonStyle(.borderedProminent)
                    Text("Reference lengths are saved separately and included in the shared ZIP. They check accuracy without moving walls or recalibrating the room.")
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach(saved.referenceMeasurements ?? []) { reference in
                        if let wall = saved.plan.walls.first(where: { $0.id == reference.wallId }),
                           let index = saved.plan.walls.firstIndex(where: { $0.id == reference.wallId }) {
                            Text("Wall \(index + 1): measured \(reference.metres, specifier: "%.3f") m · scan difference \((Double(wall.dimensions.x) - reference.metres) * 100, specifier: "%+.1f") cm")
                                .font(.callout)
                        }
                    }
                }.padding()
            }.navigationTitle(saved.name)
        } else {
            Text("This room was split or merged. Go back to the property to open the updated plan.")
        }
    }
}
