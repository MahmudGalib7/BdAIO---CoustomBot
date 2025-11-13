import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from dotenv import load_dotenv
import os
from collections import defaultdict
from datetime import datetime, timedelta
import json
import asyncio
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

# Setup logging
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='/', intents=intents, help_command=None)  # Slash commands only

# Data storage (in production, use a database)
user_activity = defaultdict(lambda: {"messages": 0, "last_seen": None})
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
BAD_WORD_WHITELIST = [int(id.strip()) for id in os.getenv('BAD_WORD_WHITELIST', '').split(',') if id.strip()]  # Channels to skip bad word detection

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
    """Check if text contains bad words, accounting for simple variations only"""
    # First normalize the text (handles @, $, 3, etc. substitutions)
    normalized = normalize_text(text)
    
    # Check normalized version with word boundaries
    # This catches: fuck, sh1t, @ss, fvck, etc.
    for bad_word in BAD_WORDS:
        pattern = r'\b' + re.escape(bad_word) + r'\b'
        if re.search(pattern, normalized):
            return True
    
    # That's it! Simple and reliable.
    # Won't catch f**k or f***k, but those are self-censored anyway.
    # Won't catch "2000" or normal messages either!
    
    return False

# ===== EVENT HANDLERS =====

async def handle_bad_word_warning(author, warning_channel):
    """Handle bad word warning notifications asynchronously"""
    user_data = bad_word_warnings[author.id]
    
    # DM user
    try:
        recent_messages = user_data["messages"][-3:]
        messages_list = "\n".join([f"  • {msg_data['content']}" for msg_data in recent_messages])
        
        await author.send(
            f"⚠️ **Official Warning - Language Violation**\n\n"
            f"You have reached the inappropriate language threshold (**{BAD_WORD_THRESHOLD} violations**).\n\n"
            f"**Recent flagged messages:**\n{messages_list}\n\n"
            f"Please review our server rules and maintain respectful communication.\n"
            f"**Note:** Accumulating 3 warnings will result in a 6-hour timeout.\n\n"
            f"—————————————————\n"
            f"*AI Olympiad Community Moderation Team*"
        )
    except:
        pass
    
    # Notify admin channel
    if warning_channel:
        embed = discord.Embed(
            title="⚠️ Bad Word Threshold Reached",
            description=f"**User:** {author.mention} ({author.name})\n**Violations:** {user_data['count']}",
            color=0xff0000,
            timestamp=datetime.now()
        )
        recent_msgs = user_data["messages"][-3:]
        for i, msg_data in enumerate(recent_msgs, 1):
            embed.add_field(
                name=f"Message {i} - #{msg_data['channel']}",
                value=f"```{msg_data['content']}```",
                inline=False
            )
        embed.set_footer(text=f"Total violations: {user_data['count']}")
        await warning_channel.send(embed=embed)
    
    # Apply timeout if 3 warnings
    bad_word_warnings[author.id]["timeouts"] += 1
    if bad_word_warnings[author.id]["timeouts"] >= 3:
        try:
            await author.timeout(timedelta(hours=6), reason="Exceeded bad word warnings 3 times")
            if warning_channel:
                await warning_channel.send(
                    f"🔇 **{author.mention} has been timed out for 6 hours** (3 warnings reached)"
                )
            try:
                await author.send(
                    f"🔇 **You have been timed out for 6 hours**\n\n"
                    f"You received 3 warnings for inappropriate language.\n"
                    f"Please review the server rules."
                )
            except:
                pass
            bad_word_warnings[author.id]["timeouts"] = 0
        except Exception as e:
            print(f"Error timing out user: {e}")
    
    # Reset count after warning
    bad_word_warnings[author.id]["count"] = 0
    bad_word_warnings[author.id]["messages"] = []

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("AI Olympiad Bot is ready!")
    print(f"Serving {len(bot.guilds)} guild(s)")
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    
    # Start background tasks to keep bot active
    if not daily_stats_update.is_running():
        daily_stats_update.start()
    
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
    
    # PRIORITY CHECK: Bad words first - instant deletion for server messages with text
    if not isinstance(message.channel, discord.DMChannel) and message.content and message.content.strip():
        # Skip bad word detection for whitelisted channels (e.g., music channels)
        if message.channel.id not in BAD_WORD_WHITELIST:
            if contains_bad_word(message.content):
                # Delete instantly
                print(f"DEBUG: Bad word detected from {message.author.name}: '{message.content[:50]}'")
                await message.delete()
                
                # Track violation
                bad_word_warnings[message.author.id]["count"] += 1
                bad_word_warnings[message.author.id]["messages"].append({
                    "content": message.content,
                    "channel": message.channel.name,
                    "timestamp": datetime.now().isoformat()
                })

                # Handle warnings (async, doesn't slow down deletion)
                if bad_word_warnings[message.author.id]["count"] >= BAD_WORD_THRESHOLD:
                    asyncio.create_task(handle_bad_word_warning(message.author, bot.get_channel(WARNING_CHANNEL_ID)))

                return  # Stop processing
    
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

