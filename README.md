# Connect Bot - AI-Powered Slack Bot for Business Operations

A Slack bot that supports either OpenRouter or local Ollama models for natural language understanding, and queries your local FastAPI server for real-time data.

## Features

✨ **AI-Powered**: Ask questions in natural language - bot understands intent and fetches the right data
📊 **Real-Time Metrics**: Access backlog, lead times, bottlenecks, and production metrics
🏭 **Business Operations**: Designed for manufacturing - asks about:
  - Lead times and ETA estimates
  - Current backlog and bottlenecks
  - Heading equipment metrics
  - Manufacturing request lot states
  - Trimmer performance data

## Quick Start

### 1. Set Up Slack App

Follow [SLACK_SETUP.md](./SLACK_SETUP.md) for step-by-step instructions to:
- Create a Slack app
- Get your bot token and signing secret
- Enable event subscriptions
- Configure slash commands (optional)

### 2. Install Dependencies

```bash
# Clone/navigate to connectBot directory
cd connectBot

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Copy example to .env and fill in your credentials
cp .env.example .env
```

Edit `.env` with:
- `SLACK_BOT_TOKEN` - from Slack app settings
- `SLACK_SIGNING_SECRET` - from Slack app settings
- `SLACK_APP_TOKEN` - for Socket Mode (optional)
- `FASTAPI_BASE_URL` - where your FastAPI server runs (e.g., http://192.168.1.x:8100)
- `FASTAPI_API_KEY` - your FastAPI API key
- `FASTAPI_TIMEOUT_SECONDS` - per-request timeout in seconds
- `FASTAPI_MAX_RETRIES` - retries for transient FastAPI failures
- `FASTAPI_RETRY_BACKOFF_SECONDS` - backoff multiplier between retries
- `LLM_PROVIDER` - `openrouter` or `ollama`
- `OLLAMA_BASE_URL` - local Ollama endpoint (default: http://127.0.0.1:11434)
- `OLLAMA_MODEL` - local model tag (recommended: gemma3:4b)
- `LOCAL_ONLY_FAIL_CLOSED` - `true` to avoid any external fallback in local mode
- `OPENROUTER_API_KEY` and `OPENROUTER_MODELS` - still used when `LLM_PROVIDER=openrouter`
- `USE_AGENT_MODE` - enables AI-first orchestration for general chat plus grounded business answers
- `AGENT_HISTORY_WINDOW` - short rolling history size per DM/thread for follow-up context

### 3.1 Local Ollama Setup (Windows Server)

```powershell
ollama --version
ollama serve
ollama pull gemma3:4b
ollama run gemma3:4b "Reply with only: ready"
```

Recommended `.env` values for local mode:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma3:4b
LLM_TIMEOUT_SECONDS=30
LOCAL_ONLY_FAIL_CLOSED=true
```

### 4. Run the Bot

```bash
# Start with Socket Mode (local development)
python main.py

# Or start with HTTP server (requires HTTPS tunneling for Slack)
USE_SOCKET_MODE=false python main.py
```

You should see:
```
Starting Connect Bot...
FastAPI Server: http://localhost:8100
Using event mode: Socket Mode
```

### 5. Test in Slack

1. Go to your Slack workspace
2. Mention the bot in any channel: `@Connect Bot what's the current backlog?`
3. Or send a direct message with questions

## Environment Configuration

### FastAPI on a Separate Linux VM

When moving FastAPI off the bot host, set:

```dotenv
FASTAPI_BASE_URL=http://YOUR_LINUX_VM_IP:8100
FASTAPI_API_KEY=your-api-key-here
FASTAPI_TIMEOUT_SECONDS=10
FASTAPI_MAX_RETRIES=2
FASTAPI_RETRY_BACKOFF_SECONDS=0.5
```

Operational checks:

- From connectBot host, verify reachability: `curl http://YOUR_LINUX_VM_IP:8100/docs`
- Ensure Linux VM firewall allows inbound TCP `8100` from connectBot host
- Keep FastAPI bound to private network only

### Development (Local RPi with Socket Mode)

```bash
USE_SOCKET_MODE=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_APP_TOKEN=xapp-...  # Required for Socket Mode
```

**Advantages:**
- No firewall/network setup needed
- Works on local network
- Good for development/testing

### Production (HTTP Server with ngrok/tunnel)

```bash
USE_SOCKET_MODE=false
SERVER_PORT=3000
SSL_CERT_FILE=/path/to/cert.pem
SSL_KEY_FILE=/path/to/key.pem
```

**Setup ngrok tunnel:**
```bash
# Install ngrok: https://ngrok.com/
ngrok http 3000

# Copy HTTPS URL (e.g., https://abc123.ngrok.io)
# Use in Slack Event Subscriptions: https://abc123.ngrok.io/slack/events
```

## Available Queries

The bot understands natural language queries like:

**Backlog & Bottlenecks**
- "What's the current backlog?"
- "What's our biggest bottleneck?"
- "Show me backlog status"

**Lead Times & ETA**
- "What would lead time be for 500 units of stud A123?"
- "How long to make 200 units of part X?"
- "ETA for 1000 units?"

**Production Status**
- "Show me heading metrics"
- "How are the trimmers performing?"
- "What's the status on manufacturing requests?"
- "How many lots are in progress?"

**Equipment Performance**
- "Show me heading data for the drill"
- "Trimmer 2 metrics"
- "What's the production quality?"

## Supported API Endpoints

The bot connects to your FastAPI server and can query:

| Endpoint | What It Does |
|----------|------------|
| `/v1/metrics/backlog` | Latest backlog snapshot |
| `/v1/metrics/heading` | Heading daily metrics |
| `/v1/metrics/trimmers` | Trimmer shift performance |
| `/v1/metrics/mfgreq/lots` | Manufacturing request lot states |
| `/v1/metrics/mfgreq/adjustments` | Daily mfgreq adjustments |
| `/v1/metrics/mfgreq/data-quality` | Data quality metrics |
| `/v1/metrics/mfgreq/eta` | ETA estimates |

## Architecture

```
┌─────────────────┐
│   Slack User    │
└────────┬────────┘
         │ Message/Mention
         ▼
    ┌─────────┐
    │   Bot   │ (connectBot/main.py)
    └────┬────┘
         │
    ┌────┴─────────────┬────────────────┐
    │                  │                │
    ▼                  ▼                ▼
┌─────────┐    ┌──────────────┐    ┌────────────┐
│ Slack   │    │ OpenRouter   │    │ FastAPI    │
│ API     │    │ (Intent      │    │ Server     │
│         │    │  Parser)     │    │ (8100)     │
└─────────┘    └──────────────┘    └────────────┘
                                         │
                                         ▼
                                   ┌──────────┐
                                   │  MySQL   │
                                   │Database  │
                                   └──────────┘
```

## Files Structure

```
connectBot/
├── main.py                           # Entry point
├── requirements.txt                  # Dependencies
├── .env.example                      # Environment template
├── SLACK_SETUP.md                    # Slack setup guide
├── README.md                         # This file
└── src/connectbot/
    ├── __init__.py
    ├── config.py                     # Settings management
    ├── api_client.py                 # FastAPI client
    ├── intent_parser.py              # LLM intent parser
    ├── response_formatter.py          # Slack message formatting
    ├── handlers.py                   # Slack event handlers
    └── utils/
        ├── __init__.py
        └── logging_setup.py          # Logging configuration
```

## Troubleshooting

### Bot doesn't respond to mentions

1. Check bot token: `echo $SLACK_BOT_TOKEN` (should start with `xoxb-`)
2. Verify bot is in the channel
3. Check logs: `tail -f logs/connectbot.log`
4. Make sure FastAPI server is running

### "Invalid request signature" error

- Verify `SLACK_SIGNING_SECRET` is correct
- Make sure you're using HTTPS (Slack requires it)
- Check request timestamp is within 5 minutes

### OpenRouter errors

- Verify `OPENROUTER_API_KEY` is valid: https://openrouter.ai/keys
- Verify each entry in `OPENROUTER_MODELS` is a valid model slug for your account
- If you want to stay on free credits, choose a model ending in `:free`
- LLM might not understand query - try simpler wording

### Multiple OpenRouter models

The bot supports ordered fallback through `OPENROUTER_MODELS`.

Example:

```dotenv
OPENROUTER_MODELS=openai/gpt-oss-120b:free,google/gemma-3-27b-it:free,openai/gpt-oss-20b:free
```

It will try the first model, then the next one if the request fails, and finally fall back to the local keyword parser if all models fail.

### Ollama local runtime errors

- Verify Ollama API is reachable: `curl http://127.0.0.1:11434/api/tags`
- Verify model is installed: `ollama list`
- Test model directly: `ollama run gemma3:4b "Reply with only: ready"`
- If local model calls are slow, reduce response size and consider switching to `gemma3:1b`

### AI-first mode

When `USE_AGENT_MODE=true`, the bot works in two internal modes behind one Slack experience:

- General chat: model-only conversational answers for casual questions and best-effort general knowledge
- Business questions: FastAPI-grounded answers synthesized from API results

If business data is unavailable, the bot will say so instead of guessing.

### FastAPI connection errors

- Check FastAPI server is running: `curl http://localhost:8100/docs`
- Verify `FASTAPI_BASE_URL` in `.env`
- Verify `FASTAPI_API_KEY` is correct
- Check firewall allows connection

### Socket Mode connection issues

- Verify `SLACK_APP_TOKEN` is correct (starts with `xapp-`)
- Socket Mode must be enabled in Slack app settings
- Check bot is installed to workspace

## Development

### Adding New Queries

1. **Update API Client** (`api_client.py`):
   - Add method to fetch new endpoint

2. **Update Intent Parser** (`intent_parser.py`):
   - Update `SYSTEM_PROMPT` with new endpoint options

3. **Add Response Formatter** (`response_formatter.py`):
   - Add `format_*` method for new data type

4. **Update Handlers** (`handlers.py`):
   - Add case in `_call_api()` and `_format_response()`

### Logging

Bot logs to:
- Console (INFO and above)
- File: `logs/connectbot.log` (rotating, 5MB max)

Change log level in `.env`:
```bash
LOG_LEVEL=DEBUG  # For verbose output
```

## Deployment on Raspberry Pi

### Requirements

- Python 3.8+
- Network access to FastAPI server
- Internet access (for Slack and OpenAI)

### Installation

```bash
# Update system
sudo apt update && sudo apt upgrade

# Install Python and dependencies
sudo apt install python3.9 python3-venv python3-pip

# Clone/setup bot
cd /home/pi/projects/connectBot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env with proper credentials
nano .env
```

### Run as Service (systemd)

Create `/etc/systemd/system/connectbot.service`:

```ini
[Unit]
Description=Connect Bot - Slack Bot for Operations
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/projects/connectBot
Environment="PATH=/home/pi/projects/connectBot/venv/bin"
ExecStart=/home/pi/projects/connectBot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start:
```bash
sudo systemctl enable connectbot
sudo systemctl start connectbot
sudo systemctl status connectbot
```

## Contributing

To extend the bot:

1. Add new API methods to `FastAPIClient`
2. Update `IntentParser.SYSTEM_PROMPT` with new capabilities
3. Add formatter in `ResponseFormatter`
4. Wire up in `handlers.py`

## License

Internal use only - IWT manufacturing

## Support

For issues or questions:
1. Check log file: `logs/connectbot.log`
2. Verify all `.env` values are correct
3. Test FastAPI connection manually: `curl -H "Authorization: Bearer YOUR_KEY" http://localhost:8100/docs`
4. Check Slack app permissions in workspace settings
