import SwiftUI

@main
struct RoomscanApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            HomeView()
                .environmentObject(model)
                .task { await model.bootstrap() }
                .onChange(of: phase) { _, p in if p == .background { model.appWentToBackground() } }
        }
    }
}
