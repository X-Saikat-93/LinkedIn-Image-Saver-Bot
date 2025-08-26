import os
import logging
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Supabase with Anon Key (respects RLS)
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Custom function to set user context for RLS
def set_user_context(telegram_id):
    """Set the user context for RLS by calling a custom RPC function"""
    try:
        # This will call a PostgreSQL function that sets the current user
        supabase.rpc("set_user_context", {"user_telegram_id": str(telegram_id)}).execute()
    except Exception as e:
        logger.error(f"Error setting user context: {e}")

# Initialize database tables
def init_db():
    try:
        # Check if users table exists
        response = supabase.table('users').select("*").limit(1).execute()
        logger.info("Database connection successful")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")

def get_or_create_user(telegram_id, username=None, first_name=None, last_name=None):
    try:
        # Set user context for RLS
        set_user_context(telegram_id)
        
        # Check if user exists
        response = supabase.table('users').select("*").eq('telegram_id', str(telegram_id)).execute()
        
        if len(response.data) == 0:
            # Create new user
            user_data = {
                'telegram_id': str(telegram_id),
                'username': username,
                'first_name': first_name,
                'last_name': last_name,
                'is_premium': False,
                'created_at': str(datetime.now()),
                'last_active': str(datetime.now())
            }
            response = supabase.table('users').insert(user_data).execute()
            return response.data[0] if response.data else None
        else:
            # Update last active
            response = supabase.table('users').update({'last_active': str(datetime.now())}).eq('telegram_id', str(telegram_id)).execute()
            return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error in get_or_create_user: {e}")
        return None

def log_usage(user_id, post_url, image_count):
    try:
        # The user_id should be the database ID, not telegram_id
        usage_data = {
            'user_id': user_id,
            'post_url': post_url,
            'image_count': image_count,
            'created_at': str(datetime.now())
        }
        response = supabase.table('usage_logs').insert(usage_data).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"Error in log_usage: {e}")
        return None

def get_today_usage(user_id):
    try:
        today = date.today().isoformat()
        
        # Get start and end of today
        today_start = f"{today}T00:00:00"
        today_end = f"{today}T23:59:59"
        
        response = supabase.table('usage_logs').select("id"). \
            eq('user_id', user_id). \
            gte('created_at', today_start). \
            lte('created_at', today_end).execute()
        return len(response.data)
    except Exception as e:
        logger.error(f"Error in get_today_usage: {e}")
        return 0

