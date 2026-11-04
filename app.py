import os
import re
import io
import json
import sqlite3
import tempfile
import zipfile
import mimetypes
from functools import wraps
from urllib.parse import quote

import requests
import internetarchive as ia

from flask import (
    Flask, request, redirect, url_for, session,
    flash, Response, render_template_string, abort,
    send_file, jsonify
)

from werkzeug.utils import secure_filename


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "ArchiveVault"

IA_ACCESS_KEY = os.environ.get("IA_ACCESS_KEY", "")
IA_SECRET_KEY = os.environ.get("IA_SECRET_KEY", "")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "change-me")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret")

PORT = int(os.environ.get("PORT", "8080"))

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# ============================================================
# DATABASE
# Automatically falls back if Blitz cannot write to DB_PATH.
# ============================================================

DB_PATH = os.environ.get("DB_PATH", "archivevault.db")


def connect_database():
    global DB_PATH

    try:
        parent = os.path.dirname(os.path.abspath(DB_PATH))
        os.makedirs(parent, exist_ok=True)

        con = sqlite3.connect(DB_PATH, timeout=30)
        con.row_factory = sqlite3.Row

        con.execute("PRAGMA journal_mode=WAL")
        return con

    except (OSError, sqlite3.Error):
        DB_PATH = "/tmp/archivevault.db"

        con = sqlite3.connect(DB_PATH, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")

        return con


def init_database():
    with connect_database() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'Other',
                tags TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


init_database()


# ============================================================
# AUTHENTICATION
# ============================================================

def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))

        return func(*args, **kwargs)

    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        password = request.form.get("password", "")

        if APP_PASSWORD and password == APP_PASSWORD:

            session["logged_in"] = True

            return redirect(url_for("home"))

        flash("Incorrect password.")

    return render("login.html", LOGIN_HTML, title="Login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# INTERNET ARCHIVE
# ============================================================

def check_archive_credentials():

    if not IA_ACCESS_KEY or not IA_SECRET_KEY:
        raise RuntimeError(
            "Internet Archive credentials are missing. "
            "Configure IA_ACCESS_KEY and IA_SECRET_KEY in Blitz."
        )


def archive_item(identifier):
    check_archive_credentials()
    return ia.get_item(identifier)


def archive_metadata(identifier):

    url = (
        "https://archive.org/metadata/"
        + quote(identifier, safe="")
    )

    response = requests.get(url, timeout=45)
    response.raise_for_status()

    return response.json()


def get_archive_files(identifier):

    metadata = archive_metadata(identifier)

    files = []

    for item in metadata.get("files", []):

        name = item.get("name")

        if not name:
            continue

        if item.get("source") == "metadata":
            continue

        if name in ("_meta.xml", "_files.xml"):
            continue

        files.append({
            "name": name,
            "size": int(item.get("size", 0) or 0),
            "format": item.get("format", ""),
            "mtime": item.get("mtime", "")
        })

    return sorted(files, key=lambda x: x["name"].lower())


def upload_archive_file(identifier, filename, local_path, project):

    check_archive_credentials()

    item = archive_item(identifier)

    metadata = {
        "mediatype": "data",
        "title": project["title"],
        "description": project["description"]
    }

    return item.upload(
        {filename: local_path},
        metadata=metadata,
        access_key=IA_ACCESS_KEY,
        secret_key=IA_SECRET_KEY,
        retries=3
    )


# ============================================================
# PROJECT HELPERS
# ============================================================

def clean_identifier(value):

    value = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "-",
        (value or "").strip().lower()
    ).strip("-_")

    if len(value) < 5 or len(value) > 80:
        raise ValueError(
            "Project identifiers must be 5–80 characters."
        )

    return value


def clean_remote_path(value):

    value = (value or "").replace("\\", "/").strip("/")

    parts = []

    for part in value.split("/"):

        if part in ("", ".", ".."):
            continue

        part = re.sub(
            r"[^A-Za-z0-9._ ()\-\[\]]+",
            "_",
            part
        )

        if part:
            parts.append(part[:180])

    if not parts:
        raise ValueError("Invalid filename.")

    return "/".join(parts)


