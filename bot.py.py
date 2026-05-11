import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Bot setup
bot = commands.Bot(command_prefix="!", intents=intents)

# Ready event
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    synced = []

    try:
        guild_id = os.getenv("GUILD_ID")

        # Guild sync
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} guild commands")

        # Global sync
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} global commands")

    except Exception as e:
        print(f"Command sync failed: {e}")

# Simple ping command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

# Example slash command
@bot.tree.command(name="hello", description="Say hello")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message("Hello!")

# Start bot
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise ValueError("TOKEN environment variable is missing")

bot.run(TOKEN)