# Discord Admin Bot

A Discord bot for bulk automation, security audits, and server health recommendations.

## Features

- **Bulk Tasks** — Message purging, member pruning, bulk role assign/remove, bulk channel create/delete
- **Security Audits** — Checks permissions, @everyone overrides, dangerous bot permissions, channel overwrites, 2FA enforcement
- **Server Audits** — Dead channel detection, missing onboarding channels, role hierarchy issues, branding gaps, channel organization
- **Scheduled** — Both audit types run automatically on a configurable interval
- **Role-gated** — All commands restricted to a single configured admin role, no exceptions

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your bot
- Go to https://discord.com/developers/applications
- Create a New Application → Bot
- Enable **Server Members Intent** and **Message Content Intent** under Bot > Privileged Gateway Intents
- Copy your bot token

### 3. Configure
Edit `config.yaml`:
```yaml
bot:
  token: "YOUR_BOT_TOKEN_HERE"

admin_role: "Bot Admin"        # Exact role name — everyone needs this including server owner

log_channel_id: 123456789      # Paste your channel IDs here
audit_channel_id: 123456789

intervals:
  security_audit: 24           # Hours between security audits
  server_audit: 168            # Hours between server audits (168 = weekly)
```

### 4. Invite the bot
Generate an invite URL in the Discord Developer Portal with these permissions:
- Manage Roles
- Manage Channels
- Kick Members
- Ban Members
- Manage Messages
- Read Message History
- Send Messages
- View Channels

### 5. Create the admin role
In your Discord server, create a role named exactly as set in `admin_role` in config.yaml and assign it to yourself and any other admins.

### 6. Run
```bash
python bot.py
```

---

## Commands

| Command | Description |
|---|---|
| `!bulkdelete #channel 50` | Delete last N messages in a channel (max 100) |
| `!prunembers 30` | Prune roleless inactive members (up to 30 days) |
| `!bulkroleadd @Role` | Add a role to all members who don't have it |
| `!bulkroleremove @Role` | Remove a role from all members who have it |
| `!bulkcreatechannels "Category" name1 name2` | Create multiple channels in a category |
| `!bulkdeletechannels "Category"` | Delete all channels in a category |
| `!tasklogs` | Show recent bulk task history |
| `!securityaudit` | Manually run a security audit |
| `!lastaudit` | Show last security audit findings |
| `!serveraudit` | Manually run a server audit |
| `!lastserveraudit` | Show last server audit findings |

All commands require the admin role configured in `config.yaml`.

---

## GitHub Actions Deployment

The workflow deploys to your self-hosted Windows runner as a Windows Scheduled Task (same pattern as DiscordVoiceDatabase).

### Required GitHub Secrets
Set these under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|---|---|
| `TOKEN` | Your Discord bot token |
| `ADMIN_ROLE` | The admin role name (e.g. `Bot Admin`) |
| `LOG_CHANNEL_ID` | Discord channel ID for general bot logs |
| `AUDIT_CHANNEL_ID` | Discord channel ID for audit results |

### What the workflow does
1. Lints with flake8
2. Robocopy's source to `C:\apps\DiscordServerAudit\src`
3. Creates/updates a venv at `C:\apps\DiscordServerAudit\.venv`
4. Sets all secrets as machine-level env vars via `setx`
5. Stops any running instance
6. Registers/restarts a Scheduled Task (`DiscordServerAudit`) that runs at startup under SYSTEM with auto-restart on failure

---

## Adding Gemini Integration (next step)

Install the Gemini SDK:
```bash
pip install google-generativeai
```

Then in any cog, you can wire in Gemini for AI-powered recommendations:
```python
import google.generativeai as genai

genai.configure(api_key="YOUR_GEMINI_API_KEY")
model = genai.GenerativeModel("gemini-pro")
response = model.generate_content(f"Given these server audit findings: {findings}, what are your top 3 recommendations?")
```
