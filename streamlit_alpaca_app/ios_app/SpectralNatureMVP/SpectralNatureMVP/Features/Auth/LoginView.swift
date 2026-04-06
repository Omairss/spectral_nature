import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var session: AppSession

    @State private var baseURL: String = ""
    @State private var email: String = ""
    @State private var password: String = ""
    @State private var loading = false

    var body: some View {
        NavigationStack {
            Form {
                Section("API") {
                    TextField("API Base URL", text: $baseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Save API URL") {
                        session.setBaseURL(baseURL)
                    }
                }

                Section("Authentication") {
                    if let authStatus = session.authStatus {
                        let enabled = authStatus.databaseAuthEnabled
                        Text(enabled ? "Database auth is enabled." : "Database auth is disabled.")
                            .foregroundStyle(enabled ? .primary : .secondary)
                    }

                    TextField("Email", text: $email)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    SecureField("Password", text: $password)

                    Button(loading ? "Signing in..." : "Sign In") {
                        Task {
                            loading = true
                            defer { loading = false }
                            await session.signIn(email: email, password: password)
                        }
                    }
                    .disabled(loading || email.trimmingCharacters(in: .whitespaces).isEmpty || password.isEmpty)
                }

                Section("Guest") {
                    Button("Continue as Guest") {
                        session.continueAsGuest()
                    }
                }

                if !session.errorMessage.isEmpty {
                    Section("Status") {
                        Text(session.errorMessage)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Spectral Nature")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Refresh Auth") {
                        Task {
                            await session.refreshAuthStatus()
                        }
                    }
                }
            }
            .onAppear {
                baseURL = session.baseURLText
            }
        }
    }
}

