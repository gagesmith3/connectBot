# Connect Bot - Quick Reference & Testing Guide

## Installation Quick Reference

```bash
# 1. Clone/navigate to connectBot
cd \\192.168.1.6\int_pc_data\connectBot

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env
# Edit .env with your credentials in a text editor

# 5. Run the bot
python main.py
```

## Testing Checklist

Before deploying to production, verify:

- [ ] FastAPI server is running and accessible
  - Test: `curl http://localhost:8100/docs` or `curl http://<fastapi-vm-ip>:8100/docs`
  - Your bot's FastAPI_BASE_URL points to correct location

- [ ] All .env variables are set correctly
  - `SLACK_BOT_TOKEN` starts with `xoxb-`
  - `SLACK_SIGNING_SECRET` is 32+ chars
  - `LLM_PROVIDER` is set to `ollama` or `openrouter`
  - If local: `OLLAMA_BASE_URL` and `OLLAMA_MODEL` are valid
  - If cloud: `OPENROUTER_API_KEY` starts with `sk-or-v1-`
  - `FASTAPI_API_KEY` matches your API server
  - `FASTAPI_TIMEOUT_SECONDS`, `FASTAPI_MAX_RETRIES`, and `FASTAPI_RETRY_BACKOFF_SECONDS` are set for your network

- [ ] Slack bot is installed to your workspace
  - Check Settings > Install to Workspace
  - Bot should appear in workspace member list

- [ ] Bot can send messages
  - Try: `@Connect Bot hi`
  - Should respond with unsupported query message

- [ ] Bot can query API
  - Try: `@Connect Bot what's the current backlog?`
  - Should fetch and display backlog data

- [ ] DM functionality works
  - Send direct message: `what's the lead time?`
  - Bot should respond

## Test Queries by Category

### Backlog & Bottlenecks
```
"What's the current backlog?"
"Show me backlog status"
"What's our biggest bottleneck?"
"How much work do we have queued?"
```

### Lead Times & ETA
```
"What would lead time be for 500 units of stud A123?"
"How long to produce 1000 units?"
"ETA for stud X234 with qty 200?"
```

### Production Status
```
"Show me heading metrics"
"What's the status of manufacturing requests?"
"How are the trimmers performing?"
"Show trimmer data"
```

### Equipment Specific
```
"Show me heading data for the drill"
"Trimmer 2 metrics"
"How many lots are in progress?"
```

## Deployment Checklist

### Before Deploying to RPi

- [ ] All tests pass locally
- [ ] `.env` created with all credentials
- [ ] Logs directory exists and is writable
- [ ] Python 3.8+ installed on target RPi
- [ ] Network connectivity verified

### RPi Setup Steps

```bash
# 1. Login to RPi
ssh pi@192.168.1.x

# 2. Navigate to bot directory
cd /path/to/connectBot

# 3. Run setup script
bash setup.sh

# 4. Test locally
python main.py

# 5. Install as service (optional)
sudo cp connectbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable connectbot
sudo systemctl start connectbot

# 6. Verify service
sudo systemctl status connectbot
tail -f logs/connectbot.log
```

### Monitoring on RPi

```bash
# Check service status
sudo systemctl status connectbot

# View logs
sudo journalctl -u connectbot -f

# Or direct log file
tail -f /path/to/connectBot/logs/connectbot.log

# Restart service
sudo systemctl restart connectbot

# Stop service
sudo systemctl stop connectbot
```

## Troubleshooting Workflow

### Issue: Bot doesn't respond

1. Check bot is running: `ps aux | grep main.py`
2. Check logs: `tail -f logs/connectbot.log`
3. Verify Slack connection: look for "Socket Mode connected" in logs
4. Test FastAPI: `curl -H "Authorization: Bearer $FASTAPI_API_KEY" http://localhost:8100/v1/metrics/backlog`

### Issue: "Invalid request signature"

1. Verify SLACK_SIGNING_SECRET is correct (copy-paste into .env)
2. Ensure HTTPS is being used (Slack requirement)
3. Check system clock is in sync (signature validation is time-based)

### Issue: local Ollama is not responding

1. Verify API is up: `curl http://127.0.0.1:11434/api/tags`
2. Verify model exists: `ollama list`
3. Test direct model run: `ollama run gemma3:4b "Reply with only: ready"`
4. If slow under load, use smaller model: `gemma3:1b`

### Issue: OpenRouter authentication error

1. Verify OPENROUTER_API_KEY is correct
2. Check API key hasn't been revoked
3. Verify usage/quota in your OpenRouter account

### Issue: Bot can connect to Slack but not FastAPI

1. Check FastAPI server is running
2. Verify FASTAPI_BASE_URL is accessible
3. Verify FASTAPI_API_KEY is correct
4. Try manual request: 
```python
import httpx
client = httpx.Client()
response = client.get(
    "http://localhost:8100/v1/metrics/backlog",
    headers={"Authorization": "Bearer YOUR_KEY"}
)
print(response.json())
```

## Network Options Summary

### Option 1: Socket Mode (Recommended)
- ✅ Best for local networks
- ✅ No firewall/network setup needed
- ✅ Easy for development and testing
- ⚠️ Requires SLACK_APP_TOKEN (Socket Mode must be enabled in Slack)

### Option 2: HTTP with ngrok (Development)
- Environment: `USE_SOCKET_MODE=false`
- Tunnel to public: `ngrok http 3000`
- Use ngrok URL in Slack Event Subscriptions
- ✅ Good for testing production setup
- ⚠️ Ngrok URL changes if restarted

### Option 3: HTTP with Firewall Port Forward (Production)
- Environment: `USE_SOCKET_MODE=false`
- Forward port 443→3000 on firewall
- Must have SSL certificates (SSL_CERT_FILE, SSL_KEY_FILE)
- ✅ More scalable
- ⚠️ Requires network/firewall knowledge

## Performance Notes

- **LLM Response Time**: depends on provider/model size (local CPU models are slower than cloud API)
- **FastAPI Query Time**: <1s typically
- **Total Response Time**: 2-3s typical
- **Rate Limiting**: No rate limiting implemented (add if needed)

## Security Notes

- Store .env in safe location (gitignore it)
- Use strong SLACK_APP_TOKEN and SLACK_SIGNING_SECRET
- Rotate API keys regularly
- Consider IP whitelisting if behind firewall
- Logs may contain sensitive data - protect log files

## Getting Help

1. Check the comprehensive README.md
2. Review SLACK_SETUP.md for Slack configuration
3. Check logs: `tail -f logs/connectbot.log`
4. Verify all .env variables are correct
5. Test FastAPI manually with curl

## Contact

For issues or enhancements, refer to the bot's repository or internal team docs.
