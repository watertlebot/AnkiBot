import os
import sys
import random
import tempfile
import shutil
import uuid
import time
import json
import urllib.request
import urllib.parse

# Fix Windows console encoding (cp1252 doesn't support emojis)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import asyncio
import sqlite3
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

# ─── AI CLIENTS SETUP & FALLBACK ─────────────────────────────
GROQ_CLIENT = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY) if GROQ_API_KEY else None
OR_CLIENT = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY) if OPENROUTER_API_KEY else None

# Multiple free models on OpenRouter — if one is rate-limited, try the next
OR_MODELS = [
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-4-maverick:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen3-235b-a22b:free",
]

def ask_ai(prompt, temperature=0.2, max_tokens=None):
    """Tries Groq first, then cycles through multiple free OpenRouter models."""
    last_err = None
    
    if GROQ_CLIENT:
        try:
            r = GROQ_CLIENT.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = r.choices[0].message.content
            if content: return content
        except Exception as e:
            print(f"⚠️ Groq failed: {e}")
            last_err = e
            
    if OR_CLIENT:
        for model in OR_MODELS:
            try:
                print(f"🔄 Trying {model}...")
                r = OR_CLIENT.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                content = r.choices[0].message.content
                if content:
                    print(f"✅ {model} responded!")
                    return content
            except Exception as e:
                print(f"⚠️ {model} failed: {e}")
                last_err = e
                continue
            
    raise Exception(f"All AI providers failed. Try again in a few minutes.")

# ─── TEMPORARY DOWNLOAD DIRECTORY ────────────────────────────
DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "ankibot_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ─── CONVERSATION HANDLER STATES ─────────────────────────────
CHOOSE_LANGUAGE, WAITING_WORD = range(2)

# ─── LANGUAGE MAPPING ────────────────────────────────────────
LANGUAGE_MAP = {
    "🇬🇧 English":   {"name": "English",    "others": ["French", "Dutch"],  "tts": "en"},
    "🇫🇷 Français":  {"name": "French",     "others": ["English", "Dutch"], "tts": "fr"},
    "🇳🇱 Nederlands": {"name": "Dutch",      "others": ["English", "French"], "tts": "nl"},
}

# ─── DATABASE INITIALIZATION ─────────────────────────────────
DB_FILE = "ankibot.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            language TEXT,
            word TEXT,
            html_content TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

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
6. LANGUAGE LEVEL: All example sentences and explanations MUST be at CEFR level B1 to B2 maximum. Use simple, everyday vocabulary. Avoid academic or literary language.

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

    draft = ask_ai(draft_prompt, temperature=0.3)

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

    try:
        final = ask_ai(review_prompt, temperature=0.1)
        return final
    except Exception:
        return draft  # Fallback to draft if review fails completely


def get_image_search_term(word, language):
    """Asks AI if this word is a concrete/visual concept. Returns English search term or None."""
    prompt = f"""Word: "{word}" (Language: {language})
Is this a concrete, visual word (object, animal, place, food, tool, etc.) that would benefit from a photo on a flashcard?
- If YES: respond with ONLY a 1-2 word English search term for finding a relevant photo. Nothing else.
- If NO (idiom, abstract concept, expression, feeling, verb, adjective): respond with ONLY the word "NO". Nothing else."""
    try:
        content = ask_ai(prompt, temperature=0.0, max_tokens=20)
        result = content.strip().strip('"')
        if result.upper() == "NO":
            return None
        return result
    except Exception:
        return None


def download_pixabay_image(search_term):
    """Downloads a photo from Pixabay. Returns filepath or None."""
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        return None
    try:
        # Sanitize search term (remove YES, NO, punctuation)
        clean_term = search_term.replace("YES", "").replace("NO", "").replace(":", "").replace(".", "").strip()
        query = urllib.parse.quote(clean_term)
        url = f"https://pixabay.com/api/?key={api_key}&q={query}&image_type=photo&per_page=3&safesearch=true"
        
        # Pixabay blocks default urllib User-Agent, so we spoof one
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 AnkiBot'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            
        if not data.get("hits"):
            return None
            
        img_url = data["hits"][0]["webformatURL"]
        img_path = os.path.join(tempfile.gettempdir(), f"ankibot_{uuid.uuid4().hex[:8]}.jpg")
        
        req_img = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0 AnkiBot'})
        with urllib.request.urlopen(req_img, timeout=10) as resp_img, open(img_path, 'wb') as f:
            f.write(resp_img.read())
            
        return img_path
    except Exception as e:
        print(f"⚠️ Image download failed: {e}")
        return None