# ===== SLASH COMMANDS =====

# Helper function for poll expiry
async def expire_poll(poll_message, duration_hours):
    await asyncio.sleep(duration_hours * 3600)
    global active_poll_message_id, poll_expiry_time, pending_registrations
    embed = discord.Embed(
        title='⏰ Contest Poll Closed',
        description=f'Registration closed! Total: **{len(contest_participants)}**',
        color=0xff9900
    )
    await poll_message.channel.send(embed=embed)
    pending_registrations = {}

# User Commands

@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def slash_ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot is online!", ephemeral=True)

@bot.tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    is_admin = interaction.user.guild_permissions.administrator
    
    embed = discord.Embed(
        title="🤖 AI Olympiad Bot Commands",
        description="All commands use slash (/) prefix",
        color=0xe74c3c
    )
    
    embed.add_field(
        name="General Commands",
        value="`/ping` - Check if bot is online\n"
              "`/help` - Show this help message\n"
              "`/activity` - Check your activity stats\n"
              "`/setkaggle` - Set your Kaggle ID\n"
              "`/mykaggle` - View your Kaggle ID",
        inline=False
    )
    
    if is_admin:
        embed.add_field(
            name="Admin Commands",
            value="`/createcontest` - Create contest poll\n"
                  "`/setcompetition` - Set Kaggle competition\n"
                  "`/leaderboard` - Show live leaderboard\n"
                  "`/participants` - Show contest participants\n"
                  "`/clearparticipants` - Clear participant list\n"
                  "`/serverstats` - Show server statistics\n"
                  "`/checkwarnings` - Check bad word warnings\n"
                  "`/clearwarnings` - Clear user warnings",
            inline=False
        )
    
    embed.add_field(
        name="Features",
      value="✅ Bad word detection\n"
          "✅ Contest registration\n"
          "✅ Daily server stats",
        inline=False
    )
    
    embed.set_footer(text="AI Olympiad Community Bot")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="activity", description="Check your server activity stats")
