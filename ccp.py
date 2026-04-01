#!/usr/bin/env python3
"""
Crash-Copilot — AI-powered crash interceptor
Drop ccp.py + .env into your project root (or anywhere up the tree).
Run: python ccp.py python my_script.py
"""

import sys
import subprocess
import re
import os
import datetime
import webbrowser
import html as html_lib
import time
import json
import requests

# ── Windows UTF-8 fix ──────────────────────────────────────────────────────────
if sys.stdout and getattr(sys.stdout, "encoding", "utf-8").lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


# ── ANSI colours ───────────────────────────────────────────────────────────────
class C:
    R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"
    B = "\033[94m"; M = "\033[95m"; BOLD = "\033[1m"; END = "\033[0m"


# ── .env loader (searches CWD → up 6 levels) ──────────────────────────────────
def load_env():
    d = os.getcwd()
    for _ in range(6):
        p = os.path.join(d, ".env")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            return
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd

load_env()

API_KEY   = os.environ.get("GLM_API_KEY", "").strip()
API_URL   = "https://api.z.ai/api/paas/v4/chat/completions"
MODEL     = os.environ.get("GLM_MODEL", "glm-4.7")

SYSTEM_PROMPT = (
    "You are Crash-Copilot, an expert debugging agent.\n"
    "Given an ERROR LOG and BROKEN CODE (with file path and line numbers), "
    "respond ONLY in this exact Markdown structure:\n\n"
    "## 🔍 Root Cause\n"
    "One clear sentence explaining the fundamental bug.\n\n"
    "## 📍 Location\n"
    "`<file_path>`, line <number>\n\n"
    "## ✅ Fixed Code\n"
    "Show the COMPLETE corrected file. Add a comment at the top: "
    "`# File: <full_path>` so the user knows exactly where to apply this.\n"
    "```<lang>\n"
    "<complete corrected code here>\n"
    "```\n\n"
    "## 💡 What Changed\n"
    "- Bullet 1: LINE <n>: specific change and why\n"
    "- Bullet 2: LINE <n>: specific change and why\n"
    "(2-4 bullets max, be concrete, reference line numbers)\n\n"
    "## ⚠️ Watch Out\n"
    "One sentence about edge cases or follow-on issues to watch for.\n\n"
    "No filler text. No greetings. Stick exactly to this format."
)

CHAT_SYSTEM = (
    "You are Crash-Copilot Chat, a focused debugging companion. "
    "You have context about a specific crash and its AI-generated fix. "
    "RULES:\n"
    "1. ONLY answer questions related to this crash, the error, the fix, "
    "or closely related debugging concepts.\n"
    "2. If the user asks something unrelated (general knowledge, other topics), "
    "politely decline: 'I can only help with this specific crash and its fix.'\n"
    "3. Format code in markdown code blocks with language tags.\n"
    "4. Use **bold** for emphasis, `inline code` for identifiers.\n"
    "5. Use bullet lists for multi-point answers.\n"
    "6. Keep answers under 200 words unless code is needed.\n"
    "7. Be concise, precise, and helpful."
)

_MAX_RETRIES  = 2
_TIMEOUT_SECS = 45
_MAX_TOKENS   = 1500
_CHAT_TOKENS  = 800


# ── GLM helper ─────────────────────────────────────────────────────────────────
def _call_glm(messages: list, max_tokens: int = _MAX_TOKENS) -> str:
    if not API_KEY or API_KEY == "your_actual_api_key_here":
        return "❌ Set GLM_API_KEY in .env first."

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL, "messages": messages,
                "temperature": 0.15, "max_tokens": max_tokens}

    last_err = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = requests.post(API_URL, headers=headers, json=payload, timeout=_TIMEOUT_SECS)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            last_err = "Timed out"
        except requests.exceptions.ConnectionError:
            last_err = "Connection error"
        except requests.exceptions.HTTPError:
            if r.status_code in (401, 403):
                return f"❌ Auth error {r.status_code}: check GLM_API_KEY."
            if r.status_code == 429:
                last_err = "Rate limited"
            else:
                return f"❌ API {r.status_code}: {r.text[:200]}"
        except (KeyError, IndexError):
            return "❌ Unexpected API response."
        except Exception as e:
            return f"❌ {e}"
        if attempt < _MAX_RETRIES:
            time.sleep(2 ** attempt)
    return f"❌ Failed after {_MAX_RETRIES} retries. {last_err}"


