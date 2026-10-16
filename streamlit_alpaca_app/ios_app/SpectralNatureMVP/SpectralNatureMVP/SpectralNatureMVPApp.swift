import SwiftUI

@main
struct SpectralNatureApp: App {
  @StateObject private var session = AppSession()
  init() { Theme.configureAppearance() }

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(session)
    }
  }
}