def get_project(identifier):

    with connect_database() as con:

        project = con.execute(
            "SELECT * FROM projects WHERE identifier=?",
            (identifier,)
        ).fetchone()

    if not project:
        abort(404)

    return project


# ============================================================
# HTML LAYOUT
# ============================================================

STYLE = """
<style>
* { box-sizing:border-box }

body {
    margin:0;
    background:#090d18;
    color:#edf3ff;
    font-family:Inter,Segoe UI,Arial,sans-serif;
}

a {
    color:inherit;
    text-decoration:none;
}

button,.button {
    background:#8273ff;
    color:white;
    padding:11px 16px;
    border:0;
    border-radius:10px;
    cursor:pointer;
    display:inline-block;
    font-weight:700;
}

.secondary {
    background:#26344a;
}

input,textarea,select {
    width:100%;
    padding:12px;
    background:#0b1423;
    color:white;
    border:1px solid #293750;
    border-radius:9px;
    margin:7px 0 14px;
}

textarea {
    min-height:140px;
}

.container {
    max-width:1200px;
    margin:auto;
    padding:24px;
}

.header {
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:28px;
    gap:15px;
}

.logo {
    font-size:23px;
    font-weight:900;
}

.hero {
    background:linear-gradient(120deg,#202548,#121b2c);
    border:1px solid #303a59;
    padding:30px;
    border-radius:20px;
    margin-bottom:22px;
}

.hero h1 {
    font-size:36px;
    margin:0 0 8px;
}

.muted {
    color:#9aa9bf;
}

.grid {
    display:grid;
    grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
    gap:15px;
}

.card {
    background:#111b2b;
    border:1px solid #293750;
    padding:19px;
    border-radius:15px;
    overflow-wrap:anywhere;
}

.card h3 {
    margin-top:0;
}

.panel {
    background:#111b2b;
    border:1px solid #293750;
    padding:20px;
    border-radius:15px;
    margin-bottom:20px;
}

.actions {
    display:flex;
    gap:9px;
    flex-wrap:wrap;
    margin-top:15px;
}

.file {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:12px;
    padding:13px;
    border-bottom:1px solid #293750;
    flex-wrap:wrap;
}

.file-name {
    overflow-wrap:anywhere;
    flex:1;
}

.preview {
    max-width:100%;
    max-height:75vh;
    display:block;
    margin:auto;
}

video {
    width:100%;
    max-height:75vh;
}

audio {
    width:100%;
}

.progress {
    background:#293750;
    height:8px;
    border-radius:20px;
    overflow:hidden;
    margin-top:12px;
}

.progress-bar {
    height:100%;
    width:0;
    background:#45d6bd;
}

.notice {
    background:#183b39;
    padding:13px;
    border-radius:10px;
    margin-bottom:14px;
}

@media(max-width:600px) {
    .container {padding:14px}
    .hero h1 {font-size:28px}
}
</style>
"""


def render(name, template, title="ArchiveVault", **context):

    body = render_template_string(template, **context)

    layout = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{ title }}</title>
        <meta name="viewport" content="width=device-width,initial-scale=1">
        """ + STYLE + """
    </head>
    <body>
    <div class="container">

        <header class="header">
            <a class="logo" href="/">☁ ArchiveVault</a>

            {% if session.get('logged_in') %}
            <a class="button secondary" href="/logout">Sign out</a>
            {% endif %}
        </header>

        {% with messages=get_flashed_messages() %}
            {% for message in messages %}
                <div class="notice">{{ message }}</div>
            {% endfor %}
        {% endwith %}

        {{ body|safe }}

    </div>
    </body>
    </html>
    """

    return render_template_string(
        layout,
        title=title,
        body=body
    )


# ============================================================
# PAGE TEMPLATES
# ============================================================

LOGIN_HTML = """
<div class="hero">
    <h1>Welcome to ArchiveVault</h1>
    <p class="muted">Your personal project storage library.</p>
</div>

<div class="panel">
    <form method="POST">
        <label>Password</label>
        <input type="password" name="password" required>
        <button>Sign in</button>
    </form>
