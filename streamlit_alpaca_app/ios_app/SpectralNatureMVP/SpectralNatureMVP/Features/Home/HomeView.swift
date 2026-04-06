import SwiftUI

struct HomeView: View {
    @EnvironmentObject private var session: AppSession

    @State private var payloadText: String = ""
    @State private var statusText: String = ""
    @State private var loading = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if !statusText.isEmpty {
                        Text(statusText)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    Text(payloadText.isEmpty ? "No data loaded." : payloadText)
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding()
                        .background(Color(uiColor: .secondarySystemBackground))
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .padding()
            }
            .navigationTitle("Home")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(loading ? "Loading..." : "Refresh") {
                        Task { await loadAttentionHome() }
                    }
                    .disabled(loading)
                }
            }
            .task {
                guard payloadText.isEmpty else { return }
                await loadAttentionHome()
            }
        }
    }

    private func loadAttentionHome() async {
        loading = true
        defer { loading = false }
        do {
            let response = try await session.apiClient.fetchDataset(name: "attention_home_1d", params: [:])
            payloadText = response.payload.prettyPrinted
            statusText = "Result: \(response.resultType)"
            session.errorMessage = ""
        } catch {
            session.errorMessage = error.localizedDescription
        }
    }
}

