# 🏏 CricBeastBot

A production-ready Telegram cricket game bot. Works in **private DMs** and
**groups/supergroups** at the same time, supports many groups
simultaneously, and never mixes up one chat's game with another's.

## Features

- **Solo Game** — group queue (`/join`, `/leavesolo`, `/startsolo`),
  hand-cricket number-selection gameplay, batter picks in the group, bowler
  picks privately in DM, over system, scoreboards, wickets, final results.
- **Team Game** — create/join teams, captain permissions, 2-innings team
  matches reusing the same ball-by-ball engine.
- **Tournament** — registration, automatic knockout bracket (Quarter
  Final → Semi Final → Final) with byes, 1v1 mini-matches, auto-advance,
  Hall of Fame recognition for the champion.
- **Hall of Fame & Profiles** — persistent stats (matches, wins, runs,
  wickets, highest score, tournaments won, win rate).
- **Owner/Admin cheat panel** — Force Result, Set Number, Force Wicket, Add
  Runs, Skip Ball/Over, Reset/End Game, Set Player, View Live Game, Lock
  Game — restricted to `BOT_OWNER_ID` (a Telegram user ID, never a
  username), every action logged.
- **Secret live game log** — every resolved ball (with both hidden numbers)
  is streamed to a private admin log channel, never shown to players.
- **Spam-free UI** — one continuously-edited status message per game
  instead of a message-per-ball; debounced/rate-limited callbacks.
- **Anti-cheat** — every action is validated against user_id, chat_id,
  game_id, and turn before anything is mutated.
- **Database-backed** — SQLite for local dev, PostgreSQL for production,
  selected purely via `DATABASE_URL`. No important state lives only in RAM.

## Project layout

```
CricBeastBot/
├── app/
│   ├── bot.py            # Application wiring / handler registration
│   ├── config.py         # Environment-driven settings
│   ├── handlers/         # Telegram command & callback handlers
│   ├── game/             # Core engine: turns, scoring, solo/team/tournament
│   ├── database/         # SQLAlchemy models + async engine/session
│   ├── keyboards/        # InlineKeyboardMarkup builders
│   ├── services/         # DM delivery, media rotation, logging, leaderboard
│   └── utils/            # Permissions, rate limiting, formatting helpers
├── main.py
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── cricbeastbot.service
```

## How the game works

1. `/start` in a group or DM shows the main menu.
2. **🏏 Solo Game → 3/6 Balls** opens a queue: `/join`, `/leavesolo`,
   `/startsolo` (or the inline buttons). The game auto-starts once enough
   players have joined, or can be force-started by the creator, a group
   admin, or the bot owner.
3. Each ball: the **bowler** gets a private DM with a 1–6 number keyboard;
   the **batter** picks a number from buttons under the group's status
   message. Neither number is revealed until both are in.
4. Same number = **OUT**. Different numbers = batter scores runs equal to
   their own number (configurable in `app/game/scoring.py`).
5. The batter keeps batting across overs (bowler rotates every completed
   over) until dismissed; then the next queued player bats. The game ends
   once everyone has batted; highest score wins.
6. Stats and the Hall of Fame update automatically.

Team Game and Tournament reuse this exact same engine per innings/match —
see `app/game/team.py` and `app/game/tournament.py`.

## Local setup

```bash
git clone <this project>
cd CricBeastBot
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set BOT_TOKEN and BOT_OWNER_ID at minimum

python main.py
```

By default `DATABASE_URL` points at a local SQLite file
(`./cricbeast.db`) — nothing else to configure for local testing.

### Getting your values

- **BOT_TOKEN** — from [@BotFather](https://t.me/BotFather) (`/newbot`).
- **BOT_OWNER_ID** — your numeric Telegram user ID, e.g. from
  [@userinfobot](https://t.me/userinfobot). Never use a username here.
- **LOG_CHANNEL_ID** (optional but recommended) — create a private channel,
  add the bot as an admin, and use the channel's numeric ID (starts with
  `-100...`). You can get it by forwarding a message from the channel to
  [@userinfobot](https://t.me/userinfobot), or by temporarily enabling
  debug logging.

## Production deployment (Ubuntu VPS)

```bash
# 1. System packages
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git

# 2. Get the code
sudo mkdir -p /opt/CricBeastBot
sudo chown $USER:$USER /opt/CricBeastBot
git clone <this project> /opt/CricBeastBot
cd /opt/CricBeastBot

# 3. Virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
nano .env   # fill in BOT_TOKEN, BOT_OWNER_ID, DATABASE_URL, LOG_CHANNEL_ID

# 5. (Optional) PostgreSQL instead of SQLite
sudo apt install -y postgresql
sudo -u postgres createuser cricbeast -P
sudo -u postgres createdb -O cricbeast cricbeast
# then set DATABASE_URL=postgresql+asyncpg://cricbeast:<password>@localhost:5432/cricbeast

# 6. First run (creates tables, verifies the token)
python main.py   # Ctrl+C once you see "CricBeastBot is up."

# 7. Run as a systemd service
sudo useradd -r -s /usr/sbin/nologin cricbeast || true
sudo chown -R cricbeast:cricbeast /opt/CricBeastBot
sudo mkdir -p /var/log/cricbeastbot && sudo chown cricbeast:cricbeast /var/log/cricbeastbot
sudo cp cricbeastbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cricbeastbot
sudo systemctl status cricbeastbot

# 8. Logs
sudo journalctl -u cricbeastbot -f
tail -f /var/log/cricbeastbot/bot.log

# 9. Restart after an update
cd /opt/CricBeastBot && git pull
source venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart cricbeastbot
```

### Optional: Docker deployment

```bash
cp .env.example .env   # fill it in
docker compose up -d --build
docker compose logs -f bot
```

`docker-compose.yml` includes an optional PostgreSQL service — only needed
if you point `DATABASE_URL` at it; SQLite (the default) needs nothing
extra and persists to the mounted `./data` volume.

## Configuration reference

See `.env.example` for the full list. Required: `BOT_TOKEN`,
`BOT_OWNER_ID`. Everything else has a sensible default or is optional
(media URLs, log channel, gameplay tuning).

## Known simplifications

- Team/Tournament matches always play out both full innings (no early
  "chase completed" cutoff once the target is passed) — kept intentionally
  simple; wire in a target-check in `app/game/team.py` /
  `app/game/tournament.py` if you want real-cricket chase behavior.
- The rate limiter and callback debounce guard are in-memory, which is
  correct for the standard single-process polling deployment this project
  ships with. If you horizontally scale to multiple bot processes, move
  `app/utils/rate_limit.py` to a shared store (e.g. Redis).
- Schema changes after your first production deploy should go through
  Alembic — see `app/database/migrations/README.md`.