</div>
"""


HOME_HTML = """
<div class="hero">
    <h1>Your projects. Your files.</h1>

    <p class="muted">
        Organize and store your files using Internet Archive.
        Upload any file type and download your projects whenever
        you need them.
    </p>

    <div class="actions">
        <a class="button" href="/new">＋ New project</a>
    </div>
</div>

<input
    id="search"
    placeholder="Search your projects..."
    oninput="filterProjects()"
>

<div class="grid">

{% for p in projects %}

<div class="card project"
     data-search="{{ (p.title+' '+p.identifier+' '+p.tags)|lower }}">

    <div class="muted">{{ p.category }}</div>

    <h3>{{ p.title }}</h3>

    <p class="muted">{{ p.description }}</p>

    <div class="muted">{{ p.identifier }}</div>

    <div class="actions">
        <a class="button" href="/project/{{ p.identifier }}">
            Open project →
        </a>
    </div>

</div>

{% endfor %}

</div>

<script>
function filterProjects() {

    const q = document.getElementById("search").value.toLowerCase();

    document.querySelectorAll(".project").forEach(card => {

        card.style.display =
            card.dataset.search.includes(q) ? "" : "none";

    });
}
</script>
"""


NEW_HTML = """
<div class="hero">
    <h1>Create a project</h1>
    <p class="muted">Create a new storage container.</p>
</div>

<div class="panel">

<form method="POST">

    <label>Project name</label>
    <input name="title" required placeholder="My project">

    <label>Internet Archive identifier</label>
    <input name="identifier" required placeholder="my-project-2026">

    <label>Category</label>
    <select name="category">
        <option>Software</option>
        <option>Games</option>
        <option>Videos</option>
        <option>Music</option>
        <option>Documents</option>
        <option>Images</option>
        <option>Other</option>
    </select>

    <label>Description</label>
    <textarea name="description"></textarea>

    <label>Tags</label>
    <input name="tags" placeholder="games, videos, projects">

    <button>Create project</button>

</form>

</div>
"""


PROJECT_HTML = """
<div class="hero">

    <div class="muted">{{ project.category }}</div>

    <h1>{{ project.title }}</h1>

    <p class="muted">{{ project.description }}</p>

    <div class="muted">
        Internet Archive: {{ project.identifier }}
    </div>

    <div class="actions">
        <a class="button" href="#upload">＋ Upload files</a>

        <a class="button secondary"
           href="/project/{{ project.identifier }}/zip">
            Download entire project
        </a>

        <a class="button secondary"
           target="_blank"
           href="https://archive.org/details/{{ project.identifier }}">
            View Archive ↗
        </a>
    </div>

</div>

<div class="panel" id="upload">

    <h2>Upload anything</h2>

    <p class="muted">
        All file extensions are accepted. Select multiple files
        or choose a folder. Folder paths are preserved.
    </p>

    <form id="uploadForm">

        <input
            type="file"
            id="files"
            name="files"
            multiple
            webkitdirectory
        >

        <div class="actions">
            <button type="submit">Upload files</button>
        </div>

        <div class="progress">
            <div class="progress-bar" id="bar"></div>
        </div>

        <p id="status" class="muted"></p>

    </form>

</div>

<div class="panel">

    <h2>Project files ({{ files|length }})</h2>

    <input
        id="fileSearch"
        placeholder="Search files..."
        oninput="filterFiles()"
    >

    {% for f in files %}

    <div class="file" data-name="{{ f.name|lower }}">

        <div class="file-name">
            📄 {{ f.name }}
        </div>

        <span class="muted">
            {{ (f.size/1048576)|round(2) }} MB
        </span>

        <div class="actions">

            <a class="button secondary"
               href="/project/{{ project.identifier }}/view/{{ f.name|urlencode }}">
                Preview
            </a>

            <a class="button"
               href="/project/{{ project.identifier }}/download/{{ f.name|urlencode }}">
                Download
            </a>

        </div>

    </div>

    {% endfor %}

    {% if not files %}
        <p class="muted">No files uploaded yet.</p>
    {% endif %}

