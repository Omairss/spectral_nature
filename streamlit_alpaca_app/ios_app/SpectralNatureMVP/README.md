# SpectralNatureMVP iOS Scaffold

Native SwiftUI client scaffold for Spectral Nature.

## What this includes

- Login + guest entry
- Home, Portfolio, and Ticker tabs
- Query-backed data loading via `/v1/query` and `/v1/dataset/{name}`
- Optional bearer token auth support
- Secure token storage in Keychain

## Prerequisites

- macOS with Xcode 16+
- XcodeGen (`brew install xcodegen`)

## Generate and run

1. Start the local backend API:

```bash
cd streamlit_alpaca_app
./scripts/run_api_local.sh
```

2. Generate the iOS project:

```bash
cd streamlit_alpaca_app/ios_app/SpectralNatureMVP
xcodegen generate
```

3. Open the generated project in Xcode and run `SpectralNatureMVP`.

## Environment config

- Debug base URL is in `Config/Debug.xcconfig` as `API_BASE_URL`.
- Release base URL is in `Config/Release.xcconfig`.

The app reads this value from `Info.plist` key `SNApiBaseURL`.

## Notes

- If backend auth is disabled in the environment, use `Continue as Guest`.
- This scaffold is intentionally thin and calls existing query contracts so backend logic remains source-of-truth.

