# Run:  python dropui.py        Then open:  http://localhost:5000
# Stdlib only (Flask is not a dependency of this project). Files land in ./challenge/ next to this script.
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
CHALLENGE = ROOT / "challenge"
MAX_UPLOAD = 100 * 1024 * 1024
RESERVED = {"task.json", "hint.txt", ".gitkeep"}  # written by the form, not by file drops
IGNORED = RESERVED | {"base64_warmup.txt"}  # never copied to the sandbox or wiped (sanity-test fixture)
CONTAINER = "ctf-sandbox"  # must match agent/tools.py
WORKDIR = "/workspace"
HINT_SEP = "\n---\n"  # hints live in hint.txt separated by this; must match agent/tools.py


def docker(*args, timeout=30):
    try:
        p = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except FileNotFoundError:
        return 127, "docker not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "docker timed out"
    return p.returncode, (p.stdout + p.stderr).strip()


def sandbox_running():
    code, out = docker("inspect", "-f", "{{.State.Running}}", CONTAINER, timeout=10)
    return code == 0 and out == "true"


def copy_to_sandbox(name):
    """Copy challenge/<name> into the sandbox's /workspace. Returns (ok, message)."""
    if not sandbox_running():
        return False, "sandbox not running"
    code, out = docker("cp", str(CHALLENGE / name), f"{CONTAINER}:{WORKDIR}/{name}")
    return code == 0, out or "copied"


class Job:
    """A background subprocess chain whose output lines can be polled."""

    def __init__(self):
        self.lines, self.code, self.proc, self.thread = [], None, None, None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, cmds, cwd=ROOT, env=None, on_success=None):
        self.lines, self.code = [], None
        self.thread = threading.Thread(target=self._run, args=(cmds, cwd, env, on_success), daemon=True)
        self.thread.start()

    def _run(self, cmds, cwd, env, on_success):
        for argv in cmds:
            self.lines.append("$ " + " ".join(argv))
            try:
                self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                             errors="replace")
            except OSError as e:
                self.lines.append(f"[error: {e}]")
                self.code = 127
                return
            for line in self.proc.stdout:
                self.lines.append(line.rstrip("\n"))
            self.code = self.proc.wait()
            if self.code != 0:
                return
        if on_success:
            on_success(self.lines)

    def stop(self):
        if self.running and self.proc:
            self.proc.terminate()
            self.lines.append("[stopped by user]")


JOBS = {"agent": Job(), "sandbox": Job()}


