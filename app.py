
import os, re, io, json, sqlite3, tempfile, zipfile, mimetypes
from pathlib import PurePosixPath
from functools import wraps
from urllib.parse import quote
from flask import Flask, request, redirect, url_for, session, flash, Response, render_template_string, abort, send_file
import requests
import internetarchive as ia
from werkzeug.security import safe_join

APP_NAME = "ArchiveVault"
DB_PATH = os.environ.get("DB_PATH", "archivevault.db")
IA_ACCESS_KEY = os.environ.get("IA_ACCESS_KEY", "")
IA_SECRET_KEY = os.environ.get("IA_SECRET_KEY", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-before-deploying")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'Other',
            tags TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
init_db()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def require_ia():
    if not IA_ACCESS_KEY or not IA_SECRET_KEY:
        abort(503, "Internet Archive credentials are not configured in Blitz.")

def clean_identifier(value):
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip().lower()).strip("-_")
    if not 5 <= len(value) <= 80:
        raise ValueError("The Internet Archive identifier must be 5–80 characters.")
    return value

def clean_remote_path(value):
    value = (value or "file").replace("\\", "/").strip("/")
    parts = []
    for part in value.split("/"):
        part = part.strip()
        if not part or part in (".", ".."):
            continue
        part = re.sub(r"[^A-Za-z0-9._ ()\-\[\]]+", "_", part)
        if part:
            parts.append(part[:180])
    if not parts:
        raise ValueError("Invalid filename.")
    return "/".join(parts)

def get_project(identifier):
    with db() as con:
        row = con.execute("SELECT * FROM projects WHERE identifier=?", (identifier,)).fetchone()
    if not row:
        abort(404)
    return row

def archive_metadata(identifier):
    r = requests.get(f"https://archive.org/metadata/{quote(identifier)}", timeout=30)
    if r.status_code == 404:
        return {"files": []}
    r.raise_for_status()
    return r.json()

def file_entries(identifier):
    data = archive_metadata(identifier)
    result = []
    for f in data.get("files", []):
        name = f.get("name")
        if not name or name in ("_meta.xml", "_files.xml"):
            continue
        if f.get("source") == "metadata":
            continue
        result.append({
            "name": name,
            "size": int(f.get("size", 0) or 0),
            "format": f.get("format", ""),
            "mtime": f.get("mtime", "")
        })
    return sorted(result, key=lambda x: x["name"].lower())

def upload_to_archive(identifier, remote_name, local_path, title, description):
    require_ia()
    item = ia.get_item(identifier)
    metadata = {
        "mediatype": "data",
        "title": title,
        "description": description or f"Project files managed with {APP_NAME}."
    }
    # The Internet Archive Python client handles IA-S3 authentication and retries.
    return item.upload(
        {remote_name: local_path},
        metadata=metadata,
        access_key=IA_ACCESS_KEY,
        secret_key=IA_SECRET_KEY,
        retries=3
    )

