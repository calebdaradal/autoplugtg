# AutoPlugTG

Forward the latest messages from your **original Telegram channel** to multiple channels and groups on a schedule. Uses your account (Pyrogram user client), so you keep admin rights and all media/animated emojis are preserved.

- **Configurable interval** (e.g. every 60 minutes)
- **Configurable destinations**: channel and group IDs or `@username`s
- **Latest N messages**: set how many of the newest messages to forward each run
- **Legacy mode**: `python forwarder.py` — simple loop, logs to console only
- **Control bot (recommended on Waifly)**: `python main.py` — BotFather bot for `/pause`, `/resume`, `/verify`, config edits, log mirroring to Telegram, and absolute **next run** times (Asia/Manila by default)

## Control bot (`main.py`)

1. Create a bot with [@BotFather](https://t.me/BotFather), copy the token.
2. Copy `.env.example` to `.env` and set at least:
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (from [my.telegram.org](https://my.telegram.org))
   - `BOT_TOKEN`
   - `ADMIN_USER_ID` — your numeric Telegram user id (e.g. from [@userinfobot](https://t.me/userinfobot))
3. Copy `config.example.yaml` to `config.yaml` and set `source_channel`, `destination_chats`, and other options (or leave placeholders and set everything via bot commands).
4. Run:

```bash
pip install -r requirements.txt
python main.py
```

On first deploy without a session file, use **`/login`** (optionally `+countrynumber` as an argument) and follow prompts in the admin chat for SMS/app code and 2FA. Logs are mirrored to the admin chat (with light throttling to avoid Telegram rate limits).

### Scheduler and pause

- State is stored in **`runtime_state.json`** (next run time in UTC, paused flag). Default on first run: **paused** until you **`/resume`**.
- **`/pause`**: stops forwarding; **does not** change the stored next run time.
- **`/resume`**: if the next run time is still in the future, the bot waits until then; if it has already passed, a cycle runs **immediately** and the next run is scheduled from the interval read **at the end of that cycle**.
- Changing **`interval_minutes`** (via `/set_interval` or YAML) while a wait is in progress does **not** change the current wait; the new interval applies when computing the **following** next run after a cycle completes.

### Bot commands (admin only)

| Command | Purpose |
|--------|---------|
| `/start` | Short help and status summary |
| `/status` | Paused state, next run (local + UTC), config summary |
| `/pause` / `/resume` | Pause or resume the scheduler |
| `/show_config` | Sends current `config.yaml` |
| `/set_source` | `/set_source <id_or_@username>` |
| `/add_target` / `/remove_target` | Add or remove a destination |
| `/set_interval` | Minutes between runs |
| `/set_messages` | `messages_to_forward` |
| `/set_delay_dest` / `/set_delay_msg` | Delays in seconds |
| `/verify` | `get_chat` on source + every destination (run after adding IDs) |
| `/dialogs` | Export dialog list (like `list_chats.py`) as a file |
| `/login` | Start user login; optional phone: `/login +63917...` |

Optional env: **`DISPLAY_TIMEZONE`** (IANA name, default `Asia/Manila`) for next-run display in `/status`.

## Legacy: `forwarder.py` only

Runs a forward cycle every `interval_minutes`, reloading `config.yaml` each time. Login uses the **console** (stdin).

```bash
python forwarder.py --once   # one cycle, then exit
python forwarder.py          # repeat forever
```

## Setup (shared)

### Telegram API credentials

1. Go to [my.telegram.org](https://my.telegram.org) and log in.
2. Open “API development tools” and create an app.
3. Set **api_id** and **api_hash** in `.env` or `config.yaml`.

### Getting channel/group IDs

Use [@userinfobot](https://t.me/userinfobot) or [@getidsbot](https://t.me/getidsbot). Channel IDs are usually negative, e.g. `-1001234567890`.

### Listing chats (optional)

```bash
python list_chats.py
```

With the control bot, prefer **`/dialogs`** or **`/verify`** instead of copying files off the server.

## Config reference (`config.yaml`)

| Key | Description |
|-----|-------------|
| `interval_minutes` | Minutes between each forward run. |
| `source_channel` | Your original channel (ID or `@username`). |
| `messages_to_forward` | Number of latest messages to forward each run (default `1`). |
| `destination_chats` | List of channel/group IDs or `@username`s. |
| `delay_between_destinations_seconds` | Pause between each destination (default `2`). |
| `delay_between_messages_seconds` | Pause between each message when forwarding (default `1`). |

API credentials: `.env` as `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` (env overrides config). When using the control bot, saving settings from commands does **not** write API keys to disk if they come from the environment.

## Files

- `main.py` — control bot + scheduler + Pyrogram user client (recommended for Waifly)
- `forwarder.py` — forward logic and legacy CLI loop
- `scheduler.py` — pause-aware scheduler with absolute `next_run_at`
- `bot_app.py` — Telegram Bot API handlers
- `settings.py` — load/save `config.yaml` (optional `ruamel.yaml` for comment-preserving writes)
- `runtime_state.json` — scheduler state (created at runtime; gitignored)
- `config.yaml` — your settings (create from `config.example.yaml`)
- Session files — created by Pyrogram after login (keep private)