def ask_glm(error_log: str, code_snippet: str) -> str:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            f"ERROR LOG:\n```\n{error_log[:3000]}\n```\n\n"
            f"BROKEN CODE (with file path and line numbers):\n```\n{code_snippet[:3000]}\n```"},
    ]
    return _call_glm(msgs, _MAX_TOKENS)


# ── Traceback extraction ───────────────────────────────────────────────────────
_TB = [
    re.compile(r'File "(.+?)", line (\d+)'),
    re.compile(r"at .+? \((.+?):(\d+):\d+\)"),
    re.compile(r"at (.+?):(\d+):\d+"),
    re.compile(r"(\S+\.go):(\d+)"),
    re.compile(r"--> (.+?):(\d+):\d+"),
    re.compile(r"at .+?\((.+?\.java):(\d+)\)"),
]
_SKIP = [
    re.compile(r"[/\\]lib[/\\]python"),
    re.compile(r"[/\\]site-packages[/\\]"),
    re.compile(r"[/\\]node_modules[/\\]"),
    re.compile(r"<frozen"), re.compile(r"<string>"),
]

def _user_file(p): return not any(s.search(p) for s in _SKIP) and os.path.isfile(p)

def extract_crash_file_info(error_log: str):
    """Extract the crash file path and line number from the error log."""
    candidates = [(f, int(n)) for pat in _TB for f, n in pat.findall(error_log) if _user_file(f)]
    if not candidates:
        return None, None
    return candidates[-1]  # (filepath, line_number)

def extract_code_context(error_log: str, ctx: int = 8) -> str:
    fp, ln = extract_crash_file_info(error_log)
    if not fp:
        return "No local source files detected in stack trace."
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        s, e = max(0, ln - ctx - 1), min(len(lines), ln + ctx)
        numbered = [f"{'>>>' if i==ln else '   '} {i:4d} | {l.rstrip()}"
                    for i, l in enumerate(lines[s:e], s + 1)]
        return f"--- {fp} (line {ln}, showing {s+1}-{e}) ---\n" + "\n".join(numbered)
    except Exception as ex:
        return f"Could not read {fp}: {ex}"


# ── HTML report ────────────────────────────────────────────────────────────────
REPORT_FILE = "crash_report.html"

def _md_to_html(md: str) -> str:
    lines, out, in_code, lang = md.split("\n"), [], False, ""
    for line in lines:
        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                lang = line.strip()[3:].strip()
                out.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
            else:
                in_code = False
                out.append("</code></pre>")
        elif in_code:
            out.append(html_lib.escape(line))
        elif line.startswith("### "): out.append(f"<h3>{html_lib.escape(line[4:])}</h3>")
        elif line.startswith("## "):  out.append(f"<h2>{html_lib.escape(line[3:])}</h2>")
        elif line.startswith("# "):   out.append(f"<h1>{html_lib.escape(line[2:])}</h1>")
        elif line.startswith("- ") or line.startswith("* "):
            out.append(f"<li>{html_lib.escape(line[2:])}</li>")
        elif line.strip() == "":     out.append("<br>")
        else:
            p = re.sub(r"`([^`]+)`", r"<code>\1</code>", html_lib.escape(line))
            p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
            out.append(f"<p>{p}</p>")
    return "\n".join(out)