async def slash_activity(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    if user_id not in user_activity:
        await interaction.response.send_message("You have no recorded activity yet!", ephemeral=True)
        return
    
    data = user_activity[user_id]
    embed = discord.Embed(
        title=f"📊 Activity Stats for {interaction.user.name}",
        color=0x9b59b6
    )
    embed.add_field(name="Messages Sent", value=str(data["messages"]), inline=True)
    
    if data["last_seen"]:
        last_seen = data["last_seen"].strftime("%Y-%m-%d %H:%M:%S")
        embed.add_field(name="Last Seen", value=last_seen, inline=True)
    
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="setkaggle", description="Set your Kaggle ID")
@app_commands.describe(kaggle_id="Your Kaggle username")
async def slash_setkaggle(interaction: discord.Interaction, kaggle_id: str):
    user_id = interaction.user.id
    
    if user_id in kaggle_ids:
        old_id = kaggle_ids[user_id]["kaggle_id"]
        kaggle_ids[user_id]["kaggle_id"] = kaggle_id
        kaggle_ids[user_id]["updated_at"] = datetime.now().isoformat()
        save_kaggle_ids()
        
        if user_id in contest_participants:
            contest_participants[user_id]["kaggle_id"] = kaggle_id
            save_participants()
        
        await interaction.response.send_message(
            f"✅ **Kaggle ID Updated!**\n\n"
            f"Previous: ~~{old_id}~~\n"
            f"New: **{kaggle_id}**",
            ephemeral=True
        )
    else:
        kaggle_ids[user_id] = {
            "name": interaction.user.name,
            "kaggle_id": kaggle_id,
            "registered_at": datetime.now().isoformat()
        }
        save_kaggle_ids()
        
        if user_id in contest_participants:
            contest_participants[user_id]["kaggle_id"] = kaggle_id
            save_participants()
        
        await interaction.response.send_message(
            f"✅ **Kaggle ID Saved!**\n\n"
            f"ID: **{kaggle_id}**\n"
            f"Profile: https://www.kaggle.com/{kaggle_id}",
            ephemeral=True
        )

