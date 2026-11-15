import SwiftUI

@main
struct RoomscanApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            Group {
                #if DEBUG
                if ProcessInfo.processInfo.arguments.contains("-preview-testing") {
                    PreviewFixtureScreen()
                } else {
                    HomeView()
                }
                #else
                HomeView()
                #endif
            }
                .environmentObject(model)
                .task {
                    #if DEBUG
                    guard !ProcessInfo.processInfo.arguments.contains("-preview-testing") else { return }
                    #endif
                    await model.bootstrap()
                }
                .onChange(of: phase) { _, p in if p == .background { model.appWentToBackground() } }
        }
    }
}
