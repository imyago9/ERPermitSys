# Auto Update + Installer + Code Signing

The app is preconfigured to check updates from this repo:

- `imyago9/ERPermitSys`
- auto-update asset: `erpermitsys-setup.exe`

Users only see **Check for Updates** in Settings. Startup update checks are always on.

## Release assets

Each release now publishes two assets:

1. `erpermitsys-setup.exe` (auto-update + fresh/manual install channel).
2. `erpermitsys-windows.zip` (legacy/manual fallback channel).

## User flow

1. Launch app.
2. App checks updates automatically.
3. If a new installer release exists, app downloads installer, closes, and launches setup.
4. Installer (`.exe`) remains available on GitHub Releases for clean installs.

## Developer flow

1. Bump `APP_VERSION` in `src/erpermitsys/version.py`.
2. Commit and push.
3. Run GitHub Action: `Release Windows Build`.

Optional helper:

- `scripts/cut_release.sh <version> --run-workflow`
- This updates `APP_VERSION`, commits, pushes, and dispatches the release workflow via `gh`.

## JournalTrade-compatible signing variables

This workflow accepts the same variable names you use in JournalTradeGUI (from GitHub Variables/Secrets):

- `ARTIFACT_ACCOUNT_NAME` (fallback alias for signing account)
- `ARTIFACT_SIGNING_ACCOUNT_NAME`
- `ARTIFACT_SIGNING_CERT_PROFILE_NAME`
- `ARTIFACT_SIGNING_ENDPOINT`
- `AZURE_CLIENT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `AZURE_TENANT_ID`

If these are all present, the workflow signs both:

- `dist/erpermitsys/erpermitsys.exe`
- `dist/erpermitsys-setup.exe`

If not present, it falls back to the legacy optional PFX signing secrets:

- `WINDOWS_SIGNING_CERT_PFX_BASE64`
- `WINDOWS_SIGNING_CERT_PASSWORD`

If neither signing path is configured, build and release still run unsigned.

## GitHub Actions release pipeline

Workflow: `.github/workflows/release-windows.yml`

On manual run, it:

1. Reads `APP_VERSION`.
2. Uses tag `v<APP_VERSION>`.
3. Fails if tag already exists.
4. Builds app with PyInstaller (onedir).
5. Builds installer with Inno Setup.
6. Signs binaries (Azure Trusted Signing or PFX fallback, when configured).
7. Publishes both zip + installer assets to GitHub Release.
