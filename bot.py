import os
import sys
import random
import tempfile
import shutil
import uuid
import time

# Fix Windows console encoding (cp1252 doesn't support emojis)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import asyncio
import genanki
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes

# ─── CONFIGURATION ───────────────────────────────────────────
load_dotenv()  # Loads .env locally, ignored on Render

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN environment variable is required!")
if not OPENROUTER_API_KEY and not GROQ_API_KEY:
    raise ValueError("❌ At least one API key required: OPENROUTER_API_KEY or GROQ_API_KEY!")

# Primary client: Groq (ultra-fast) if available, otherwise OpenRouter
if GROQ_API_KEY:
    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=GROQ_API_KEY,
    )
    MODEL = "llama-3.3-70b-versatile"
    print("⚡ Groq mode (fast)")
else:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    MODEL = "google/gemma-4-31b-it:free"
    print("🌐 OpenRouter mode")

# ─── TEMPORARY DOWNLOAD DIRECTORY ────────────────────────────
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "ankibot_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─── CONVERSATION HANDLER STATES ─────────────────────────────
CHOOSE_LANGUAGE, WAITING_WORD = range(2)

# ─── LANGUAGE MAPPING ────────────────────────────────────────
LANGUAGE_MAP = {
    "🇬🇧 English":   {"name": "English",    "others": ["French", "Dutch"]},
    "🇫🇷 Français":  {"name": "French",     "others": ["English", "Dutch"]},
    "🇳🇱 Nederlands": {"name": "Dutch",      "others": ["English", "French"]},
}

# ─── CORE FUNCTIONS ──────────────────────────────────────────
def generate_definition(word, language):
    """2-step agentic pipeline: Draft → Self-Review → Final output."""
    lang_info = LANGUAGE_MAP.get(language, {"name": language, "others": ["English", "French"]})
    lang_name = lang_info["name"]
    other1, other2 = lang_info["others"]

    # ── STEP 1: Generate the draft ────────────────────────────
    draft_prompt = f"""You are a world-class linguistic expert, lexicographer, and polyglot professor.
Your task is to create a PERFECT, ACCURATE dictionary entry.

Target Word: "{word}"
Target Language: {lang_name}

ABSOLUTE RULES:
1. ACCURACY IS PARAMOUNT. Every definition, example, and etymology MUST be factually correct. Do NOT invent false etymologies or incorrect meanings.
2. Output the card content in {lang_name}.
3. Format: Use HTML <b> and <i> tags ONLY. Absolutely NO markdown (no **, no ##, no *).
4. Be CONCISE. One short paragraph per section maximum.
5. Identify the most common meanings only (max 2-3 senses).

For EACH meaning, use this EXACT structure:

🏷️ <b>{word.upper()}</b> ([part of speech]) - [Common/Rare/Formal]
📖 <b>Definition:</b> <i>[clear, precise definition]</i>
💬 <b>In context:</b>
  • "[Example sentence 1]" → <i>[brief explanation of what the word/idiom means in this specific sentence]</i>
  • "[Example sentence 2]" → <i>[brief explanation of what the word/idiom means in this specific sentence]</i>
🔄 <b>Synonyms:</b> [2-3 synonyms]
🌍 <b>Translations:</b> {other1}: [translation] | {other2}: [translation]
💡 <b>Tip:</b> [One memorable mnemonic trick or etymology fact]

IMPORTANT for the Translations line:
- Provide the most natural equivalent in {other1} and {other2}.
- If no single word exists, use a short phrase (2-4 words max).
- For idioms, give the equivalent idiom in each language if one exists, otherwise a literal explanation."""

    draft_response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": draft_prompt}],
        temperature=0.3
    )
    draft = draft_response.choices[0].message.content
    if not draft:
        raise Exception("Empty response from AI model")

    # ── STEP 2: Self-review & polish ─────────────────────────
    review_prompt = f"""You are a ruthlessly precise language professor and proofreader.
Below is a draft dictionary entry for the word "{word}" in {lang_name}.

YOUR TASK — Review and improve it:
1. Are the example sentences truly natural for a native {lang_name} speaker? If not, rewrite them.
2. Are there any grammar or spelling mistakes? Fix them.
3. Is the definition too complex or too vague? Make it clearer.
4. Are the translations into {other1} and {other2} accurate and natural? Fix if wrong.
5. Do the contextual explanations after each example sentence clearly explain the meaning? Improve if unclear.
6. Keep the EXACT same HTML format (<b>, <i> tags, emojis). Do NOT add markdown.

DRAFT TO REVIEW:
{draft}

Return ONLY the final corrected HTML. No commentary, no preamble."""

    review_response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": review_prompt}],
        temperature=0.1
    )
    final = review_response.choices[0].message.content
    if not final:
        return draft  # Fallback to draft if review fails
    return final


def create_anki_file(word, language_label, html_content):
    """Creates an .apkg file in the system temp directory."""
    language_name = language_label.split()[-1]
    deck_name = f"Vocabulary::{language_name}"

    model_id = 1607392319
    deck_id = random.randrange(1 << 30, 1 << 31)

    anki_model = genanki.Model(
        model_id,
        'AI Expert Model',
        fields=[{'name': 'Question'}, {'name': 'Answer'}],
        templates=[{
            'name': 'Card 1',
            'qfmt': '<div style="text-align:center; font-family:Arial; font-size:30px;"><b>{{Question}}</b></div>',
            'afmt': '{{FrontSide}}<hr><div style="font-family:Arial; font-size:16px; text-align:left;">{{Answer}}</div>',
        }]
    )

    deck = genanki.Deck(deck_id, deck_name)
    note = genanki.Note(model=anki_model, fields=[word.capitalize(), html_content.replace("\n", "<br>")])
    deck.add_note(note)

    safe_word = word.replace(" ", "_").replace("'", "_")
    filename = f"{safe_word}_{random.randint(1000, 9999)}.apkg"
    filepath = os.path.join(tempfile.gettempdir(), filename)

    genanki.Package(deck).write_to_file(filepath)
    return filepath