def _build_html(md: str, error_log: str, command: str, ts: str, code_ctx: str) -> str:
    ai_html      = _md_to_html(md)
    err_esc      = html_lib.escape(error_log.strip()[:3000])
    ctx_esc      = html_lib.escape(code_ctx)
    api_key_safe = html_lib.escape(API_KEY)
    model_safe   = html_lib.escape(MODEL)

    # Extract crash file info
    crash_fp, crash_ln = extract_crash_file_info(error_log)
    crash_loc_html = ""
    if crash_fp:
        crash_loc_html = f'<div class="crash-loc"><span class="loc-icon">📍</span> <span class="loc-path">{html_lib.escape(crash_fp)}</span><span class="loc-line">line {crash_ln}</span></div>'

    # Build the initial chat context as a JSON array embedded in the page
    chat_init = json.dumps([
        {"role": "system", "content": CHAT_SYSTEM},
        {"role": "assistant", "content":
            f"I've analysed a crash. Here's the context:\n\n"
            f"**Error:**\n```\n{error_log[:800]}\n```\n\n"
            f"**AI Fix:**\n{md[:1200]}\n\n"
            f"Ask me anything about this crash or the fix."}
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crash-Copilot Report</title>
<meta name="description" content="AI-powered crash diagnosis and fix report generated by Crash-Copilot">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#09090f;--surface:#111118;--surface2:#18181f;--border:#222233;
  --text:#e8e8f0;--muted:#6e6e88;--accent:#8b7cf7;--accent2:#f97316;
  --red:#f87171;--green:#34d399;--yellow:#fbbf24;--blue:#60a5fa;
  --radius:12px;--mono:'JetBrains Mono',monospace;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:40px 20px 140px;line-height:1.6}}
.wrap{{max-width:780px;margin:0 auto}}

