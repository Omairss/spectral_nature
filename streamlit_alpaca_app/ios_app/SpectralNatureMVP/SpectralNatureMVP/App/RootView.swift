import SwiftUI

struct RootView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.scenePhase) private var scenePhase
  var body: some View {
    TabView {
      NavigationStack { HomeView() }
        .tabItem { Label("Brief", systemImage: "waveform.path") }
      NavigationStack { AccessGate { MarketsView() } }
        .tabItem { Label("Markets", systemImage: "chart.xyaxis.line") }
      if session.context?.isAdmin == true {
        NavigationStack { TradingView() }
          .tabItem { Label("Trading", systemImage: "arrow.triangle.branch") }
      }
      NavigationStack { AccessGate { PortfolioView() } }
        .tabItem { Label("Portfolio", systemImage: "chart.pie") }
      NavigationStack { WorkspaceList() }
        .tabItem { Label("Explore", systemImage: "square.grid.2x2") }
    }
    .environmentObject(session.research)
    .modifier(ResearchPresentationHost(activity: session.research))
    .id(session.revision)
    .tint(Theme.teal)
    .preferredColorScheme(.dark)
    .sheet(isPresented: $session.showingLogin) {
      NavigationStack { LoginView() }.preferredColorScheme(.dark).tint(Theme.teal)
    }
    .task { await session.restore() }
    .task(id: "\(session.context?.userId ?? "")|\(scenePhase == .active)") {
      guard scenePhase == .active, session.authenticated else { return }
      await session.research.monitor()
    }
    .onChange(of: scenePhase) { _, phase in
      if phase == .active { Task { await session.restore() } }
    }
    .onReceive(NotificationCenter.default.publisher(for: APIClient.sessionExpired)) { _ in
      session.handleSessionExpiry()
    }
    .onOpenURL { session.openAccountLink($0) }
    .alert(
      "Account link",
      isPresented: Binding(
        get: { !session.accountLinkError.isEmpty },
        set: { if !$0 { session.accountLinkError = "" } })
    ) {
      Button("Close", role: .cancel) { session.accountLinkError = "" }
    } message: {
      Text(session.accountLinkError)
    }
  }
}

private struct WorkspaceList: View {
  @EnvironmentObject private var session: AppSession
  var body: some View {
    List {
      Section {
        NavigationLink {
          AccessGate { MacroView() }
        } label: {
          Label("Broad Economy", systemImage: "globe.americas")
        }
        NavigationLink {
          AccessGate { InvestigatorSearch() }
        } label: {
          Label("Stock Investigator", systemImage: "magnifyingglass")
        }
        NavigationLink {
          AccessGate { OptionsView() }
        } label: {
          Label("Option Strategizer", systemImage: "point.3.connected.trianglepath.dotted")
        }
        if session.context?.isAdmin == true {
          NavigationLink {
            AdminView()
          } label: {
            Label("Admin", systemImage: "slider.horizontal.3")
          }
        }
      }
      Section {
        NavigationLink {
          AccessGate { FeatureRequestsView() }
        } label: {
          Label("Feature requests", systemImage: "lightbulb")
        }
        if let context = session.context {
          VStack(alignment: .leading, spacing: 6) {
            Text(context.displayName).font(.headline)
            Text(context.email).font(.caption).foregroundStyle(.secondary)
          }
          Button("Sign out", role: .destructive) { Task { await session.signOut() } }
        } else {
          Button("Sign in") { session.showingLogin = true }
        }
        if !session.error.isEmpty {
          Failure(message: session.error, retry: { Task { await session.restore() } })
        }
      }
      Section {
        Text("TORRES CAPITAL").font(.system(size: 10, weight: .medium)).tracking(3).foregroundStyle(
          .secondary)
      }
    }
    .scrollContentBackground(.hidden)
    .navigationTitle("Explore")
    .workbench().researchContext("Explore")
  }
}