def sandbox_start_cmds():
    if docker("inspect", CONTAINER, timeout=10)[0] == 0:
        return [["docker", "start", CONTAINER]]
    cmds = []
    if docker("image", "inspect", CONTAINER, timeout=10)[0] != 0:
        cmds.append(["docker", "build", "-t", CONTAINER, str(ROOT)])
    cmds.append(["docker", "run", "-d", "--name", CONTAINER, CONTAINER])
    return cmds

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hacking agent</title>
<style>
  :root {
    --bg: #0b0d0b; --panel: #101310; --line: #223022; --fg: #b8d8b8;
    --dim: #5f7a5f; --accent: #5dde7a; --warn: #e0b84a; --bad: #e0614a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace;
  }
  main { max-width: 760px; margin: 0 auto; padding: 32px 16px 64px; }
  h1 { margin: 0; font-size: 22px; font-weight: 600; color: var(--accent); letter-spacing: .08em; }
  h1::before { content: "$ "; color: var(--dim); }
  .cursor { display: inline-block; width: .6em; height: 1em; background: var(--accent);
            vertical-align: -2px; animation: blink 1.1s steps(1) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .sub { color: var(--dim); margin: 4px 0 28px; }
  section { border: 1px solid var(--line); background: var(--panel); padding: 16px; margin-bottom: 16px; }
  h2 { margin: 0 0 12px; font-size: 12px; font-weight: 400; color: var(--dim);
       text-transform: uppercase; letter-spacing: .15em; }
  h2 b { color: var(--accent); font-weight: 400; margin-right: 8px; }
  label { display: block; color: var(--dim); font-size: 12px; margin: 10px 0 4px; }
  input, textarea, select {
    width: 100%; background: var(--bg); color: var(--fg); border: 1px solid var(--line);
    padding: 8px 10px; font: inherit; border-radius: 0; outline: none;
  }
  input:focus, textarea:focus, select:focus { border-color: var(--accent); }
  textarea { resize: vertical; min-height: 84px; }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  .row > div.narrow { flex: 0 0 120px; }
  button {
    background: transparent; color: var(--accent); border: 1px solid var(--accent);
    padding: 7px 14px; font: inherit; cursor: pointer; border-radius: 0;
  }
  button:hover { background: var(--accent); color: var(--bg); }
  button.quiet { color: var(--dim); border-color: var(--line); }
  button.quiet:hover { color: var(--fg); background: transparent; border-color: var(--dim); }
  #drop {
    border: 1px dashed var(--dim); padding: 34px 16px; text-align: center; color: var(--dim);
    cursor: pointer; transition: border-color .1s, color .1s;
  }
  #drop.over, #drop:hover { border-color: var(--accent); color: var(--accent); }
  #drop small { display: block; margin-top: 4px; font-size: 12px; }
  #file { display: none; }
  .msg { margin-top: 10px; min-height: 1.5em; font-size: 13px; }
  .ok { color: var(--accent); } .err { color: var(--bad); } .warn { color: var(--warn); }
  .confirm { margin-top: 10px; padding: 10px; border: 1px solid var(--warn); color: var(--warn); }
  .confirm button { margin: 8px 8px 0 0; color: var(--warn); border-color: var(--warn); }
  .confirm button:hover { background: var(--warn); color: var(--bg); }
  ul#files { list-style: none; margin: 12px 0 0; padding: 0; font-size: 13px; }
  ul#files li { padding: 2px 0; color: var(--dim); }
  ul#files li::before { content: "> "; color: var(--line); }
  ol#hintList { margin: 12px 0 0; padding-left: 22px; font-size: 13px; color: var(--dim); }
  ol#hintList li { padding: 2px 0; white-space: pre-wrap; word-break: break-word; }
  pre { margin: 0; padding: 10px; background: var(--bg); border: 1px solid var(--line);
        white-space: pre-wrap; word-break: break-all; color: var(--fg);
        max-height: 420px; overflow: auto; font-size: 13px; }
  [hidden] { display: none !important; }
  .actions { margin-top: 12px; display: flex; gap: 8px; align-items: center; }
  footer { color: var(--dim); font-size: 12px; margin-top: 24px; }
  @media (max-width: 520px) { .row { flex-direction: column; } .row > div.narrow { flex: 1; } }
