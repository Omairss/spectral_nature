import SwiftUI

struct RootView: View {
    @EnvironmentObject private var session: AppSession

    var body: some View {
        Group {
            switch session.mode {
            case .unauthenticated:
                LoginView()
            case .authenticated, .guest:
                MainTabView()
            }
        }
        .task {
            await session.refreshAuthStatus()
        }
    }
}

private struct MainTabView: View {
    @EnvironmentObject private var session: AppSession

    var body: some View {
        TabView {
            HomeView()
                .tabItem {
                    Label("Home", systemImage: "house")
                }

            PortfolioView()
                .tabItem {
                    Label("Portfolio", systemImage: "chart.line.uptrend.xyaxis")
                }

            TickerView()
                .tabItem {
                    Label("Ticker", systemImage: "magnifyingglass")
                }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Sign Out") {
                    Task {
                        await session.signOut()
                    }
                }
            }
        }
    }
}