</div>

<script>

const form = document.getElementById("uploadForm");

form.addEventListener("submit", function(e) {

    e.preventDefault();

    const selected = document.getElementById("files").files;

    if (!selected.length) {
        document.getElementById("status").textContent =
            "Choose some files first.";
        return;
    }

    const data = new FormData();

    for (const file of selected) {

        data.append(
            "files",
            file,
            file.webkitRelativePath || file.name
        );

    }

    const xhr = new XMLHttpRequest();

    xhr.open("POST", location.pathname + "/upload");

    xhr.upload.onprogress = function(e) {

        if (e.lengthComputable) {

            const percent = Math.round(
                e.loaded / e.total * 100
            );

            document.getElementById("bar").style.width =
                percent + "%";

            document.getElementById("status").textContent =
                "Uploading: " + percent + "%";

        }

    };

    xhr.onload = function() {

        let result = {};

        try {
            result = JSON.parse(xhr.responseText);
        } catch(e) {}

        document.getElementById("status").textContent =
            result.message || "Upload finished.";

        if (xhr.status >= 200 && xhr.status < 300) {
            setTimeout(() => location.reload(), 1500);
        }

    };

    xhr.onerror = function() {

        document.getElementById("status").textContent =
            "Upload failed. Check your connection and server logs.";

    };

    xhr.send(data);

});


function filterFiles() {

    const q = document.getElementById("fileSearch")
        .value.toLowerCase();

    document.querySelectorAll(".file").forEach(file => {

        file.style.display =
            file.dataset.name.includes(q) ? "" : "none";

    });

}

</script>
"""


VIEW_HTML = """
<div class="actions">
    <a class="button secondary"
       href="/project/{{ project.identifier }}">
        ← Back
    </a>

    <a class="button"
       href="/project/{{ project.identifier }}/download/{{ name|urlencode }}">
        Download original
    </a>
</div>

<div class="hero">
    <h1>{{ name }}</h1>
    <p class="muted">{{ mime }}</p>
</div>

<div class="panel">

{% if kind == "image" %}

    <img class="preview" src="{{ stream_url }}">

{% elif kind == "video" %}

    <video controls preload="metadata">
        <source src="{{ stream_url }}" type="{{ mime }}">
        Your browser cannot play this video format.
    </video>

{% elif kind == "audio" %}

    <audio controls preload="metadata">
        <source src="{{ stream_url }}" type="{{ mime }}">
    </audio>

{% elif kind == "pdf" %}

    <iframe
        src="{{ stream_url }}"
        style="width:100%;height:75vh;background:white;border:0">
    </iframe>

{% elif kind == "text" %}

    <form method="POST"
          action="/project/{{ project.identifier }}/edit/{{ name|urlencode }}">

        <textarea
            name="content"
            style="min-height:450px;font-family:monospace"
        >{{ content }}</textarea>

        <button>Save changes</button>

    </form>

{% else %}

    <h2>Preview unavailable</h2>

    <p class="muted">
        This file is supported for downloading, but this browser
        cannot display its contents.
    </p>

    <a class="button"
       href="/project/{{ project.identifier }}/download/{{ name|urlencode }}">
        Download file
    </a>

{% endif %}

</div>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
@login_required
def home():

    with connect_database() as con:

        projects = con.execute(
            "SELECT * FROM projects ORDER BY created_at DESC"
        ).fetchall()

    return render(
        "home.html",
        HOME_HTML,
        projects=projects
    )


# ============================================================
# CREATE PROJECT
# ============================================================

@app.route("/new", methods=["GET", "POST"])
@login_required
def new_project():

    if request.method == "POST":

        try:

            title = request.form.get("title", "").strip()

            identifier = clean_identifier(
                request.form.get("identifier")
            )

            if not title:
                raise ValueError("Project title is required.")

            with connect_database() as con:

                con.execute("""
                    INSERT INTO projects
                    (identifier,title,description,category,tags)
                    VALUES (?,?,?,?,?)
                """, (
                    identifier,
                    title,
                    request.form.get("description", ""),
                    request.form.get("category", "Other"),
                    request.form.get("tags", "")
                ))

            flash("Project created successfully.")

            return redirect(
                url_for("project", identifier=identifier)
            )

        except sqlite3.IntegrityError:

            flash("That identifier is already in your library.")

        except Exception as e:

            flash(str(e))

    return render("new.html", NEW_HTML)


