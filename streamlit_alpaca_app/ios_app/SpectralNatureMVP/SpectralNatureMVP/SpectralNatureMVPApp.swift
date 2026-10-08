import SwiftUI

@main
struct SpectralNatureApp: App {
  @StateObject private var session = AppSession()

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(session)
    }
  }
}
