# ArchiveVault

A Flask project library for Internet Archive storage, designed for deployment on Blitz.

## What it does

- Organizes Internet Archive items as projects in a local SQLite library database.
- Uploads multiple files and folder selections to an Internet Archive item.
- Lists and searches project files.
- Streams/previews common image, video, audio and PDF formats.
- Edits small text/code files and uploads the replacement.
- Downloads individual files or a whole project as a ZIP.
- Uses a workspace password to protect the web interface.

## Important storage note

Internet Archive is a public preservation/archive service, **not private cloud storage**. Items and files uploaded to it may be publicly accessible. Do not upload private, confidential, copyrighted, or personal material unless you have permission and understand the Archive's terms. Upload only files you are allowed to share. Internet Archive may process files and make derivatives.

## 1. Create the Internet Archive credentials

1. Create/sign in to an Internet Archive account.
2. Open https://archive.org/account/s3.php
3. Generate/copy your S3 access key and secret key.
4. Keep the secret private. Never commit it to GitHub.

## 2. GitHub repository

Create a repository named `archivevault` and add these files:

- `app.py`
- `requirements.txt`
- `Dockerfile`
- `README.md`

Push the files to the repository.

## 3. Deploy on Blitz

1. In Blitz, choose **Host something new** → **My own code**.
2. Connect GitHub and select this repository.
3. Build and deploy. The app listens on the `PORT` environment variable (8080 by default).
4. Open the app's **Environment** tab and add:

| Variable | Value |
|---|---|
| `IA_ACCESS_KEY` | Your Internet Archive S3 access key |
| `IA_SECRET_KEY` | Your Internet Archive S3 secret key |
| `APP_PASSWORD` | A strong password for this app |
| `SECRET_KEY` | A long random secret string |
| `DB_PATH` | `/data/archivevault.db` |
| `MAX_UPLOAD_MB` | `2048` |

5. If Blitz offers persistent storage for the app, mount it at `/data`. This keeps the project list across redeploys/restarts. The actual uploaded files remain on Internet Archive.
6. Restart/redeploy after setting environment variables.

Do not put any of these values in the GitHub repository.

## 4. Use it

- Sign in with `APP_PASSWORD`.
- Create a project with a unique 5–80 character Internet Archive identifier.
- Open it and upload files or select a folder.
- Use Preview for common media and text formats.
- Use Download project ZIP to export all files.

## Notes and limitations

- The app stores the project index in SQLite. Mount persistent storage at `/data` and set `DB_PATH=/data/archivevault.db` on Blitz.
- Internet Archive identifiers must be unique across the Archive. If an identifier is already in use by someone else, choose another.
- Internet Archive can take time to reflect uploads or replacement files.
- The built-in editor is for small text/code files (up to 2 MB). Binary files are replaced by uploading a new version, not edited in-browser.
- Streaming is proxied through Flask and supports HTTP Range requests where the Archive endpoint supports them.
- The upload limit defaults to 2 GB per HTTP request; hosting/platform request limits may be lower.
- This starter app does not delete Archive items/files. That avoids accidental destructive actions.
- For production use, add stronger user accounts/CSRF protection and consider a managed database if you need multiple users.