# ============================================================
# PROJECT PAGE
# ============================================================

@app.route("/project/<identifier>")
@login_required
def project(identifier):

    p = get_project(identifier)

    try:

        files = get_archive_files(identifier)

    except Exception as e:

        files = []

        flash("Archive listing error: " + str(e))

    return render(
        "project.html",
        PROJECT_HTML,
        project=p,
        files=files
    )


# ============================================================
# UPLOAD ANY FILE TYPE
# ============================================================

@app.route("/project/<identifier>/upload", methods=["POST"])
@login_required
def upload(identifier):

    project_data = get_project(identifier)

    check_archive_credentials()

    uploads = request.files.getlist("files")

    if not uploads:
        return jsonify({
            "message": "No files selected."
        }), 400

    uploaded = 0
    errors = []

    for file in uploads:

        if not file.filename:
            continue

        local_path = None

        try:

            # Preserve folder paths from browser folder selection.
            remote_name = clean_remote_path(file.filename)

            suffix = os.path.splitext(remote_name)[1]

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            ) as temp:

                file.save(temp.name)
                local_path = temp.name

            upload_archive_file(
                identifier,
                remote_name,
                local_path,
                project_data
            )

            uploaded += 1

        except Exception as e:

            errors.append(
                f"{file.filename}: {str(e)}"
            )

        finally:

            if local_path and os.path.exists(local_path):
                os.unlink(local_path)

    message = f"Uploaded {uploaded} file(s)."

    if errors:
        message += " Some uploads failed: " + " | ".join(errors[:4])

    return jsonify({
        "message": message,
        "uploaded": uploaded,
        "errors": errors
    }), 207 if errors else 200


# ============================================================
# STREAM / DOWNLOAD PROXY
# Supports HTTP Range requests when the upstream supports them.
# ============================================================

def archive_response(identifier, name, inline=False):

    url = (
        f"https://archive.org/download/"
        f"{quote(identifier, safe='')}/"
        f"{quote(name, safe='/')}"
    )

    headers = {}

    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]

    upstream = requests.get(
        url,
        headers=headers,
        stream=True,
        timeout=(20, 180)
    )

    if upstream.status_code not in (200, 206):

        status = upstream.status_code
        upstream.close()

        abort(status)

    mime = (
        upstream.headers.get("Content-Type")
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )

    disposition = "inline" if inline else "attachment"

    safe_name = secure_filename(os.path.basename(name)) or "download"

    response = Response(
        upstream.iter_content(chunk_size=256 * 1024),
        status=upstream.status_code,
        content_type=mime,
        direct_passthrough=True
    )

    response.headers["Content-Disposition"] = (
        f'{disposition}; filename="{safe_name}"'
    )

    for header in (
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "ETag",
        "Last-Modified"
    ):

        if header in upstream.headers:
            response.headers[header] = upstream.headers[header]

    response.call_on_close(upstream.close)

    return response


@app.route("/project/<identifier>/stream/<path:name>")
@login_required
def stream_file(identifier, name):

    get_project(identifier)

    return archive_response(identifier, name, inline=True)


@app.route("/project/<identifier>/download/<path:name>")
@login_required
def download_file(identifier, name):

    get_project(identifier)

    return archive_response(identifier, name, inline=False)


# ============================================================
# FILE PREVIEW AND EDITOR
# ============================================================

