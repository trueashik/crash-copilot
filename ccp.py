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
    "You are Crash-Copilot, an elite debugging AI agent.\n"
    "Given an ERROR LOG and BROKEN CODE (with file path and line numbers), "
    "respond ONLY in this exact Markdown structure:\n\n"
    "## ✨ Root Cause\n"
    "One clear, precise sentence explaining the fundamental bug.\n\n"
    "## 📍 Location\n"
    "`<file_path>`, line <number>\n\n"
    "## ✅ Fixed Code\n"
    "Show the COMPLETE corrected file. Add a comment at the top: "
    "`# File: <full_path>` so the user knows exactly where to apply this.\n"
    "```<lang>\n"
    "<complete corrected code here>\n"
    "```\n\n"
    "## 💡 What Changed\n"
    "- **LINE <n>** - specific change and why\n"
    "- **LINE <n>** - specific change and why\n"
    "(2-4 bullets max, be concrete, reference line numbers)\n\n"
    "## ⚠️ Watch Out\n"
    "One sentence about edge cases or follow-on issues to watch for.\n\n"
    "No filler text. No greetings. Follow exactly."
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
            p = re.sub(r"`([^`]+)`", r"<code>\1</code>", html_lib.escape(line[2:]))
            p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
            out.append(f"<li>{p}</li>")
        elif line.strip() == "":     out.append("<br>")
        else:
            p = re.sub(r"`([^`]+)`", r"<code>\1</code>", html_lib.escape(line))
            p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)
            out.append(f"<p>{p}</p>")
    return "\n".join(out)


def _build_html(md: str, error_log: str, command: str, ts: str) -> str:
    ai_html      = _md_to_html(md)
    err_esc      = html_lib.escape(error_log.strip()[:3000])
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
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #030305;
  --surface: rgba(18, 18, 24, 0.65);
  --border: rgba(255, 255, 255, 0.08);
  --border-hover: rgba(255, 255, 255, 0.15);
  --text: #f0f0f5;
  --muted: #888899;
  --accent: #8b7cf7;
  --accent-glow: rgba(139, 124, 247, 0.4);
  --accent2: #f97316;
  --red: #ff5e5e;
  --red-bg: rgba(255, 94, 94, 0.1);
  --green: #2dd4bf;
  --green-bg: rgba(45, 212, 191, 0.1);
  --blue: #3b82f6;
  --yellow: #fbbf24;
  --radius: 16px;
  --mono: 'JetBrains Mono', monospace;
  --font: 'Outfit', sans-serif;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  font-family: var(--font);
  background-color: var(--bg);
  background-image: 
    radial-gradient(circle at 50% 0%, rgba(139, 124, 247, 0.10) 0%, transparent 50%),
    radial-gradient(circle at 100% 50%, rgba(249, 115, 22, 0.05) 0%, transparent 40%);
  background-attachment: fixed;
  color: var(--text);
  min-height: 100vh;
  padding: 50px 20px 140px;
  line-height: 1.6;
}}
.wrap {{ max-width: 800px; margin: 0 auto; position: relative; z-index: 1; }}

/* Glassmorphism Classes */
.glass {{
  background: var(--surface);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--border);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
}}

