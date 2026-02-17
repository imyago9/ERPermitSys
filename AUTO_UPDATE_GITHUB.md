# Auto Update + Manual GitHub Actions Releases

The app is now preconfigured to check updates from this repo:

- `imyago9/ERPermitSys`
- asset name: `erpermitsys-windows.zip`

Users only see **Check for Updates** in Settings. Startup update checks are always on.

## User Flow (your mom)

- Launch app.
- App checks for updates automatically.
- If new version exists, she can accept update.
- Or she can click `Settings -> Check for Updates` manually.

## Developer Flow (you)

1. In VSCode, bump `APP_VERSION` in `src/erpermitsys/version.py`.
2. Commit and push your code normally.
3. Open GitHub -> Actions -> `Release Windows Build`.
4. Click `Run workflow` on the branch that contains your pushed commit.

## GitHub Actions Release Pipeline

Workflow file: `.github/workflows/release-windows.yml`

On manual run, it:

1. Reads `APP_VERSION` from `src/erpermitsys/version.py`.
2. Uses tag `v<APP_VERSION>` for the release.
3. Fails fast if that tag already exists (so you do not overwrite releases).
4. Installs dependencies.
5. Builds Windows app with PyInstaller.
6. Zips build output to `dist/erpermitsys-windows.zip`.
7. Creates a GitHub Release and uploads that zip asset.

## First-time setup checklist

1. Push this branch (including `.github/workflows/release-windows.yml`).
2. In GitHub repo settings, ensure Actions are enabled.
3. Bump `APP_VERSION`, commit, and push.
4. Run `Release Windows Build` from the Actions tab.
5. Verify workflow success in GitHub Actions.
6. Verify release asset exists under Releases.