@app.route("/project/<identifier>/view/<path:name>")
@login_required
def view_file(identifier, name):

    p = get_project(identifier)

    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"

    ext = os.path.splitext(name)[1].lower()

    if mime.startswith("image/"):
        kind = "image"

    elif mime.startswith("video/"):
        kind = "video"

    elif mime.startswith("audio/"):
        kind = "audio"

    elif ext == ".pdf":
        kind = "pdf"

    elif ext in (
        ".txt", ".md", ".py", ".js", ".css",
        ".html", ".htm", ".json", ".xml",
        ".yml", ".yaml", ".csv", ".toml",
        ".ini", ".sql", ".sh", ".ps1",
        ".java", ".c", ".cpp", ".h",
        ".rs", ".go"
    ):
        kind = "text"

    else:
        kind = "other"

    content = ""

    if kind == "text":

        url = (
            f"https://archive.org/download/"
            f"{quote(identifier, safe='')}/"
            f"{quote(name, safe='/')}"
        )

        response = requests.get(url, timeout=60)
        response.raise_for_status()

        content = response.content[:2_000_000].decode(
            "utf-8",
            errors="replace"
        )

    return render(
        "view.html",
        VIEW_HTML,
        title=name,
        project=p,
        name=name,
        mime=mime,
        kind=kind,
        content=content,
        stream_url=url_for(
            "stream_file",
            identifier=identifier,
            name=name
        )
    )


# ============================================================
# EDIT TEXT FILES
# ============================================================

@app.route(
    "/project/<identifier>/edit/<path:name>",
    methods=["POST"]
)
@login_required
def edit_file(identifier, name):

    p = get_project(identifier)

    check_archive_credentials()

    content = request.form.get("content", "")

    if len(content.encode("utf-8")) > 2_000_000:

        flash("Text editor limit is 2 MB.")

        return redirect(
            url_for(
                "view_file",
                identifier=identifier,
                name=name
            )
        )

    local_path = None

    try:

        with tempfile.NamedTemporaryFile(
            delete=False,
            mode="w",
            encoding="utf-8"
        ) as temp:

            temp.write(content)
            local_path = temp.name

        upload_archive_file(
            identifier,
            name,
            local_path,
            p
        )

        flash("File updated successfully.")

    except Exception as e:

        flash("Save failed: " + str(e))

    finally:

        if local_path and os.path.exists(local_path):
            os.unlink(local_path)

    return redirect(
        url_for(
            "view_file",
            identifier=identifier,
            name=name
        )
    )


# ============================================================
# DOWNLOAD ENTIRE PROJECT AS ZIP
# ============================================================

@app.route("/project/<identifier>/zip")
@login_required
def project_zip(identifier):

    get_project(identifier)

    files = get_archive_files(identifier)

    if not files:

        flash("There are no files to download.")

        return redirect(
            url_for("project", identifier=identifier)
        )

    temp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".zip"
    )

    zip_path = temp.name
    temp.close()

    try:

        with zipfile.ZipFile(
            zip_path,
            "w",
            zipfile.ZIP_DEFLATED
        ) as archive:

            for file in files:

                remote_name = file["name"]

                url = (
                    f"https://archive.org/download/"
                    f"{quote(identifier, safe='')}/"
                    f"{quote(remote_name, safe='/')}"
                )

                response = requests.get(
                    url,
                    stream=True,
                    timeout=(20, 180)
                )

                response.raise_for_status()

                with archive.open(remote_name, "w") as output:

                    for chunk in response.iter_content(
                        chunk_size=256 * 1024
                    ):

                        if chunk:
                            output.write(chunk)

                response.close()

        response = send_file(
            zip_path,
            as_attachment=True,
            download_name=f"{identifier}.zip",
            mimetype="application/zip"
        )

        response.call_on_close(
            lambda: os.path.exists(zip_path) and os.unlink(zip_path)
        )

        return response

    except Exception:

        if os.path.exists(zip_path):
            os.unlink(zip_path)

        raise


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    try:

        with connect_database() as con:
            con.execute("SELECT 1")

        return jsonify({
            "status": "healthy",
            "application": APP_NAME,
            "database": "connected"
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(413)
def upload_too_large(error):

    return (
        "Upload exceeds MAX_UPLOAD_MB or the hosting platform's "
        "request-size limit.",
        413
    )


@app.errorhandler(503)
def unavailable(error):

    return str(error.description), 503


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT
    )