def generate_audio(word, language):
    """Generates an MP3 pronunciation of the word using Google TTS. Returns filepath or None."""
    lang_info = LANGUAGE_MAP.get(language)
    if not lang_info:
        return None
    tts_lang = lang_info.get("tts")
    if not tts_lang:
        return None
    try:
        from gtts import gTTS
        audio_path = os.path.join(tempfile.gettempdir(), f"ankibot_audio_{uuid.uuid4().hex[:8]}.mp3")
        tts = gTTS(text=word, lang=tts_lang, slow=False)
        tts.save(audio_path)
        return audio_path
    except Exception as e:
        print(f"⚠️ TTS failed: {e}")
        return None


def create_anki_file(word, language_label, html_content, image_path=None, audio_path=None):
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

    # If an image was downloaded, prepend it to the answer HTML
    media_files = []
    image_filename = None
    if image_path:
        image_filename = os.path.basename(image_path)
        img_tag = f'<div style="text-align:center; margin-bottom:12px;"><img src="{image_filename}" style="max-width:300px; border-radius:8px;"></div>'
        html_content = img_tag + html_content
        media_files.append(image_path)

    # If audio was generated, append sound tag and add to media
    audio_filename = None
    if audio_path:
        audio_filename = os.path.basename(audio_path)
        html_content += f'<br><br>🔊 [sound:{audio_filename}]'
        media_files.append(audio_path)

    note = genanki.Note(model=anki_model, fields=[word.capitalize(), html_content.replace("\n", "<br>")])
    deck.add_note(note)

    safe_word = word.replace(" ", "_").replace("'", "_")
    filename = f"{safe_word}_{random.randint(1000, 9999)}.apkg"
    filepath = os.path.join(tempfile.gettempdir(), filename)

    package = genanki.Package(deck)
    if media_files:
        package.media_files = media_files
    package.write_to_file(filepath)
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
    user_id = update.message.from_user.id
    
    # Auto-detect language if user misclicked
    valid_languages = ["🇬🇧 English", "🇫🇷 Français", "🇳🇱 Nederlands"]
    if language not in valid_languages:
        await update.message.reply_text("🤖 <i>Invalid language detected. Determining the correct deck...</i>", parse_mode="HTML")
        detect_prompt = f"""Word: "{word}"
Which of these 3 languages does this word belong to: English, French, or Dutch?
Reply with ONLY the exact name of the language (English, French, or Dutch). No punctuation."""
        try:
            detected_lang = ask_ai(detect_prompt, temperature=0.0, max_tokens=10).strip().lower()
            if "french" in detected_lang:
                language = "🇫🇷 Français"
            elif "dutch" in detected_lang:
                language = "🇳🇱 Nederlands"
            else:
                language = "🇬🇧 English"
        except Exception:
            language = "🇬🇧 English"
            
        context.user_data['language'] = language
        await update.message.reply_text(f"✅ Auto-detected: <b>{language}</b>", parse_mode="HTML")
        
    # Auto-spellcheck
    spellcheck_prompt = f"""Target word: "{word}"
Language: {language}
Correct any obvious spelling mistakes or missing hyphens in this word. If it's already perfectly correct, return it exactly as is.
Return ONLY the corrected word. No punctuation, no explanation."""
    try:
        corrected = ask_ai(spellcheck_prompt, temperature=0.0, max_tokens=15).strip().replace('"', '').strip('.').lower()
        if corrected and len(corrected) > 0 and corrected != word.lower():
            word = corrected
            await update.message.reply_text(f"✨ <i>Auto-corrected typo to: <b>{word}</b></i>", parse_mode="HTML")
    except Exception:
        pass
    
    # Check if word was already searched
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM cards WHERE user_id = ? AND language = ? AND LOWER(word) = LOWER(?)", 
              (user_id, language, word))
    times_searched = c.fetchone()[0]
    conn.close()

    reminder = ""
    if times_searched > 0:
        times_str = "time" if times_searched == 1 else "times"
        reminder = f"🧠 <i>Memory check: You already generated a card for this word {times_searched} {times_str} before!</i>\n\n"

    await update.message.reply_text(f"{reminder}⏳ Analyzing '{word}'...", parse_mode="HTML")

    try:
        # 1. Generate AI definition
        result_html = generate_definition(word, language)
        await update.message.reply_text(result_html, parse_mode="HTML")

        # Save to database
        user_id = update.message.from_user.id
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO cards (user_id, language, word, html_content) VALUES (?, ?, ?, ?)",
                  (user_id, language, word, result_html))
        conn.commit()
        conn.close()

        # 2. Try to find a relevant image (only for concrete/visual words)
        image_path = None
        search_term = get_image_search_term(word, language)
        if search_term:
            image_path = download_pixabay_image(search_term)

        # 3. Generate pronunciation audio
        audio_path = generate_audio(word, language)

        # 4. Create Anki file (with optional image + audio)
        filepath = create_anki_file(word, language, result_html, image_path, audio_path)

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

        # 5. Cleanup temp files
        for f_path in [filepath, image_path, audio_path]:
            if f_path:
                try:
                    os.remove(f_path)
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