def prepare_download_link(filepath, word):
    """Copies the .apkg to the download directory and returns a unique token."""
    safe_word = word.replace(" ", "_").replace("'", "_")
    token = f"{safe_word}_{uuid.uuid4().hex[:8]}.apkg"
    destination = os.path.join(DOWNLOAD_DIR, token)
    shutil.copy2(filepath, destination)
    return token


def cleanup_old_files():
    """Deletes download files older than 1 hour."""
    try:
        now = time.time()
        for file in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, file)
            if os.path.isfile(path) and now - os.path.getmtime(path) > 3600:
                os.remove(path)
    except Exception:
        pass


# ─── TELEGRAM HANDLERS ───────────────────────────────────────
async def start_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🇬🇧 English", "🇫🇷 Français", "🇳🇱 Nederlands"]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Choose a language:", reply_markup=markup)
    return CHOOSE_LANGUAGE


async def receive_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # If user tapped "❌ Done", end the conversation
    if text == "❌ Done":
        await update.message.reply_text("See you! 👋 Type /new anytime.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    context.user_data['language'] = text
    await update.message.reply_text("Enter a word or expression:", reply_markup=ReplyKeyboardRemove())
    return WAITING_WORD


async def receive_word_and_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    word = update.message.text
    language = context.user_data['language']
    await update.message.reply_text(f"⏳ Analyzing '{word}'...")

    try:
        # 1. Generate AI definition
        result_html = generate_definition(word, language)
        await update.message.reply_text(result_html, parse_mode="HTML")

        # 2. Create Anki file
        filepath = create_anki_file(word, language, result_html)

        # 3. Send file via Telegram + AnkiDroid import button
        safe_filename = word.replace(" ", "_").replace("'", "") + ".apkg"

        # Prepare download link on our server (correct MIME type for Android)
        RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
        reply_markup = None
        if RENDER_URL:
            cleanup_old_files()
            token = prepare_download_link(filepath, word)
            link = f"{RENDER_URL}/dl/{token}"
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Import to AnkiDroid", url=link)]
            ])

        with open(filepath, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=safe_filename,
                caption=f"📦 {word} → Deck: {language}",
                reply_markup=reply_markup
            )

        # 4. Cleanup temp file
        try:
            os.remove(filepath)
        except Exception:
            pass

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

    # 5. LOOP: automatically offer another word (no need to retype /new)
    keyboard = [["🇬🇧 English", "🇫🇷 Français", "🇳🇱 Nederlands"], ["❌ Done"]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("🔄 Another word?", reply_markup=markup)
    return CHOOSE_LANGUAGE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─── BOT SETUP ───────────────────────────────────────────────
def create_app():
    """Creates and configures the PTB application with ConversationHandler."""
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('new', start_creation)],
        states={
            CHOOSE_LANGUAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_language)],
            WAITING_WORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_word_and_generate)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    app.add_handler(conv_handler)
    return app


# ─── CLOUD MODE (Webhook + Health Check + File Server) ───────
async def run_cloud():
    """Starts the bot in webhook mode with /health and /dl/ file server."""
    from aiohttp import web

    RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
    PORT = int(os.environ.get("PORT", 10000))

    ptb_app = create_app()

    async with ptb_app:
        await ptb_app.start()
        await ptb_app.bot.set_webhook(url=f"{RENDER_URL}/webhook")

        # --- HTTP Routes ---
        async def health(request):
            """Health check endpoint for UptimeRobot — keeps Render awake."""
            return web.Response(text="✅ AnkiBot is running!")

        async def webhook(request):
            """Receives Telegram updates — responds IMMEDIATELY to prevent duplicates."""
            try:
                data = await request.json()
                update = Update.de_json(data, ptb_app.bot)
                asyncio.create_task(ptb_app.process_update(update))
            except Exception as e:
                print(f"⚠️ Webhook error: {e}")
            return web.Response(text="OK")

        async def download(request):
            """Serves .apkg files with Content-Type: application/apkg so Android opens AnkiDroid."""
            token = request.match_info['token']
            if '/' in token or '\\' in token or '..' in token:
                return web.Response(text="Forbidden.", status=403)
            file_path = os.path.join(DOWNLOAD_DIR, token)
            if not os.path.exists(file_path):
                return web.Response(text="❌ File expired.", status=404)
            # MIME type application/apkg = AnkiDroid registers for this type on Android
            return web.FileResponse(
                file_path,
                headers={
                    'Content-Type': 'application/apkg',
                    'Content-Disposition': f'attachment; filename="{token}"',
                }
            )

        http_app = web.Application()
        http_app.router.add_get("/health", health)
        http_app.router.add_get("/", health)
        http_app.router.add_post("/webhook", webhook)
        http_app.router.add_get("/dl/{token}", download)

        runner = web.AppRunner(http_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()

        print(f"🌐 Cloud mode active!")
        print(f"📡 Webhook  : {RENDER_URL}/webhook")
        print(f"❤️  Health   : {RENDER_URL}/health")
        print(f"📁 Downloads: {RENDER_URL}/dl/...")

        await asyncio.Event().wait()


# ─── ENTRY POINT ─────────────────────────────────────────────
if __name__ == '__main__':
    if os.environ.get("RENDER_EXTERNAL_URL"):
        print("🚀 Starting in Cloud mode...")
        asyncio.run(run_cloud())
    else:
        print("🏠 Local mode — Polling... Send /new on Telegram!")
        app = create_app()
        app.run_polling()