</style>
</head>
<body>
<main>
  <h1>HACKING_AGENT<span class="cursor"></span></h1>
  <p class="sub">feed it a target. it does the rest.</p>

  <section>
    <h2><b>01</b>target</h2>
    <label for="name">ctf / challenge name (used for the log file)</label>
    <input id="name" autocomplete="off" spellcheck="false">
    <div class="row">
      <div><label for="host">host</label><input id="host" autocomplete="off" spellcheck="false"></div>
      <div class="narrow"><label for="port">port</label><input id="port" type="number" min="1" max="65535"></div>
    </div>
    <label for="desc">challenge description</label>
    <textarea id="desc" spellcheck="false"></textarea>
    <div class="row">
      <div><label for="flag">known flag (optional - leave blank if you do not have one)</label><input id="flag" autocomplete="off" spellcheck="false"></div>
      <div class="narrow"><label for="steps">max steps (blank = 20)</label><input id="steps" type="number" min="1"></div>
    </div>
    <label for="model">model (blank = default)</label>
    <input id="model" autocomplete="off" spellcheck="false">
    <div class="actions">
      <button id="saveTask">save task</button>
      <span class="msg" id="taskMsg"></span>
    </div>
  </section>

  <section>
    <h2><b>02</b>files</h2>
    <div id="drop" tabindex="0">drop challenge files here<small>or click to browse &middot; saved to challenge/</small></div>
    <input id="file" type="file" multiple>
    <div id="confirmBox"></div>
    <div class="msg" id="upMsg"></div>
    <ul id="files"></ul>
  </section>

  <section>
    <h2><b>03</b>hints</h2>
    <label for="hint">add as many as you like - the agent gets them one at a time, only when it asks</label>
    <textarea id="hint" spellcheck="false"></textarea>
    <div class="actions">
      <button id="saveHint">add hint</button>
      <button id="clearHint" class="quiet">clear all</button>
      <span class="msg" id="hintMsg"></span>
    </div>
    <ol id="hintList"></ol>
  </section>

  <section>
    <h2><b>04</b>run</h2>
    <div class="actions" style="margin:0 0 12px">
      <span id="sbState" class="msg" style="margin:0">sandbox: ...</span>
      <button id="sbStart" class="quiet" hidden>start sandbox</button>
    </div>
    <div class="actions" style="margin:0 0 12px">
      <button id="run">run agent</button>
      <button id="stop" class="quiet" hidden>stop</button>
      <label style="margin:0;display:flex;gap:6px;align-items:center"><input id="wipe" type="checkbox" checked style="width:auto"> delete files + hint when the flag is found</label>
      <span class="msg" id="runMsg" style="margin:0"></span>
    </div>
    <pre id="out" hidden></pre>
  </section>

  <footer>listening on localhost only &middot; files overwrite by name</footer>
</main>

<script>
const $ = id => document.getElementById(id);
const say = (el, text, cls) => { el.textContent = text; el.className = "msg " + (cls || ""); };

// ---- files ----
const queue = [];
let busy = false;

function enqueue(fileList) {
  for (const f of fileList) queue.push(f);
  if (!busy) next();
}

async function next() {
  busy = true;
  while (queue.length) {
    const f = queue.shift();
    try {
      const r = await fetch("/api/exists?name=" + encodeURIComponent(f.name));
      const { exists } = await r.json();
      if (exists && !(await confirmOverwrite(f.name))) { say($("upMsg"), "skipped " + f.name, "warn"); continue; }
      await upload(f);
    } catch (e) {
      say($("upMsg"), "failed: " + e.message, "err");
    }
  }
  busy = false;
  refresh();
}

function confirmOverwrite(name) {
  return new Promise(resolve => {
    const box = $("confirmBox");
    box.innerHTML = "";
    const d = document.createElement("div");
    d.className = "confirm";
    d.textContent = name + " already exists. overwrite?";
    d.appendChild(document.createElement("br"));
    for (const [label, val] of [["overwrite", true], ["skip", false]]) {
      const b = document.createElement("button");
      b.textContent = label;
      b.onclick = () => { box.innerHTML = ""; resolve(val); };
      d.appendChild(b);
    }
    box.appendChild(d);
  });
}

async function upload(f) {
  say($("upMsg"), "uploading " + f.name + " ...");
  const r = await fetch("/api/upload", { method: "POST", headers: { "X-Filename": encodeURIComponent(f.name) }, body: f });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  if (j.sandbox_ok) say($("upMsg"), "saved " + j.saved + " (" + j.size + " bytes) -> copied to sandbox:/workspace", "ok");
  else say($("upMsg"), "saved " + j.saved + " (" + j.size + " bytes). not in sandbox yet: " + j.sandbox_msg + " (it is copied again on run)", "warn");
}

