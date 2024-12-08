import discord
from discord.ext import commands
from discord import Embed
import asyncio
import sys
import signal

# Bot setup
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Constants
COMMANDS_CHANNEL = "customs_bot_commands"
STATUS_CHANNEL = "lobby_status"
CATEGORY_NAME = "Lobbies"
MAX_LOBBIES = 5
MAX_PLAYERS = 8

# Override the default help command
bot.remove_command("help")

# Custom help command
@bot.command()
async def help(ctx):
    """Custom help command restricted to `customs_bot_commands`."""
    if not await ensure_commands_channel(ctx):
        return
    embed = Embed(title="Custom Bot Commands", color=discord.Color.blue())
    embed.add_field(name="`!open_lobby`", value="Opens a new lobby.", inline=False)
    embed.add_field(name="`!close_lobby <lobby_id>`", value="Closes a specific lobby.", inline=False)
    embed.add_field(name="`!sign_up <lobby_id>`", value="Signs up for a specific lobby.", inline=False)
    embed.add_field(name="`!sign_out`", value="Signs out from your current lobby or queue.", inline=False)
    embed.add_field(name="`!list_lobbies`", value="Lists all active lobbies.", inline=False)
    await ctx.send(embed=embed)

# Lobby object
class Lobby:
    def __init__(self, id, guild, category):
        self.id = id
        self.players = []
        self.queue = []
        self.message = None
        self.voice_channel = None
        self.category = category
        self.guild = guild
        self.open = True

    async def create_voice_channel(self):
        self.voice_channel = await self.guild.create_voice_channel(f"Lobby {self.id}", category=self.category)

    async def update_status_message(self, channel):
        embed = Embed(
            title=f"Lobby {self.id}",
            color=discord.Color.green() if self.open else discord.Color.red(),
            description="Status of the lobby."
        )
        embed.add_field(name="Status", value="Open" if self.open else "Closed", inline=False)
        embed.add_field(name="Players", value=", ".join(self.players) or "None", inline=False)
        embed.add_field(name="Queue", value=", ".join(self.queue) or "Empty", inline=False)
        
        if self.message:
            await self.message.edit(embed=embed)
        else:
            self.message = await channel.send(embed=embed)

    async def close(self, channel):
        if self.voice_channel:
            await self.voice_channel.delete()
        if self.message:
            await self.message.delete()
            self.message = None
        self.open = False

# Global Variables
lobbies = {}
user_status = {}

# Utility Functions
async def get_channel_by_name(guild, name, channel_type):
    channel = discord.utils.get(guild.text_channels if channel_type == "text" else guild.categories, name=name)
    return channel or (await guild.create_text_channel(name) if channel_type == "text" else await guild.create_category(name))

async def cleanup():
    """Deletes all bot-related messages and voice channels, then closes the bot."""
    print("Starting cleanup...")
    for guild in bot.guilds:
        print(f"Cleaning up guild: {guild.name} ({guild.id})")
        
        # Clean up the lobby_status channel
        status_channel = await get_channel_by_name(guild, STATUS_CHANNEL, "text")
        if status_channel:
            print(f"Found status channel: {status_channel.name} ({status_channel.id})")
            try:
                async for message in status_channel.history(limit=None):
                    print(f"Deleting message: {message.id}")
                    await message.delete()
                await status_channel.delete()
                print(f"Deleted status channel: {status_channel.name}")
            except Exception as e:
                print(f"Error deleting status channel: {e}")

        # Clean up the Lobbies category and its channels
        category = await get_channel_by_name(guild, CATEGORY_NAME, "category")
        if category:
            print(f"Found category: {category.name} ({category.id})")
            try:
                for channel in category.channels:
                    print(f"Deleting channel: {channel.name} ({channel.id})")
                    await channel.delete()
                await category.delete()
                print(f"Deleted category: {category.name}")
            except Exception as e:
                print(f"Error deleting category or channels: {e}")
    print("Cleanup complete.")
    await bot.close()  # Gracefully close the bot after cleanup

def shutdown_handler(signal_received, frame):
    """Handles shutdown signals and ensures cleanup."""
    print(f"Received signal {signal_received}, shutting down...")
    loop = asyncio.get_event_loop()
    cleanup_task = loop.create_task(cleanup())  # Schedule cleanup coroutine
    cleanup_task.add_done_callback(lambda t: loop.stop())  # Stop the loop when cleanup is done

