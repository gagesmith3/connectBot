# Slack Bot Setup Guide for Connect Bot

## Step 1: Create a Slack App

1. Go to https://api.slack.com/apps
2. Click **"Create an App"** → Choose **"From scratch"**
3. **App Name**: `Connect Bot` (or your preferred name)
4. **Workspace**: Select your workspace
5. Click **"Create App"**

---

## Step 2: Set Up Bot Token Scopes

1. In the left sidebar, go to **"OAuth & Permissions"**
2. Under **"Scopes"**, click **"Add an OAuth Scope"**
3. Add these **Bot Token Scopes**:
   - `app_mentions:read` - Listen for bot mentions
   - `chat:write` - Send messages
   - `im:history` - Read DM history
   - `im:read` - Read DMs
   - `im:write` - Send DMs
   - `commands` - Slash commands

4. Scroll to **"OAuth Tokens for Your Workspace"** and click **"Install to Workspace"**
5. **Trust & Authorize** the app
6. Copy the **"Bot User OAuth Token"** (starts with `xoxb-`) and save it

---

## Step 3: Enable Events

1. Go to **"Event Subscriptions"** in the sidebar
2. Toggle **"Enable Events"** to ON
3. For **"Request URL"**, enter your bot's public URL:
   ```
   https://your-rpi-ip:3000/slack/events
   ```
   *(See deployment instructions for tunneling/firewall setup)*

4. Under **"Subscribe to bot events"**, add:
   - `app_mention` - When bot is mentioned
   - `message.im` - Direct messages to bot
   - `message.mpim` - Group DMs

5. Click **"Save Changes"**

---

## Step 4: Create Slash Commands (Optional)

1. Go to **"Slash Commands"** in the sidebar
2. Click **"Create New Command"**

**Example commands:**

| Command | Request URL | Description |
|---------|-------------|-------------|
| `/backlog` | Your bot URL + `/slack/commands/backlog` | Check current backlog |
| `/eta` | Your bot URL + `/slack/commands/eta` | Get ETA estimate |
| `/heading` | Your bot URL + `/slack/commands/heading` | Get heading status |

---

## Step 5: Get Your Credentials

From the **"Basic Information"** tab, copy:
- **Signing Secret** (under "App Credentials")
- **Bot Token** (from OAuth & Permissions, saved earlier)

Store these as environment variables:
```bash
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_SIGNING_SECRET=your-signing-secret
```

---

## Step 6: Network Access (For RPi Deployment)

Since your bot runs on a local RPi, you have two options:

### Option A: Firewall Port Forwarding (Simple)
```bash
# On your router/firewall:
# Forward external port 443 (HTTPS) to RPi port 3000
# Then use: https://your-public-ip/slack/events
```

### Option B: Use a Tunnel (Recommended for Development)
```bash
# Install ngrok: https://ngrok.com/
ngrok http 3000
# Copy the HTTPS URL provided
# Use in Slack Event Subscriptions: https://xxx.ngrok.io/slack/events
```

### Option C: Direct Local Network (If Slack Enterprise)
If your Slack workspace is on the same network, you can use:
```
https://192.168.1.x:3000/slack/events
```

---

## Troubleshooting

- **Request URL not verifying?**
  - Make sure your bot is running and responding to verification challenges
  - Check firewall/network is allowing the connection
  - Verify HTTPS is enabled

- **Bot not responding to mentions?**
  - Check `SLACK_BOT_TOKEN` environment variable
  - Verify bot is in the channel
  - Check bot event subscriptions

- **"Invalid request signature" errors?**
  - Verify `SLACK_SIGNING_SECRET` is correct
  - Make sure you're using HTTPS (required by Slack)

---

## Next Steps

1. Save credentials in `.env.example` and create `.env`
2. Install requirements: `pip install -r requirements.txt`
3. Run the bot: `python main.py`
4. Test in Slack: `@Connect Bot what's the current backlog?`