BASE = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · ArchiveVault</title>
<style>
:root{color-scheme:dark;--bg:#090d18;--panel:#111827;--panel2:#182235;--line:#29364c;--muted:#9aa9bf;--text:#edf3ff;--accent:#8b7cff;--teal:#45d6bd}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#1d2443 0,transparent 38%),var(--bg);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui,Segoe UI,sans-serif}
a{color:inherit;text-decoration:none}button,.btn{border:0;border-radius:10px;padding:10px 14px;background:var(--accent);color:white;font-weight:700;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:8px}
button.secondary,.btn.secondary{background:#26344a;color:var(--text)}button.danger{background:#743c54}
input,textarea,select{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:#0b1220;color:var(--text);font:inherit}
textarea{min-height:100px;resize:vertical}label{display:block;color:#c7d2e4;font-size:13px;margin:12px 0 6px}
.shell{max-width:1250px;margin:auto;padding:24px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:26px}
.brand{display:flex;align-items:center;gap:12px;font-size:20px;font-weight:850;letter-spacing:-.5px}.logo{width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--teal));display:grid;place-items:center;font-size:20px}
.muted,.sub{color:var(--muted)}.hero{padding:26px;border:1px solid var(--line);border-radius:20px;background:linear-gradient(120deg,#171f37,#111827);margin-bottom:20px}
.hero h1{font-size:clamp(26px,4vw,38px);line-height:1.1;margin:0 0 8px}.hero p{margin:0;color:var(--muted);max-width:700px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(245px,1fr));gap:14px}.card{border:1px solid var(--line);background:rgba(17,24,39,.92);border-radius:16px;padding:17px;min-width:0}
.card h3{margin:7px 0 4px;font-size:17px;overflow-wrap:anywhere}.card p{color:var(--muted);margin:0 0 14px;font-size:13px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.between{justify-content:space-between}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:18px 0}.toolbar input{flex:1;min-width:180px}
.section{margin:24px 0}.section h2{font-size:19px;margin:0 0 12px}.file{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:12px 14px;border-bottom:1px solid var(--line)}.file:first-child{border-top:1px solid var(--line)}
.file-name{overflow-wrap:anywhere}.size{color:var(--muted);font-size:12px;white-space:nowrap}.pill{font-size:11px;padding:4px 8px;border-radius:99px;background:#24334a;color:#b9c9df}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px}.formgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.wide{grid-column:1/-1}
.notice{padding:12px 14px;border:1px solid #385b67;background:#112b32;border-radius:12px;color:#b8f4e8;margin:12px 0}.error{border-color:#71445a;background:#321b2a;color:#ffd5e2}
.empty{text-align:center;padding:40px 15px;color:var(--muted)}.preview{width:100%;max-height:70vh;object-fit:contain;background:#05070c;border-radius:12px}
pre,code{white-space:pre-wrap;overflow-wrap:anywhere}.editor{min-height:360px;font-family:ui-monospace,Consolas,monospace}
.progress{height:7px;background:#26344a;border-radius:99px;overflow:hidden;margin-top:12px}.progress i{display:block;height:100%;width:0;background:var(--teal)}
@media(max-width:650px){.shell{padding:15px}.formgrid{grid-template-columns:1fr}.wide{grid-column:auto}.file{grid-template-columns:1fr auto}.file .size{grid-column:1}.top{align-items:flex-start}}
</style></head><body><div class="shell">
<header class="top"><a class="brand" href="/"><span class="logo">▣</span><span>ArchiveVault</span></a>
<div class="row">{% if session.get('logged_in') %}<a class="btn secondary" href="/">Library</a><a class="btn secondary" href="/logout">Sign out</a>{% endif %}</div></header>
{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="notice {{ 'error' if category=='error' else '' }}">{{ message }}</div>{% endfor %}{% endwith %}
{{ body|safe }}
<footer class="muted" style="padding:28px 0 10px;font-size:12px">ArchiveVault · Files are stored by Internet Archive. Only upload material you have permission to share.</footer>
</div></body></html>
"""

def page(body, title="Your library", **context):
    inner = render_template_string(body, **context)
    return render_template_string(BASE, body=inner, title=title)

LOGIN = r"""
<div class="hero"><h1>Your files, in one place.</h1><p>Sign in to your personal ArchiveVault workspace.</p></div>
<div class="panel" style="max-width:440px;margin:40px auto"><h2>Welcome back</h2><form method="post"><label>Workspace password</label><input type="password" name="password" autofocus required><div class="actions"><button>Sign in</button></div></form></div>
"""

HOME = r"""
<section class="hero"><div class="row between"><div><span class="pill">PERSONAL ARCHIVE</span><h1 style="margin-top:12px">Your projects.<br>One organized library.</h1><p>Store project files on Internet Archive, then browse, preview, stream and download them here.</p></div><div style="font-size:54px">☁️</div></div>
<div class="actions"><a class="btn" href="/new">＋ New project</a></div></section>
<div class="toolbar"><input id="search" placeholder="Search projects..." oninput="filterCards()"><span class="pill">{{ projects|length }} projects</span></div>
<div class="grid" id="projects">{% for p in projects %}<article class="card project" data-search="{{ (p.title+' '+p.description+' '+p.tags+' '+p.identifier)|lower }}"><div class="row between"><span class="pill">{{ p.category }}</span><span class="muted" style="font-size:11px">IA ITEM</span></div><h3>{{ p.title }}</h3><p>{{ p.description or 'No description yet.' }}</p><div class="muted" style="font-size:12px;overflow-wrap:anywhere">{{ p.identifier }}</div><div class="actions"><a class="btn" href="/project/{{ p.identifier }}">Open project →</a></div></article>{% endfor %}</div>
{% if not projects %}<div class="panel empty"><div style="font-size:40px">🗂️</div><h3>No projects yet</h3><p>Create your first project to start organizing files.</p><a class="btn" href="/new">Create a project</a></div>{% endif %}
<script>function filterCards(){let q=document.getElementById('search').value.toLowerCase();document.querySelectorAll('.project').forEach(x=>x.style.display=x.dataset.search.includes(q)?'':'none')}</script>
"""

NEW = r"""
<div class="hero"><h1>Create a project</h1><p>A project becomes an Internet Archive item. Files you upload will be stored under its identifier.</p></div>
<form class="panel" method="post"><div class="formgrid">
<div><label>Project name</label><input name="title" placeholder="My game project" required></div>
<div><label>Archive identifier</label><input name="identifier" placeholder="my-game-project" required pattern="[A-Za-z0-9_-]{5,80}"><div class="muted" style="font-size:11px">Must be unique on Internet Archive, 5–80 characters.</div></div>
<div><label>Category</label><select name="category"><option>Software</option><option>Games</option><option>Media</option><option>Documents</option><option>Website</option><option>Other</option></select></div>
<div><label>Tags</label><input name="tags" placeholder="python, game, source"></div>
<div class="wide"><label>Description</label><textarea name="description" placeholder="What is this project?"></textarea></div></div><div class="actions"><button>Create project</button><a class="btn secondary" href="/">Cancel</a></div></form>
"""

PROJECT = r"""
<div class="hero"><div class="row between"><div><span class="pill">{{ project.category }}</span><h1 style="margin-top:10px">{{ project.title }}</h1><p>{{ project.description }}</p><div class="muted" style="font-size:12px">archive.org/details/{{ project.identifier }}</div></div><div style="font-size:48px">📁</div></div>
<div class="actions"><a class="btn" href="#upload">＋ Upload files</a><a class="btn secondary" href="/project/{{ project.identifier }}/zip">Download project ZIP</a><a class="btn secondary" target="_blank" href="https://archive.org/details/{{ project.identifier }}">Open Internet Archive ↗</a></div></div>
<div class="panel" id="upload"><h2>Upload to this project</h2><form id="uploadForm" enctype="multipart/form-data"><input type="file" name="files" id="files" multiple webkitdirectory directory><div class="muted" style="font-size:12px;margin-top:7px">Choose files, or select a folder to preserve its folder structure. Large uploads may take time.</div><div class="actions"><button type="submit">Upload selected files</button></div><div class="progress"><i id="bar"></i></div><div id="status" class="muted" style="font-size:12px;margin-top:6px"></div></form></div>
<div class="section"><div class="row between"><h2>Files <span class="muted">({{ files|length }})</span></h2><input id="fileSearch" style="max-width:280px" placeholder="Filter files..." oninput="filterFiles()"></div>
<div class="panel" id="fileList">{% for f in files %}<div class="file" data-name="{{ f.name|lower }}"><div class="file-name">📄 {{ f.name }}</div><span class="size">{{ (f.size/1024/1024)|round(2) }} MB</span><div class="row"><a class="btn secondary" href="/project/{{ project.identifier }}/view/{{ f.name|urlencode }}">Preview</a><a class="btn secondary" href="/project/{{ project.identifier }}/download/{{ f.name|urlencode }}">Download</a></div></div>{% endfor %}{% if not files %}<div class="empty">This project has no uploaded files yet.</div>{% endif %}</div></div>
<script>
const form=document.getElementById('uploadForm');form.addEventListener('submit',e=>{e.preventDefault();const files=document.getElementById('files').files;if(!files.length){document.getElementById('status').textContent='Choose at least one file.';return}
const fd=new FormData();for(const f of files){fd.append('files',f,f.webkitRelativePath||f.name)}
const xhr=new XMLHttpRequest();xhr.open('POST',location.pathname+'/upload');xhr.upload.onprogress=e=>{if(e.lengthComputable){let n=Math.round(e.loaded/e.total*100);document.getElementById('bar').style.width=n+'%';document.getElementById('status').textContent='Sending files: '+n+'%'}}
xhr.onload=()=>{let d={};try{d=JSON.parse(xhr.responseText)}catch(e){};document.getElementById('status').textContent=d.message||'Upload finished.';if(xhr.status>=200&&xhr.status<300)setTimeout(()=>location.reload(),1200)}
xhr.onerror=()=>document.getElementById('status').textContent='Upload connection failed.';xhr.send(fd)});
function filterFiles(){let q=document.getElementById('fileSearch').value.toLowerCase();document.querySelectorAll('.file').forEach(x=>x.style.display=x.dataset.name.includes(q)?'':'none')}
</script>
"""

VIEW = r"""
<div class="row between"><div><a class="muted" href="/project/{{ project.identifier }}">← Back to project</a><h1 style="overflow-wrap:anywhere">{{ name }}</h1></div><a class="btn" href="/project/{{ project.identifier }}/download/{{ name|urlencode }}">Download</a></div>
<div class="panel">
{% if kind=='image' %}<img class="preview" src="{{ stream_url }}">
{% elif kind=='video' %}<video class="preview" controls preload="metadata" src="{{ stream_url }}"></video>
{% elif kind=='audio' %}<audio controls style="width:100%" src="{{ stream_url }}"></audio>
{% elif kind=='pdf' %}<iframe src="{{ stream_url }}" style="width:100%;height:75vh;border:0;background:white;border-radius:10px"></iframe>
{% elif kind=='text' %}<form method="post" action="/project/{{ project.identifier }}/edit/{{ name|urlencode }}"><label>Text editor</label><textarea class="editor" name="content" spellcheck="false">{{ content }}</textarea><div class="actions"><button>Save changes to Internet Archive</button></div></form>
{% else %}<div class="empty"><div style="font-size:42px">📦</div><h3>Preview isn't available for this file type</h3><p>You can still download the original file.</p><a class="btn" href="/project/{{ project.identifier }}/download/{{ name|urlencode }}">Download file</a></div>{% endif %}
</div>
"""

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        if APP_PASSWORD and request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("home"))
        flash("Incorrect password, or APP_PASSWORD is not configured.", "error")
    return page(LOGIN, "Sign in")

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
@login_required
def home():
    with db() as con:
        projects = con.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return page(HOME, projects=projects)

@app.route("/new", methods=["GET","POST"])
@login_required
def new_project():
    if request.method == "POST":
        try:
            identifier = clean_identifier(request.form.get("identifier"))
            title = (request.form.get("title") or "").strip()
            if not title: raise ValueError("Project name is required.")
            with db() as con:
                con.execute("INSERT INTO projects(identifier,title,description,category,tags) VALUES(?,?,?,?,?)",
                    (identifier,title,request.form.get("description",""),request.form.get("category","Other"),request.form.get("tags","")))
            flash("Project created. Upload files to create its Internet Archive item.")
            return redirect(url_for("project", identifier=identifier))
        except sqlite3.IntegrityError:
            flash("That identifier is already in your library.", "error")
        except Exception as e:
            flash(str(e), "error")
    return page(NEW, "New project")

@app.get("/project/<identifier>")
@login_required
def project(identifier):
    p = get_project(identifier)
    try: files = file_entries(identifier)
    except Exception as e:
        files=[]; flash(f"Could not read Internet Archive files: {e}", "error")
    return page(PROJECT, p.title, project=p, files=files)

@app.post("/project/<identifier>/upload")
@login_required
def upload(identifier):
    p = get_project(identifier)
    require_ia()
    uploads = request.files.getlist("files")
    if not uploads: return {"message":"No files selected."}, 400
    done, errors = 0, []
    for f in uploads:
        if not f.filename: continue
        try:
            remote = clean_remote_path(f.filename)
            suffix = os.path.splitext(remote)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                f.save(tmp.name); path=tmp.name
            try:
                upload_to_archive(identifier, remote, path, p["title"], p["description"])
                done += 1
            finally:
                os.unlink(path)
        except Exception as e:
            errors.append(f"{f.filename}: {e}")
    msg=f"Uploaded {done} file(s)."
    if errors: msg += " Some files failed: " + " | ".join(errors[:3])
    return {"message":msg, "uploaded":done, "errors":errors}, (207 if errors else 200)

def archive_file_response(identifier, name, inline=False):
    url=f"https://archive.org/download/{quote(identifier)}/{quote(name, safe='/')}"
    headers={}
    if request.headers.get("Range"): headers["Range"]=request.headers["Range"]
    upstream=requests.get(url,headers=headers,stream=True,timeout=(20,120))
    if upstream.status_code not in (200,206):
        abort(upstream.status_code)
    content_type=upstream.headers.get("Content-Type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    disposition="inline" if inline else "attachment"
    response=Response(upstream.iter_content(chunk_size=1024*256),status=upstream.status_code,content_type=content_type,direct_passthrough=True)
    response.headers["Content-Disposition"]=f'{disposition}; filename="{os.path.basename(name).replace(chr(34), "")}"'
    for h in ("Content-Length","Content-Range","Accept-Ranges","ETag","Last-Modified"):
        if h in upstream.headers: response.headers[h]=upstream.headers[h]
    response.call_on_close(upstream.close)
    return response

@app.get("/project/<identifier>/view/<path:name>")
@login_required
def view_file(identifier,name):
    p=get_project(identifier)
    ext=os.path.splitext(name)[1].lower()
    mime=mimetypes.guess_type(name)[0] or ""
    if mime.startswith("image/"): kind="image"
    elif mime.startswith("video/"): kind="video"
    elif mime.startswith("audio/"): kind="audio"
    elif ext==".pdf": kind="pdf"
    elif ext in (".txt",".md",".py",".js",".css",".html",".htm",".json",".xml",".yml",".yaml",".toml",".ini",".csv",".sh",".ps1",".bat",".sql",".java",".c",".cpp",".h",".rs",".go"): kind="text"
    else: kind="other"
    content=""
    if kind=="text":
        r=requests.get(f"https://archive.org/download/{quote(identifier)}/{quote(name,safe='/')}",timeout=60)
        r.raise_for_status()
        content=r.content[:2_000_000].decode("utf-8",errors="replace")
    return page(VIEW, name, project=p, name=name, kind=kind, content=content, stream_url=url_for("stream_file",identifier=identifier,name=name))

@app.get("/project/<identifier>/stream/<path:name>")
@login_required
def stream_file(identifier,name):
    get_project(identifier)
    return archive_file_response(identifier,name,True)

@app.get("/project/<identifier>/download/<path:name>")
@login_required
def download_file(identifier,name):
    get_project(identifier)
    return archive_file_response(identifier,name,False)

@app.post("/project/<identifier>/edit/<path:name>")
@login_required
def edit_file(identifier,name):
    p=get_project(identifier); require_ia()
    content=request.form.get("content","")
    if len(content.encode("utf-8")) > 2_000_000:
        flash("Text editor is limited to 2 MB. Download and edit larger files locally.", "error")
        return redirect(url_for("view_file",identifier=identifier,name=name))
    suffix=os.path.splitext(name)[1]
    with tempfile.NamedTemporaryFile(delete=False,mode="w",encoding="utf-8",newline="") as tmp:
        tmp.write(content); path=tmp.name
    try:
        upload_to_archive(identifier,name,path,p["title"],p["description"])
        flash("Saved. Internet Archive may take a short time to reflect the updated file.")
    except Exception as e:
        flash(f"Could not save file: {e}", "error")
    finally:
        os.unlink(path)
    return redirect(url_for("view_file",identifier=identifier,name=name))

@app.get("/project/<identifier>/zip")
@login_required
def project_zip(identifier):
    p=get_project(identifier)
    files=file_entries(identifier)
    if not files: flash("This project has no files to download.", "error"); return redirect(url_for("project",identifier=identifier))
    tmp=tempfile.NamedTemporaryFile(delete=False,suffix=".zip"); tmp.close()
    try:
        with zipfile.ZipFile(tmp.name,"w",zipfile.ZIP_DEFLATED) as z:
            for f in files:
                r=requests.get(f"https://archive.org/download/{quote(identifier)}/{quote(f['name'],safe='/')}",stream=True,timeout=(20,180))
                r.raise_for_status()
                with z.open(f["name"],"w") as out:
                    for chunk in r.iter_content(1024*256):
                        if chunk: out.write(chunk)
        response = send_file(tmp.name,as_attachment=True,download_name=f"{identifier}.zip",mimetype="application/zip")
        response.call_on_close(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        return response
    except Exception:
        try: os.unlink(tmp.name)
        except OSError: pass
        raise


@app.get("/health")
def health():
    return {"ok":True,"app":"ArchiveVault"}

@app.errorhandler(413)
def too_large(_):
    return "Upload exceeds MAX_UPLOAD_MB. Increase that Blitz environment variable if needed.",413

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","8080")))