# LinkedIn Scraper
class LinkedInScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
    
    def extract_images(self, post_url):
        """Extract images from LinkedIn post"""
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Use webdriver-manager to automatically manage ChromeDriver
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            
            # Execute script to avoid detection
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            images = []
            try:
                logger.info(f"Loading URL: {post_url}")
                driver.get(post_url)
                time.sleep(8)  # Increased wait time
                
                # Scroll to load all content multiple times
                for i in range(3):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                
                # Find image elements with multiple selectors
                img_elements = driver.find_elements("tag name", "img")
                logger.info(f"Found {len(img_elements)} image elements")
                
                # Debug: Log all image sources to understand the pattern
                all_images_debug = []
                for i, img in enumerate(img_elements):
                    try:
                        src = img.get_attribute('src')
                        alt = img.get_attribute('alt') or ''
                        width = img.get_attribute('width') or '0'
                        height = img.get_attribute('height') or '0'
                        if src:
                            logger.info(f"Image {i+1}: src={src[:150]}, alt={alt[:50]}, size={width}x{height}")
                            all_images_debug.append(src)
                    except Exception as e:
                        logger.error(f"Error getting attributes for image {i+1}: {e}")
                
                logger.info(f"Debug - Total images with src: {len(all_images_debug)}")
                
                # Try alternative approach - look for background images and other elements
                try:
                    # Check for div elements with background images
                    all_elements = driver.find_elements("css selector", "*")
                    logger.info(f"Found {len(all_elements)} total elements, checking for background images...")
                    
                    bg_images_found = 0
                    for element in all_elements[:100]:  # Check first 100 elements
                        try:
                            style = element.get_attribute('style')
                            if style and 'background-image' in style and 'url(' in style:
                                bg_images_found += 1
                                logger.info(f"Background image found in style: {style[:100]}")
                        except:
                            pass
                    
                    logger.info(f"Found {bg_images_found} elements with background images")
                except Exception as e:
                    logger.error(f"Error checking background images: {e}")
                
                for i, img in enumerate(img_elements):
                    try:
                        src = img.get_attribute('src')
                        alt = img.get_attribute('alt') or ''
                        
                        if not src or src.startswith('data:'):
                            logger.info(f"Image {i+1}: Skipped - no src or data URL")
                            continue
                        
                        # VERY AGGRESSIVE APPROACH - Include almost all images except obvious UI elements
                        exclude_patterns = [
                            'linkedin.com/in/',  # Profile photos
                            '/company-logo/',    # Company logos
                            '/vector/',          # Vector icons
                            'sprite',            # Icon sprites
                            'logo',              # Any logos
                            'icon',              # Icons
                            'emoji',             # Emojis
                            'reaction',          # Reaction images
                        ]
                        
                        # Skip only if it matches exclusion patterns
                        should_exclude = any(pattern in src.lower() for pattern in exclude_patterns)
                        
                        if should_exclude:
                            logger.info(f"Image {i+1}: Excluded - {src[:100]}")
                            continue
                        
                        # Include if it's an HTTP image that doesn't match exclusions
                        if src.startswith('http'):
                            # Additional check for very small images (likely icons)
                            try:
                                width = img.get_attribute('width')
                                height = img.get_attribute('height')
                                
                                if width and height:
                                    w, h = int(width), int(height)
                                    if w < 50 or h < 50:  # Skip very small images
                                        logger.info(f"Image {i+1}: Skipped small image ({w}x{h}): {src[:100]}")
                                        continue
                            except:
                                pass
                            
                            images.append(src)
                            logger.info(f"Image {i+1}: ✅ ADDED - {src[:100]}...")
                        else:
                            logger.info(f"Image {i+1}: Skipped - not HTTP URL: {src[:100]}")
                                
                    except Exception as e:
                        logger.error(f"Error processing image element {i+1}: {e}")
                        continue
                        
            except Exception as e:
                logger.error(f"Selenium error: {e}")
            finally:
                driver.quit()
            
            # Remove duplicates while preserving order
            seen = set()
            unique_images = []
            for img in images:
                if img not in seen:
                    seen.add(img)
                    unique_images.append(img)
            
            logger.info(f"Extracted {len(unique_images)} unique images")
            return unique_images
            
        except Exception as e:
            logger.error(f"Error in extract_images: {e}")
            return []

# Bot Handlers
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    get_or_create_user(
        user.id, 
        user.username, 
        user.first_name, 
        user.last_name
    )
    
    welcome_message = """
📸 *LinkedIn Image Downloader Bot*

Send me a LinkedIn post URL and I'll download all images for you!

✨ *How to use:*
1. Copy a LinkedIn post URL
2. Paste it here
3. Get all images in high quality

💡 *Daily limit:* 5 downloads
🚀 *Premium:* Unlimited downloads (coming soon)

Made with ❤️ for professionals!
"""
    
    keyboard = [
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        welcome_message,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

def help_command(update: Update, context: CallbackContext):
    help_text = """
❓ *Help & Usage*

1. *Find a LinkedIn post* with images
2. *Copy the post URL* from your browser
3. *Paste it here* and send
4. *Wait* for the bot to process
5. *Receive* all images in your chat!

⚠️ *Limitations:*
• Only public posts work
• Maximum 5 downloads per day
• Some posts may have no extractable images

💡 *Tips:*
• Make sure the post is public
• Wait for images to load in your browser before copying URL
• Try again if it fails the first time
"""
    update.message.reply_text(help_text, parse_mode='Markdown')

def stats_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    user = update.effective_user
    db_user = get_or_create_user(user.id)
    
    if db_user:
        today_usage = get_today_usage(db_user['id'])
        
        stats_text = f"""
📊 *Your Stats*

🆔 User ID: `{user.id}`
📝 Name: {user.first_name or ''} {user.last_name or ''}
📅 Joined: {db_user['created_at'][:10] if db_user['created_at'] else 'N/A'}
🔥 Today's Downloads: {today_usage}/5
⭐ Premium: No
"""
    else:
        stats_text = "❌ Error retrieving your stats. Please try again."
    
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)

