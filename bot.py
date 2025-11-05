import discord
from discord.ext import commands, tasks
import logging
from dotenv import load_dotenv
import os
from collections import defaultdict
from datetime import datetime, timedelta
import json
import asyncio
import random
import re

# Load environment variables FIRST
load_dotenv()

# Set Kaggle credentials in environment BEFORE importing KaggleApi
os.environ['KAGGLE_USERNAME'] = os.getenv('KAGGLE_USERNAME', '')
os.environ['KAGGLE_KEY'] = os.getenv('KAGGLE_KEY', '')

# NOW import and initialize Kaggle API
from kaggle.api.kaggle_api_extended import KaggleApi
kaggle_api = KaggleApi()
kaggle_api.authenticate()

token = os.getenv('DISCORD_TOKEN')
WARNING_CHANNEL_ID = int(os.getenv('WARNING_CHANNEL_ID', '0'))  # Set this in .env
LEADERBOARD_CHANNEL_ID = int(os.getenv('LEADERBOARD_CHANNEL_ID', '0'))  # Set this in .env for contest leaderboard
STATS_CHANNEL_ID = int(os.getenv('STATS_CHANNEL_ID', '0'))  # Set this in .env for daily server stats
TIPS_CHANNEL_ID = int(os.getenv('TIPS_CHANNEL_ID', '0'))  # Set this in .env for tips/motivation messages

# Setup logging
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)  # Disable default help

# Data storage (in production, use a database)
user_activity = defaultdict(lambda: {"messages": 0, "last_seen": None})
message_tracker = defaultdict(list)  # Track messages for spam detection
kaggle_ids = {}  # Permanent storage: {user_id: {"name": str, "kaggle_id": str, "registered_at": str}}
contest_participants = {}  # Temporary contest data: {user_id: {"name": str, "kaggle_id": str, "score": float, "confirmed": bool}}
active_poll_message_id = None  # Track the current active poll
poll_expiry_time = None  # Track when the poll expires
pending_registrations = {}  # Track users waiting to provide Kaggle ID {user_id: dm_message}
active_competition = None  # Track current Kaggle competition ID
competition_end_time = None  # Track when competition ends
kaggle_stats_cache = {}  # Cache Kaggle stats {user_id: {stats, last_updated}}
contest_scores = defaultdict(dict)  # Track contest scores {contest_name: {user_id: score}}
bad_word_warnings = defaultdict(lambda: {"count": 0, "messages": [], "timeouts": 0})  # Track bad word usage and timeouts

# Bad words list (customize as needed)
BAD_WORDS = [
    # Profanity (strong language)
    'shit', 'fuck', 'bitch', 'bastard', 'piss',
    'cock', 'dick', 'pussy', 'cunt', 'twat', 'bollocks', 'wanker', 'asshole',
    'motherfucker', 'fuckface', 'shithead', 'dickhead', 'dumbass', 'jackass',
    'bullshit', 'horseshit', 'bitchass', 'dipshit', 'shitty', 'fucking',
    'fucked', 'fucker', 'fucks', 'arse', 'arsehole',
    'son of a bitch', 'piece of shit', 'full of shit', 'eat shit', 'holy shit',

    # Slurs and hate speech (racial/ethnic)
    'nigger', 'nigga', 'chink', 'gook', 'spic', 'kike', 'wetback', 'beaner',
    'towelhead', 'raghead', 'cracker', 'honky', 'paki', 'jap', 'injun',

    # Sexual/inappropriate
    'porn', 'hentai', 'rape', 'whore', 'slut', 'hoe',
    'milf', 'dildo', 'boobs', 'tits', 'titties', 'penis', 'vagina',

    # Homophobic/transphobic
    'fag', 'faggot', 'dyke', 'tranny', 'shemale',

    # Ableist
    'retard', 'retarded', 'downy', 'spaz', 'cripple', 'midget',

    # Other offensive or harmful
    'nazi', 'hitler', 'pedo', 'pedophile', 'kill yourself', 'kys',
    'nsfw'
]

# Bad word detection settings
BAD_WORD_THRESHOLD = 15  # Number of violations before warning admin

# Spam detection settings (stricter to catch fast typing)
SPAM_THRESHOLD = 4  # messages (lowered from 5)
SPAM_TIME_WINDOW = 3  # seconds (lowered from 5)

def normalize_text(text):
    """Remove special characters and normalize text for bad word detection"""
    # Convert to lowercase
    normalized = text.lower()
    # Replace common character substitutions
    char_map = {
        '@': 'a', '4': 'a', 
        '3': 'e', 
        '1': 'i', '!': 'i',
        '0': 'o',
        '$': 's', '5': 's',
        '7': 't',
        '8': 'b'
    }
    for char, replacement in char_map.items():
        normalized = normalized.replace(char, replacement)
    
    # Remove all remaining special characters and numbers
    normalized = re.sub(r'[^a-z\s]', '', normalized)
    # Remove extra spaces
    normalized = ' '.join(normalized.split())
    return normalized

def contains_bad_word(text):
    """Check if text contains bad words, accounting for variations"""
    # First normalize the text (handles @, $, 3, etc. substitutions)
    normalized = normalize_text(text)
    
    # Check normalized version with word boundaries
    for bad_word in BAD_WORDS:
        if bad_word in normalized:
            pattern = r'\b' + re.escape(bad_word) + r'\b'
            if re.search(pattern, normalized):
                return True
    
    # Simple obfuscation check: f**k, f@ck, etc.
    # Only check if special characters are present
    if re.search(r'[^a-zA-Z\s]', text):
        text_lower = text.lower()
        for bad_word in BAD_WORDS:
            if len(bad_word) >= 4:  # Only for longer words
                # Create a pattern where special chars can replace letters
                # But require at least 60% of the original letters to be present
                pattern_chars = []
                for char in bad_word:
                    # Allow this letter OR a special character
                    pattern_chars.append(f'[{char}*@#$%!0-9]')
                pattern = r'\b' + ''.join(pattern_chars) + r'\b'
                if re.search(pattern, text_lower):
                    return True
    
    return False

# ===== EVENT HANDLERS =====

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("AI Olympiad Bot is ready!")
    print(f"Serving {len(bot.guilds)} guild(s)")
    
    # Start background tasks to keep bot active
    if not daily_stats_update.is_running():
        daily_stats_update.start()
    if not motivation_and_tips.is_running():
        motivation_and_tips.start()
    
