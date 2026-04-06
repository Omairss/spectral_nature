import SwiftUI

struct PortfolioView: View {
    @EnvironmentObject private var session: AppSession

    @State private var accountText: String = ""
    @State private var timeseriesText: String = ""
    @State private var loading = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    payloadPanel(title: "Account", text: accountText)
                    payloadPanel(title: "Portfolio Timeseries (1Y)", text: timeseriesText)
                }
                .padding()
            }
            .navigationTitle("Portfolio")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(loading ? "Loading..." : "Refresh") {
                        Task { await loadPortfolio() }
                    }
                    .disabled(loading)
                }
            }
            .task {
                guard accountText.isEmpty && timeseriesText.isEmpty else { return }
                await loadPortfolio()
            }
        }
    }

    @ViewBuilder
    private func payloadPanel(title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
            Text(text.isEmpty ? "No data loaded." : text)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
                .background(Color(uiColor: .secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    private func loadPortfolio() async {
        loading = true
        defer { loading = false }
        do {
            async let accountResponse = session.apiClient.fetchDataset(name: "account", params: [:])
            async let timeseriesResponse = session.apiClient.fetchDataset(
                name: "portfolio_timeseries",
                params: ["period": .string("1Y")]
            )
            let (account, timeseries) = try await (accountResponse, timeseriesResponse)
            accountText = account.payload.prettyPrinted
            timeseriesText = timeseries.payload.prettyPrinted
            session.errorMessage = ""
        } catch {
            session.errorMessage = error.localizedDescription
        }
    }
}

