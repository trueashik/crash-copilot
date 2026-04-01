# 🚨 Crash-Copilot

> **AI-powered crash interceptor** — wrap any command, catch crashes, get an instant AI-diagnosed fix with a beautiful HTML report and a built-in chat companion.

---

## ✨ Features

- 🔍 **Instant AI diagnosis** — Root cause, fixed code, and bullet-point explanation
- 📋 **One-click copy** — Every code block has a clipboard button
- 💬 **Chat companion** — Ask follow-up questions with full crash context (15 messages/session)
- 🌐 **Auto-opens** a stunning styled HTML report in your browser
- 🔒 **Zero dependencies** beyond `requests` — no frameworks, no install hassle
- 🌍 **Multi-language** — Python, Node.js, Go, Rust, Java, and more

---

## 🚀 Quick Start

### 1. Clone into your project root

```bash
git clone https://github.com/your-username/crash-copilot
```

> This creates a `crash-copilot/` folder inside your project.

### 2. Add your API key

```bash
cp crash-copilot/.env.example crash-copilot/.env
```

Edit `crash-copilot/.env`:

```env
GLM_API_KEY=your_actual_api_key_here
```

Get a free key → [z.ai/manage-apikey/apikey-list](https://z.ai/manage-apikey/apikey-list)

### 3. Install once (make `ccp` a global command)

**Windows:**
```bash
crash-copilot\install.bat
```
Then open a new terminal.

**macOS / Linux:**
```bash
chmod +x crash-copilot/install.sh && crash-copilot/install.sh
```

### 4. Run

```bash
ccp python script.py
ccp node server.js
ccp cargo run
```

That's it. If your script crashes, Crash-Copilot catches it and opens the fix in your browser.

---

## 📂 Project Structure

```
crash-copilot/
├── ccp.py         ← The entire tool (single file)
├── ccp.bat        ← Windows runner
├── ccp            ← macOS/Linux runner
├── install.bat    ← Windows global install (run once)
├── install.sh     ← macOS/Linux global install (run once)
├── .env.example   ← API key template
└── script.py      ← Example bad script for testing
```

---

## 🧪 Testing It

```bash
ccp python crash-copilot/script.py
```

This deliberately crashes and demonstrates the full Crash-Copilot flow.

---

## ⚙️ Configuration

| Variable | Default | Description |
|---|---|---|
| `GLM_API_KEY` | *(required)* | Your Z.AI API key |
| `GLM_MODEL` | `glm-5` | Model to use |

The `.env` is automatically discovered from the current directory up to 6 parent levels — no matter where you run `ccp` from.

---

## 📄 License

MIT