async def export_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports all saved cards for the user, grouped by language."""
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT language, word, html_content FROM cards WHERE user_id = ?", (user_id,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        await update.message.reply_text("📭 You haven't generated any cards yet. Type /new to start!")
        return
        
    await update.message.reply_text("🗄️ Packing your global decks...")
    
    # Group by language
    decks_by_lang = {}
    for language, word, html_content in rows:
        if language not in decks_by_lang:
            decks_by_lang[language] = []
        decks_by_lang[language].append((word, html_content))
        
    for language, cards in decks_by_lang.items():
        language_name = language.split()[-1]
        deck_name = f"My Global Deck::{language_name}"
        
        model_id = 1607392319
        deck_id = hash(deck_name) % (1 << 31) # stable id
        
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
        
        for word, html_content in cards:
            note = genanki.Note(model=anki_model, fields=[word.capitalize(), html_content.replace("\n", "<br>")])
            deck.add_note(note)
            
        filename = f"Global_Deck_{language_name}.apkg"
        filepath = os.path.join(tempfile.gettempdir(), filename)
        genanki.Package(deck).write_to_file(filepath)
        
        # Prepare for download
        RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
        reply_markup = None
        if RENDER_URL:
            cleanup_old_files()
            token = f"Global_{language_name}_{uuid.uuid4().hex[:8]}.apkg"
            dest = os.path.join(DOWNLOAD_DIR, token)
            shutil.copy2(filepath, dest)
            link = f"{RENDER_URL}/dl/{token}"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Import to AnkiDroid", url=link)]])
            
        with open(filepath, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=filename,
                caption=f"📚 Your complete {language_name} deck ({len(cards)} cards)",
                reply_markup=reply_markup
            )
        try:
            os.remove(filepath)
        except Exception:
            pass


async def clear_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes all saved cards for the user."""
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM cards WHERE user_id = ?", (user_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"🗑️ Memory wiped! Deleted {deleted} saved cards.")


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows card generation statistics for the user."""
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT language, COUNT(*) FROM cards WHERE user_id = ? GROUP BY language", (user_id,))
    rows = c.fetchall()
    c.execute("SELECT COUNT(*) FROM cards WHERE user_id = ?", (user_id,))
    total = c.fetchone()[0]
    conn.close()
    
    if not rows:
        await update.message.reply_text("📊 No cards yet! Type /new to start generating flashcards.")
        return
    
    lines = ["📊 <b>Your AnkiBot Stats</b>\n"]
    for language, count in rows:
        lines.append(f"  • {language}: <b>{count}</b> cards")
    lines.append(f"\n🎯 <b>Total: {total} cards</b>")
    lines.append("\n💡 Type /export to download all your cards!")
    
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows all available commands."""
    help_text = (
        "🤖 <b>AnkiBot Commands:</b>\n\n"
        "🔹 /new - Start generating a flashcard\n"
        "🔹 /export - Download ALL your saved cards in Anki decks\n"
        "🔹 /stats - See your learning statistics\n"
        "🔹 /clear - Delete all your saved memory\n"
        "🔹 /cancel - Cancel the current action\n"
        "🔹 /help - Show this message"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


# ─── BOT SETUP ───────────────────────────────────────────────
def create_app():
    """Creates and configures the PTB application with ConversationHandler."""
    init_db()  # Initialize the database on startup
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler('export', export_cards))
    app.add_handler(CommandHandler('clear', clear_cards))
    app.add_handler(CommandHandler('stats', show_stats))
    app.add_handler(CommandHandler('help', help_command))
    
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