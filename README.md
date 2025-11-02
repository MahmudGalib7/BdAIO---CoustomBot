# 🤖 AI Olympiad Discord Bot

[![Python](https://img.shields.io/badge/Python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> A comprehensive Discord bot for AI/ML competition management and community moderation, featuring live Kaggle integration and automated contest tracking.

## 📋 Table of Contents

- [Features](#-features)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Commands](#-commands)
- [Usage](#-usage)
- [Architecture](#-architecture)
- [Contributing](#-contributing)

## ✨ Features

### 🛡️ Moderation System

- **Bad Word Detection**

  - Filters profanity with special character handling (`h3ll0`, `h.e.l.l.o`)
  - Configurable threshold (default: 3 warnings → kick)
  - DM warnings to offenders
  - Public warnings in designated channel

- **Spam Prevention**

  - Detects 4+ messages in 3 seconds
  - Auto-timeout for 5 minutes
  - Warnings sent to moderation channel

- **Welcome System**
  - Greets new members with customized embeds
  - Server info and rules display

### 🏆 Contest & Kaggle Integration

- **Live Kaggle Leaderboards**
  - Real-time score fetching from Kaggle API
  - Automatic participant matching
  - Smart name normalization (handles display names vs usernames)
- **Contest Management**

  - Duration-based contest polls
  - Emoji-based registration (👍)
  - Automatic Kaggle ID collection via DM
  - Persistent participant tracking

- **Winner System**
  - Automatic "🏆 Contest Winner" role assignment
  - Role awarded to top performer among participants
  - Auto-removal from previous winners
  - Beautiful leaderboard embeds with medals (🥇🥈🥉)

### 💾 Data Persistence

- **Dual Storage System**
  - `kaggle_ids.json` - Permanent Kaggle ID storage
  - `contest_participants.json` - Per-contest participant tracking
  - Automatic save/load on bot restart

### 📊 Statistics & Engagement

- **Daily Stats**

  - Total members, online count
  - Active contests and warnings
  - 24-hour automated updates

- **Motivation System**
  - Random AI/ML tips every 6 hours
  - Inspirational messages
  - Educational content delivery

## 🚀 Installation

### Prerequisites

- Python 3.13+
- Discord Bot Token ([Create one here](https://discord.com/developers/applications))
- Kaggle API Credentials ([Get them here](https://www.kaggle.com/settings))

### Setup

1. **Clone the repository**

```bash
git clone https://github.com/yourusername/ai-olympiad-bot.git
cd ai-olympiad-bot
```

2. **Create virtual environment**

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**

```bash
pip install -r requirements.txt
```

4. **Configure environment variables**

Create a `.env` file in the root directory:

```env
DISCORD_TOKEN=your_discord_bot_token
WARNING_CHANNEL_ID=your_warning_channel_id
LEADERBOARD_CHANNEL_ID=your_leaderboard_channel_id
STATS_CHANNEL_ID=0
KAGGLE_USERNAME=your_kaggle_username
KAGGLE_KEY=your_kaggle_api_key
```

5. **Run the bot**

```bash
python bot.py
```

## ⚙️ Configuration

### Required Permissions

The bot needs the following Discord permissions:

- Manage Roles
- Manage Messages
- Send Messages
- Embed Links
- Add Reactions
- Moderate Members (for timeouts)
- Read Message History

### Channel IDs

Get channel IDs by enabling Developer Mode in Discord:

1. User Settings → Advanced → Developer Mode
2. Right-click channel → Copy ID

## 📝 Commands

### User Commands

| Command              | Description               | Example                  |
| -------------------- | ------------------------- | ------------------------ |
| `!help`              | Interactive help menu     | `!help`                  |
| `!my_kaggle [id]`    | View/update Kaggle ID     | `!my_kaggle mahmudgalib` |
| `!show_participants` | List contest participants | `!show_participants`     |
| `!ping`              | Check bot latency         | `!ping`                  |

### Admin Commands (Requires Administrator Permission)

| Command                              | Description                   | Example                                 |
| ------------------------------------ | ----------------------------- | --------------------------------------- |
| `!create_contest <hours> <question>` | Create contest poll           | `!create_contest 48 Who wants to join?` |
| `!set_competition <id>`              | Set active Kaggle competition | `!set_competition digit-recognizer`     |
| `!contest_leaderboard`               | Display live Kaggle rankings  | `!contest_leaderboard`                  |
| `!warn <user> [reason]`              | Issue manual warning          | `!warn @user Spam`                      |
| `!stats`                             | Force stats update            | `!stats`                                |

## 💡 Usage

### Running a Contest

1. **Create the contest**

```
!create_contest 48 Who wants to join this week's ML challenge?
```

The bot posts a poll embed with 👍 reaction.

2. **Users join**

- Click 👍 on the poll
- Bot DMs asking for Kaggle ID
- Reply with your Kaggle username

3. **Set the competition**

```
!set_competition bdaio-2025-margin-masters
```

Bot verifies the competition exists on Kaggle.

4. **View live leaderboard**

```
!contest_leaderboard
```

Bot fetches live rankings and awards winner role to top participant!

### Example Workflow

```
Admin: !create_contest 72 Join our weekly Kaggle competition!
[Users react with 👍]

Bot (DM): Please provide your Kaggle ID/username
User: mahmudgalib
Bot: ✅ Thanks! You're registered for the contest.

Admin: !set_competition playground-series-s5e1
Bot: 🎯 Competition Set! Now tracking: Playground Series S5E1

Admin: !contest_leaderboard
Bot: [Shows live leaderboard with ranks, scores, and awards 🏆 role]
```

## 🏗️ Architecture

### Tech Stack

- **Language:** Python 3.13+
- **Framework:** Discord.py 2.3+
- **API:** Kaggle API (kagglesdk)
- **Storage:** JSON file-based persistence

### Project Structure

```
discord-bot/
├── bot.py                          # Main bot code
├── requirements.txt                # Python dependencies
├── .env                            # Environment variables (gitignored)
├── kaggle_ids.json                 # Permanent Kaggle ID storage
├── contest_participants.json       # Current contest participants
└── README.md                       # This file
```

### Key Features

**Intelligent Name Matching**

- Normalizes team names and Kaggle IDs
- Removes non-alphanumeric characters
- Case-insensitive comparison
- Example: "Mahmud Galib" matches "mahmudgalib"

**Error Handling**

- Comprehensive try/except blocks
- Detailed terminal logging
- User-friendly Discord error messages
- Auto-cleanup of error messages (15s)

**Background Tasks**

- Daily stats update (every 24 hours)
- Motivation tips (every 6 hours)
- Automatic startup after bot ready

## 📦 Dependencies

```txt
discord.py>=2.3.0
python-dotenv>=1.0.0
kaggle>=1.6.0
```

## 🔧 Development

### Running in Development

```bash
# Activate virtual environment
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Run with auto-reload (using nodemon or similar)
python bot.py
```

### Debug Mode

The bot includes extensive DEBUG logging. Check terminal output for:

- Command execution flow
- Leaderboard matching process
- Role assignment details
- API interactions

## 🐛 Known Issues & Solutions

### Issue: Competition not found

**Solution:** Use the exact competition slug from Kaggle URL

```
❌ !set_competition Margin Masters
✅ !set_competition bdaio-2025-margin-masters
```

### Issue: Name matching fails

**Solution:** Already handled! The bot normalizes names automatically.

### Issue: Bot forgets Kaggle IDs

**Solution:** Fixed! IDs stored in permanent `kaggle_ids.json`

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Discord.py](https://github.com/Rapptz/discord.py) - Discord API wrapper
- [Kaggle API](https://github.com/Kaggle/kaggle-api) - Kaggle integration
- AI Olympiad community for testing and feedback

## 📞 Support

For issues, questions, or suggestions:

- Open an [Issue](https://github.com/yourusername/ai-olympiad-bot/issues)
- Join our [Discord Server](https://discord.gg/your-invite)

## 🌟 Features Coming Soon

- [ ] Team-based competitions
- [ ] Multi-competition tracking
- [ ] Advanced statistics with graphs
- [ ] Integration with other ML platforms
- [ ] Competition reminders/notifications
- [ ] Custom leaderboard themes

---

**Built with ❤️ for the AI/ML community**

_Making competitive machine learning more accessible and fun!_ 🚀