/* Header */
.hdr {{ text-align: center; padding: 20px 24px 50px; position: relative; }}
.hdr h1 {{ font-size: 36px; font-weight: 800; letter-spacing: -1px; margin-bottom: 8px; }}
.hdr h1 span {{
  background: linear-gradient(135deg, #ff5e5e, #f97316, #8b7cf7);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  text-shadow: 0 0 30px rgba(249, 115, 22, 0.3);
}}
.hdr .sub {{ color: var(--muted); font-size: 13px; font-family: var(--font); font-weight: 500; margin-bottom: 20px; letter-spacing: 1px; text-transform: uppercase; }}
.chip {{ display: inline-block; background: rgba(0,0,0,0.5); padding: 10px 24px; border-radius: 30px; color: var(--text); font-family: var(--mono); font-size: 13.5px; border: 1px solid var(--border); box-shadow: inset 0 0 10px rgba(255,255,255,0.02); }}
.chip-prefix {{ color: var(--accent); font-weight: 700; margin-right: 8px; }}

/* Crash location */
.crash-loc {{ display: flex; align-items: center; gap: 12px; background: rgba(255, 94, 94, 0.05); border: 1px solid rgba(255, 94, 94, 0.2); border-radius: 12px; padding: 14px 20px; margin-bottom: 24px; font-size: 14px; box-shadow: 0 4px 20px rgba(255, 94, 94, 0.05); }}
.loc-icon {{ font-size: 18px; filter: drop-shadow(0 0 8px rgba(255, 94, 94, 0.6)); }}
.loc-path {{ font-family: var(--mono); color: #ff8a8a; font-size: 13px; word-break: break-all; font-weight: 500; }}
.loc-line {{ margin-left: auto; font-family: var(--mono); color: var(--yellow); font-size: 13px; background: rgba(251, 191, 36, 0.1); padding: 4px 10px; border-radius: 6px; font-weight: 600; white-space: nowrap; }}

/* Cards */
.card {{ border-radius: var(--radius); padding: 32px; margin-bottom: 24px; transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275), border-color 0.3s ease, box-shadow 0.3s ease; }}
.card:hover {{ border-color: var(--border-hover); transform: translateY(-4px); box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4); }}
.card-hdr {{ font-size: 18px; font-weight: 700; margin-bottom: 24px; display: flex; align-items: center; gap: 12px; color: #fff; letter-spacing: -0.5px; }}
.icon {{ font-size: 22px; filter: drop-shadow(0 0 8px rgba(255,255,255,0.2)); }}
.badge {{ display: inline-flex; align-items: center; justify-content: center; padding: 4px 12px; border-radius: 30px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; margin-left: auto; }}
.b-crash {{ background: var(--red-bg); color: var(--red); border: 1px solid rgba(255, 94, 94, 0.3); box-shadow: 0 0 15px rgba(255, 94, 94, 0.15); }}
.b-fix {{ background: var(--green-bg); color: var(--green); border: 1px solid rgba(45, 212, 191, 0.3); box-shadow: 0 0 15px rgba(45, 212, 191, 0.15); }}

/* AI answer sections */
.ai h2 {{ color: #d8b4fe; font-size: 18px; font-weight: 700; margin: 32px 0 12px; padding-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.05); letter-spacing: -0.5px; display: flex; align-items: center; }}
.ai h2:first-child {{ margin-top: 0; }}
.ai p {{ line-height: 1.8; color: #d1d1dd; margin-bottom: 12px; font-size: 15px; font-weight: 300; }}
.ai li {{ line-height: 1.8; color: #d1d1dd; margin: 6px 0 6px 24px; list-style: circle; font-size: 15px; font-weight: 300; }}
.ai code:not(pre code) {{ background: rgba(139, 124, 247, 0.15); color: #e0e7ff; padding: 3px 8px; border-radius: 6px; font-family: var(--mono); font-size: 13px; font-weight: 500; border: 1px solid rgba(139, 124, 247, 0.3); }}

/* Code blocks */
.code-wrap {{ position: relative; margin: 20px 0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }}
.code-wrap pre {{ background: #050508; border: 1px solid var(--border); padding: 24px; overflow-x: auto; margin: 0; }}
.code-wrap pre code {{ background: none; color: #93c5fd; padding: 0; font-size: 13.5px; line-height: 1.7; font-family: var(--mono); border: none; font-weight: 400; }}
.copy-btn {{ position: absolute; top: 12px; right: 12px; background: rgba(255,255,255,0.05); backdrop-filter: blur(4px); border: 1px solid rgba(255,255,255,0.1); color: var(--text); padding: 6px 14px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s; font-family: var(--font); }}
.copy-btn:hover {{ background: rgba(255,255,255,0.15); transform: translateY(-1px); }}
.copy-btn.copied {{ background: var(--green-bg); color: var(--green); border-color: rgba(45, 212, 191, 0.4); }}

/* Error log */
.err-block {{ background: #030305; border: 1px solid rgba(255, 94, 94, 0.2); border-radius: 12px; padding: 20px; max-height: 350px; overflow-y: auto; box-shadow: inset 0 0 30px rgba(255, 94, 94, 0.03); }}
.err-block::-webkit-scrollbar {{ width: 6px; height: 6px; }}
.err-block::-webkit-scrollbar-thumb {{ background: rgba(255, 94, 94, 0.3); border-radius: 6px; }}
.err-block::-webkit-scrollbar-corner {{ background: transparent; }}
.err-block pre {{ color: #fca5a5; font-family: var(--mono); font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; font-weight: 400; text-shadow: 0 0 1px rgba(252, 165, 165, 0.2); }}
.err-lbl {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--red); font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
.err-lbl::before {{ content: ''; width: 8px; height: 8px; border-radius: 50%; background: var(--red); box-shadow: 0 0 8px var(--red); }}

/* Chat panels (retained style exactly but slightly matched design) */
.chat-cta {{ background: linear-gradient(135deg, rgba(139, 124, 247, 0.1), rgba(249, 115, 22, 0.05)); border: 1px solid rgba(139, 124, 247, 0.3); border-radius: var(--radius); padding: 24px 32px; margin-top: 32px; margin-bottom: 16px; display: flex; align-items: center; gap: 20px; cursor: pointer; transition: all 0.3s ease; box-shadow: 0 10px 30px rgba(139, 124, 247, 0.1); backdrop-filter: blur(10px); }}
.chat-cta:hover {{ border-color: rgba(139, 124, 247, 0.6); background: linear-gradient(135deg, rgba(139, 124, 247, 0.15), rgba(249, 115, 22, 0.1)); transform: translateY(-3px) scale(1.01); box-shadow: 0 15px 40px rgba(139, 124, 247, 0.2); }}
.chat-cta-icon {{ width: 56px; height: 56px; border-radius: 16px; background: linear-gradient(135deg, #8b7cf7, #f97316); display: flex; align-items: center; justify-content: center; font-size: 24px; flex-shrink: 0; box-shadow: 0 8px 20px rgba(139, 124, 247, 0.4); }}
.chat-cta-body h4 {{ font-size: 17px; font-weight: 700; color: #fff; margin-bottom: 4px; letter-spacing: -0.3px; }}
.chat-cta-body p {{ font-size: 14px; color: var(--muted); line-height: 1.5; font-weight: 400; }}
.chat-cta-arrow {{ margin-left: auto; color: #fff; font-size: 24px; transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); background: rgba(255,255,255,0.1); width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; border-radius: 50%; }}
.chat-cta:hover .chat-cta-arrow {{ transform: translateX(5px); background: var(--accent); }}

#chat-toggle {{ position: fixed; bottom: 30px; right: 30px; width: 64px; height: 64px; border-radius: 50%; background: linear-gradient(135deg, #8b7cf7, #f97316); border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 28px; box-shadow: 0 10px 30px rgba(139, 124, 247, 0.5); z-index: 1000; transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); }}
#chat-toggle:hover {{ transform: scale(1.1) translateY(-2px); box-shadow: 0 15px 40px rgba(139, 124, 247, 0.6); }}
#chat-panel {{ position: fixed; bottom: 106px; right: 30px; width: 440px; height: 600px; max-height: calc(100vh - 120px); border-radius: 20px; box-shadow: 0 30px 80px rgba(0, 0, 0, 0.8), 0 0 0 1px var(--border); z-index: 999; display: none; flex-direction: column; overflow: hidden; transition: width 0.3s ease, height 0.3s ease; opacity: 0; transform: translateY(20px); }}
#chat-panel.open {{ display: flex; animation: slideUp 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards; }}
@keyframes slideUp {{ to {{ opacity: 1; transform: translateY(0); }} }}
#chat-panel.expanded {{ width: 800px; height: calc(100vh - 120px); max-width: calc(100vw - 60px); }}
#chat-hdr {{ padding: 18px 20px; background: rgba(10, 10, 15, 0.8); backdrop-filter: blur(10px); border-bottom: 1px solid rgba(255,255,255,0.05); display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
#chat-hdr h3 {{ font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 10px; flex: 1; color: #fff; }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 10px var(--green); animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%, 100% {{ opacity: 1; transform: scale(1); }} 50% {{ opacity: 0.5; transform: scale(0.8); }} }}
.chat-btn {{ background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; width: 32px; height: 32px; border-radius: 8px; cursor: pointer; font-size: 14px; transition: all 0.2s; display: flex; align-items: center; justify-content: center; }}
.chat-btn:hover {{ background: rgba(255,255,255,0.15); transform: translateY(-1px); }}
#chat-msgs {{ flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; scroll-behavior: smooth; background: rgba(15, 15, 20, 0.6); }}
#chat-msgs::-webkit-scrollbar {{ width: 6px; }}
#chat-msgs::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.1); border-radius: 6px; }}

.msg {{ max-width: 85%; padding: 14px 18px; border-radius: 14px; font-size: 14px; line-height: 1.6; animation: fadeIn 0.3s ease; word-break: break-word; font-weight: 400; }}
@keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
.msg.user {{ background: linear-gradient(135deg, rgba(139, 124, 247, 0.2), rgba(139, 124, 247, 0.4)); align-self: flex-end; border-bottom-right-radius: 4px; color: #fff; border: 1px solid rgba(139, 124, 247, 0.3); box-shadow: 0 4px 15px rgba(139, 124, 247, 0.1); }}
.msg.ai {{ background: rgba(25, 25, 35, 0.8); align-self: flex-start; border-bottom-left-radius: 4px; border: 1px solid rgba(255,255,255,0.08); color: #e0e0e0; box-shadow: 0 4px 15px rgba(0,0,0,0.2); }}

.msg h1, .msg h2, .msg h3 {{ color: #d8b4fe; font-size: 15px; font-weight: 700; margin: 10px 0 6px; }}
.msg p {{ margin: 6px 0; }}
.msg ul, .msg ol {{ margin: 6px 0 6px 20px; }}
.msg li {{ margin: 3px 0; }}
.msg code:not(pre code) {{ background: rgba(0,0,0,0.5); padding: 2px 6px; border-radius: 4px; font-family: var(--mono); font-size: 12.5px; color: #a5f3fc; border: 1px solid rgba(255,255,255,0.05); }}
.msg pre {{ background: #08080c; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 16px; margin: 10px 0; overflow-x: auto; font-family: var(--mono); font-size: 12px; line-height: 1.6; color: #93c5fd; white-space: pre-wrap; position: relative; box-shadow: inset 0 2px 10px rgba(0,0,0,0.5); }}
.msg pre .chat-copy {{ position: absolute; top: 8px; right: 8px; background: rgba(255,255,255,0.1); border: none; color: #fff; padding: 4px 10px; border-radius: 6px; font-size: 11px; cursor: pointer; font-family: var(--font); font-weight: 600; transition: all 0.2s; }}
.msg pre .chat-copy:hover {{ background: rgba(255,255,255,0.2); }}
.msg strong {{ color: #fff; font-weight: 700; }}

.typing {{ display: flex; gap: 6px; align-items: center; padding: 14px 20px; align-self: flex-start; }}
.typing span {{ width: 8px; height: 8px; background: var(--accent); border-radius: 50%; animation: bounce 1s infinite; opacity: 0.8; }}
.typing span:nth-child(2) {{ animation-delay: 0.2s; }}
.typing span:nth-child(3) {{ animation-delay: 0.4s; }}

#chat-footer {{ padding: 16px 20px; background: rgba(10, 10, 15, 0.8); backdrop-filter: blur(10px); border-top: 1px solid rgba(255,255,255,0.05); display: flex; gap: 12px; flex-shrink: 0; align-items: flex-end; }}
#chat-input {{ flex: 1; background: rgba(0, 0, 0, 0.4); border: 1px solid rgba(255,255,255,0.1); color: #fff; padding: 12px 16px; border-radius: 12px; font-size: 14px; font-family: inherit; resize: none; outline: none; min-height: 48px; max-height: 120px; transition: all 0.2s; box-shadow: inset 0 2px 10px rgba(0,0,0,0.2); font-weight: 400; }}
#chat-input:focus {{ border-color: rgba(139, 124, 247, 0.5); background: rgba(0, 0, 0, 0.6); box-shadow: inset 0 2px 10px rgba(0,0,0,0.2), 0 0 15px rgba(139, 124, 247, 0.1); }}
#chat-input::placeholder {{ color: rgba(255,255,255,0.3); }}
#chat-send {{ background: linear-gradient(135deg, #8b7cf7, #f97316); border: none; color: #fff; width: 48px; height: 48px; border-radius: 12px; cursor: pointer; font-size: 18px; transition: all 0.2s; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 15px rgba(139, 124, 247, 0.3); }}
#chat-send:hover:not(:disabled) {{ transform: translateY(-2px); box-shadow: 0 6px 20px rgba(139, 124, 247, 0.4); }}
#chat-send:disabled {{ opacity: 0.5; cursor: not-allowed; filter: grayscale(1); box-shadow: none; }}
.ctx-note {{ font-size: 11px; color: var(--muted); text-align: center; padding: 6px 0 10px; background: rgba(10, 10, 15, 0.8); flex-shrink: 0; font-weight: 500; font-family: var(--mono); }}

.footer {{ text-align: center; padding: 50px 0; color: rgba(255,255,255,0.5); font-size: 14px; font-weight: 500; letter-spacing: 0.5px; }}
.footer-top {{ margin-bottom: 12px; }}
.footer-links {{ font-size: 13px; font-weight: 600; display: flex; justify-content: center; gap: 16px; flex-wrap: wrap; }}
.footer a {{ color: #a5b4fc; text-decoration: none; transition: all 0.2s; }}
.footer a:hover {{ color: var(--accent); text-shadow: 0 0 10px rgba(139, 124, 247, 0.4); }}

@media(max-width: 600px) {{
  #chat-panel {{ width: calc(100vw - 32px); right: 16px; height: 500px; }}
  #chat-panel.expanded {{ width: calc(100vw - 32px); height: calc(100vh - 110px); }}
  body {{ padding: 30px 16px 120px; }}
  .chat-cta {{ flex-direction: column; text-align: center; gap: 16px; padding: 24px; }}
  .chat-cta-arrow {{ display: none; }}
  .card {{ padding: 24px; }}
}}
</style>
</head>
<body>
<div class="wrap">

  <div class="hdr">
    <h1>🚨 <span>Crash-Copilot</span></h1>
    <div class="sub">{ts}</div>
    <div><span class="chip"><span class="chip-prefix">❯</span> {html_lib.escape(command)}</span></div>
  </div>

  {crash_loc_html}

  <!-- AI Diagnosis -->
  <div class="card glass">
    <div class="card-hdr"><span class="icon">✨</span> AI Diagnosis <span class="badge b-fix">Fix ready</span></div>
    <div class="ai" id="ai-content">
      {ai_html}
    </div>
  </div>

  <!-- Error Log -->
  <div class="card glass">
    <div class="card-hdr"><span class="icon">📋</span> Error Output <span class="badge b-crash">Crash trace</span></div>
    <div class="err-lbl">Stack Trace</div>
    <div class="err-block"><pre>{err_esc}</pre></div>
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

  <div class="footer">
    <div class="footer-top">Generated by <strong>Crash-Copilot v2.0</strong> &mdash; AI-Powered Debugging Agent &mdash; <a href="https://z.ai">Z.AI</a></div>
    <div class="footer-links">
      <a href="https://github.com/trueashik/crash-copilot" target="_blank">⭐ Star on GitHub</a> &bull; 
      <a href="https://github.com/trueashik/crash-copilot" target="_blank">🤝 Contribute</a> &bull; 
      <a href="https://linkedin.com/in/trueashik" target="_blank">🔗 Connect on LinkedIn</a>
    </div>
  </div>
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


def save_report(md: str, error_log: str, command: list):
    ts      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cmd_str = " ".join(command)
    html    = _build_html(md, error_log, cmd_str, ts)
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

    save_report(solution, error_log, command)


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