const drop = $("drop");
drop.onclick = () => $("file").click();
drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") $("file").click(); };
$("file").onchange = e => { enqueue(e.target.files); e.target.value = ""; };
["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => enqueue(e.dataTransfer.files));
// stop the browser from navigating away if a file misses the zone
["dragover", "drop"].forEach(t => window.addEventListener(t, e => e.preventDefault()));

// ---- task / hint ----
const fields = ["name", "host", "port", "desc", "flag", "steps", "model"];

async function refresh() {
  const s = await (await fetch("/api/state")).json();
  const ul = $("files");
  ul.innerHTML = "";
  for (const n of s.files) { const li = document.createElement("li"); li.textContent = n; ul.appendChild(li); }
  if (!s.files.length) { const li = document.createElement("li"); li.textContent = "(empty)"; ul.appendChild(li); }
  return s;
}

function taskObj() {
  return Object.fromEntries(fields.map(k => [k, $(k).value]));
}

// ---- sandbox + run ----
let lastLog = "", outPos = 0, jobName = null, timer = null;

async function pollSandbox() {
  try {
    const s = await (await fetch("/api/sandbox")).json();
    const el = $("sbState");
    if (s.running) { el.textContent = "sandbox: up"; el.className = "msg ok"; $("sbStart").hidden = true; }
    else if (s.starting) { el.textContent = "sandbox: starting (first build can take a few minutes)"; el.className = "msg warn"; $("sbStart").hidden = true; }
    else { el.textContent = "sandbox: down"; el.className = "msg err"; $("sbStart").hidden = false; }
  } catch {}
}
setInterval(pollSandbox, 3000);
pollSandbox();

async function watch(job) {
  jobName = job; outPos = 0;
  $("out").hidden = false; $("out").textContent = "";
  clearInterval(timer);
  timer = setInterval(async () => {
    const j = await (await fetch("/api/job?job=" + job + "&since=" + outPos)).json();
    outPos = j.next;
    if (job === "agent" && j.lines.some(l => l.includes("[HINT NEEDED]"))) {
      say($("runMsg"), "agent is asking for a hint - add one in section 03", "warn");
      $("hint").focus();
    }
    if (j.lines.length) { $("out").textContent += j.lines.join("\n") + "\n"; $("out").scrollTop = 1e9; }
    if (job === "agent") $("stop").hidden = !j.running;
    if (!j.running) {
      clearInterval(timer);
      if (job === "agent") {
        $("run").disabled = false; refresh();
        const hadFlag = $("flag").value.trim() !== "";
        say($("runMsg"), (j.code === 0 ? (hadFlag ? "solved" : "flag submitted but UNVERIFIED (no known flag) - check it") : "finished, not solved (exit " + j.code + ")") + " - " + lastLog, j.code === 0 ? "ok" : "warn");
        if (j.code === 0 && $("wipe").checked) { [...fields, "hint"].forEach(k => $(k).value = ""); hints = []; renderHints(); }
      }
      pollSandbox();
    }
  }, 700);
}

$("sbStart").onclick = async () => {
  await post("/api/sandbox/start", {});
  $("sbStart").hidden = true;
  watch("sandbox");
};
$("run").onclick = async () => {
  try {
    await post("/api/task", taskObj());
    const r = await post("/api/run", { ...taskObj(), hints, wipe: $("wipe").checked });
    $("run").disabled = true;
    lastLog = r.log;
    say($("runMsg"), "running ... log: " + r.log);
    watch("agent");
  } catch (e) { say($("runMsg"), e.message, "err"); }
};
$("stop").onclick = () => post("/api/run/stop", {});

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

$("saveTask").onclick = async () => {
  try { await post("/api/task", taskObj()); say($("taskMsg"), "saved challenge/task.json", "ok"); refresh(); }
  catch (e) { say($("taskMsg"), e.message, "err"); }
};
let hints = [];
function renderHints() {
  const ol = $("hintList");
  ol.innerHTML = "";
  for (const h of hints) { const li = document.createElement("li"); li.textContent = h; ol.appendChild(li); }
}
async function saveHints() {
  await post("/api/hint", { hints });
  renderHints();
}
$("saveHint").onclick = async () => {
  const t = $("hint").value.trim();
  if (!t) return say($("hintMsg"), "type a hint first", "warn");
  try {
    hints.push(t); $("hint").value = "";
    await saveHints();
    say($("hintMsg"), "hint " + hints.length + " saved", "ok");
    if ($("run").disabled) say($("runMsg"), "hint added - running ...", "ok");
  } catch (e) { hints.pop(); say($("hintMsg"), e.message, "err"); }
};
$("clearHint").onclick = async () => {
  try { hints = []; $("hint").value = ""; await saveHints(); say($("hintMsg"), "cleared", "ok"); }
  catch (e) { say($("hintMsg"), e.message, "err"); }
};

(async () => {
  const s = await refresh();
  for (const k of fields) if (s.task[k] !== undefined) $(k).value = s.task[k];
  hints = s.hints || [];
  renderHints();
})();
</script>
</body>
</html>
"""


def safe_name(raw):
    """Reduce a client-supplied filename to a plain basename, or None if unusable."""
    name = re.split(r"[\\/]", unquote(raw or ""))[-1].strip().strip(".")
    if not name or name in RESERVED or re.search(r'[<>:"|?*\x00-\x1f]', name):
        return None
    return name


def wipe_challenge(names, log):
    """Delete the challenge files (host + sandbox), task.json and hint.txt. Keeps .gitkeep and run logs."""
    for name in names:
        docker("exec", CONTAINER, "rm", "-f", f"{WORKDIR}/{name}", timeout=15)
    removed = []
    for p in CHALLENGE.iterdir():
        if p.is_file() and p.name not in IGNORED - {"task.json", "hint.txt"}:
            try:
                p.unlink()
                removed.append(p.name)
            except OSError as e:
                log.append(f"[cleanup: could not delete {p.name}: {e}]")
    log.append("[cleanup] flag found - deleted: " + (", ".join(removed) or "nothing"))


def unique_log_path(name):
    """workspace/<name>.json, or <name>2.json, <name>3.json ... if taken."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")[:60] or "ctf"
    logdir = ROOT / "workspace"
    logdir.mkdir(exist_ok=True)
    path, n = logdir / f"{slug}.json", 1
    while path.exists():
        n += 1
        path = logdir / f"{slug}{n}.json"
    return path