# Register shutdown handler for signals
signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

async def ensure_commands_channel(ctx):
    if ctx.channel.name != COMMANDS_CHANNEL:
        await ctx.send(f"Commands must be used in the `{COMMANDS_CHANNEL}` channel.")
        return False
    return True

# Commands
@bot.command()
async def open_lobby(ctx):
    """Opens a new lobby."""
    if not await ensure_commands_channel(ctx):
        return
    if len(lobbies) >= MAX_LOBBIES:
        await ctx.send("Maximum number of lobbies reached!")
        return

    guild = ctx.guild
    status_channel = await get_channel_by_name(guild, STATUS_CHANNEL, "text")
    category = await get_channel_by_name(guild, CATEGORY_NAME, "category")

    lobby_id = next(i for i in range(1, MAX_LOBBIES + 1) if i not in lobbies)
    lobby = Lobby(lobby_id, guild, category)
    lobbies[lobby_id] = lobby
    await lobby.create_voice_channel()
    await lobby.update_status_message(status_channel)
    await ctx.send(f"Lobby {lobby_id} created!")

@bot.command()
async def close_lobby(ctx, lobby_id: int):
    """Closes an existing lobby."""
    if not await ensure_commands_channel(ctx):
        return
    if lobby_id not in lobbies:
        await ctx.send(f"Lobby {lobby_id} does not exist!")
        return

    guild = ctx.guild
    status_channel = await get_channel_by_name(guild, STATUS_CHANNEL, "text")
    lobby = lobbies.pop(lobby_id)
    await lobby.close(status_channel)

    # Allow all users from this lobby to sign up for new lobbies
    for user in lobby.players + lobby.queue:
        user_status.pop(user, None)

    await ctx.send(f"Lobby {lobby_id} closed!")

@bot.command()
async def sign_up(ctx, lobby_id: int):
    """Signs up for a lobby."""
    if not await ensure_commands_channel(ctx):
        return
    user = ctx.author.name
    if user in user_status:
        await ctx.send(f"{user}, you are already signed up for a lobby or queue!")
        return
    if lobby_id not in lobbies:
        await ctx.send(f"Lobby {lobby_id} does not exist!")
        return

    lobby = lobbies[lobby_id]
    if len(lobby.players) < MAX_PLAYERS:
        lobby.players.append(user)
        user_status[user] = lobby_id
        await ctx.send(f"{user} joined Lobby {lobby_id}!")
    else:
        lobby.queue.append(user)
        user_status[user] = lobby_id
        await ctx.send(f"Lobby {lobby_id} is full. {user} added to the queue.")

    await lobby.update_status_message(await get_channel_by_name(ctx.guild, STATUS_CHANNEL, "text"))

@bot.command()
async def sign_out(ctx):
    """Signs out from a lobby or queue."""
    if not await ensure_commands_channel(ctx):
        return
    user = ctx.author.name
    if user not in user_status:
        await ctx.send(f"{user}, you are not in any lobby or queue!")
        return

    lobby_id = user_status.pop(user)
    lobby = lobbies[lobby_id]

    if user in lobby.players:
        lobby.players.remove(user)
        if lobby.queue:
            next_user = lobby.queue.pop(0)
            lobby.players.append(next_user)
            user_status[next_user] = lobby_id
            await ctx.send(f"{next_user} moved from queue to Lobby {lobby_id}!")
    elif user in lobby.queue:
        lobby.queue.remove(user)

    await ctx.send(f"{user} left Lobby {lobby_id}.")
    await lobby.update_status_message(await get_channel_by_name(ctx.guild, STATUS_CHANNEL, "text"))

@bot.command()
async def list_lobbies(ctx):
    """Lists all active lobbies."""
    if not await ensure_commands_channel(ctx):
        return
    if not lobbies:
        await ctx.send("No active lobbies.")
        return

    for lobby_id, lobby in lobbies.items():
        await ctx.send(f"Lobby {lobby_id}: Players: {len(lobby.players)}, Queue: {len(lobby.queue)}")

# Run Bot
def get_token():
    try:
        with open("bot_token.txt", "r") as file:
            return file.read().strip()
    except FileNotFoundError:
        print("Bot token file not found!")
        sys.exit(1)

bot.run(get_token())