def back_to_main_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    welcome_message = """
📸 *LinkedIn Image Downloader Bot*

Send me a LinkedIn post URL and I'll download all images for you!

✨ *How to use:*
1. Copy a LinkedIn post URL
2. Paste it here
3. Get all images in high quality

💡 *Daily limit:* 5 downloads
🚀 *Premium:* Unlimited downloads (coming soon)
"""
    
    keyboard = [
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(welcome_message, parse_mode='Markdown', reply_markup=reply_markup)

def handle_url(update: Update, context: CallbackContext):
    user = update.effective_user
    db_user = get_or_create_user(
        user.id, 
        user.username, 
        user.first_name, 
        user.last_name
    )
    
    if not db_user:
        update.message.reply_text("❌ Error creating user account. Please try again later.")
        return
    
    # Check daily limit
    today_usage = get_today_usage(db_user['id'])
    if today_usage >= 5:
        update.message.reply_text("❌ You've reached your daily limit of 5 downloads!\n\nCome back tomorrow or wait for premium features! 🚀")
        return
    
    url = update.message.text.strip()
    
    # Validate LinkedIn URL
    if "linkedin.com" not in url:
        update.message.reply_text("❌ Please send a valid LinkedIn post URL!\n\nExample: https://www.linkedin.com/posts/...")
        return
    
    # Send processing message
    processing_msg = update.message.reply_text("🔍 Processing your LinkedIn post...\n\nThis may take 15-30 seconds...")
    
    try:
        # Extract images
        scraper = LinkedInScraper()
        images = scraper.extract_images(url)
        
        if not images:
            processing_msg.edit_text("❌ No images found in this LinkedIn post!\n\n💡 *Possible reasons:*\n• Post has no images\n• Post is private/restricted\n• Images failed to load\n\nTry with a different public post!")
            return
        
        # Limit to 10 images to prevent spam
        images = images[:10]
        
        # Update processing message
        processing_msg.edit_text(f"✅ Found {len(images)} images!\n\nDownloading and sending them now...")
        
        # Send images
        success_count = 0
        failed_count = 0
        
        for i, image_url in enumerate(images):
            try:
                context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=image_url,
                    caption=f"🖼️ Image {i+1}/{len(images)}"
                )
                success_count += 1
                time.sleep(1)  # Increased delay to prevent rate limiting
            except Exception as e:
                logger.error(f"Error sending image {i+1}: {e}")
                failed_count += 1
                # Try sending the image URL as text if direct sending fails
                try:
                    context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"📎 Image {i+1} (direct link):\n{image_url}"
                    )
                except:
                    pass
                continue
        
        # Log usage only if at least one image was processed
        if success_count > 0 or failed_count > 0:
            log_usage(db_user['id'], url, success_count)
        
        # Final message
        if success_count > 0:
            remaining_downloads = 4 - today_usage
            final_text = f"🎉 Download complete!\n\n✅ Successfully sent {success_count} images"
            if failed_count > 0:
                final_text += f"\n⚠️ {failed_count} images failed to send"
            final_text += f"\n📊 You have {remaining_downloads} downloads left today"
            update.message.reply_text(final_text)
        else:
            update.message.reply_text("❌ Failed to send any images. The images might be too large or have restricted access.")
        
    except Exception as e:
        logger.error(f"Error processing URL: {e}")
        processing_msg.edit_text("❌ Something went wrong while processing your request!\n\nPlease try again later or contact support.")

def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Update {update} caused error {context.error}")

def main():
    # Initialize database
    init_db()
    
    # Get token from environment
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        logger.error("TELEGRAM_TOKEN not found in environment variables!")
        return
    
    # Create updater and dispatcher
    updater = Updater(token, use_context=True)
    dp = updater.dispatcher
    
    # Add handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CallbackQueryHandler(stats_callback, pattern="^stats$"))
    dp.add_handler(CallbackQueryHandler(back_to_main_callback, pattern="^back_to_main$"))
    dp.add_handler(CallbackQueryHandler(help_command, pattern="^help$"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_url))
    
    # Add error handler
    dp.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Starting LinkedIn Image Downloader Bot...")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()