/* Header — Minimal & Clean */
.hdr{{text-align:center;padding:48px 24px 40px;margin-bottom:32px;position:relative}}
.hdr h1{{font-size:28px;font-weight:700;color:var(--text);letter-spacing:-.5px;margin-bottom:8px}}
.hdr h1 span{{background:linear-gradient(135deg,#f87171,#f97316);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.hdr .sub{{color:var(--muted);font-size:12px;font-family:var(--mono);margin-bottom:16px;letter-spacing:.5px}}
.chip{{display:inline-block;background:var(--surface);padding:8px 20px;border-radius:8px;color:var(--blue);font-family:var(--mono);font-size:12.5px;border:1px solid var(--border)}}
.hdr-line{{width:60px;height:2px;background:linear-gradient(90deg,transparent,var(--border),transparent);margin:20px auto 0}}

/* Crash location */
.crash-loc{{display:flex;align-items:center;gap:8px;background:rgba(248,113,113,.06);border:1px solid rgba(248,113,113,.15);border-radius:8px;padding:10px 16px;margin-bottom:20px;font-size:13px}}
.loc-icon{{font-size:16px}}
.loc-path{{font-family:var(--mono);color:var(--red);font-size:12px;word-break:break-all}}
.loc-line{{margin-left:auto;font-family:var(--mono);color:var(--yellow);font-size:12px;white-space:nowrap;padding-left:12px}}

/* Section labels */
.section-label{{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);font-weight:600;margin-bottom:12px;padding-left:2px}}

/* Cards — Flat & Clean */
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin-bottom:16px;transition:border-color .25s ease}}
.card:hover{{border-color:#333348}}
.card-hdr{{font-size:15px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:10px;color:var(--text)}}
.icon{{font-size:18px}}
.badge{{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:20px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-left:auto}}
.b-crash{{background:rgba(248,113,113,.1);color:var(--red);border:1px solid rgba(248,113,113,.2)}}
.b-fix{{background:rgba(52,211,153,.1);color:var(--green);border:1px solid rgba(52,211,153,.2)}}

/* AI answer sections */
.ai h2{{color:#a78bfa;font-size:16px;font-weight:600;margin:24px 0 10px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
.ai h2:first-child{{margin-top:0}}
.ai h3{{color:#c4b5fd;font-size:14px;font-weight:600;margin:16px 0 7px}}
.ai p{{line-height:1.75;color:#bbb;margin-bottom:8px;font-size:14px}}
.ai li{{line-height:1.7;color:#bbb;margin:4px 0 4px 20px;list-style:disc;font-size:14px}}
.ai code{{background:#1e1e2a;color:#f9a8d4;padding:2px 7px;border-radius:4px;font-family:var(--mono);font-size:12px}}

/* Code blocks */
.code-wrap{{position:relative;margin:14px 0}}
.code-wrap pre{{background:#08080e;border:1px solid var(--border);border-radius:10px;padding:18px;overflow-x:auto}}
.code-wrap pre code{{background:none;color:#a5f3fc;padding:0;font-size:12.5px;line-height:1.7;font-family:var(--mono)}}
.copy-btn{{position:absolute;top:8px;right:8px;background:#1e1e2a;border:1px solid var(--border);color:var(--muted);padding:4px 12px;border-radius:6px;font-size:11px;cursor:pointer;transition:all .2s;font-family:var(--mono)}}
.copy-btn:hover{{background:#2a2a3c;color:var(--text)}}
.copy-btn.copied{{background:rgba(52,211,153,.12);color:var(--green);border-color:rgba(52,211,153,.25)}}

/* Error log */
.err-block{{background:#0c0810;border:1px solid rgba(248,113,113,.15);border-radius:10px;padding:18px;max-height:300px;overflow-y:auto}}
.err-block pre{{color:#fca5a5;font-family:var(--mono);font-size:11.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word}}
.err-lbl{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--red);font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}}
.err-lbl::before{{content:'';width:6px;height:6px;border-radius:50%;background:var(--red)}}

/* Source context (below error) */
.src-label{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--blue);font-weight:600;margin:16px 0 10px;display:flex;align-items:center;gap:6px}}
.src-label::before{{content:'';width:6px;height:6px;border-radius:50%;background:var(--blue)}}
.src-block{{background:#08080e;border:1px solid var(--border);border-radius:10px;padding:18px;overflow-x:auto}}
.src-block pre{{font-family:var(--mono);font-size:11.5px;line-height:1.55;white-space:pre-wrap;color:#8ab4f8}}
.src-block pre .err-line{{color:var(--red);font-weight:600}}

/* ── Chat Companion CTA Banner ── */
.chat-cta{{background:linear-gradient(135deg,rgba(124,106,247,.08),rgba(249,115,22,.06));border:1px solid rgba(124,106,247,.2);border-radius:var(--radius);padding:20px 24px;margin-bottom:16px;display:flex;align-items:center;gap:16px;cursor:pointer;transition:all .25s ease}}
.chat-cta:hover{{border-color:rgba(124,106,247,.4);background:linear-gradient(135deg,rgba(124,106,247,.12),rgba(249,115,22,.08));transform:translateY(-1px)}}
.chat-cta-icon{{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#7c6af7,#f97316);display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}}
.chat-cta-body h4{{font-size:14px;font-weight:600;color:var(--text);margin-bottom:3px}}
.chat-cta-body p{{font-size:12px;color:var(--muted);line-height:1.5}}
.chat-cta-arrow{{margin-left:auto;color:var(--accent);font-size:20px;transition:transform .2s}}
.chat-cta:hover .chat-cta-arrow{{transform:translateX(3px)}}

/* ── Chat panel ── */
#chat-toggle{{position:fixed;bottom:24px;right:24px;width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,#7c6af7,#f97316);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:22px;box-shadow:0 4px 24px rgba(124,106,247,.35);z-index:1000;transition:transform .2s}}
#chat-toggle:hover{{transform:scale(1.08)}}
#chat-panel{{position:fixed;bottom:86px;right:24px;width:400px;height:520px;background:#111118;border:1px solid #252538;border-radius:16px;box-shadow:0 24px 64px rgba(0,0,0,.6);z-index:999;display:none;flex-direction:column;overflow:hidden;transition:width .3s ease,height .3s ease}}
#chat-panel.open{{display:flex}}
#chat-panel.expanded{{width:600px;height:calc(100vh - 120px);bottom:86px}}
#chat-hdr{{padding:14px 16px;background:linear-gradient(135deg,#151520,#111118);border-bottom:1px solid #252538;display:flex;align-items:center;gap:10px;flex-shrink:0}}
#chat-hdr h3{{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px;flex:1}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.chat-btn{{background:none;border:1px solid var(--border);color:var(--muted);padding:4px 8px;border-radius:5px;cursor:pointer;font-size:14px;transition:all .2s}}
.chat-btn:hover{{color:var(--text);border-color:#444}}
#chat-msgs{{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;scroll-behavior:smooth}}
#chat-msgs::-webkit-scrollbar{{width:4px}}
#chat-msgs::-webkit-scrollbar-thumb{{background:#333;border-radius:4px}}

/* Chat messages */
.msg{{max-width:85%;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.65;animation:fadeIn .25s ease;word-break:break-word}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(6px)}}to{{opacity:1;transform:translateY(0)}}}}
.msg.user{{background:linear-gradient(135deg,#252050,#1f2d50);align-self:flex-end;border-bottom-right-radius:4px;color:#d4d4ff}}
.msg.ai{{background:#171720;align-self:flex-start;border-bottom-left-radius:4px;border:1px solid #252538;color:#ccc}}

/* Chat markdown rendering */
.msg h1,.msg h2,.msg h3{{color:#a78bfa;font-size:14px;font-weight:600;margin:8px 0 4px}}
.msg h1{{font-size:15px}}
.msg p{{margin:4px 0;line-height:1.65}}
.msg ul,.msg ol{{margin:4px 0 4px 18px}}
.msg li{{margin:2px 0;line-height:1.6}}
.msg code{{background:#0d0d18;padding:1px 6px;border-radius:4px;font-family:var(--mono);font-size:11.5px;color:#a5f3fc}}
.msg pre{{background:#0a0a14;border:1px solid #252538;border-radius:8px;padding:12px;margin:8px 0;overflow-x:auto;font-family:var(--mono);font-size:11px;line-height:1.6;color:#a5f3fc;white-space:pre-wrap;position:relative}}
.msg pre .chat-copy{{position:absolute;top:4px;right:4px;background:#1a1a2a;border:1px solid #333;color:var(--muted);padding:2px 8px;border-radius:4px;font-size:10px;cursor:pointer;font-family:var(--mono);transition:all .2s}}
.msg pre .chat-copy:hover{{color:#fff;background:#2a2a3c}}
.msg strong{{color:#d4d4ff}}
.msg em{{color:var(--muted);font-style:italic}}
.msg hr{{border:none;border-top:1px solid #252538;margin:8px 0}}
.msg blockquote{{border-left:3px solid var(--accent);padding-left:10px;margin:6px 0;color:var(--muted)}}

/* Typing animation */
.typing{{display:flex;gap:4px;align-items:center;padding:12px 14px;align-self:flex-start}}
.typing span{{width:6px;height:6px;background:var(--accent);border-radius:50%;animation:bounce .9s infinite}}
.typing span:nth-child(2){{animation-delay:.2s}}
.typing span:nth-child(3){{animation-delay:.4s}}
@keyframes bounce{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-5px)}}}}
#chat-footer{{padding:10px 14px;border-top:1px solid #252538;display:flex;gap:8px;flex-shrink:0}}
#chat-input{{flex:1;background:#0a0a14;border:1px solid #252538;color:var(--text);padding:10px 14px;border-radius:10px;font-size:13px;font-family:inherit;resize:none;outline:none;min-height:40px;max-height:120px;transition:border-color .2s}}
#chat-input:focus{{border-color:var(--accent)}}
#chat-send{{background:linear-gradient(135deg,#7c6af7,#a855f7);border:none;color:#fff;padding:10px 16px;border-radius:10px;cursor:pointer;font-size:15px;transition:opacity .2s;align-self:flex-end}}
#chat-send:hover{{opacity:.85}}
#chat-send:disabled{{opacity:.35;cursor:default}}
.ctx-note{{font-size:10px;color:var(--muted);text-align:center;padding:3px 0 0;flex-shrink:0}}

/* Footer */
.footer{{text-align:center;padding:32px 0;color:#333348;font-size:11.5px;letter-spacing:.3px}}
.footer a{{color:var(--blue);text-decoration:none}}

@media(max-width:600px){{
  #chat-panel{{width:calc(100vw - 32px);right:16px;height:450px}}
  #chat-panel.expanded{{width:calc(100vw - 32px);height:calc(100vh - 110px)}}
  body{{padding:20px 12px 120px}}
  .chat-cta{{flex-direction:column;text-align:center;gap:10px}}
  .chat-cta-arrow{{display:none}}
}}
</style>
</head>
<body>
<div class="wrap">

  <div class="hdr">
    <h1>🚨 <span>Crash-Copilot</span></h1>
    <div class="sub">{ts}</div>
    <div><span class="chip">$ {html_lib.escape(command)}</span></div>
    <div class="hdr-line"></div>
  </div>

  {crash_loc_html}

  <!-- Error Log + Source Context -->
  <div class="card">
    <div class="card-hdr"><span class="icon">📋</span> Error Output <span class="badge b-crash">Crash</span></div>
    <div class="err-lbl">Stack Trace</div>
    <div class="err-block"><pre>{err_esc}</pre></div>
    <div class="src-label">Source Context</div>
    <div class="src-block">
      <pre>{ctx_esc}</pre>
    </div>
  </div>

  <!-- AI Diagnosis -->
  <div class="card">
    <div class="card-hdr"><span class="icon">🧠</span> AI Diagnosis <span class="badge b-fix">Fix ready</span></div>
    <div class="ai" id="ai-content">
      {ai_html}
    </div>
  </div>

  <!-- Chat Companion CTA -->
  <div class="chat-cta" onclick="toggleChat()">
    <div class="chat-cta-icon">💬</div>
    <div class="chat-cta-body">
      <h4>Got questions? Ask the Companion</h4>
      <p>Chat with Crash-Copilot about this error, the fix, or related debugging tips. 15 messages per session.</p>
    </div>
    <div class="chat-cta-arrow">→</div>
  </div>

  <div class="footer">Generated by <strong>Crash-Copilot v2.0</strong> &mdash; AI-Powered Debugging Agent &mdash; <a href="https://z.ai">Z.AI</a></div>
</div>

<!-- Chat Companion -->
<button id="chat-toggle" title="Chat with Crash-Copilot">💬</button>

<div id="chat-panel">
  <div id="chat-hdr">
    <h3><span class="dot"></span> Crash-Copilot Chat</h3>
    <button class="chat-btn" id="expand-btn" onclick="toggleExpand()" title="Expand">⤢</button>
    <button class="chat-btn" onclick="toggleChat()" title="Close">✕</button>
  </div>
  <div id="chat-msgs">
    <div class="msg ai">👋 I've analysed your crash. Ask me anything about the <strong>error</strong> or the <strong>fix</strong> — I have full context.</div>
  </div>
  <div id="chat-footer">
    <textarea id="chat-input" placeholder="Ask about this error…" rows="1" onkeydown="handleKey(event)"></textarea>
    <button id="chat-send" onclick="sendMsg()">➤</button>
  </div>
  <div class="ctx-note" id="ctx-note">0 / 15 messages used</div>
</div>

<script>
// ── Copy buttons for all code blocks ────────────────────────────────────────
document.querySelectorAll('.ai pre').forEach(pre => {{
  const wrap = document.createElement('div');
  wrap.className = 'code-wrap';
  pre.parentNode.insertBefore(wrap, pre);
  wrap.appendChild(pre);
  const btn = document.createElement('button');
  btn.className = 'copy-btn';
  btn.textContent = 'Copy';
  btn.onclick = function(){{ copyCode(this); }};
  wrap.appendChild(btn);
}});

function copyCode(btn) {{
  const code = btn.closest('.code-wrap').querySelector('pre').innerText;
  navigator.clipboard.writeText(code).then(() => {{
    btn.textContent = '✓ Copied';
    btn.classList.add('copied');
    setTimeout(() => {{ btn.textContent = 'Copy'; btn.classList.remove('copied'); }}, 2000);
  }});
}}

// ── Chat ───────────────────────────────────────────────────────────────────
const API_KEY = {json.dumps(api_key_safe)};
const API_URL = 'https://api.z.ai/api/paas/v4/chat/completions';
const MODEL   = {json.dumps(model_safe)};
const MAX_MSGS = 15;

let history = {chat_init};
let msgCount = 0;
let isExpanded = false;

function toggleChat() {{
  const p = document.getElementById('chat-panel');
  p.classList.toggle('open');
  if (p.classList.contains('open')) document.getElementById('chat-input').focus();
}}
document.getElementById('chat-toggle').onclick = toggleChat;

function toggleExpand() {{
  const p = document.getElementById('chat-panel');
  const btn = document.getElementById('expand-btn');
  isExpanded = !isExpanded;
  p.classList.toggle('expanded', isExpanded);
  btn.textContent = isExpanded ? '⤡' : '⤢';
  btn.title = isExpanded ? 'Collapse' : 'Expand';
}}

function handleKey(e) {{
  if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); sendMsg(); }}
  // Auto-resize textarea
  const ta = e.target;
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
}}

function updateCounter() {{
  const note = document.getElementById('ctx-note');
  note.textContent = msgCount + ' / ' + MAX_MSGS + ' messages used';
  if (msgCount >= MAX_MSGS - 2) note.style.color = 'var(--yellow)';
  if (msgCount >= MAX_MSGS) note.style.color = 'var(--red)';
}}

// ── Markdown parser for chat messages ──────────────────────────────────────
function parseMd(text) {{
  // Escape HTML first
  let s = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Code blocks with language
  s = s.replace(/```(\\w*)[\\n]?([\\s\\S]*?)```/g, function(m, lang, code) {{
    const id = 'cb-' + Math.random().toString(36).substr(2,6);
    return '<pre id="' + id + '"><button class="chat-copy" onclick="copyChat(\\'' + id + '\\')">Copy</button>' + code.trim() + '</pre>';
  }});

  // Inline code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Bold
  s = s.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');

  // Italic
  s = s.replace(/(?<![*])\\*([^*]+?)\\*(?![*])/g, '<em>$1</em>');

  // Headers
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // Blockquote
  s = s.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');

  // Unordered list
  s = s.replace(/^[\\-*] (.+)$/gm, '<li>$1</li>');

  // Ordered list
  s = s.replace(/^\\d+\\. (.+)$/gm, '<li>$1</li>');

  // Horizontal rule
  s = s.replace(/^---$/gm, '<hr>');

  // Line breaks (but not inside pre)
  s = s.replace(/\\n/g, '<br>');

  // Clean up consecutive <br> inside certain elements
  s = s.replace(/<br><li>/g, '<li>');
  s = s.replace(/<\\/li><br>/g, '</li>');
  s = s.replace(/<br><h/g, '<h');
  s = s.replace(/<\\/h(\\d)><br>/g, '</h$1>');

  return s;
}}

function copyChat(id) {{
  const el = document.getElementById(id);
  if (!el) return;
  const text = el.innerText.replace('Copy', '').trim();
  navigator.clipboard.writeText(text);
  const btn = el.querySelector('.chat-copy');
  if (btn) {{ btn.textContent = '✓'; setTimeout(() => btn.textContent = 'Copy', 1500); }}
}}

function appendMsg(role, text) {{
  const box = document.getElementById('chat-msgs');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'ai');
  div.innerHTML = parseMd(text);
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}}

function showTyping() {{
  const box = document.getElementById('chat-msgs');
  const div = document.createElement('div');
  div.className = 'msg ai typing';
  div.id = 'typing';
  div.innerHTML = '<span></span><span></span><span></span>';
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}}

function removeTyping() {{
  const t = document.getElementById('typing');
  if (t) t.remove();
}}

async function sendMsg() {{
  if (msgCount >= MAX_MSGS) {{
    appendMsg('ai', '⚠️ **Session limit reached** (15 messages). Refresh the page to start a new session.');
    return;
  }}
  const input = document.getElementById('chat-input');
  const text  = input.value.trim();
  if (!text) return;
  input.value = '';
  input.style.height = 'auto';

  appendMsg('user', text);
  history.push({{ role: 'user', content: text }});
  msgCount++;
  updateCounter();

  const btn = document.getElementById('chat-send');
  btn.disabled = true;
  showTyping();

  // Keep last messages (plus system msgs) within context
  const systemMsgs = history.filter(m => m.role === 'system');
  const userAiMsgs = history.filter(m => m.role !== 'system').slice(-MAX_MSGS);
  const payload = {{
    model: MODEL,
    messages: [...systemMsgs, ...userAiMsgs],
    temperature: 0.3,
    max_tokens: 800
  }};

  try {{
    const res = await fetch(API_URL, {{
      method: 'POST',
      headers: {{ 'Authorization': 'Bearer ' + API_KEY, 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload)
    }});
    const data = await res.json();
    removeTyping();
    if (!res.ok) {{
      appendMsg('ai', '❌ API error ' + res.status + ': ' + (data.error?.message || JSON.stringify(data)));
    }} else {{
      const reply = data.choices[0].message.content;
      history.push({{ role: 'assistant', content: reply }});
      appendMsg('ai', reply);
    }}
  }} catch(e) {{
    removeTyping();
    appendMsg('ai', '❌ Network error: ' + e.message);
  }}

  btn.disabled = false;
  updateCounter();
}}
</script>
</body>
</html>"""


def save_report(md: str, error_log: str, command: list, code_ctx: str):
    ts      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cmd_str = " ".join(command)
    html    = _build_html(md, error_log, cmd_str, ts, code_ctx)
    path    = os.path.abspath(REPORT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{C.G}✅ Report saved → {C.BOLD}{path}{C.END}")
    try:
        webbrowser.open(f"file:///{path.replace(os.sep, '/')}")
        print(f"{C.B}🌐 Opened in browser.{C.END}")
    except Exception:
        print(f"{C.Y}Open manually: {path}{C.END}")


# ── Main interceptor ───────────────────────────────────────────────────────────
def run_and_catch(command: list):
    cmd_display = " ".join(command)
    print(f"\n{C.B}🚀 Crash-Copilot{C.END} monitoring: {C.BOLD}{cmd_display}{C.END}\n")

    t0     = datetime.datetime.now()
    result = subprocess.run(command, capture_output=True, text=True, errors="replace")
    elapsed= (datetime.datetime.now() - t0).total_seconds()

    if result.returncode == 0:
        if result.stdout.strip(): print(result.stdout)
        print(f"{C.G}✅ Exited cleanly in {elapsed:.1f}s — no bugs found.{C.END}\n")
        return

    print(f"{C.R}🚨 CRASH DETECTED{C.END} (exit {result.returncode}, {elapsed:.1f}s)\n")

    error_log = result.stderr or result.stdout or "No error output captured."
    for line in error_log.strip().splitlines()[-6:]:
        print(f"  {C.R}{line}{C.END}")
    print()

    print(f"{C.M}📂 Extracting source context…{C.END}")
    code_ctx = extract_code_context(error_log)

    print(f"{C.B}🧠 Diagnosing with {MODEL}…{C.END}\n")
    solution = ask_glm(error_log, code_ctx)

    print(f"{C.Y}{'─'*55}{C.END}")
    print(solution)
    print(f"{C.Y}{'─'*55}{C.END}\n")

    save_report(solution, error_log, command, code_ctx)


USAGE = f"""{C.BOLD}Crash-Copilot{C.END} — AI-powered crash interceptor

{C.Y}Usage:{C.END}
  python ccp.py <command> [args...]

{C.Y}Examples:{C.END}
  python ccp.py python my_script.py
  python ccp.py node server.js
  python ccp.py cargo run

{C.Y}Or globally after running install:{C.END}
  ccp python my_script.py

{C.Y}Environment (.env):{C.END}
  GLM_API_KEY   Your Z.AI API key  →  https://z.ai/manage-apikey/apikey-list
  GLM_MODEL     Model override (default: glm-4.7)
"""


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)
    run_and_catch(sys.argv[1:])


if __name__ == "__main__":
    main()
