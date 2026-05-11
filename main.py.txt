import discord, os, sqlite3, datetime
from discord.ext import commands
from discord import app_commands

# ---------------- CONFIG ----------------
CONFIG = {
    "log_channel": 123456789012345678,  # REPLACE
    "staff_role": "Staff",
    "reaction_role_channel": 123456789012345678,  # REPLACE
    "warn_limit": 3
}

# ---------------- SETUP ----------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ----------------
conn = sqlite3.connect("database.db")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS warns (user_id INTEGER, reason TEXT)")
conn.commit()

# ---------------- LOG FUNCTION ----------------
async def send_log(guild, embed):
    ch = guild.get_channel(CONFIG["log_channel"])
    if ch:
        await ch.send(embed=embed)

# ---------------- READY ----------------
@bot.event
async def on_ready():
    print(f"Online: {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(e)

# ---------------- ERROR HANDLER ----------------
@bot.tree.error
async def on_app_command_error(interaction, error):
    await interaction.response.send_message(
        "❌ Error: Missing permissions or invalid input.", ephemeral=True
    )

# ---------------- AUDIT LOGGING ----------------
@bot.event
async def on_message_delete(msg):
    if msg.author.bot:
        return

    embed = discord.Embed(title="🗑 Message Deleted", color=discord.Color.red())
    embed.add_field(name="User", value=msg.author)
    embed.add_field(name="Channel", value=msg.channel)
    embed.add_field(name="Content", value=msg.content or "None", inline=False)
    embed.timestamp = discord.utils.utcnow()

    await send_log(msg.guild, embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content:
        return

    embed = discord.Embed(title="✏️ Message Edited", color=discord.Color.orange())
    embed.add_field(name="User", value=before.author)
    embed.add_field(name="Before", value=before.content or "None", inline=False)
    embed.add_field(name="After", value=after.content or "None", inline=False)
    embed.timestamp = discord.utils.utcnow()

    await send_log(before.guild, embed)

@bot.event
async def on_member_update(before, after):
    if before.nick != after.nick:
        embed = discord.Embed(title="🔄 Nickname Change", color=discord.Color.blue())
        embed.add_field(name="User", value=after)
        embed.add_field(name="Before", value=before.nick or "None")
        embed.add_field(name="After", value=after.nick or "None")
        embed.timestamp = discord.utils.utcnow()

        await send_log(after.guild, embed)

# ---------------- WARN SYSTEM ----------------
@bot.tree.command(name="warn", description="Warn a user")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    cursor.execute("INSERT INTO warns VALUES (?, ?)", (member.id, reason))
    conn.commit()

    cursor.execute("SELECT * FROM warns WHERE user_id=?", (member.id,))
    warns = cursor.fetchall()

    await interaction.response.send_message("User warned.", ephemeral=True)

    embed = discord.Embed(title="⚠️ Warn Issued", color=discord.Color.orange())
    embed.add_field(name="User", value=member)
    embed.add_field(name="Reason", value=reason)
    embed.timestamp = discord.utils.utcnow()

    await send_log(interaction.guild, embed)

    if len(warns) >= CONFIG["warn_limit"]:
        await member.timeout(discord.utils.utcnow() + datetime.timedelta(minutes=10))

# ---------------- PURGE ----------------
@bot.tree.command(name="purge", description="Delete messages")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)

    deleted = await interaction.channel.purge(limit=amount)

    await interaction.followup.send(f"Deleted {len(deleted)} messages", ephemeral=True)

    embed = discord.Embed(title="🧹 Purge", color=discord.Color.red())
    embed.add_field(name="Amount", value=len(deleted))
    embed.timestamp = discord.utils.utcnow()

    await send_log(interaction.guild, embed)

# ---------------- LOCKDOWN ----------------
@bot.tree.command(name="lockdown", description="Lock all channels")
@app_commands.checks.has_permissions(administrator=True)
async def lockdown(interaction: discord.Interaction):
    for ch in interaction.guild.text_channels:
        overwrite = ch.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)

    await interaction.response.send_message("🔒 Server locked.", ephemeral=True)

# ---------------- UNLOCKDOWN ----------------
@bot.tree.command(name="unlockdown", description="Unlock all channels")
@app_commands.checks.has_permissions(administrator=True)
async def unlockdown(interaction: discord.Interaction):
    for ch in interaction.guild.text_channels:
        overwrite = ch.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = True
        await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)

    await interaction.response.send_message("🔓 Server unlocked.", ephemeral=True)

# ---------------- TICKET SYSTEM ----------------
class TicketView(discord.ui.View):
    @discord.ui.select(
        placeholder="Select Ticket Type",
        options=[
            discord.SelectOption(label="Support"),
            discord.SelectOption(label="Report"),
            discord.SelectOption(label="Staff Help")
        ]
    )
    async def select_callback(self, interaction, select):
        staff = discord.utils.get(interaction.guild.roles, name=CONFIG["staff_role"])

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True),
            staff: discord.PermissionOverwrite(view_channel=True)
        }

        channel = await interaction.guild.create_text_channel(
            f"ticket-{interaction.user.name}",
            overwrites=overwrites
        )

        await channel.send("🎫 Ticket created.", view=TicketControls())

class TicketControls(discord.ui.View):
    @discord.ui.button(label="Close", style=discord.ButtonStyle.red)
    async def close(self, interaction, button):
        messages = [m async for m in interaction.channel.history(limit=200)]

        with open("transcript.html", "w", encoding="utf-8") as f:
            for m in messages:
                f.write(f"<p>{m.author}: {m.content}</p>")

        await interaction.channel.send(file=discord.File("transcript.html"))
        await interaction.channel.delete()

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.blurple)
    async def claim(self, interaction, button):
        await interaction.response.send_message(
            f"{interaction.user.mention} claimed this ticket."
        )

@bot.tree.command(name="ticketpanel", description="Send ticket panel")
async def ticketpanel(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Click below to open a ticket:", view=TicketView()
    )

# ---------------- REACTION ROLES ----------------
@bot.event
async def on_raw_reaction_add(payload):
    if payload.channel_id != CONFIG["reaction_role_channel"]:
        return

    guild = bot.get_guild(payload.guild_id)
    role = discord.utils.get(guild.roles, name=str(payload.emoji))

    if role:
        member = guild.get_member(payload.user_id)
        await member.add_roles(role)

# ---------------- AUTOMOD ----------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.count("!") > 10:
        await message.delete()

    if "http://" in message.content or "https://" in message.content:
        await message.delete()

    if message.content.isupper() and len(message.content) > 10:
        await message.delete()

    await bot.process_commands(message)

# ---------------- RUN ----------------
bot.run(os.getenv("TOKEN"))
TOKEN = "MTUwMTAyODU2NDA0MzE3NDA2MQ.GdeSh3.PWsVsDqduncqJ1BjBgNTVKu_WmZTJVOi1iDN4A"
bot.run(TOKEN)