def write_hints(hints):
    """Save hints to challenge/hint.txt, one per entry, separated by HINT_SEP."""
    clean = [str(h).strip().replace(HINT_SEP, "\n--\n") for h in (hints if isinstance(hints, list) else [])]
    (CHALLENGE / "hint.txt").write_text(HINT_SEP.join(h for h in clean if h), encoding="utf-8")


def list_files():
    CHALLENGE.mkdir(exist_ok=True)
    return sorted(p.name for p in CHALLENGE.iterdir() if p.is_file() and p.name not in IGNORED)


def build_description(task, hint, files):
    parts = [task.get("desc", "").strip()]
    if files:
        parts.append(f"Challenge files are already in {WORKDIR} inside the sandbox: " + ", ".join(files)
                     + ". Read and analyse them with run_command.")
    host, port = task.get("host", "").strip(), task.get("port", "").strip()
    if host and port:
        parts.append(f"Server: nc {host} {port}")
    if hint.strip():
        parts.append("Hint from the operator: " + hint.strip())
    return "\n".join(p for p in parts if p)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[dropui] " + fmt % args + "\n")

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if n < 0 or n > MAX_UPLOAD:
            return None
        return self.rfile.read(n)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/state":
            files = list_files()
            task, hints = {}, []
            try:
                task = json.loads((CHALLENGE / "task.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            try:
                hints = [h for h in (CHALLENGE / "hint.txt").read_text(encoding="utf-8").split(HINT_SEP) if h.strip()]
            except OSError:
                pass
            self.send_json({"files": files, "task": task, "hints": hints})
        elif url.path == "/api/sandbox":
            self.send_json({"running": sandbox_running(), "starting": JOBS["sandbox"].running})
        elif url.path == "/api/job":
            q = parse_qs(url.query)
            job = JOBS.get(q.get("job", [""])[0])
            if not job:
                return self.send_json({"error": "unknown job"}, 404)
            since = int(q.get("since", ["0"])[0] or 0)
            self.send_json({"lines": job.lines[since:], "next": len(job.lines),
                            "running": job.running, "code": job.code})
        elif url.path == "/api/exists":
            name = safe_name(parse_qs(url.query).get("name", [""])[0])
            self.send_json({"exists": bool(name and (CHALLENGE / name).exists())})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()
        if body is None:
            return self.send_json({"error": "bad or oversized body"}, 400)
        CHALLENGE.mkdir(exist_ok=True)

        if path == "/api/upload":
            name = safe_name(self.headers.get("X-Filename"))
            if not name:
                return self.send_json({"error": "invalid filename"}, 400)
            (CHALLENGE / name).write_bytes(body)
            ok, msg = copy_to_sandbox(name)
            return self.send_json({"saved": name, "size": len(body), "sandbox_ok": ok, "sandbox_msg": msg})

        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return self.send_json({"error": "invalid json"}, 400)

        if path == "/api/task":
            keys = ("name", "host", "port", "desc", "flag", "steps", "model")
            task = {k: str(data.get(k, "")) for k in keys}
            (CHALLENGE / "task.json").write_text(json.dumps(task, indent=2), encoding="utf-8")
            return self.send_json({"ok": True})
        if path == "/api/hint":
            write_hints(data.get("hints"))
            return self.send_json({"ok": True})
        if path == "/api/sandbox/start":
            job = JOBS["sandbox"]
            if sandbox_running():
                return self.send_json({"ok": True, "already": True})
            if not job.running:
                job.start(sandbox_start_cmds())
            return self.send_json({"ok": True})
        if path == "/api/run/stop":
            JOBS["agent"].stop()
            return self.send_json({"ok": True})
        if path == "/api/run":
            job = JOBS["agent"]
            if job.running:
                return self.send_json({"error": "agent already running"}, 409)
            if not sandbox_running():
                return self.send_json({"error": "sandbox is not running - start it first"}, 400)
            task = {k: str(data.get(k, "")) for k in ("name", "host", "port", "desc", "flag", "steps", "model")}
            if not task["desc"].strip():
                return self.send_json({"error": "add a challenge description first"}, 400)
            files = list_files()
            for name in files:  # make sure the sandbox has the latest copy of every file
                ok, msg = copy_to_sandbox(name)
                if not ok:
                    return self.send_json({"error": f"could not copy {name}: {msg}"}, 500)
            hint_path = CHALLENGE / "hint.txt"
            write_hints(data.get("hints"))  # agent reads these only if it asks
            log_path = unique_log_path(task["name"])
            argv = [sys.executable, "-u", str(ROOT / "agent" / "agent.py"),
                    "--description", build_description(task, "", files),
                    "--log", str(log_path), "--hint-file", str(hint_path)]
            if task["flag"].strip():
                argv += ["--flag", task["flag"].strip()]
            if task["steps"].strip().isdigit():
                argv += ["--max-steps", task["steps"].strip()]
            if task["model"].strip():
                argv += ["--model", task["model"].strip()]
            wipe = bool(data.get("wipe"))
            job.start([argv], env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
                      on_success=(lambda log: wipe_challenge(files, log)) if wipe else None)
            return self.send_json({"ok": True, "files": files, "log": str(log_path.relative_to(ROOT))})
        self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    CHALLENGE.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 5000), Handler)
    print("hacking agent drop ui -> http://localhost:5000  (saving to %s)" % CHALLENGE)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
