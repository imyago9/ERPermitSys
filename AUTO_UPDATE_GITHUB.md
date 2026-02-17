# Auto Update + One-Command Releases

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

One command to cut and publish a release tag:

```bash
scripts/cut_release.sh 0.0.3
```

What it does:

1. Bumps `APP_VERSION` in `src/erpermitsys/version.py`.
2. Commits release metadata.
3. Creates and pushes tag `v0.0.3`.
4. Triggers GitHub Actions workflow.

## GitHub Actions Release Pipeline

Workflow file: `.github/workflows/release-windows.yml`

On tag push (`v*`), it:

1. Installs dependencies.
2. Builds Windows app with PyInstaller.
3. Zips build output to `dist/erpermitsys-windows.zip`.
4. Publishes that zip to the GitHub Release for the tag.

## First-time setup checklist

1. Push this branch (including `.github/workflows/release-windows.yml`).
2. In GitHub repo settings, ensure Actions are enabled.
3. Run your first release command:
   - `scripts/cut_release.sh 0.0.3`
4. Verify workflow success in GitHub Actions.
5. Verify release asset exists under Releases.

After that, each release is the same one-liner.