@bot.tree.command(name="mykaggle", description="View your saved Kaggle ID")
async def slash_mykaggle(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    if user_id in kaggle_ids:
        kaggle_id = kaggle_ids[user_id]["kaggle_id"]
        await interaction.response.send_message(
            f"**Your Kaggle Profile:**\n"
            f"ID: **{kaggle_id}**\n"
            f"Profile: https://www.kaggle.com/{kaggle_id}\n\n"
            f"💡 Use `/setkaggle` to update",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ **No Kaggle ID Found**\n\n"
            f"Set it with: `/setkaggle <username>`\n"
            f"Example: `/setkaggle johndoe123`",
            ephemeral=True
        )

# Admin Commands

@bot.tree.command(name="createcontest", description="[ADMIN] Create a contest poll")
@app_commands.describe(duration_hours="Hours until poll expires", question="Contest poll question")
async def slash_createcontest(interaction: discord.Interaction, duration_hours: float, question: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    global active_poll_message_id, poll_expiry_time, pending_registrations, contest_participants
    
    contest_participants = {}
    pending_registrations = {}
    save_participants()
    
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
        value="React with 👍 to join!\nYou'll receive a DM for your Kaggle ID.",
        inline=False
    )
    embed.add_field(
        name="⏰ Deadline",
        value=f"Expires: **{expiry_str}**\n({duration_hours} hours)",
        inline=False
    )
    embed.set_footer(text="AI Olympiad Community")
    
    await interaction.response.send_message(embed=embed)
    poll_message = await interaction.original_response()
    await poll_message.add_reaction("👍")
    
    active_poll_message_id = poll_message.id
    asyncio.create_task(expire_poll(poll_message, duration_hours))

@bot.tree.command(name="participants", description="[ADMIN] Show contest participants")
async def slash_participants(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    if not contest_participants:
        await interaction.response.send_message("No participants yet!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🏆 Contest Participants",
        color=0x0099ff,
        timestamp=datetime.now()
    )
    
    for user_id, data in contest_participants.items():
        kaggle_url = f"https://www.kaggle.com/{data['kaggle_id']}"
        embed.add_field(
            name=data['name'],
            value=f"[{data['kaggle_id']}]({kaggle_url})",
            inline=False
        )
    
    embed.set_footer(text=f"Total: {len(contest_participants)}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clearparticipants", description="[ADMIN] Clear all participants")
async def slash_clearparticipants(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    global contest_participants, active_poll_message_id, poll_expiry_time, pending_registrations
    contest_participants = {}
    active_poll_message_id = None
    poll_expiry_time = None
    pending_registrations = {}
    save_participants()
    await interaction.response.send_message("✅ Cleared!", ephemeral=True)

@bot.tree.command(name="serverstats", description="[ADMIN] Show server statistics")
async def slash_serverstats(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    total_messages = sum(data["messages"] for data in user_activity.values())
    active_users = len(user_activity)
    
    embed = discord.Embed(
        title="📈 Server Statistics",
        color=0x3498db
    )
    embed.add_field(name="Total Members", value=str(interaction.guild.member_count), inline=True)
    embed.add_field(name="Active Users", value=str(active_users), inline=True)
    embed.add_field(name="Messages Tracked", value=str(total_messages), inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="checkwarnings", description="[ADMIN] Check bad word warnings")
@app_commands.describe(member="User to check (optional)")
async def slash_checkwarnings(interaction: discord.Interaction, member: discord.Member = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    if member:
        if member.id in bad_word_warnings and bad_word_warnings[member.id]["count"] > 0:
            data = bad_word_warnings[member.id]
            embed = discord.Embed(
                title=f"⚠️ Warnings for {member.name}",
                description=f"Violations: {data['count']}/{BAD_WORD_THRESHOLD}",
                color=0xff9900
            )
            
            for i, msg_data in enumerate(data["messages"][-5:], 1):
                embed.add_field(
                    name=f"Violation {i}",
                    value=f"```{msg_data['content'][:100]}```",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(f"{member.mention} has no warnings", ephemeral=True)
    else:
        users_with_warnings = [(uid, d) for uid, d in bad_word_warnings.items() if d["count"] > 0]
        
        if users_with_warnings:
            embed = discord.Embed(
                title="⚠️ All Warnings",
                description=f"Threshold: {BAD_WORD_THRESHOLD}",
                color=0xff9900
            )
            
            for user_id, data in users_with_warnings[:10]:
                member_obj = interaction.guild.get_member(user_id)
                if member_obj:
                    embed.add_field(
                        name=member_obj.name,
                        value=f"{data['count']}/{BAD_WORD_THRESHOLD}",
                        inline=True
                    )
            
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("No warnings", ephemeral=True)

@bot.tree.command(name="clearwarnings", description="[ADMIN] Clear warnings for a user")
@app_commands.describe(member="User to clear warnings for")
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    if member.id in bad_word_warnings:
        bad_word_warnings[member.id]["count"] = 0
        bad_word_warnings[member.id]["messages"] = []
        await interaction.response.send_message(f"✅ Cleared for {member.mention}", ephemeral=True)
    else:
        await interaction.response.send_message(f"{member.mention} has no warnings", ephemeral=True)

@bot.tree.command(name="setcompetition", description="[ADMIN] Set Kaggle competition ID and notify participants")
@app_commands.describe(competition_id="Kaggle competition ID (e.g., titanic)")
async def slash_setcompetition(interaction: discord.Interaction, competition_id: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    global active_competition, competition_end_time
    
    await interaction.response.defer()  # This might take time
    
    try:
        # Set competition (simplified - just verify it's accessible)
        active_competition = competition_id
        
        # Try to get competition info for deadline (optional)
        deadline_str = "Check Kaggle for deadline"
        try:
            # This is optional - if it fails, we still set the competition
            comp_list = kaggle_api.competitions_list(search=competition_id)
            if comp_list:
                for comp in comp_list:
                    if comp.ref == competition_id:
                        deadline = comp.deadline
                        if deadline:
                            deadline_str = deadline.strftime("%B %d, %Y at %I:%M %p UTC")
                            competition_end_time = deadline
                        break
        except:
            pass  # If we can't get deadline, that's okay
        
        # Get competition deadline if available (kept for compatibility)
        deadline = deadline_str
        # Get competition deadline if available (kept for compatibility)
        deadline = deadline_str
        if deadline and deadline != "Check Kaggle for deadline":
            # Already formatted above
            pass
        else:
            deadline_str = "Check Kaggle for deadline"
            competition_end_time = None
        
        # Send DMs to all registered participants
        participants_notified = 0
        participants_failed = 0
        
        if contest_participants:
            for user_id, user_data in contest_participants.items():
                try:
                    member = interaction.guild.get_member(user_id)
                    if member:
                        # Create beautiful DM with competition info
                        dm_embed = discord.Embed(
                            title="🏆 New Competition Announced!",
                            description=f"A new Kaggle competition has been set for our contest!",
                            color=0x00ff00,
                            timestamp=datetime.now()
                        )
                        dm_embed.add_field(
                            name="📌 Competition",
                            value=f"**{competition_id}**",
                            inline=False
                        )
                        dm_embed.add_field(
                            name="🔗 Competition Link",
                            value=f"[Click here to join!](https://www.kaggle.com/c/{competition_id})",
                            inline=False
                        )
                        dm_embed.add_field(
                            name="⏰ Deadline",
                            value=deadline_str,
                            inline=False
                        )
                        dm_embed.add_field(
                            name="📝 Your Kaggle ID",
                            value=f"**{user_data.get('kaggle_id', 'Not set')}**",
                            inline=False
                        )
                        dm_embed.add_field(
                            name="🎯 Next Steps",
                            value="1. Join the competition on Kaggle\n"
                                  "2. Make your submissions\n"
                                  "3. Check <#" + str(LEADERBOARD_CHANNEL_ID) + "> after contest ends for rankings!" if LEADERBOARD_CHANNEL_ID else "3. Check the #leaderboard channel for rankings!",
                            inline=False
                        )
                        dm_embed.set_footer(
                            text="AI Olympiad Community",
                            icon_url="https://www.kaggle.com/static/images/site-logo.png"
                        )
                        
                        await member.send(embed=dm_embed)
                        participants_notified += 1
                        print(f"Notified {member.name} about competition")
                except Exception as e:
                    participants_failed += 1
                    print(f"Failed to notify user {user_id}: {e}")
        
        # Send confirmation to admin
        embed = discord.Embed(
            title="✅ Competition Set Successfully!",
            description=f"**Competition ID:** `{competition_id}`",
            color=0x00ff00,
            timestamp=datetime.now()
        )
        embed.add_field(name="🔗 URL", value=f"https://www.kaggle.com/c/{competition_id}", inline=False)
        embed.add_field(name="⏰ Deadline", value=deadline_str, inline=False)
        
        if contest_participants:
            embed.add_field(
                name="📨 Notifications Sent",
                value=f"✅ Notified: **{participants_notified}** participants\n"
                      f"❌ Failed: **{participants_failed}** participants",
                inline=False
            )
        else:
            embed.add_field(
                name="⚠️ No Participants",
                value="No registered participants to notify.\nUse `/createcontest` to gather participants first!",
                inline=False
            )
        
        embed.add_field(name="📊 Next Step", value="Use `/leaderboard` to fetch live scores", inline=False)
        embed.set_footer(text="Competition tracking active")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(
            f"❌ **Error**: Could not set competition!\n"
            f"**Details:** {str(e)}\n\n"
            f"**Possible reasons:**\n"
            f"• Competition ID is incorrect\n"
            f"• Competition doesn't exist on Kaggle\n"
            f"• Kaggle API credentials are invalid",
            ephemeral=True
        )
        print(f"Error setting competition: {e}")

@bot.tree.command(name="leaderboard", description="[ADMIN] Show live Kaggle leaderboard")
async def slash_leaderboard(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return
    
    if not active_competition:
        await interaction.response.send_message(
            "❌ No active competition set!\nUse `/setcompetition <id>` first.",
            ephemeral=True
        )
        return
    
    await interaction.response.defer()  # This can take time
    
    try:
        # Download leaderboard to a temporary directory
        import tempfile
        import csv
        import zipfile
        
        # Create temp directory
        tmp_dir = tempfile.mkdtemp()
        
        # Download leaderboard (it downloads as a zip file)
        kaggle_api.competition_leaderboard_download(active_competition, tmp_dir)
        
        # Find the CSV file in the zip
        import os
        zip_path = os.path.join(tmp_dir, f"{active_competition}.zip")
        
        participant_scores = []
        
        # Extract and read the CSV from the zip
        leaderboard = []
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # List all CSV files in the zip
            csv_files = [f for f in zip_ref.namelist() if f.endswith('.csv')]
            
            print(f"DEBUG: Found CSV files in zip: {csv_files}")
            
            if csv_files:
                # Look for private leaderboard first, then public
                private_file = next((f for f in csv_files if 'private' in f.lower()), None)
                public_file = next((f for f in csv_files if 'public' in f.lower()), None)
                
                # Use the first available file (prefer private if exists)
                csv_to_read = private_file or public_file or csv_files[0]
                
                print(f"DEBUG: Reading leaderboard from: {csv_to_read}")
                
                with zip_ref.open(csv_to_read) as csv_file:
                    import io
                    csv_content = io.TextIOWrapper(csv_file, encoding='utf-8')
                    csv_reader = csv.DictReader(csv_content)
                    leaderboard = list(csv_reader)
        
        # Clean up temp files
        import shutil
        shutil.rmtree(tmp_dir)
        
        # Debug: Print CSV columns and first entries
        if leaderboard:
            print(f"DEBUG: CSV Columns: {list(leaderboard[0].keys())}")
            print(f"DEBUG: First 3 entries:")
            for i, entry in enumerate(leaderboard[:3]):
                print(f"  {i+1}. {entry}")
        
        print(f"DEBUG: Looking for participants: {[(uid, data.get('kaggle_id')) for uid, data in contest_participants.items()]}")
        
        # Filter participants who are registered
        for user_id, user_data in contest_participants.items():
            kaggle_id = user_data.get("kaggle_id", "").lower()
            
            # Search for this user in leaderboard by checking ALL columns
            for entry in leaderboard:
                # Check all fields for a match
                found = False
                for key, value in entry.items():
                    if kaggle_id in str(value).lower():
                        # Get all rank-related columns
                        public_rank = 'N/A'
                        private_rank = 'N/A'
                        
                        for rank_key in entry.keys():
                            key_lower = rank_key.lower()
                            if 'public' in key_lower and 'rank' in key_lower:
                                public_rank = entry[rank_key]
                            elif 'private' in key_lower and 'rank' in key_lower:
                                private_rank = entry[rank_key]
                            elif key_lower == 'rank' or 'rank' in key_lower:
                                # Generic rank column (usually public)
                                if public_rank == 'N/A':
                                    public_rank = entry[rank_key]
                        
                        # Get Kaggle username from CSV
                        kaggle_username = entry.get('TeamMemberUserNames', kaggle_id)
                        
                        participant_scores.append({
                            "name": user_data.get("name", "Unknown"),
                            "kaggle_id": entry.get('TeamName', kaggle_id),
                            "kaggle_username": kaggle_username,
                            "score": float(entry.get('Score', 0)),
                            "public_rank": public_rank,
                            "private_rank": private_rank,
                            "user_id": user_id  # Store user_id for role assignment
                        })
                        print(f"DEBUG: Found match! '{kaggle_id}' found in {key}='{value}' (Public: {public_rank}, Private: {private_rank})")
                        found = True
                        break
                if found:
                    break
        
        if not participant_scores:
            # Show helpful debug info
            registered_ids = [data.get("kaggle_id") for data in contest_participants.values()]
            await interaction.followup.send(
                f"📊 No registered participants found on the leaderboard yet!\n\n"
                f"**Registered Kaggle IDs:** {', '.join(registered_ids)}\n"
                f"**Hint:** Make sure participants have submitted to the competition and their Kaggle username matches exactly.",
                ephemeral=True
            )
            return
        
        # Sort by public rank (lower is better)
        participant_scores.sort(key=lambda x: int(x["public_rank"]) if str(x["public_rank"]).isdigit() else 999999)
        
        embed = discord.Embed(
            title=f"🏆 Contest Leaderboard",
            description=f"**Competition:** {active_competition}",
            color=0xffd700,
            timestamp=datetime.now()
        )
        
        # Add competition info at the top
        embed.add_field(
            name="📌 Competition Info",
            value=f"🔗 [View on Kaggle](https://www.kaggle.com/c/{active_competition})\n"
                  f"👥 **{len(participant_scores)}** registered participants",
            inline=False
        )
        embed.add_field(name="━━━━━━━━━━━━━━━━━━━━", value="", inline=False)  # Separator
        
        # Show top 10 in detail, rest as summary
        top_count = min(10, len(participant_scores))
        
        for i, player in enumerate(participant_scores[:top_count], 1):
            # Fancy medals and emojis
            if i == 1:
                medal = "🥇"
                rank_emoji = "👑"
            elif i == 2:
                medal = "🥈"
                rank_emoji = "⭐"
            elif i == 3:
                medal = "🥉"
                rank_emoji = "✨"
            else:
                medal = f"**{i}.**"
                rank_emoji = "📊"
            
            # Build rank display with emojis
            rank_display = ""
            if player['public_rank'] != 'N/A':
                rank_display += f"🎯 Rank: **#{player['public_rank']}**"
            if player['private_rank'] != 'N/A':
                if rank_display:
                    rank_display += f"\n🔒 Private: **#{player['private_rank']}**"
                else:
                    rank_display += f"🔒 Private: **#{player['private_rank']}**"
            if not rank_display:
                rank_display = "🎯 Rank: **N/A**"
            
            # Kaggle profile link
            kaggle_username = player.get('kaggle_username', '')
            profile_link = f"\n👤 [{player['kaggle_id']}](https://www.kaggle.com/{kaggle_username})" if kaggle_username else f"\n👤 {player['kaggle_id']}"
            
            embed.add_field(
                name=f"{medal} {rank_emoji} {player['name']}",
                value=f"💯 Score: **{player['score']:.5f}**\n{rank_display}{profile_link}",
                inline=False
            )
        
        # If more than 10 participants, show remaining as compact list
        if len(participant_scores) > 10:
            embed.add_field(name="━━━━━━━━━━━━━━━━━━━━", value="", inline=False)
            
            remaining_text = "**📋 Other Participants:**\n"
            for i, player in enumerate(participant_scores[10:20], 11):  # Show next 10
                rank = player['public_rank'] if player['public_rank'] != 'N/A' else '?'
                remaining_text += f"`{i}.` {player['name']} - Rank #{rank} ({player['score']:.3f})\n"
            
            if len(participant_scores) > 20:
                remaining_text += f"\n*...and {len(participant_scores) - 20} more participants*"
            
            embed.add_field(name="", value=remaining_text, inline=False)
        
        # Add footer with timestamp
        embed.set_footer(
            text=f"🏆 Total Participants: {len(participant_scores)} • Updated",
            icon_url="https://www.kaggle.com/static/images/site-logo.png"
        )
        
        # Set thumbnail (Kaggle logo or trophy)
        embed.set_thumbnail(url="https://www.kaggle.com/static/images/site-logo.png")
        
        # Assign 🏆 Contest Winner role to top 3
        if participant_scores:
            winner_role = discord.utils.get(interaction.guild.roles, name="🏆 Contest Winner")
            
            # Create role if it doesn't exist
            if not winner_role:
                try:
                    winner_role = await interaction.guild.create_role(
                        name="🏆 Contest Winner",
                        color=discord.Color.gold(),
                        reason="Auto-created for contest winners"
                    )
                    print(f"Created '🏆 Contest Winner' role")
                except Exception as e:
                    print(f"Error creating winner role: {e}")
            
            # Assign to top 3 participants
            if winner_role:
                for i, player in enumerate(participant_scores[:3]):
                    try:
                        member = interaction.guild.get_member(player['user_id'])
                        if member and winner_role not in member.roles:
                            await member.add_roles(winner_role, reason=f"Top {i+1} in contest")
                            print(f"Assigned winner role to {player['name']}")
                    except Exception as e:
                        print(f"Error assigning role to {player['name']}: {e}")
        
        # Send to leaderboard channel if set
        if LEADERBOARD_CHANNEL_ID:
            leaderboard_channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
            if leaderboard_channel:
                await leaderboard_channel.send(embed=embed)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(
            f"❌ Error fetching leaderboard: {str(e)}\n"
            f"Make sure the competition ID is correct and participants have submitted.",
            ephemeral=True
        )
        print(f"Leaderboard error: {e}")

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
