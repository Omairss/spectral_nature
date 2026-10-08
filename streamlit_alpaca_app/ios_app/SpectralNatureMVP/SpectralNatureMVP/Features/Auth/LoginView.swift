import SwiftUI

struct LoginView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.dismiss) private var dismiss
  @State private var email = ""
  @State private var password = ""
  @State private var confirmation = ""
  @State private var firstName = ""
  @State private var lastName = ""
  @State private var displayName = ""
  @State private var linkText = ""
  @State private var mode = "login"
  @State private var submitting = false
  @State private var error = ""
  @State private var notice = ""
  private var action: String { session.accountLink?.action ?? mode }
  private var title: String {
    switch action {
    case "invite": return "Create account"
    case "reset": return "Set a new password"
    case "recover": return "Reset password"
    default: return "Sign in"
    }
  }
  private var valid: Bool {
    if action == "login" { return !email.isEmpty && !password.isEmpty }
    if action == "recover" { return !email.isEmpty }
    return password.count >= 12 && password.count <= 1024 && !confirmation.isEmpty
      && (action != "invite" || (!firstName.isEmpty && !lastName.isEmpty))
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 24) {
        Brand().padding(.top, 32)
        Text(title).font(.largeTitle.weight(.semibold))
        VStack(spacing: 16) {
          if ["login", "recover"].contains(action) {
            TextField("Email", text: $email).textContentType(.username).keyboardType(.emailAddress)
              .textInputAutocapitalization(.never).autocorrectionDisabled()
              .accessibilityIdentifier("login.email")
          }
          if action == "invite" {
            TextField("First name", text: $firstName).textContentType(.givenName)
            TextField("Last name", text: $lastName).textContentType(.familyName)
            TextField("Display name", text: $displayName).textContentType(.nickname)
          }
          if action != "recover" {
            SecureField(action == "reset" ? "New password" : "Password", text: $password)
              .textContentType(action == "login" ? .password : .newPassword)
              .accessibilityIdentifier("login.password").onSubmit { submit() }
          }
          if ["invite", "reset"].contains(action) {
            SecureField("Confirm password", text: $confirmation).textContentType(.newPassword)
            Text("12–1,024 characters").font(.caption).foregroundStyle(.secondary)
          }
        }.padding(18).background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
        if !error.isEmpty { Failure(message: error) }
        if !notice.isEmpty { Text(notice).font(.subheadline).foregroundStyle(Theme.teal) }
        Button(action: submit) {
          Group {
            if submitting {
              ProgressView()
            } else {
              Text(
                action == "recover"
                  ? "Send reset instructions" : action == "reset" ? "Update password" : title
              ).fontWeight(.semibold)
            }
          }
          .frame(maxWidth: .infinity).frame(height: 32)
        }.buttonStyle(.borderedProminent).disabled(submitting || !valid)
          .accessibilityIdentifier("login.submit")
        if action == "login" {
          Button("Forgot password?") { changeMode("recover") }
          DisclosureGroup("Open account link") {
            TextField("Invitation or reset URL", text: $linkText).keyboardType(.URL)
              .textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive()
            Button("Open link") { openLink() }.disabled(linkText.isEmpty || submitting)
          }.font(.subheadline)
        } else {
          Button("Back to sign in") {
            session.accountLink = nil
            changeMode("login")
          }.disabled(submitting)
        }
      }.padding(24).frame(maxWidth: 480)
    }.workbench()
      .toolbar {
        ToolbarItem(placement: .cancellationAction) {
          Button("Close") { dismiss() }.disabled(submitting)
        }
      }
      .interactiveDismissDisabled(submitting)
      .onChange(of: session.accountLink) { _, link in
        guard link != nil else { return }
        password = ""
        confirmation = ""
        firstName = ""
        lastName = ""
        displayName = ""
        error = ""
        notice = ""
      }
      .onDisappear {
        password = ""
        confirmation = ""
        linkText = ""
        session.accountLink = nil
      }
  }
  private func changeMode(_ value: String) {
    mode = value
    password = ""
    confirmation = ""
    error = ""
    notice = ""
  }
  private func openLink() {
    do {
      guard let url = URL(string: linkText.trimmingCharacters(in: .whitespacesAndNewlines)) else {
        throw APIError.server(400, "Account link is invalid.")
      }
      session.accountLink = try AccountLink(url: url)
      linkText = ""
      error = ""
      notice = ""
    } catch { self.error = error.localizedDescription }
  }
  private func submit() {
    guard !submitting, valid else { return }
    guard !["invite", "reset"].contains(action) || password == confirmation else {
      error = "Passwords do not match."
      return
    }
    submitting = true
    error = ""
    notice = ""
    let requested = action
    Task {
      defer { submitting = false }
      do {
        switch requested {
        case "recover":
          let _: JSONValue = try await session.api.post(
            "/api/auth/account/recover", body: ["email": .string(email)])
          notice = "Reset instructions requested."
        case "invite":
          try await session.activate(body: [
            "invite_token": .string(session.accountLink?.token ?? ""),
            "first_name": .string(firstName), "last_name": .string(lastName),
            "display_name": .string(displayName), "password": .string(password),
          ])
          password = ""
          confirmation = ""
        case "reset":
          let _: JSONValue = try await session.api.post(
            "/api/auth/account/reset",
            body: [
              "reset_token": .string(session.accountLink?.token ?? ""),
              "new_password": .string(password),
            ])
          await session.api.clearSession()
          session.expire()
          session.accountLink = nil
          changeMode("login")
          notice = "Password updated. Sign in."
        default:
          if await session.signIn(email: email, password: password) {
            password = ""
          } else {
            error = session.error
          }
        }
      } catch { self.error = error.localizedDescription }
    }
  }
}
