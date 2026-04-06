import SwiftUI

struct TickerView: View {
    @EnvironmentObject private var session: AppSession

    @State private var ticker: String = "AAPL"
    @State private var snapshotText: String = ""
    @State private var backgroundText: String = ""
    @State private var loading = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack {
                        TextField("Ticker", text: $ticker)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .textFieldStyle(.roundedBorder)
                        Button(loading ? "Loading..." : "Load") {
                            Task { await loadTicker() }
                        }
                        .disabled(loading || ticker.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }

                    payloadPanel(title: "Ticker Snapshot", text: snapshotText)
                    payloadPanel(title: "Ticker Background", text: backgroundText)
                }
                .padding()
            }
            .navigationTitle("Ticker")
            .task {
                guard snapshotText.isEmpty && backgroundText.isEmpty else { return }
                await loadTicker()
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

    private func loadTicker() async {
        let normalized = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !normalized.isEmpty else { return }
        loading = true
        defer { loading = false }
        do {
            async let snapshot = session.apiClient.fetchDataset(
                name: "attention_ticker_snapshot",
                params: ["ticker": .string(normalized)]
            )
            async let background = session.apiClient.fetchDataset(
                name: "attention_ticker_background",
                params: ["ticker": .string(normalized)]
            )
            let (snapshotResponse, backgroundResponse) = try await (snapshot, background)
            snapshotText = snapshotResponse.payload.prettyPrinted
            backgroundText = backgroundResponse.payload.prettyPrinted
            session.errorMessage = ""
        } catch {
            session.errorMessage = error.localizedDescription
        }
    }
}