@bot.event
async def on_member_join(member):
    """Welcome new members with a cool message in the server"""
    # Find a general/welcome channel (you can change the channel name)
    welcome_channel = discord.utils.get(member.guild.text_channels, name='general') or \
                      discord.utils.get(member.guild.text_channels, name='welcome') or \
                      member.guild.system_channel
    
    if welcome_channel:
        embed = discord.Embed(
            title="🎉 Welcome to AI Olympiad Community!",
            description=f"Hey {member.mention}! Welcome to the server! 🤖\n\n"
                       f"We're excited to have you here!\n"
                       f"• Check out our contests and challenges\n"
                       f"• Use `!help` to see all available commands\n"
                       f"• Link your Kaggle profile with `!my_kaggle`\n\n"
                       f"Let's build something amazing together! 🚀",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        
        await welcome_channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    # Check if this is a DM response for Kaggle ID registration
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        
        # Check if user is in pending registrations and poll hasn't expired
        global poll_expiry_time
        if user_id in pending_registrations:
            if poll_expiry_time and datetime.now() < poll_expiry_time:
                pending_data = pending_registrations[user_id]
                user_response = message.content.strip()
                
                # Check if user already had Kaggle ID (just confirming)
                if pending_data.get("has_kaggle_id"):
                    # User already has Kaggle ID, check for Yes/No confirmation
                    if user_response.lower() in ['yes', 'y', 'yeah', 'yep', 'sure', 'ok', 'okay']:
                        # Confirm registration with existing Kaggle ID from permanent storage
                        kaggle_id = kaggle_ids[user_id]["kaggle_id"]
                        contest_participants[user_id] = {
                            "name": message.author.name,
                            "kaggle_id": kaggle_id,
                            "registered_at": datetime.now().isoformat(),
                            "confirmed": True
                        }
                        
                        await message.author.send(
                            f"✅ **Registration Confirmed!**\n\n"
                            f"Kaggle ID: **{kaggle_id}**\n"
                            f"You'll receive the competition link once registration closes!\n"
                            f"Good luck in the contest! 🚀"
                        )
                        
                        # Remove from pending
                        del pending_registrations[user_id]
                        save_participants()
                        return
                        
                    elif user_response.lower() in ['no', 'n', 'nope', 'cancel', 'nah']:
                        # User declined participation
                        await message.author.send("❌ Registration cancelled. You can react again if you change your mind!")
                        del pending_registrations[user_id]
                        return
                        
                    else:
                        # Invalid response
                        await message.author.send("Please reply with **Yes** to confirm or **No** to cancel.")
                        return
                
                else:
                    # User doesn't have Kaggle ID yet
                    # Check if they already said Yes and provided Kaggle ID
                    if "temp_kaggle_id" in pending_data:
                        # They already provided Kaggle ID, save it and confirm
                        kaggle_id = pending_data["temp_kaggle_id"]
                        contest_participants[user_id] = {
                            "name": message.author.name,
                            "kaggle_id": kaggle_id,
                            "registered_at": datetime.now().isoformat(),
                            "confirmed": True
                        }
                        
                        await message.author.send(
                            f"✅ **Registration Confirmed!**\n\n"
                            f"Kaggle ID: **{kaggle_id}**\n"
                            f"Your Kaggle ID has been saved for future contests.\n\n"
                            f"You'll receive the competition link once registration closes!\n\n"
                            f"💡 **Tip:** Use `!my_kaggle <new_id>` anytime to update your Kaggle ID.\n\n"
                            f"Good luck in the contest! 🚀"
                        )
                        
                        # Remove from pending
                        del pending_registrations[user_id]
                        save_participants()
                        return
                    
                    elif "waiting_for_kaggle_id" in pending_data:
                        # They said Yes, now they're providing Kaggle ID
                        kaggle_id = user_response
                        
                        # Save the Kaggle ID and mark as registered
                        pending_registrations[user_id]["temp_kaggle_id"] = kaggle_id
                        
                        contest_participants[user_id] = {
                            "name": message.author.name,
                            "kaggle_id": kaggle_id,
                            "registered_at": datetime.now().isoformat(),
                            "confirmed": True
                        }
                        
                        await message.author.send(
                            f"✅ **Registration Confirmed!**\n\n"
                            f"Kaggle ID: **{kaggle_id}**\n"
                            f"Your Kaggle ID has been saved for future contests.\n\n"
                            f"You'll receive the competition link once registration closes!\n\n"
                            f"💡 **Tip:** Use `!my_kaggle <new_id>` anytime to update your Kaggle ID.\n\n"
                            f"Good luck in the contest! 🚀"
                        )
                        
                        # Remove from pending
                        del pending_registrations[user_id]
                        save_participants()
                        return
                    
                    else:
                        # First response - asking if they want to participate
                        if user_response.lower() in ['yes', 'y', 'yeah', 'yep', 'sure', 'ok', 'okay']:
                            # They said yes, now ask for Kaggle ID
                            pending_registrations[user_id]["waiting_for_kaggle_id"] = True
                            
                            await message.author.send(
                                f"Great! 🎉\n\n"
                                f"Please reply with your **Kaggle ID** (username).\n\n"
                                f"**Format:** Just type your Kaggle username (e.g., johndoe123)\n"
                                f"Example: If your profile is kaggle.com/johndoe123, reply with: johndoe123"
                            )
                            return
                            
                        elif user_response.lower() in ['no', 'n', 'nope', 'cancel', 'nah']:
                            # User declined participation
                            await message.author.send("❌ Registration cancelled. You can react again if you change your mind!")
                            del pending_registrations[user_id]
                            return
                            
                        else:
                            # Invalid response
                            await message.author.send("Please reply with **Yes** to participate or **No** to cancel.")
                            return
                
            else:
                await message.author.send("⏰ Sorry, the contest poll has expired. Registration is closed.")
                if user_id in pending_registrations:
                    del pending_registrations[user_id]
                return
    
    # Track user activity
    user_activity[message.author.id]["messages"] += 1
    user_activity[message.author.id]["last_seen"] = datetime.now()
    
    # Check for bad words (silent warning with threshold)
    if contains_bad_word(message.content):
        # Delete the message immediately
        try:
            await message.delete()
        except:
            pass
        
        bad_word_warnings[message.author.id]["count"] += 1
        bad_word_warnings[message.author.id]["messages"].append({
            "content": message.content,
            "channel": message.channel.name,
            "timestamp": datetime.now().isoformat()
        })
        
        # Only send warning if threshold is reached
        if bad_word_warnings[message.author.id]["count"] >= BAD_WORD_THRESHOLD:
            warning_channel = bot.get_channel(WARNING_CHANNEL_ID)
            user_data = bad_word_warnings[message.author.id]
            
            # Send DM to user
            try:
                # Get last 3 messages
                recent_messages = user_data["messages"][-3:]
                messages_list = "\n".join([f"  • {msg_data['content']}" for msg_data in recent_messages])
                
                await message.author.send(
                    f"⚠️ **Official Warning - Language Violation**\n\n"
                    f"You have reached the inappropriate language threshold (**{BAD_WORD_THRESHOLD} violations**).\n\n"
                    f"**Recent flagged messages:**\n{messages_list}\n\n"
                    f"Please review our server rules and maintain respectful communication.\n"
                    f"**Note:** Accumulating 3 warnings will result in a 10-minute timeout.\n\n"
                    f"—————————————————\n"
                    f"*AI Olympiad Community Moderation Team*"
                )
            except:
                pass
            
            # Send warning to admin channel
            if warning_channel:
                embed = discord.Embed(
                    title="⚠️ Bad Word Threshold Reached",
                    description=f"**User:** {message.author.mention} ({message.author.name})\n**Violations:** {user_data['count']}",
                    color=0xff0000,
                    timestamp=datetime.now()
                )
                
                # Add recent messages
                recent_msgs = user_data["messages"][-3:]  # Last 3 messages
                for i, msg_data in enumerate(recent_msgs, 1):
                    embed.add_field(
                        name=f"Message {i} - #{msg_data['channel']}",
                        value=f"```{msg_data['content']}```",
                        inline=False
                    )
                
                embed.set_footer(text=f"Total violations: {user_data['count']}")
                await warning_channel.send(embed=embed)
            
            # Increment timeout counter
            bad_word_warnings[message.author.id]["timeouts"] += 1
            
            # Apply timeout if 3 warnings reached
            if bad_word_warnings[message.author.id]["timeouts"] >= 3:
                try:
                    # Timeout for 6 hours
                    await message.author.timeout(timedelta(hours=6), reason="Exceeded bad word warnings 3 times")
                    
                    # Notify in warning channel
                    if warning_channel:
                        await warning_channel.send(
                            f"🔇 **{message.author.mention} has been timed out for 6 hours** (3 warnings reached)"
                        )
                    
                    # DM the user
                    try:
                        await message.author.send(
                            f"🔇 **You have been timed out for 10 minutes**\n\n"
                            f"You received 3 warnings for inappropriate language.\n"
                            f"Please review the server rules."
                        )
                    except:
                        pass
                    
                    # Reset timeout counter
                    bad_word_warnings[message.author.id]["timeouts"] = 0
                except Exception as e:
                    print(f"Error timing out user: {e}")
            
            # Reset count after warning
            bad_word_warnings[message.author.id]["count"] = 0
            bad_word_warnings[message.author.id]["messages"] = []
        
        # Don't process this message further (already deleted)
        return
    
    # Check for @everyone spam
    if '@everyone' in message.content and not message.author.guild_permissions.mention_everyone:
        await message.delete()
        await message.channel.send(f"{message.author.mention}, please don't spam @everyone!", delete_after=5)
        return
    
    # Spam detection
    current_time = datetime.now()
    message_tracker[message.author.id].append(current_time)
    
    # Remove old messages outside the time window
    message_tracker[message.author.id] = [
        msg_time for msg_time in message_tracker[message.author.id]
        if current_time - msg_time < timedelta(seconds=SPAM_TIME_WINDOW)
    ]
    
    # Check if user is spamming
    if len(message_tracker[message.author.id]) > SPAM_THRESHOLD:
        await message.delete()
        await message.channel.send(
            f"{message.author.mention}, please slow down! You're sending messages too quickly.",
            delete_after=5
        )
        # Clear their tracker to give them a fresh start
        message_tracker[message.author.id] = []
        return
    
    await bot.process_commands(message)

@bot.event
async def on_reaction_add(reaction, user):
    # Check if this is a contest poll reaction
    if user.bot:
        return
    
    message = reaction.message
    global active_poll_message_id, poll_expiry_time, pending_registrations
    
    # Check if this message is the active contest poll
    if message.embeds and len(message.embeds) > 0:
        embed = message.embeds[0]
        if embed.title and "Contest Poll" in embed.title and message.id == active_poll_message_id:
            # Check if poll hasn't expired
            if poll_expiry_time and datetime.now() >= poll_expiry_time:
                try:
                    await user.send("⏰ Sorry, the contest poll has expired. Registration is closed.")
                except:
                    pass
                return
            
            # Check if user already confirmed registration for this poll
            if user.id in contest_participants and contest_participants[user.id].get("confirmed"):
                try:
                    kaggle_id = contest_participants[user.id]["kaggle_id"]
                    await user.send(f"✅ You're already registered with Kaggle ID: **{kaggle_id}**")
                except:
                    pass
                return
            
            # Check if user has Kaggle ID already set in permanent storage
            has_kaggle_id = user.id in kaggle_ids
            
            # Send DM asking for participation (no timeout, poll expiry handles it)
            try:
                if has_kaggle_id:
                    # User already has Kaggle ID, just ask for confirmation
                    kaggle_id = kaggle_ids[user.id]["kaggle_id"]
                    await user.send(
                        f"Hi {user.name}! 👋\n\n"
                        f"Thanks for your interest in our weekly contest!\n\n"
                        f"**Your Kaggle ID:** {kaggle_id}\n\n"
                        f"Are you ready to participate?\n"
                        f"Reply with **Yes** to confirm or **No** to cancel.\n\n"
                        f"⏰ You can register until the poll expires."
                    )
                    # Mark as pending with existing Kaggle ID
                    pending_registrations[user.id] = {"has_kaggle_id": True, "timestamp": datetime.now()}
                else:
                    # User doesn't have Kaggle ID - ask if they want to participate first
                    await user.send(
                        f"Hi {user.name}! 👋\n\n"
                        f"Thanks for your interest in our weekly contest!\n\n"
                        f"Would you like to participate?\n"
                        f"Reply with **Yes** to continue or **No** to cancel.\n\n"
                        f"⏰ You can register until the poll expires."
                    )
                    # Mark as pending without Kaggle ID
                    pending_registrations[user.id] = {"has_kaggle_id": False, "timestamp": datetime.now()}
                
            except Exception as e:
                print(f"Error sending DM to user: {e}")

@bot.event
async def on_reaction_remove(reaction, user):
    """Handle when someone removes their reaction from the poll"""
    if user.bot:
        return
    
    message = reaction.message
    global active_poll_message_id, poll_expiry_time, pending_registrations, contest_participants
    
    # Check if this is the active contest poll
    if message.embeds and len(message.embeds) > 0:
        embed = message.embeds[0]
        if embed.title and "Contest Poll" in embed.title and message.id == active_poll_message_id:
            # Remove from participants if registered
            if user.id in contest_participants:
                kaggle_id = contest_participants[user.id]["kaggle_id"]
                del contest_participants[user.id]
                save_participants()
                
                try:
                    await user.send(f"❌ You've been removed from the contest. Your Kaggle ID **{kaggle_id}** has been unregistered.")
                except:
                    pass
                
                print(f"Removed {user.name} from contest participants")
            
            # Remove from pending registrations
            if user.id in pending_registrations:
                del pending_registrations[user.id]
                try:
                    await user.send("❌ Registration cancelled. You can react again if you change your mind!")
                except:
                    pass

# ===== ERROR HANDLERS =====

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    print(f"DEBUG: Command error occurred!")
    print(f"DEBUG: Command: {ctx.message.content}")
    print(f"DEBUG: Error type: {type(error).__name__}")
    print(f"DEBUG: Error: {error}")
    
    if isinstance(error, commands.MissingPermissions):
        print(f"DEBUG: Permission denied for {ctx.author.name} - {error}")
        embed = discord.Embed(
            title="❌ Permission Denied",
            description="You don't have permission to use this command!\nThis command is for administrators only.",
            color=0xff0000
        )
        await ctx.send(embed=embed, delete_after=10)
        await ctx.message.delete()
    elif isinstance(error, commands.MissingRequiredArgument):
        print(f"DEBUG: Missing argument - {error}")
        await ctx.send(f"❌ Missing required argument: `{error.param.name}`", delete_after=10)
        await ctx.message.delete()
    elif isinstance(error, commands.CommandNotFound):
        print(f"DEBUG: Command not found - {ctx.message.content}")
        # Delete invalid command messages (like !halp instead of !help)
        try:
            await ctx.message.delete()
        except:
            pass
    else:
        # Log other errors
        print(f"Error in command {ctx.command}: {error}")
        import traceback
        traceback.print_exc()  # Print full traceback

# ===== COMMANDS =====

# Traditional prefix commands (!command)

@bot.command()
@commands.has_permissions(administrator=True)
async def create_contest(ctx, duration_hours: float, *, question):
    """Create a poll for weekly contests with a time limit
    
    Usage: !create_contest <hours> <question>
    Example: !create_contest 48 Who wants to join this week's ML challenge?
    Example: !create_contest 0.1 Quick 6-minute test poll
    """
    print(f"DEBUG: create_contest called by {ctx.author.name}")
    print(f"DEBUG: duration_hours = {duration_hours}")
    print(f"DEBUG: question = '{question}'")
    await ctx.message.delete()  # Delete the command message
    print(f"DEBUG: Message deleted, creating poll...")
    
    global active_poll_message_id, poll_expiry_time, pending_registrations
    
    # Clear previous participants for new contest
    global contest_participants
    contest_participants = {}
    pending_registrations = {}
    save_participants()
    
    # Calculate expiry time
    poll_expiry_time = datetime.now() + timedelta(hours=duration_hours)
    expiry_str = poll_expiry_time.strftime("%Y-%m-%d %H:%M:%S")
    
    embed = discord.Embed(
        title="📊 Contest Poll - Weekly AI Competition",
        description=question,
        color=0x00ff00,
        timestamp=datetime.now()
    )
    embed.add_field(
        name="How to Participate",
        value="React with 👍 to join the contest!\nYou'll receive a DM asking for your Kaggle ID.",
        inline=False
    )
    embed.add_field(
        name="⏰ Registration Deadline",
        value=f"Poll expires: **{expiry_str}**\n({duration_hours} hours from now)",
        inline=False
    )
    embed.set_footer(text="AI Olympiad Community")
    
    print(f"DEBUG: Sending embed...")
    try:
        poll_message = await ctx.send(embed=embed)
        print(f"DEBUG: Embed sent successfully, adding reaction...")
        await poll_message.add_reaction("👍")
        print(f"DEBUG: Reaction added!")
    except Exception as e:
        print(f"DEBUG ERROR: Failed to send embed or add reaction: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Track this poll as the active one
    active_poll_message_id = poll_message.id
    
    # Schedule poll expiry notification
    asyncio.create_task(expire_poll(poll_message, duration_hours))
    print(f"DEBUG: Contest created successfully!")

async def expire_poll(poll_message, duration_hours):
    """Auto-close poll after duration expires"""
    await asyncio.sleep(duration_hours * 3600)  # Convert hours to seconds
    
    global active_poll_message_id, poll_expiry_time, pending_registrations
    
    # Send expiry notification
    embed = discord.Embed(
        title="⏰ Contest Poll Closed",
        description=f"Registration has closed! Total participants: **{len(contest_participants)}**",
        color=0xff9900
    )
    
    await poll_message.channel.send(embed=embed)
    
    # Clear pending registrations
    pending_registrations = {}
    
    print(f"Poll expired with {len(contest_participants)} participants")

@bot.command()
@commands.has_permissions(administrator=True)
async def show_participants(ctx):
    """Show all contest participants with their Kaggle profiles"""
    await ctx.message.delete()  # Delete the command message
    
    if not contest_participants:
        await ctx.send("No participants registered yet!", delete_after=5)
        return
    
    embed = discord.Embed(
        title="🏆 Contest Participants",
        description="List of registered participants",
        color=0x0099ff,
        timestamp=datetime.now()
    )
    
    for user_id, data in contest_participants.items():
        kaggle_url = f"https://www.kaggle.com/{data['kaggle_id']}"
        embed.add_field(
            name=data['name'],
            value=f"Kaggle: [{data['kaggle_id']}]({kaggle_url})",
            inline=False
        )
    
    embed.set_footer(text=f"Total Participants: {len(contest_participants)}")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def clear_participants(ctx):
    """Clear all contest participants (for new contest)"""
    await ctx.message.delete()  # Delete the command message
    
    global contest_participants, active_poll_message_id, poll_expiry_time, pending_registrations
    contest_participants = {}
    active_poll_message_id = None
    poll_expiry_time = None
    pending_registrations = {}
    save_participants()
    await ctx.send("✅ Participant list cleared!", delete_after=5)

@bot.command()
async def activity(ctx, member: discord.Member = None):
    """Check user activity stats"""
    await ctx.message.delete()  # Delete command message
    
    member = member or ctx.author
    
    if member.id not in user_activity:
        await ctx.send(f"{member.mention} has no recorded activity yet!", delete_after=15)
        return
    
    data = user_activity[member.id]
    embed = discord.Embed(
        title=f"📊 Activity Stats for {member.name}",
        color=0x9b59b6
    )
    embed.add_field(name="Messages Sent", value=str(data["messages"]), inline=True)
    
    if data["last_seen"]:
        last_seen = data["last_seen"].strftime("%Y-%m-%d %H:%M:%S")
        embed.add_field(name="Last Seen", value=last_seen, inline=True)
    
    embed.set_thumbnail(url=member.avatar.url if member.avatar else None)
    await ctx.send(embed=embed, delete_after=30)

@bot.command()
@commands.has_permissions(administrator=True)
async def check_warnings(ctx, member: discord.Member = None):
    """Check bad word warnings for a user or all users"""
    await ctx.message.delete()
    
    if member:
        # Check specific user
        if member.id in bad_word_warnings and bad_word_warnings[member.id]["count"] > 0:
            data = bad_word_warnings[member.id]
            embed = discord.Embed(
                title=f"⚠️ Warnings for {member.name}",
                description=f"**Current violations:** {data['count']}/{BAD_WORD_THRESHOLD}",
                color=0xff9900
            )
            
            for i, msg_data in enumerate(data["messages"][-5:], 1):
                embed.add_field(
                    name=f"Violation {i} - #{msg_data['channel']}",
                    value=f"```{msg_data['content'][:100]}```",
                    inline=False
                )
            
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{member.mention} has no warnings.", delete_after=5)
    else:
        # Check all users with warnings
        users_with_warnings = [(user_id, data) for user_id, data in bad_word_warnings.items() if data["count"] > 0]
        
        if users_with_warnings:
            embed = discord.Embed(
                title="⚠️ All User Warnings",
                description=f"Users with pending violations (threshold: {BAD_WORD_THRESHOLD})",
                color=0xff9900
            )
            
            for user_id, data in users_with_warnings[:10]:  # Show top 10
                member = ctx.guild.get_member(user_id)
                if member:
                    embed.add_field(
                        name=member.name,
                        value=f"Violations: {data['count']}/{BAD_WORD_THRESHOLD}",
                        inline=True
                    )
            
            await ctx.send(embed=embed)
        else:
            await ctx.send("No users have warnings currently.", delete_after=5)

@bot.command()
@commands.has_permissions(administrator=True)
async def clear_warnings(ctx, member: discord.Member):
    """Clear bad word warnings for a specific user"""
    await ctx.message.delete()
    
    if member.id in bad_word_warnings:
        bad_word_warnings[member.id]["count"] = 0
        bad_word_warnings[member.id]["messages"] = []
        await ctx.send(f"✅ Cleared warnings for {member.mention}", delete_after=5)
    else:
        await ctx.send(f"{member.mention} has no warnings to clear.", delete_after=5)

@bot.command()
@commands.has_permissions(administrator=True)
async def server_stats(ctx):
    """Show overall server activity statistics"""
    await ctx.message.delete()  # Delete the command message
    
    total_messages = sum(data["messages"] for data in user_activity.values())
    active_users = len(user_activity)
    
    embed = discord.Embed(
        title="📈 Server Statistics",
        description="Overall activity in the AI Olympiad Community",
        color=0x3498db
    )
    embed.add_field(name="Total Members", value=str(ctx.guild.member_count), inline=True)
    embed.add_field(name="Active Users", value=str(active_users), inline=True)
    embed.add_field(name="Total Messages Tracked", value=str(total_messages), inline=True)
    
    await ctx.send(embed=embed)

@bot.command()
async def my_kaggle(ctx, kaggle_id: str = None):
    """Set or view your Kaggle ID"""
    await ctx.message.delete()  # Delete the command message for privacy
    
    if kaggle_id:
        # Check if updating existing ID
        if ctx.author.id in kaggle_ids:
            old_id = kaggle_ids[ctx.author.id]["kaggle_id"]
            kaggle_ids[ctx.author.id]["kaggle_id"] = kaggle_id
            kaggle_ids[ctx.author.id]["updated_at"] = datetime.now().isoformat()
            save_kaggle_ids()
            
            # Also update in contest_participants if they're in an active contest
            if ctx.author.id in contest_participants:
                contest_participants[ctx.author.id]["kaggle_id"] = kaggle_id
                save_participants()
            
            await ctx.author.send(
                f"✅ **Kaggle ID Updated!**\n\n"
                f"Previous ID: ~~{old_id}~~\n"
                f"New ID: **{kaggle_id}**\n\n"
                f"Your new ID will be used for future contests."
            )
        else:
            # Setting for the first time
            kaggle_ids[ctx.author.id] = {
                "name": ctx.author.name,
                "kaggle_id": kaggle_id,
                "registered_at": datetime.now().isoformat()
            }
            save_kaggle_ids()
            
            # Also update in contest_participants if they're in an active contest
            if ctx.author.id in contest_participants:
                contest_participants[ctx.author.id]["kaggle_id"] = kaggle_id
                save_participants()
            
            await ctx.author.send(
                f"✅ **Kaggle ID Saved!**\n\n"
                f"Your Kaggle ID: **{kaggle_id}**\n"
                f"Profile: https://www.kaggle.com/{kaggle_id}\n\n"
                f"💡 **Tip:** Use `!my_kaggle <new_id>` anytime to update it."
            )
    else:
        if ctx.author.id in kaggle_ids:
            kaggle_id = kaggle_ids[ctx.author.id]["kaggle_id"]
            kaggle_url = f"https://www.kaggle.com/{kaggle_id}"
            await ctx.author.send(
                f"**Your Kaggle Profile:**\n"
                f"ID: **{kaggle_id}**\n"
                f"Profile: {kaggle_url}\n\n"
                f"💡 To update: `!my_kaggle <new_id>`"
            )
        else:
            await ctx.author.send(
                f"❌ **No Kaggle ID Found**\n\n"
                f"You haven't set your Kaggle ID yet!\n"
                f"Use: `!my_kaggle <your_kaggle_id>`\n\n"
                f"**Example:** `!my_kaggle johndoe123`"
            )

@bot.command(name='help')
async def help_command(ctx):
    """Show all available commands (only visible to you)"""
    # Delete the command message FIRST
    try:
        await ctx.message.delete()
    except:
        pass
    
    is_admin = ctx.author.guild_permissions.administrator
    
    embed = discord.Embed(
        title="🤖 AI Olympiad Bot Commands",
        description="Here are the commands you can use:",
        color=0xe74c3c
    )
    
    embed.add_field(
        name="General Commands",
        value="`!activity [@user]` - Check activity stats\n"
              "`!my_kaggle [kaggle_id]` - Set/view your Kaggle ID\n"
              "`!help` - Show this help message",
        inline=False
    )
    
    if is_admin:
        embed.add_field(
            name="Admin Commands",
            value="`!create_contest <hours> <question>` - Create contest poll with time limit\n"
                  "`!set_competition <competition-id>` - Set active Kaggle competition\n"
                  "`!contest_leaderboard` - Show live Kaggle leaderboard & award winner\n"
                  "`!show_participants` - Show contest participants\n"
                  "`!clear_participants` - Clear participant list\n"
                  "`!server_stats` - Show server statistics\n"
                  "`!check_warnings [@user]` - Check bad word warnings\n"
                  "`!clear_warnings @user` - Clear warnings for a user",
            inline=False
        )
    
    embed.add_field(
        name="Automatic Features",
        value="✅ Smart bad word detection (handles variations like sh#t)\n"
              f"✅ Warning threshold: {BAD_WORD_THRESHOLD} violations before admin alert\n"
              "✅ Auto-timeout after 3 warnings (10 minutes)\n"
              "✅ Spam prevention\n"
              "✅ @everyone spam blocking\n"
              "✅ User activity tracking\n"
              "✅ Contest registration via reactions\n"
              "✅ Daily motivational AI tips",
        inline=False
    )
    
    embed.set_footer(text="AI Olympiad Community Bot • Only you can see this")
    
    # Send as ephemeral message (only visible to user)
    help_msg = await ctx.send(embed=embed)
    
    # Delete after 60 seconds
    await asyncio.sleep(60)
    try:
        await help_msg.delete()
    except:
        pass

# ===== KAGGLE FEATURES =====

@bot.command(name='set_competition')
@commands.has_permissions(administrator=True)
async def set_competition(ctx, *, competition_id: str):
    """Set the active Kaggle competition to track
    
    Usage: !set_competition digit-recognizer
    """
    print("=" * 60)
    print(f"DEBUG: set_competition CALLED by {ctx.author.name}")
    print(f"DEBUG: Competition ID = '{competition_id}'")
    print("=" * 60)
    
    try:
        await ctx.message.delete()
        print("DEBUG: Command message deleted")
    except discord.errors.Forbidden:
        print("Warning: Bot lacks 'Manage Messages' permission to delete command")
    except Exception as e:
        print(f"Warning: Could not delete command message: {e}")
    
    global active_competition, competition_end_time
    
    try:
        loading_msg = await ctx.send(f"🔄 Verifying competition: **{competition_id}**...")
        
        # Try to access the competition directly to verify it exists
        try:
            # This will throw an error if competition doesn't exist
            leaderboard = kaggle_api.competition_leaderboard_view(competition_id)
            
            # Competition exists! Now get more details if possible
            comp_list = list(kaggle_api.competitions_list(search=competition_id))
            
            if comp_list:
                # Found in search results, use detailed info
                competition = comp_list[0]
                active_competition = competition.ref
                competition_end_time = competition.deadline
                comp_title = competition.title
                deadline_str = competition.deadline.strftime("%Y-%m-%d %H:%M UTC") if competition.deadline else "N/A"
            else:
                # Not in search but leaderboard works, use competition_id directly
                active_competition = competition_id
                competition_end_time = None
                comp_title = competition_id.replace('-', ' ').title()
                deadline_str = "Check Kaggle website"
            
        except Exception as verify_error:
            await loading_msg.delete()
            await ctx.send(f"❌ Competition '{competition_id}' not found or not accessible!\n💡 Make sure the competition ID is correct.", delete_after=15)
            return
        
        embed = discord.Embed(
            title="🎯 Competition Set!",
            description=f"Now tracking: **{comp_title}**",
            color=0x00ff00,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Competition ID", value=active_competition, inline=False)
        embed.add_field(name="Deadline", value=deadline_str, inline=False)
        embed.add_field(name="URL", value=f"https://www.kaggle.com/c/{active_competition}", inline=False)
        
        # DM all registered participants with the competition link
        dm_count = 0
        dm_failed = 0
        
        if contest_participants:
            embed.add_field(
                name="📨 Sending Links",
                value=f"DMing competition link to {len(contest_participants)} registered participant(s)...",
                inline=False
            )
            
            for user_id, data in contest_participants.items():
                try:
                    user = await bot.fetch_user(int(user_id))
                    await user.send(
                        f"🎯 **Competition is Live!**\n\n"
                        f"**{comp_title}**\n\n"
                        f"🔗 **Link:** https://www.kaggle.com/c/{active_competition}\n"
                        f"⏰ **Deadline:** {deadline_str}\n\n"
                        f"Your registered Kaggle ID: **{data['kaggle_id']}**\n\n"
                        f"Good luck! 🚀"
                    )
                    dm_count += 1
                    print(f"DEBUG: Sent competition link to {data['name']}")
                except Exception as dm_error:
                    print(f"Warning: Could not DM user {user_id}: {dm_error}")
                    dm_failed += 1
            
            embed.add_field(
                name="✅ Links Sent",
                value=f"Successfully sent to {dm_count} participant(s)" + (f" • {dm_failed} failed" if dm_failed > 0 else ""),
                inline=False
            )
        
        embed.set_footer(text="Use !contest_leaderboard to view rankings")
        
        await loading_msg.delete()
        await ctx.send(embed=embed, delete_after=60)
        
    except Exception as e:
        print(f"Error setting competition: {e}")
        import traceback
        traceback.print_exc()
        await ctx.send(f"❌ Error setting competition: {str(e)}", delete_after=15)

@bot.command()
@commands.has_permissions(administrator=True)
async def contest_leaderboard(ctx):
    """Display Kaggle competition leaderboard for registered participants"""
    await ctx.message.delete()
    
    if not active_competition:
        await ctx.send("❌ No active competition set! Use `!set_competition <competition-id>` first.", delete_after=15)
        return
    
    if not contest_participants:
        await ctx.send("❌ No contest participants registered!", delete_after=15)
        return
    
    loading_msg = await ctx.send(f"🔄 Fetching leaderboard from Kaggle competition: **{active_competition}**...")
    
    try:
        # Get the competition leaderboard from Kaggle
        leaderboard = kaggle_api.competition_leaderboard_view(active_competition)
        
        print(f"DEBUG: First 3 leaderboard entries:")
        for i, entry in enumerate(leaderboard[:3]):
            print(f"  Position {i}: teamName={entry.team_name}, rank={getattr(entry, 'rank', 'N/A')}, score={entry.score}")
        
        # Create a dict of Kaggle IDs from our participants
        participant_kaggle_ids = {data["kaggle_id"]: user_id for user_id, data in contest_participants.items()}
        
        # Find our participants on the leaderboard
        # Normalize both team names and kaggle IDs by removing non-alphanumeric
        # characters and comparing lowercase forms. This handles display names
        # like "Mahmud Galib" vs username "mahmudgalib".
        import re
        team_results = []
        
        print(f"DEBUG: Looking for participants: {participant_kaggle_ids}")
        print(f"DEBUG: Total leaderboard entries: {len(leaderboard)}")
        
        for rank, entry in enumerate(leaderboard, 1):
            team_name = entry.team_name or ""
            normalized_team = re.sub(r"[^0-9a-zA-Z]", "", team_name).lower()
            
            # Use actual rank from Kaggle entry if available
            actual_rank = getattr(entry, 'rank', rank)

            # Check if this team matches any of our participants
            for kaggle_id, user_id in participant_kaggle_ids.items():
                normalized_kaggle = re.sub(r"[^0-9a-zA-Z]", "", kaggle_id).lower()

                if not normalized_kaggle or not normalized_team:
                    continue

                if normalized_kaggle in normalized_team or normalized_team in normalized_kaggle:
                    print(f"DEBUG: MATCH FOUND! Position {rank}, Actual Rank {actual_rank}: '{team_name}' matches '{kaggle_id}'")
                    member = ctx.guild.get_member(user_id)
                    if member:
                        print(f"DEBUG: Member found: {member.name}")
                        team_results.append({
                            'member': member,
                            'kaggle_id': kaggle_id,
                            'rank': actual_rank,
                            'score': entry.score
                        })
                    else:
                        print(f"DEBUG: Member NOT found for user_id {user_id}")
                    break
        
        print(f"DEBUG: Found {len(team_results)} results")
        
        # Sort by rank
        team_results.sort(key=lambda x: x['rank'])
        
        # Create leaderboard embed
        embed = discord.Embed(
            title=f"🏆 Contest Leaderboard - {active_competition}",
            description="**Live Kaggle Rankings**",
            color=0xffd700,
            timestamp=datetime.now()
        )
        
        if team_results:
            medals = ["🥇", "🥈", "🥉"]
            for i, result in enumerate(team_results, 1):
                medal = medals[i-1] if i <= 3 else f"{i}."
                
                embed.add_field(
                    name=f"{medal} {result['member'].name}",
                    value=f"Kaggle: [{result['kaggle_id']}](https://www.kaggle.com/{result['kaggle_id']})\n"
                          f"**Rank:** #{result['rank']}\n"
                          f"**Score:** {result['score']}",
                    inline=False
                )
            
            # Award winner role to top participant (best among registered participants)
            if team_results:
                winner = team_results[0]  # Best performer among registered participants
                print(f"DEBUG: Winner is {winner['member'].name} at rank #{winner['rank']}")
                
                winner_role = discord.utils.get(ctx.guild.roles, name="🏆 Contest Winner")
                
                # Create role if it doesn't exist
                if not winner_role:
                    print("DEBUG: Creating Contest Winner role...")
                    winner_role = await ctx.guild.create_role(
                        name="🏆 Contest Winner",
                        color=discord.Color.gold(),
                        reason="Competition winner role"
                    )
                    print("DEBUG: Role created!")
                
                # Remove role from previous winners
                for member in ctx.guild.members:
                    if winner_role in member.roles and member.id != winner['member'].id:
                        print(f"DEBUG: Removing role from {member.name}")
                        await member.remove_roles(winner_role, reason="New competition winner")
                
                # Award role to current winner
                if winner_role not in winner['member'].roles:
                    print(f"DEBUG: Awarding role to {winner['member'].name}...")
                    await winner['member'].add_roles(winner_role, reason=f"Won competition: {active_competition}")
                    print("DEBUG: Role awarded!")
                    winner_message = f"🎊 {winner['member'].name} has been awarded the Contest Winner role!"
                else:
                    print(f"DEBUG: {winner['member'].name} already has the role")
                    winner_message = f"🏆 {winner['member'].name} is the current winner!"
                
                # Set footer showing found/total participants
                found_count = len(team_results)
                total_count = len(contest_participants)
                if found_count < total_count:
                    missing_count = total_count - found_count
                    embed.set_footer(text=f"{winner_message}\nShowing {found_count}/{total_count} participants • {missing_count} not found on leaderboard")
                else:
                    embed.set_footer(text=f"{winner_message}\nAll {total_count} participants found!")
        else:
            embed.add_field(
                name="No Results", 
                value="No registered participants found on the leaderboard yet.\n"
                      "Make sure to submit your solution on Kaggle first!", 
                inline=False
            )
            embed.set_footer(text=f"Total Registered: {len(contest_participants)} • Try !my_kaggle to verify your username")
        
        await loading_msg.delete()
        
        # Send to leaderboard channel
        leaderboard_channel = ctx.guild.get_channel(LEADERBOARD_CHANNEL_ID) if LEADERBOARD_CHANNEL_ID else ctx.channel
        await leaderboard_channel.send(embed=embed)
        
    except Exception as e:
        print(f"Error fetching leaderboard: {e}")
        import traceback
        traceback.print_exc()
        await loading_msg.delete()
        await ctx.send(f"❌ Error fetching leaderboard: {str(e)}", delete_after=15)


# ===== BACKGROUND TASKS (Keep bot active 24/7) =====

@tasks.loop(hours=24)
async def daily_stats_update():
    """Daily task: Post server stats to specific stats channel"""
    for guild in bot.guilds:
        # Use the stats channel from .env
        stats_channel = bot.get_channel(STATS_CHANNEL_ID)
        
        if stats_channel:
            total_messages = sum(data["messages"] for data in user_activity.values())
            active_users = len([u for u in user_activity.values() if u["messages"] > 0])
            
            embed = discord.Embed(
                title="📊 Daily Server Summary",
                description=f"**{datetime.now().strftime('%B %d, %Y')}**",
                color=0x00ff00
            )
            embed.add_field(name="Active Users", value=str(active_users), inline=True)
            embed.add_field(name="Total Messages", value=str(total_messages), inline=True)
            
            await stats_channel.send(embed=embed)

@tasks.loop(hours=6)
async def motivation_and_tips():
    """Every 6 hours: Post EITHER a tip OR inspiration (alternates)"""
    tips = [
        # Validation & Testing
        "💡 **Tip**: Always validate your models with cross-validation!",
        "🧪 **A/B Testing**: Always test your assumptions with real data!",
        "🎯 **Baseline First**: Always establish a simple baseline before trying complex models!",
        
        # Feature Engineering
        "🚀 **Did you know?** Feature engineering can boost your model's performance more than switching algorithms!",
        "🎨 **Feature Selection**: Remove irrelevant features - they just add noise!",
        "🌐 **Data Collection**: More quality data beats a better algorithm every time!",
        
        # Learning & Practice
        "📚 **Learning Tip**: Read at least one AI research paper this week!",
        "🎯 **Challenge**: Try implementing a model from scratch without using libraries!",
        "� **Practice**: Spend 30 minutes daily on coding challenges to improve your skills!",
        "🎓 **Study Goal**: Master one new ML algorithm every week!",
        "� **Read Code**: Reading others' code teaches you more than tutorials!",
        "🎓 **Continuous Learning**: AI evolves fast - dedicate time weekly to learn new techniques!",
        
        # Deep Learning
        "🧠 **Fact**: Neural networks are inspired by the human brain's structure!",
        "🧠 **Deep Learning**: More layers ≠ better results. Start simple, then add complexity!",
        "💡 **Transfer Learning**: Don't train from scratch when pretrained models exist!",
        "⚡ **Batch Size**: Larger batches train faster but smaller batches generalize better!",
        
        # Optimization
        "⚡ **Quick Tip**: Use early stopping to prevent overfitting in your models!",
        "📈 **Learning Rate**: Too high and you overshoot, too low and you never converge. Find the balance!",
        "🔥 **Hot Tip**: Normalize your inputs - it helps models converge faster!",
        "🚀 **GPU Power**: Use cloud GPUs for heavy training - it's faster and often cheaper!",
        
        # Best Practices
        "📊 **Data Insight**: 80% of data science is cleaning data, 20% is complaining about cleaning data!",
        "� **Random Seed Tip**: Always set random seeds for reproducible results!",
        "� **Save Often**: Save your models and checkpoints. You never know when you'll need them!",
        "📊 **Metrics Matter**: Choose the right evaluation metric for your problem!",
        "� **Explore Data**: Spend time understanding your data before building models!",
        
        # Debugging & Development
        "🏃 **Speed Tip**: Profile your code before optimizing - don't guess where the bottleneck is!",
        "🔍 **Debugging**: Print statements are your friend. Use them generously!",
        "📝 **Documentation**: Future you will thank present you for writing clear comments!",
        "🔬 **Experimentation**: Keep a log of all experiments - what worked and what didn't!",
        
        # Problem Solving
        "🎯 **Focus**: Master one algorithm deeply before jumping to the next one!",
        "🔄 **Iteration**: Your first model won't be your best. Keep iterating!",
        "🧩 **Problem Solving**: Break complex problems into smaller, manageable pieces!",
        "🎯 **Target Variable**: Make sure you understand what you're predicting!",
        "💪 **Persistence**: Stuck on a bug? Take a break and come back with fresh eyes!",
        
        # Model Evaluation
        "🎯 **Overfitting**: If training accuracy is 99% but validation is 60%, you're overfitting!",
        "� **Learning Curves**: Plot them to understand if you need more data or a better model!",
        "📊 **Bias-Variance**: Understanding this tradeoff is key to better models!",
        "📊 **Data Leakage**: Watch out for it - it's the silent killer of ML models!",
        
        # Tools & Techniques
        "🎨 **Visualization Tip**: A good plot can reveal insights that numbers alone cannot!",
        "� **Experiment**: Try different hyperparameters - sometimes small changes make big differences!",
        "🌟 **Ensemble Magic**: Combining multiple models often beats a single perfect model!",
        "🔧 **Tool Tip**: Learn pandas, numpy, and matplotlib well - they're your foundation!",
        
        # Kaggle Specific
        "🎓 **Kaggle Wisdom**: Learn from others' notebooks - they're full of valuable insights!",
        "🌟 **Community**: Join ML communities - collaboration accelerates learning!",
        "🌐 **Open Source**: Contribute to open source ML projects to learn and give back!",
        
        # Theory & Understanding
        "📐 **Math Matters**: Understanding the math behind algorithms makes you a better ML practitioner!",
        "🧠 **Intuition**: Build intuition by implementing algorithms from scratch at least once!",
        "🌟 **Remember**: The best model is the one that solves the problem, not the most complex one!",
        
        # Additional Tips
        "🔄 **Cross-Validation**: K-fold is your best friend for small datasets!",
        "📊 **Imbalanced Data**: Don't ignore class imbalance - use SMOTE or weighted loss!",
        "🎯 **Precision vs Recall**: Know when to optimize for which metric!",
        "🧪 **Hyperparameter Tuning**: Use grid search or random search, but understand what you're tuning!",
        "📈 **Gradient Descent**: Understanding optimization is key to training better models!",
        "🎨 **Data Augmentation**: Especially powerful for image and text data!",
        "� **Regularization**: L1, L2, dropout - know when to use each!",
        "🔍 **EDA First**: Exploratory Data Analysis reveals patterns models might miss!",
        "🎯 **Test Set**: Never touch it until final evaluation - it's your ground truth!",
        "📊 **Confusion Matrix**: It tells you more than accuracy alone ever will!",
        "🚀 **Batch Normalization**: Speeds up training and improves stability!",
        "🧠 **Attention Mechanisms**: They revolutionized NLP and are spreading everywhere!",
        "📈 **Loss Function**: Choose wisely - MSE for regression, cross-entropy for classification!",
        "🎯 **Outliers**: Detect them, understand them, decide whether to remove them!",
        "💾 **Version Control**: Git your models and experiments, not just code!",
        "� **Reproducibility**: Document everything - seeds, versions, environment!",
        "🎨 **Feature Scaling**: StandardScaler, MinMaxScaler - know the difference!",
        "📊 **ROC-AUC**: Great for binary classification, but understand its limitations!",
        "🧪 **Train-Val-Test Split**: 60-20-20 or 70-15-15, but keep it consistent!",
        "🎯 **Early Signs**: Monitor validation loss - divergence from training is a red flag!",
        "🚀 **Parallel Processing**: Use joblib or multiprocessing for CPU-heavy tasks!",
        "📈 **Gradient Clipping**: Prevents exploding gradients in RNNs and deep networks!",
        "🧠 **Activation Functions**: ReLU is good, but try Leaky ReLU or GELU too!",
        "� **Dimensionality Reduction**: PCA and t-SNE are powerful visualization tools!"
    ]
    
    
    # Inspirational quotes
    inspirations = [
        "🌟 **Motivation**: Every Kaggle Grandmaster started as a beginner. Keep learning!",
        "� **Andrew Ng**: 'AI is the new electricity' - It's transforming every industry!",
        "🔥 **Geoffrey Hinton**: 'Deep learning is going to be able to do everything'",
        "🎯 **Inspiration**: Your first 100 models will be terrible. Build them anyway!",
        "🌟 **Wisdom**: The difference between a beginner and expert is that the expert has failed more times!",
        "💡 **Yann LeCun**: 'The most important thing in AI is not to be afraid of making mistakes'",
        "🚀 **Growth Mindset**: Every error message is a learning opportunity in disguise!",
        "🎓 **Success Formula**: Consistency beats intensity. Code a little every day!",
        "🔥 **Remember**: You don't need a PhD to make an impact in AI!",
        "💪 **Perseverance**: That bug you've been fighting? Solving it will make you 10x better!",
        "🌟 **Yoshua Bengio**: 'Be patient with yourself. Learning takes time'",
        "🎯 **Achievement**: You're competing with who you were yesterday, not others!",
        "🚀 **Vision**: The models you build today could change someone's life tomorrow!",
        "� **Innovation**: The best AI solutions come from understanding the problem, not the algorithm!",
        "🔥 **Demis Hassabis**: 'AI is a tool to amplify human creativity and ingenuity'",
        "🌟 **Mindset**: Imposter syndrome means you're challenging yourself. That's growth!",
        "💪 **Community**: Share your failures, not just successes. Others learn from both!",
        "🎓 **Kaiming He**: 'Simple ideas often lead to the most significant breakthroughs'",
        "🚀 **Progress**: Six months of consistent learning will put you ahead of 90% of beginners!",
        "🔥 **Reminder**: AI doesn't replace jobs, it replaces tasks. Learn to use it!",
        "💡 **Fei-Fei Li**: 'We need to make AI more human, not make humans more like AI'",
        "🌟 **Perspective**: That Kaggle bronze medal? It's better than 75% of participants!",
        "🎯 **Focus**: Master the fundamentals - they'll serve you for decades!",
        "💪 **Resilience**: Model didn't converge? Try again. That's what separates winners from quitters!",
        "🚀 **Ian Goodfellow**: 'The best way to learn deep learning is to do deep learning'",
        "� **Truth**: Your 'bad' model taught you more than reading 10 tutorials would!",
        "🌟 **Inspiration**: AGI might be years away, but YOUR breakthrough could happen today!",
        "� **Sebastian Thrun**: 'AI will enable us to solve problems we didn't even know we had'",
        "🎓 **Learning**: You don't need to know everything. You need to know where to find everything!",
        "🚀 **Momentum**: Small daily improvements compound into extraordinary results!",
        "🔥 **Jeff Dean**: 'Build systems that learn from data, not just rules'",
        "💪 **Courage**: That competition you're nervous about? Enter it. Learn by doing!",
        "🌟 **Sam Altman**: 'The best way to predict the future is to invent it'",
        "🎯 **Reality Check**: Nobody writes perfect code on the first try. Iteration is the game!",
        "🚀 **Purpose**: Use AI to solve real problems, not just to chase leaderboards!",
        "💡 **Judea Pearl**: 'Data is profoundly dumb. You need intelligence to extract wisdom'",
        "🔥 **Achievement Unlocked**: Every model you deploy is a step toward mastery!",
        "🌟 **Community Spirit**: The AI community is collaborative, not competitive. Ask for help!",
        "💪 **Peter Norvig**: 'Learn from everyone, compete with no one'",
        "🎓 **Long Game**: AI is a marathon, not a sprint. Pace yourself and enjoy the journey!",
        "🚀 **Impact**: Your beginner project today could inspire the next breakthrough tomorrow!",
        "� **Andrej Karpathy**: 'The best way to learn is to teach'",
        "💡 **Transformation**: Every line of code you write is an investment in your future!",
        "🌟 **Belief**: If others can become ML engineers, so can you. It's determination, not talent!",
        "� **François Chollet**: 'Intelligence is the ability to learn, not what you already know'",
        "💪 **Validation**: Your code works? Great! It doesn't? Even better - you're learning!",
        "🚀 **Momentum**: The hardest part is starting. You've already done that!",
        "🔥 **Lex Fridman**: 'Fall in love with the process, not just the outcome'",
        "🌟 **Growth**: Six months ago, this problem would have seemed impossible. Look at you now!",
        "💡 **Remember**: Every expert was once a beginner who refused to give up!"
    ]
    
    # Combine tips and inspirations
    all_messages = tips + inspirations
    
    for guild in bot.guilds:
        # Use configured TIPS_CHANNEL_ID, fallback to searching for "general" or first channel
        if TIPS_CHANNEL_ID:
            channel = bot.get_channel(TIPS_CHANNEL_ID)
        else:
            channel = discord.utils.get(guild.text_channels, name="general")
            if not channel:
                channel = guild.text_channels[0] if guild.text_channels else None
        
        if channel:
            message = random.choice(all_messages)
            await channel.send(message)

# ===== HELPER FUNCTIONS =====

def save_kaggle_ids():
    """Save Kaggle IDs to a permanent JSON file"""
    try:
        with open('kaggle_ids.json', 'w') as f:
            json.dump(kaggle_ids, f, indent=2)
    except Exception as e:
        print(f"Error saving Kaggle IDs: {e}")

def load_kaggle_ids():
    """Load Kaggle IDs from JSON file"""
    global kaggle_ids
    try:
        if os.path.exists('kaggle_ids.json'):
            with open('kaggle_ids.json', 'r') as f:
                content = f.read().strip()
                if content:
                    kaggle_ids = json.loads(content)
                    # Convert string keys back to integers
                    kaggle_ids = {int(k): v for k, v in kaggle_ids.items()}
                    print(f"Loaded {len(kaggle_ids)} Kaggle IDs from file")
    except Exception as e:
        print(f"Error loading Kaggle IDs: {e}")

def save_participants():
    """Save contest participants to a JSON file"""
    try:
        with open('contest_participants.json', 'w') as f:
            json.dump(contest_participants, f, indent=2)
    except Exception as e:
        print(f"Error saving participants: {e}")

def load_participants():
    """Load contest participants from JSON file"""
    global contest_participants
    try:
        if os.path.exists('contest_participants.json'):
            with open('contest_participants.json', 'r') as f:
                content = f.read().strip()
                if content:  # Only load if file has content
                    contest_participants = json.loads(content)
                    # Convert string keys back to integers
                    contest_participants = {int(k): v for k, v in contest_participants.items()}
                    print(f"Loaded {len(contest_participants)} contest participants from file")
    except Exception as e:
        print(f"Error loading participants: {e}")

# Load existing data on startup
load_kaggle_ids()
load_participants()

# Run the bot
bot.run(token, log_handler=handler, log_level=logging.DEBUG)
