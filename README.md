# AnkiBot, an AI-Powered Anki Flashcard Generator for Telegram

A Telegram bot that generates expert-level Anki flashcards using AI. Send it a word in any supported language, and it creates a detailed definition card with examples, synonyms, and mnemonics, then sends you the `.apkg` file to import directly into Anki/AnkiDroid.

## Features

- **Multilingual**: English, French, Dutch (easily extendable)
- **AI-powered definitions**: Uses Groq or OpenRouter (free tier available)
- **Auto-generates .apkg files**: Ready to import into Anki
- **Android-friendly**: One-tap import into AnkiDroid via inline button
- **Continuous mode**: Create multiple cards without retyping commands
- **Cloud-ready**: Deploys on Render.com (free tier) with UptimeRobot keep-alive

## Setup Guide

### Prerequisites

- A [Telegram](https://telegram.org/) account
- A [Groq](https://console.groq.com/) API key (free) **OR** an [OpenRouter](https://openrouter.ai/) API key (free)
- A [GitHub](https://github.com/) account
- A [Render](https://render.com/) account (free)

### Step 1: Create your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name and username for your bot
4. Copy the **API token** you receive

### Step 2: Get an AI API Key

**Option A - Groq (much faster, but worse models):**
1. Go to [console.groq.com](https://console.groq.com/)
2. Create a free account
3. Go to API Keys → Create new key
4. Copy your key

**Option B - OpenRouter (more models, but slower):**
1. Go to [openrouter.ai](https://openrouter.ai/)
2. Create a free account
3. Go to [Keys](https://openrouter.ai/keys) → Create Key
4. Copy your key

### Step 3: Fork & Deploy

1. **Fork this repository** on GitHub (click the "Fork" button above)

2. **Create a Web Service on Render:**
   - Go to [render.com](https://render.com) → New → Web Service
   - Connect your forked GitHub repo
   - Configure:
     | Setting | Value |
     |---|---|
     | Runtime | Python |
     | Build Command | `pip install -r requirements.txt` |
     | Start Command | `python bot.py` |
     | Instance Type | Free |

3. **Add Environment Variables** on Render (Environment tab):
   | Variable | Value |
   |---|---|
   | `TELEGRAM_TOKEN` | Your BotFather token |
   | `GROQ_API_KEY` | Your Groq key (if using Groq) |
   | `OPENROUTER_API_KEY` | Your OpenRouter key (if using OpenRouter) |

   > You need at least one of `GROQ_API_KEY` or `OPENROUTER_API_KEY`.

4. **Deploy** - Click "Create Web Service". Your bot will be live in ~2 minutes.

### Step 4: Keep it alive (optional but recommended)

Render's free tier sleeps after 15 minutes of inactivity. To prevent this:

1. Go to [uptimerobot.com](https://uptimerobot.com) → Create a free account
2. Add a new monitor:
   - Type: `HTTP(s)`
   - URL: `https://YOUR-APP.onrender.com/health`
   - Interval: `5 minutes`

## Local Development

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/AnkiBot.git
cd AnkiBot

# Install dependencies
pip install -r requirements.txt

# Create a .env file with your keys
echo "TELEGRAM_TOKEN=your_token_here" > .env
echo "GROQ_API_KEY=your_key_here" >> .env

# Run
python bot.py
```

## Project Structure

```
AnkiBot/
├── bot.py             # Main bot code (polling + webhook modes)
├── requirements.txt   # Python dependencies
├── .env               # Your API keys (local only, never commit!)
└── .gitignore         # Excludes .env and temp files
```

## Customization

**Add more languages:** Edit the keyboard in the `start_creation()` function:
```python
keyboard = [["🇬🇧 English", "🇫🇷 Français", "🇳🇱 Nederlands", "🇪🇸 Español"]]
```

**Change the AI model:** Edit the `MODEL` variable or the model selection logic at the top of `bot.py`.

## Disclaimer

**USE AT YOUR OWN RISK.** This software is provided without warranty of any kind. I am **not responsible** for any damages, data loss, API charges, broken flashcards, existential crises about vocabulary, or any other issues arising from the use of this software. You are solely responsible for your own API keys, deployments, and usage. By using this software, you agree that you understand the risks and accept full responsibility.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/F1F21UOFHY)
