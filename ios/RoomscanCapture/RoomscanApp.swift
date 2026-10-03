import SwiftUI

@main
struct RoomscanApp: App {
    @StateObject private var model = AppModel()
    var body: some Scene {
        WindowGroup {
            HomeView()
                .environmentObject(model)
                .task { await model.bootstrap() }
        }
    }
}
