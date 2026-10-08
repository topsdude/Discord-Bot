import discord
import os
import sys
import sqlite3
import datetime
import time
import random
import re
from discord.ext import commands
from discord import app_commands
#god knows how this works :) 

# ================================================================
#  CONFIG
# ================================================================
GUILD_ID = int(os.getenv("GUILD_ID", "1496298492312686644"))
GUILD    = discord.Object(id=GUILD_ID)

intents = discord.Intents.all()
bot     = commands.Bot(command_prefix="!", intents=intents)
bot_start_time = time.time()

# ================================================================
#  DATABASE
# ================================================================
conn   = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.executescript("""
    CREATE TABLE IF NOT EXISTS warns (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    INTEGER,
        user_id     INTEGER,
        mod_id      INTEGER,
        reason      TEXT,
        timestamp   INTEGER
    );
    CREATE TABLE IF NOT EXISTS economy (
        guild_id    INTEGER,
        user_id     INTEGER,
        balance     INTEGER DEFAULT 0,
        last_daily  INTEGER DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id    INTEGER,
        key         TEXT,
        value       TEXT,
        PRIMARY KEY (guild_id, key)
    );
    CREATE TABLE IF NOT EXISTS word_filter (
        guild_id    INTEGER,
        word        TEXT,
        PRIMARY KEY (guild_id, word)
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id    INTEGER,
        target_id   INTEGER,
        mod_id      INTEGER,
        action      TEXT,
        reason      TEXT,
        timestamp   INTEGER
    );
    CREATE TABLE IF NOT EXISTS user_activity (
        guild_id    INTEGER,
        user_id     INTEGER,
        last_seen   INTEGER DEFAULT 0,
        last_action TEXT DEFAULT 'Joined server',
        PRIMARY KEY (guild_id, user_id)
    );
""")
conn.commit()

# ================================================================
#  CONFIG HELPERS
# ================================================================
def get_cfg(guild_id: int, key: str, default=None):
    cursor.execute("SELECT value FROM guild_config WHERE guild_id=? AND key=?", (guild_id, key))
    row = cursor.fetchone()
    return row[0] if row else default

def set_cfg(guild_id: int, key: str, value: str):
    cursor.execute("INSERT OR REPLACE INTO guild_config (guild_id, key, value) VALUES (?,?,?)",
                   (guild_id, key, value))
    conn.commit()

def log_channel(guild: discord.Guild):
    ch_id = get_cfg(guild.id, "log_channel",
                    os.getenv("LOG_CHANNEL_ID", "0"))
    return guild.get_channel(int(ch_id))

def staff_roles(guild: discord.Guild):
    names = get_cfg(guild.id, "staff_roles",
                    os.getenv("STAFF_ROLE_NAMES", "Moderator,Admin"))
    return [r for name in names.split(",")
            if (r := discord.utils.get(guild.roles, name=name.strip()))]

WARN_LIMIT = 3
DAILY_AMOUNT = 100

# ================================================================
#  HELPERS
# ================================================================
async def send_log(guild: discord.Guild, embed: discord.Embed, file: discord.File = None):
    ch = log_channel(guild)
    if ch:
        try:
            await ch.send(embed=embed, file=file)
        except discord.Forbidden:
            pass

def build_transcript(channel_name: str, messages: list) -> str:
    """Build a plain-text transcript from a list of messages and save to file."""
    path = f"transcript-{channel_name}.txt"
    divider = "=" * 60
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{divider}\n")
        f.write(f"  TICKET TRANSCRIPT — #{channel_name}\n")
        f.write(f"  Generated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")
        f.write(f"  Total messages: {len(messages)}\n")
        f.write(f"{divider}\n\n")

        for m in messages:
            ts = m.created_at.strftime("%Y-%m-%d %H:%M UTC")
            author = str(m.author)
            # Mark bot actions differently
            prefix = "[BOT]" if m.author.bot else "[MSG]"
            f.write(f"{prefix} [{ts}] {author}\n")
            if m.content:
                f.write(f"      {m.content}\n")
            # Log attachments
            for attachment in m.attachments:
                f.write(f"      [Attachment: {attachment.filename} — {attachment.url}]\n")
            # Log embeds
            for embed in m.embeds:
                title = embed.title or "Embed"
                f.write(f"      [Embed: {title}]\n")
            f.write("\n")

        f.write(f"{divider}\n")
        f.write("  END OF TRANSCRIPT\n")
        f.write(f"{divider}\n")
    return path

def track_activity(guild_id: int, user_id: int, action: str):
    """Upsert the user's last seen timestamp and action."""
    cursor.execute(
        "INSERT INTO user_activity (guild_id, user_id, last_seen, last_action) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET last_seen=excluded.last_seen, last_action=excluded.last_action",
        (guild_id, user_id, int(time.time()), action)
    )
    conn.commit()

def add_audit(guild_id, target_id, mod_id, action, reason=""):
    cursor.execute(
        "INSERT INTO audit_log (guild_id, target_id, mod_id, action, reason, timestamp) VALUES (?,?,?,?,?,?)",
        (guild_id, target_id, mod_id, action, reason, int(time.time()))
    )
    conn.commit()

def mod_embed(title, color, **fields):
    e = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    for k, v in fields.items():
        e.add_field(name=k, value=str(v), inline=True)
    return e

def can_action(interaction: discord.Interaction, member: discord.Member):
    if member == interaction.guild.owner:
        return False, "I can't action the server owner."
    if member.top_role >= interaction.guild.me.top_role:
        return False, "That member's role is higher than or equal to mine."
    if member == interaction.user:
        return False, "You can't action yourself."
    return True, None

# ================================================================
#  READY
# ================================================================
@bot.event
async def on_ready():
    print(f"Online: {bot.user}")
    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        synced = await bot.tree.sync(guild=GUILD)
        print(f"Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        print(f"Sync error: {e}")

    # Send startup notification to log channel in all guilds
    for guild in bot.guilds:
        e = discord.Embed(
            title="✅ Bot Online",
            description=f"**{bot.user}** has started successfully.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        e.add_field(name="Guilds",   value=len(bot.guilds))
        e.add_field(name="Commands", value=len(synced))
        e.add_field(name="Latency",  value=f"{round(bot.latency * 1000)}ms")
        await send_log(guild, e)

# ================================================================
#  ERROR HANDLER
# ================================================================
@bot.tree.error
async def on_error(interaction: discord.Interaction, error):
    msg = "An error occurred."
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "I'm missing the permissions to do that."
    if not interaction.response.is_done():
        await interaction.response.send_message(msg, ephemeral=True)
    else:
        await interaction.followup.send(msg, ephemeral=True)

# ================================================================
#  AUDIT / EVENT LOGGING
# ================================================================
@bot.event
async def on_message_delete(msg):
    if msg.author.bot or not msg.guild:
        return
    e = mod_embed("🗑️ Message Deleted", discord.Color.red(),
                  User=msg.author.mention, Channel=msg.channel.mention)
    e.add_field(name="Content", value=msg.content[:1024] or "None", inline=False)
    await send_log(msg.guild, e)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content or not before.guild:
        return
    e = mod_embed("✏️ Message Edited", discord.Color.orange(),
                  User=before.author.mention, Channel=before.channel.mention)
    e.add_field(name="Before", value=before.content[:512] or "None", inline=False)
    e.add_field(name="After",  value=after.content[:512]  or "None", inline=False)
    await send_log(before.guild, e)

@bot.event
async def on_member_join(member):
    track_activity(member.guild.id, member.id, "Joined the server")
    e = mod_embed("📥 Member Joined", discord.Color.green(),
                  User=member.mention,
                  **{"Account Age": member.created_at.strftime("%Y-%m-%d")})
    await send_log(member.guild, e)

@bot.event
async def on_member_remove(member):
    e = mod_embed("📤 Member Left", discord.Color.red(),
                  User=str(member))
    await send_log(member.guild, e)

@bot.event
async def on_member_update(before, after):
    if before.nick != after.nick:
        e = mod_embed("🔄 Nickname Changed", discord.Color.blue(),
                      User=after.mention,
                      Before=before.nick or "None",
                      After=after.nick or "None")
        await send_log(after.guild, e)

    added   = [r for r in after.roles if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]
    if added or removed:
        e = mod_embed("🎭 Roles Updated", discord.Color.purple(),
                      User=after.mention)
        if added:
            e.add_field(name="Added",   value=" ".join(r.mention for r in added),   inline=False)
        if removed:
            e.add_field(name="Removed", value=" ".join(r.mention for r in removed), inline=False)
        await send_log(after.guild, e)

@bot.event
async def on_guild_channel_create(channel):
    e = mod_embed("📢 Channel Created", discord.Color.green(),
                  Name=channel.name, Type=str(channel.type))
    await send_log(channel.guild, e)

@bot.event
async def on_guild_channel_delete(channel):
    e = mod_embed("🗑️ Channel Deleted", discord.Color.red(),
                  Name=channel.name, Type=str(channel.type))
    await send_log(channel.guild, e)

# ================================================================
#  AUTOMOD
# ================================================================
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if before.channel is None and after.channel is not None:
        track_activity(member.guild.id, member.id, f"Joined VC: {after.channel.name}")
    elif before.channel is not None and after.channel is None:
        track_activity(member.guild.id, member.id, f"Left VC: {before.channel.name}")
    elif before.channel != after.channel:
        track_activity(member.guild.id, member.id, f"Moved VC → {after.channel.name}")

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot or not reaction.message.guild:
        return
    track_activity(reaction.message.guild.id, user.id,
                   f"Reacted {reaction.emoji} in #{reaction.message.channel.name}")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    # Track activity on every message
    track_activity(message.guild.id, message.author.id,
                   f"Sent message in #{message.channel.name}")

    content = message.content

    # Word filter
    cursor.execute("SELECT word FROM word_filter WHERE guild_id=?", (message.guild.id,))
    banned = [r[0].lower() for r in cursor.fetchall()]
    if any(w in content.lower() for w in banned):
        await message.delete()
        await message.channel.send(f"{message.author.mention} Watch your language!", delete_after=5)
        await bot.process_commands(message)
        return

    # Anti-spam (excessive !)
    if content.count("!") > 10:
        await message.delete()
        await bot.process_commands(message)
        return

    # Link logger — log any links for staff review (no deletion)
    if "http://" in content or "https://" in content:
        urls = re.findall(r'https?://\S+', content)
        if urls:
            e = discord.Embed(title="🔗 Link Detected", color=discord.Color.yellow(),
                              timestamp=discord.utils.utcnow())
            e.add_field(name="User",    value=message.author.mention, inline=True)
            e.add_field(name="Channel", value=message.channel.mention, inline=True)
            e.add_field(name="Link(s)", value="\n".join(urls)[:1024],  inline=False)
            e.add_field(name="Message", value=message.content[:500],   inline=False)
            e.set_footer(text=f"User ID: {message.author.id}")
            await send_log(message.guild, e)

    # Anti-caps
    if content.isupper() and len(content) > 10:
        await message.delete()
        await bot.process_commands(message)
        return

    await bot.process_commands(message)

# ================================================================
#  SETUP COMMANDS
# ================================================================
@bot.tree.command(name="setup", description="Auto-create logs channel, ticket channel, and staff roles", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    created = []

    for role_name in ["Moderator", "Admin"]:
        if not discord.utils.get(guild.roles, name=role_name):
            await guild.create_role(name=role_name, mentionable=True)
            created.append(f"Role: **{role_name}**")

    logs_ch = discord.utils.get(guild.text_channels, name="logs")
    if not logs_ch:
        logs_ch = await guild.create_text_channel("logs")
        created.append(f"Channel: {logs_ch.mention}")

    tickets_cat = discord.utils.get(guild.categories, name="Tickets")
    if not tickets_cat:
        tickets_cat = await guild.create_category("Tickets")
        created.append(f"Category: **Tickets**")

    tickets_ch = discord.utils.get(guild.text_channels, name="tickets")
    if not tickets_ch:
        tickets_ch = await guild.create_text_channel("tickets", category=tickets_cat)
        created.append(f"Channel: {tickets_ch.mention}")

    set_cfg(guild.id, "log_channel", str(logs_ch.id))
    set_cfg(guild.id, "staff_roles", "Moderator,Admin")

    summary = "\n".join(created) if created else "Everything already exists."
    await interaction.followup.send(f"✅ Setup complete!\n{summary}", ephemeral=True)

@bot.tree.command(name="setlogchannel", description="Set the channel where logs are sent", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel to use for logs")
async def setlogchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_cfg(interaction.guild.id, "log_channel", str(channel.id))
    await interaction.response.send_message(f"✅ Log channel set to {channel.mention}.", ephemeral=True)

@bot.tree.command(name="setstaffrole", description="Set the staff role name(s) (comma-separated)", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(roles="Role names separated by commas e.g. Moderator,Admin")
async def setstaffrole(interaction: discord.Interaction, roles: str):
    set_cfg(interaction.guild.id, "staff_roles", roles)
    await interaction.response.send_message(f"✅ Staff roles set to: `{roles}`", ephemeral=True)

@bot.tree.command(name="addword", description="Add a word to the automod filter", guild=GUILD)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(word="Word to ban")
async def addword(interaction: discord.Interaction, word: str):
    cursor.execute("INSERT OR IGNORE INTO word_filter (guild_id, word) VALUES (?,?)",
                   (interaction.guild.id, word.lower()))
    conn.commit()
    await interaction.response.send_message(f"✅ Added `{word}` to the word filter.", ephemeral=True)

@bot.tree.command(name="removeword", description="Remove a word from the automod filter", guild=GUILD)
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(word="Word to remove")
async def removeword(interaction: discord.Interaction, word: str):
    cursor.execute("DELETE FROM word_filter WHERE guild_id=? AND word=?",
                   (interaction.guild.id, word.lower()))
    conn.commit()
    await interaction.response.send_message(f"✅ Removed `{word}` from the word filter.", ephemeral=True)

@bot.tree.command(name="filterlist", description="Show all filtered words", guild=GUILD)
@app_commands.checks.has_permissions(manage_guild=True)
async def filterlist(interaction: discord.Interaction):
    cursor.execute("SELECT word FROM word_filter WHERE guild_id=?", (interaction.guild.id,))
    words = [r[0] for r in cursor.fetchall()]
    await interaction.response.send_message(
        f"Filtered words: `{', '.join(words)}`" if words else "No words filtered.", ephemeral=True
    )

# ================================================================
#  MODERATION
# ================================================================
@bot.tree.command(name="warn", description="Issue a warning to a member", guild=GUILD)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(member="Member to warn", reason="Reason for warning")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member == interaction.user:
        return await interaction.response.send_message("You can't warn yourself.", ephemeral=True)
    if member == interaction.guild.owner:
        return await interaction.response.send_message("You can't warn the server owner.", ephemeral=True)

    cursor.execute(
        "INSERT INTO warns (guild_id, user_id, mod_id, reason, timestamp) VALUES (?,?,?,?,?)",
        (interaction.guild.id, member.id, interaction.user.id, reason, int(time.time()))
    )
    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM warns WHERE guild_id=? AND user_id=?",
                   (interaction.guild.id, member.id))
    count = cursor.fetchone()[0]

    add_audit(interaction.guild.id, member.id, interaction.user.id, "warn", reason)
    await interaction.response.send_message(
        f"⚠️ {member.mention} warned. Total: **{count}** warning(s).", ephemeral=True
    )
    e = mod_embed("⚠️ Warning Issued", discord.Color.orange(),
                  User=member.mention, By=interaction.user.mention,
                  **{"Total Warns": count})
    e.add_field(name="Reason", value=reason, inline=False)
    await send_log(interaction.guild, e)

    if count >= WARN_LIMIT:
        try:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=10)
            await member.timeout(until, reason=f"Reached {WARN_LIMIT} warnings")
        except (discord.Forbidden, discord.HTTPException):
            pass

@bot.tree.command(name="warnings", description="View or clear a member's warnings", guild=GUILD)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(member="Member to check", clear="Clear all their warnings")
async def warnings(interaction: discord.Interaction, member: discord.Member, clear: bool = False):
    if clear:
        cursor.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?",
                       (interaction.guild.id, member.id))
        conn.commit()
        add_audit(interaction.guild.id, member.id, interaction.user.id, "warns_cleared")
        await interaction.response.send_message(f"✅ Cleared warnings for {member.mention}.", ephemeral=True)
        await send_log(interaction.guild, mod_embed("🗑️ Warnings Cleared", discord.Color.green(),
                                                     User=member.mention, By=interaction.user.mention))
    else:
        cursor.execute("SELECT reason, timestamp FROM warns WHERE guild_id=? AND user_id=?",
                       (interaction.guild.id, member.id))
        rows = cursor.fetchall()
        e = discord.Embed(title=f"⚠️ Warnings for {member}", color=discord.Color.orange(),
                          timestamp=discord.utils.utcnow())
        if rows:
            for i, (reason, ts) in enumerate(rows, 1):
                dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                e.add_field(name=f"Warn #{i} ({dt})", value=reason, inline=False)
        else:
            e.description = "No warnings on record."
        await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="timeout", description="Timeout (mute) a member", guild=GUILD)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
@app_commands.describe(member="Member to timeout", minutes="Duration in minutes", reason="Reason")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member,
                      minutes: int = 10, reason: str = "No reason provided"):
    ok, err = can_action(interaction, member)
    if not ok:
        return await interaction.response.send_message(err, ephemeral=True)

    until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    add_audit(interaction.guild.id, member.id, interaction.user.id, "timeout", reason)
    await interaction.response.send_message(
        f"🔇 {member.mention} timed out for **{minutes} minute(s)**.", ephemeral=True
    )
    e = mod_embed("🔇 Member Timed Out", discord.Color.orange(),
                  User=member.mention, By=interaction.user.mention,
                  Duration=f"{minutes} min")
    e.add_field(name="Reason", value=reason, inline=False)
    await send_log(interaction.guild, e)

@bot.tree.command(name="kick", description="Kick a member from the server", guild=GUILD)
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.checks.bot_has_permissions(kick_members=True)
@app_commands.describe(member="Member to kick", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member,
               reason: str = "No reason provided"):
    ok, err = can_action(interaction, member)
    if not ok:
        return await interaction.response.send_message(err, ephemeral=True)

    await member.kick(reason=reason)
    add_audit(interaction.guild.id, member.id, interaction.user.id, "kick", reason)
    await interaction.response.send_message(f"👢 {member} has been kicked.", ephemeral=True)
    e = mod_embed("👢 Member Kicked", discord.Color.orange(),
                  User=str(member), By=interaction.user.mention)
    e.add_field(name="Reason", value=reason, inline=False)
    await send_log(interaction.guild, e)

@bot.tree.command(name="ban", description="Ban a member from the server", guild=GUILD)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
@app_commands.describe(member="Member to ban", reason="Reason", delete_days="Days of messages to delete (0–7)")
async def ban(interaction: discord.Interaction, member: discord.Member,
              reason: str = "No reason provided", delete_days: int = 0):
    ok, err = can_action(interaction, member)
    if not ok:
        return await interaction.response.send_message(err, ephemeral=True)

    delete_days = max(0, min(7, delete_days))
    await member.ban(reason=reason, delete_message_days=delete_days)
    add_audit(interaction.guild.id, member.id, interaction.user.id, "ban", reason)
    await interaction.response.send_message(f"🔨 {member} has been banned.", ephemeral=True)
    e = mod_embed("🔨 Member Banned", discord.Color.dark_red(),
                  User=str(member), By=interaction.user.mention)
    e.add_field(name="Reason", value=reason, inline=False)
    await send_log(interaction.guild, e)

@bot.tree.command(name="unban", description="Unban a user by their ID", guild=GUILD)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
@app_commands.describe(user_id="The user's Discord ID", reason="Reason")
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        add_audit(interaction.guild.id, user.id, interaction.user.id, "unban", reason)
        await interaction.response.send_message(f"✅ {user} has been unbanned.", ephemeral=True)
        e = mod_embed("✅ Member Unbanned", discord.Color.green(),
                      User=str(user), By=interaction.user.mention)
        e.add_field(name="Reason", value=reason, inline=False)
        await send_log(interaction.guild, e)
    except (discord.NotFound, ValueError):
        await interaction.response.send_message("User not found or not banned.", ephemeral=True)

@bot.tree.command(name="history", description="View a user's full moderation history", guild=GUILD)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(member="Member to look up")
async def history(interaction: discord.Interaction, member: discord.Member):
    cursor.execute(
        "SELECT action, reason, mod_id, timestamp FROM audit_log WHERE guild_id=? AND target_id=? ORDER BY timestamp DESC LIMIT 20",
        (interaction.guild.id, member.id)
    )
    rows = cursor.fetchall()
    e = discord.Embed(title=f"📜 Mod History — {member}", color=discord.Color.blurple(),
                      timestamp=discord.utils.utcnow())
    if rows:
        for action, reason, mod_id, ts in rows:
            dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            mod = interaction.guild.get_member(mod_id)
            mod_name = str(mod) if mod else f"ID:{mod_id}"
            e.add_field(
                name=f"{action.upper()} — {dt}",
                value=f"By: {mod_name}\nReason: {reason or 'None'}",
                inline=False
            )
    else:
        e.description = "No moderation history found."
    await interaction.response.send_message(embed=e, ephemeral=True)

# ================================================================
#  QUARANTINE
# ================================================================
async def get_or_create_imprisoned_role(guild: discord.Guild) -> discord.Role:
    role = discord.utils.get(guild.roles, name="Imprisoned")
    if not role:
        role = await guild.create_role(
            name="Imprisoned",
            color=discord.Color.dark_gray(),
            reason="Auto-created for quarantine system"
        )
    return role

@bot.tree.command(name="quarantine", description="Quarantine a member — removes all channel visibility and gives Imprisoned role", guild=GUILD)
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.checks.bot_has_permissions(manage_roles=True, manage_channels=True)
@app_commands.describe(member="Member to quarantine", reason="Reason for quarantine")
async def quarantine(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    ok, err = can_action(interaction, member)
    if not ok:
        return await interaction.response.send_message(err, ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    imprisoned_role = await get_or_create_imprisoned_role(guild)

    if imprisoned_role in member.roles:
        return await interaction.followup.send(f"{member.mention} is already quarantined.", ephemeral=True)

    await member.add_roles(imprisoned_role, reason=reason)

    denied = discord.PermissionOverwrite(
        view_channel=False,
        send_messages=False,
        connect=False,
        speak=False,
        read_message_history=False,
        add_reactions=False
    )
    failed = 0
    for channel in guild.channels:
        try:
            await channel.set_permissions(imprisoned_role, overwrite=denied, reason=f"Quarantine: {reason}")
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    add_audit(guild.id, member.id, interaction.user.id, "quarantine", reason)
    e = mod_embed("🔒 Member Quarantined", discord.Color.dark_red(),
                  User=member.mention, By=interaction.user.mention)
    e.add_field(name="Reason", value=reason, inline=False)
    if failed:
        e.add_field(name="Note", value=f"Could not update {failed} channel(s) (missing permissions).", inline=False)
    await send_log(guild, e)

    await interaction.followup.send(
        f"🔒 {member.mention} has been quarantined and given the **Imprisoned** role.", ephemeral=True
    )

@bot.tree.command(name="unquarantine", description="Release a member from quarantine and remove Imprisoned role", guild=GUILD)
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.checks.bot_has_permissions(manage_roles=True, manage_channels=True)
@app_commands.describe(member="Member to release", reason="Reason for release")
async def unquarantine(interaction: discord.Interaction, member: discord.Member, reason: str = "Released from quarantine"):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    imprisoned_role = discord.utils.get(guild.roles, name="Imprisoned")
    if not imprisoned_role or imprisoned_role not in member.roles:
        return await interaction.followup.send(f"{member.mention} is not currently quarantined.", ephemeral=True)

    await member.remove_roles(imprisoned_role, reason=reason)

    for channel in guild.channels:
        try:
            overwrite = channel.overwrites_for(imprisoned_role)
            if overwrite.is_empty() or all(v is None for v in overwrite):
                continue
            await channel.set_permissions(imprisoned_role, overwrite=None, reason=f"Unquarantine: {reason}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    add_audit(guild.id, member.id, interaction.user.id, "unquarantine", reason)
    e = mod_embed("🔓 Member Released from Quarantine", discord.Color.green(),
                  User=member.mention, By=interaction.user.mention)
    e.add_field(name="Reason", value=reason, inline=False)
    await send_log(guild, e)

    await interaction.followup.send(
        f"🔓 {member.mention} has been released from quarantine.", ephemeral=True
    )

# ================================================================
#  ROLE MANAGEMENT
# ================================================================
@bot.tree.command(name="massrole", description="Give or remove a role from everyone, or only members with a specific role", guild=GUILD)
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.checks.bot_has_permissions(manage_roles=True)
@app_commands.describe(
    action="Give or remove the role",
    role="The role to give/remove",
    filter_role="Only apply to members who already have this role (optional — leave blank for everyone)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="Give", value="give"),
    app_commands.Choice(name="Remove", value="remove"),
])
async def massrole(interaction: discord.Interaction,
                   action: app_commands.Choice[str],
                   role: discord.Role,
                   filter_role: discord.Role = None):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    if role >= guild.me.top_role:
        return await interaction.followup.send(
            "❌ That role is higher than or equal to my highest role. I can't assign it.", ephemeral=True
        )

    if filter_role:
        targets = [m for m in guild.members if filter_role in m.roles and not m.bot]
    else:
        targets = [m for m in guild.members if not m.bot]

    success, failed = 0, 0
    for member in targets:
        try:
            if action.value == "give":
                if role not in member.roles:
                    await member.add_roles(role, reason=f"Massrole by {interaction.user}")
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason=f"Massrole by {interaction.user}")
            success += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    scope = f"members with **{filter_role.name}**" if filter_role else "**everyone**"
    verb = "given to" if action.value == "give" else "removed from"
    e = mod_embed(f"🎭 Mass Role — {action.name}", discord.Color.purple(),
                  Role=role.mention, By=interaction.user.mention, Scope=scope)
    e.add_field(name="✅ Success", value=success, inline=True)
    e.add_field(name="❌ Failed",  value=failed,  inline=True)
    await send_log(guild, e)
    await interaction.followup.send(
        f"✅ **{role.name}** {verb} **{success}** member(s){f' ({failed} failed)' if failed else ''}.",
        ephemeral=True
    )

# ================================================================
#  CHAT CONTROL
# ================================================================
@bot.tree.command(name="purge", description="Delete messages in a channel", guild=GUILD)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True)
@app_commands.describe(amount="Number of messages to delete (1–100)")
async def purge(interaction: discord.Interaction, amount: int):
    amount = max(1, min(100, amount))
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 Deleted **{len(deleted)}** messages.", ephemeral=True)
    e = mod_embed("🧹 Purge", discord.Color.red(),
                  Amount=len(deleted), Channel=interaction.channel.mention,
                  By=interaction.user.mention)
    await send_log(interaction.guild, e)

@bot.tree.command(name="lock", description="Lock a channel so members can't send messages", guild=GUILD)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="Channel to lock (defaults to current)", reason="Reason")
async def lock(interaction: discord.Interaction, channel: discord.TextChannel = None,
               reason: str = "No reason provided"):
    ch = channel or interaction.channel
    overwrite = ch.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(f"🔒 {ch.mention} is now locked.", ephemeral=True)
    e = mod_embed("🔒 Channel Locked", discord.Color.red(),
                  Channel=ch.mention, By=interaction.user.mention)
    e.add_field(name="Reason", value=reason, inline=False)
    await send_log(interaction.guild, e)

@bot.tree.command(name="unlock", description="Unlock a channel", guild=GUILD)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="Channel to unlock (defaults to current)")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    overwrite = ch.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await ch.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.response.send_message(f"🔓 {ch.mention} is now unlocked.", ephemeral=True)
    e = mod_embed("🔓 Channel Unlocked", discord.Color.green(),
                  Channel=ch.mention, By=interaction.user.mention)
    await send_log(interaction.guild, e)

@bot.tree.command(name="lockdown", description="Lock or unlock every channel in the server", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(lock="True to lock, False to unlock", reason="Reason")
async def lockdown(interaction: discord.Interaction, lock: bool = True,
                   reason: str = "Emergency lockdown"):
    await interaction.response.defer(ephemeral=True)
    for ch in interaction.guild.text_channels:
        ow = ch.overwrites_for(interaction.guild.default_role)
        ow.send_messages = not lock
        await ch.set_permissions(interaction.guild.default_role, overwrite=ow)

    status = "🔒 Locked" if lock else "🔓 Unlocked"
    await interaction.followup.send(f"{status} all channels.", ephemeral=True)
    e = mod_embed(f"{status} — Server-wide", discord.Color.red() if lock else discord.Color.green(),
                  By=interaction.user.mention)
    e.add_field(name="Reason", value=reason, inline=False)
    await send_log(interaction.guild, e)

@bot.tree.command(name="slowmode", description="Set slowmode delay on a channel", guild=GUILD)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(seconds="Delay in seconds (0 to disable, max 21600)", channel="Channel (defaults to current)")
async def slowmode(interaction: discord.Interaction, seconds: int,
                   channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    seconds = max(0, min(21600, seconds))
    await ch.edit(slowmode_delay=seconds)
    msg = f"⏱️ Slowmode {'disabled' if seconds == 0 else f'set to **{seconds}s**'} in {ch.mention}."
    await interaction.response.send_message(msg, ephemeral=True)
    e = mod_embed("⏱️ Slowmode Updated", discord.Color.blue(),
                  Channel=ch.mention, Delay=f"{seconds}s", By=interaction.user.mention)
    await send_log(interaction.guild, e)

@bot.tree.command(name="nuke", description="Delete and recreate a channel (wipes all messages)", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_channels=True)
@app_commands.describe(channel="Channel to nuke (defaults to current)")
async def nuke(interaction: discord.Interaction, channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    await interaction.response.defer(ephemeral=True)
    new_ch = await ch.clone(reason=f"Nuke by {interaction.user}")
    await ch.delete(reason=f"Nuke by {interaction.user}")
    await new_ch.send("💥 Channel has been nuked.", delete_after=5)
    await interaction.followup.send(f"💥 Nuked and recreated {new_ch.mention}.", ephemeral=True)
    e = mod_embed("💥 Channel Nuked", discord.Color.dark_red(),
                  Channel=new_ch.name, By=interaction.user.mention)
    await send_log(interaction.guild, e)

# ================================================================
#  UTILITY
# ================================================================
@bot.tree.command(name="ping", description="Check the bot's latency", guild=GUILD)
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Latency: **{latency}ms**", ephemeral=True)

@bot.tree.command(name="serverinfo", description="View server information", guild=GUILD)
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    e = discord.Embed(title=g.name, color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="Owner",    value=g.owner.mention)
    e.add_field(name="Members",  value=g.member_count)
    e.add_field(name="Channels", value=len(g.channels))
    e.add_field(name="Roles",    value=len(g.roles))
    e.add_field(name="Boosters", value=g.premium_subscription_count)
    e.add_field(name="Created",  value=g.created_at.strftime("%Y-%m-%d"))
    await interaction.response.send_message(embed=e)

@bot.tree.command(name="userinfo", description="View information about a user", guild=GUILD)
@app_commands.describe(member="Member to look up (defaults to you)")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    cursor.execute("SELECT COUNT(*) FROM warns WHERE guild_id=? AND user_id=?",
                   (interaction.guild.id, m.id))
    warn_count = cursor.fetchone()[0]

    e = discord.Embed(title=str(m), color=m.color, timestamp=discord.utils.utcnow())
    e.set_thumbnail(url=m.display_avatar.url)
    e.add_field(name="ID",       value=m.id)
    e.add_field(name="Joined",   value=m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "N/A")
    e.add_field(name="Created",  value=m.created_at.strftime("%Y-%m-%d"))
    e.add_field(name="Warnings", value=warn_count)
    e.add_field(name="Top Role", value=m.top_role.mention)
    e.add_field(name="Bot",      value="Yes" if m.bot else "No")
    await interaction.response.send_message(embed=e)

@bot.tree.command(name="avatar", description="Show a user's profile picture", guild=GUILD)
@app_commands.describe(member="Member to look up (defaults to you)")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    e = discord.Embed(title=f"{m}'s Avatar", color=discord.Color.blurple())
    e.set_image(url=m.display_avatar.url)
    await interaction.response.send_message(embed=e)

# ================================================================
#  TICKET SYSTEM
# ================================================================
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Select a ticket type...",
        custom_id="ticket_select",
        options=[
            discord.SelectOption(label="Support",    emoji="🛠️", description="Need help with something"),
            discord.SelectOption(label="Report",     emoji="🚨", description="Report a user or issue"),
            discord.SelectOption(label="Staff Help", emoji="👮", description="Contact staff directly"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        ticket_type = select.values[0]
        existing = discord.utils.get(interaction.guild.text_channels,
                                     name=f"ticket-{interaction.user.name.lower()}")
        if existing:
            return await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}", ephemeral=True
            )

        # Find or create a "Tickets" category
        category = discord.utils.get(interaction.guild.categories, name="Tickets")
        if not category:
            category = await interaction.guild.create_category("Tickets")

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for r in staff_roles(interaction.guild):
            overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await interaction.guild.create_text_channel(
            f"ticket-{interaction.user.name}", overwrites=overwrites, category=category
        )

        e = discord.Embed(
            title=f"🎫 {ticket_type} Ticket",
            description=f"Opened by {interaction.user.mention}. Staff will be with you shortly.\nUse the buttons below to manage this ticket.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )
        await channel.send(embed=e, view=TicketControls())
        await interaction.response.send_message(
            f"Your ticket has been created: {channel.mention}", ephemeral=True
        )

        log_e = mod_embed("🎫 Ticket Opened", discord.Color.blurple(),
                          User=interaction.user.mention, Type=ticket_type, Channel=channel.mention)
        await send_log(interaction.guild, log_e)

class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, emoji="🔒", custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Saving transcript and closing...", ephemeral=True)

        messages = [m async for m in interaction.channel.history(limit=500, oldest_first=True)]
        path = build_transcript(interaction.channel.name, messages)

        log_e = mod_embed("🔒 Ticket Closed", discord.Color.red(),
                          Channel=interaction.channel.name, By=interaction.user.mention,
                          Messages=len(messages))
        await send_log(interaction.guild, log_e, file=discord.File(path))

        await interaction.channel.delete()

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.blurple, emoji="✋", custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = f"Claimed by {interaction.user.name}"
        await interaction.response.edit_message(view=self)
        await interaction.channel.send(f"✋ {interaction.user.mention} has claimed this ticket.")

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.green, emoji="➕", custom_id="ticket_add")
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Use `/adduser @member` to add someone to this ticket.", ephemeral=True
        )

@bot.tree.command(name="ticketpanel", description="Send the ticket creation panel", guild=GUILD)
@app_commands.checks.has_permissions(manage_channels=True)
async def ticketpanel(interaction: discord.Interaction):
    e = discord.Embed(
        title="🎫 Open a Support Ticket",
        description="Select a category below to open a ticket with our staff team.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=e, view=TicketView())

@bot.tree.command(name="adduser", description="Add a user to the current ticket", guild=GUILD)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(member="Member to add")
async def adduser(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True)
    await interaction.response.send_message(f"✅ {member.mention} has been added to this ticket.")

@bot.tree.command(name="removeuser", description="Remove a user from the current ticket", guild=GUILD)
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(member="Member to remove")
async def removeuser(interaction: discord.Interaction, member: discord.Member):
    await interaction.channel.set_permissions(member, view_channel=False, send_messages=False)
    await interaction.response.send_message(f"✅ {member.mention} has been removed from this ticket.")

@bot.tree.command(name="close", description="Close the current ticket channel", guild=GUILD)
@app_commands.checks.has_permissions(manage_channels=True)
async def close(interaction: discord.Interaction):
    await interaction.response.send_message("🔒 Closing ticket and saving transcript...", ephemeral=True)

    messages = [m async for m in interaction.channel.history(limit=500, oldest_first=True)]
    path = build_transcript(interaction.channel.name, messages)

    log_e = mod_embed("🔒 Ticket Closed", discord.Color.red(),
                      Channel=interaction.channel.name, By=interaction.user.mention,
                      Messages=len(messages))
    await send_log(interaction.guild, log_e, file=discord.File(path))
    await interaction.channel.delete()

# ================================================================
#  REACTION ROLES
# ================================================================
@bot.tree.command(name="reactionrole", description="Create a reaction-role message", guild=GUILD)
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(role="Role to assign", emoji="Emoji to react with", description="Message description")
async def reactionrole(interaction: discord.Interaction, role: discord.Role,
                       emoji: str, description: str = "React to get a role!"):
    e = discord.Embed(
        title="🎭 Reaction Role",
        description=f"{emoji} — {role.mention}\n\n{description}",
        color=discord.Color.blurple()
    )
    msg = await interaction.channel.send(embed=e)
    await msg.add_reaction(emoji)

    # Store mapping: channel_id + message_id → emoji → role name
    set_cfg(interaction.guild.id, f"rr_{msg.id}_{emoji}", f"{role.name}|{interaction.channel.id}")
    await interaction.response.send_message("✅ Reaction role created!", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    key = f"rr_{payload.message_id}_{payload.emoji}"
    val = get_cfg(payload.guild_id, key)
    if val:
        role_name = val.split("|")[0]
        role = discord.utils.get(guild.roles, name=role_name)
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.user_id == bot.user.id:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    key = f"rr_{payload.message_id}_{payload.emoji}"
    val = get_cfg(payload.guild_id, key)
    if val:
        role_name = val.split("|")[0]
        role = discord.utils.get(guild.roles, name=role_name)
        member = guild.get_member(payload.user_id)
        if role and member:
            await member.remove_roles(role)

# Slop code
# ================================================================
#  ACTIVITY TRACKER 
# ================================================================
@bot.tree.command(name="activity", description="Show activity — all members or a specific user", guild=GUILD)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(
    member="Check a specific member's activity (leave empty for full list)",
    limit="Number of members to show in the full list (default 20)"
)
async def activity(interaction: discord.Interaction, member: discord.Member = None, limit: int = 20):
    # ── Single member lookup ──────────────────────────────────────
    if member:
        cursor.execute(
            "SELECT last_seen, last_action FROM user_activity WHERE guild_id=? AND user_id=?",
            (interaction.guild.id, member.id)
        )
        row = cursor.fetchone()

        e = discord.Embed(title=f"📊 Activity — {member}", color=member.color or discord.Color.blurple(),
                          timestamp=discord.utils.utcnow())
        e.set_thumbnail(url=member.display_avatar.url)

        if not row:
            e.description = "No activity recorded for this member yet."
        else:
            last_seen, last_action = row
            dt = datetime.datetime.fromtimestamp(last_seen)
            seconds_ago = int(time.time()) - last_seen
            if seconds_ago < 60:
                ago = f"{seconds_ago}s ago"
            elif seconds_ago < 3600:
                ago = f"{seconds_ago // 60}m ago"
            elif seconds_ago < 86400:
                ago = f"{seconds_ago // 3600}h ago"
            else:
                ago = f"{seconds_ago // 86400}d ago"

            e.add_field(name="Last Seen",   value=f"`{dt.strftime('%Y-%m-%d %H:%M UTC')}` ({ago})")
            e.add_field(name="Last Action", value=last_action)
            e.add_field(name="Status",      value="🟢 Online" if member.status != discord.Status.offline else "⚫ Offline")
            e.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "N/A")

            # Full recent history from audit log
            cursor.execute(
                "SELECT action, reason, timestamp FROM audit_log WHERE guild_id=? AND target_id=? ORDER BY timestamp DESC LIMIT 5",
                (interaction.guild.id, member.id)
            )
            mod_rows = cursor.fetchall()
            if mod_rows:
                history = "\n".join(
                    f"`{datetime.datetime.fromtimestamp(ts).strftime('%m/%d')}` {act} — {reason or 'No reason'}"
                    for act, reason, ts in mod_rows
                )
                e.add_field(name="Recent Mod Actions", value=history, inline=False)

        return await interaction.response.send_message(embed=e, ephemeral=True)

    # ── Full server list ──────────────────────────────────────────
    limit = max(1, min(50, limit))
    cursor.execute(
        "SELECT user_id, last_seen, last_action FROM user_activity WHERE guild_id=? ORDER BY last_seen DESC LIMIT ?",
        (interaction.guild.id, limit)
    )
    rows = cursor.fetchall()

    e = discord.Embed(title="📊 Member Activity", color=discord.Color.blurple(),
                      timestamp=discord.utils.utcnow())
    e.set_footer(text=f"Showing {len(rows)} member(s) — most recent first")

    if not rows:
        e.description = "No activity recorded yet."
    else:
        lines = []
        for user_id, last_seen, last_action in rows:
            m = interaction.guild.get_member(user_id)
            name = m.mention if m else f"User {user_id}"
            dt = datetime.datetime.fromtimestamp(last_seen).strftime("%m/%d %H:%M")
            lines.append(f"{name} — `{dt}` — {last_action}")
        e.description = "\n".join(lines)

    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="inactivity", description="Show members inactive for X or more days", guild=GUILD)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(days="Days of inactivity threshold (default 7)")
async def inactivity(interaction: discord.Interaction, days: int = 7):
    threshold = int(time.time()) - (days * 86400)
    cursor.execute(
        "SELECT user_id, last_seen, last_action FROM user_activity WHERE guild_id=? AND last_seen < ? ORDER BY last_seen ASC",
        (interaction.guild.id, threshold)
    )
    rows = cursor.fetchall()

    # Also include tracked members not seen at all
    e = discord.Embed(
        title=f"😴 Members Inactive for {days}+ Days",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    e.set_footer(text=f"{len(rows)} inactive member(s) found")

    if not rows:
        e.description = f"No members have been inactive for {days}+ days!"
    else:
        lines = []
        for user_id, last_seen, last_action in rows:
            member = interaction.guild.get_member(user_id)
            if not member:
                continue
            dt = datetime.datetime.fromtimestamp(last_seen).strftime("%Y-%m-%d")
            days_ago = (int(time.time()) - last_seen) // 86400
            lines.append(f"{member.mention} — Last seen `{dt}` ({days_ago}d ago) — {last_action}")
        e.description = "\n".join(lines) if lines else f"No current members inactive for {days}+ days."

    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="resetactivity", description="Reset a member's activity record", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(member="Member to reset")
async def resetactivity(interaction: discord.Interaction, member: discord.Member):
    cursor.execute("DELETE FROM user_activity WHERE guild_id=? AND user_id=?",
                   (interaction.guild.id, member.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Cleared activity record for {member.mention}.", ephemeral=True)

# ================================================================
#  BOT CONTROL
# ================================================================
@bot.tree.command(name="botinfo", description="Show live bot stats — uptime, ping, members, and more", guild=GUILD)
async def botinfo(interaction: discord.Interaction):
    uptime_secs = int(time.time() - bot_start_time)
    days, rem    = divmod(uptime_secs, 86400)
    hours, rem   = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    uptime_str   = f"{days}d {hours}h {minutes}m {secs}s"

    total_members = sum(g.member_count for g in bot.guilds)
    total_commands = len(bot.tree.get_commands(guild=GUILD)) + len(bot.tree.get_commands())
    ping = round(bot.latency * 1000)

    e = discord.Embed(title=f"🤖 {bot.user.name} — Bot Info",
                      color=discord.Color.blurple(),
                      timestamp=discord.utils.utcnow())
    e.set_thumbnail(url=bot.user.display_avatar.url)
    e.add_field(name="⏱️ Uptime",    value=uptime_str,      inline=True)
    e.add_field(name="📡 Ping",      value=f"{ping}ms",     inline=True)
    e.add_field(name="👥 Members",   value=total_members,   inline=True)
    e.add_field(name="🏠 Servers",   value=len(bot.guilds), inline=True)
    e.add_field(name="⚙️ Commands",  value=total_commands,  inline=True)
    e.add_field(name="📚 Library",   value=f"discord.py {discord.__version__}", inline=True)
    e.set_footer(text=f"Bot ID: {bot.user.id}")
    await interaction.response.send_message(embed=e)

@bot.tree.command(name="synccommands", description="Force sync all slash commands to this server", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
async def synccommands(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        synced = await bot.tree.sync(guild=GUILD)
        await interaction.followup.send(
            f"✅ Force synced **{len(synced)} commands** to this server.", ephemeral=True
        )
        e = mod_embed("🔄 Commands Force Synced", discord.Color.green(),
                      By=interaction.user.mention, Commands=len(synced))
        await send_log(interaction.guild, e)
    except Exception as ex:
        await interaction.followup.send(f"❌ Sync failed: {ex}", ephemeral=True)


@bot.tree.command(name="botstop", description="Shut the bot down", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
async def botstop(interaction: discord.Interaction):
    e = mod_embed("🛑 Bot Stopping", discord.Color.dark_red(),
                  By=interaction.user.mention)
    await send_log(interaction.guild, e)
    await interaction.response.send_message("🛑 Shutting down...", ephemeral=True)
    await bot.close()

@bot.tree.command(name="botrestart", description="Restart the bot", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
async def botrestart(interaction: discord.Interaction):
    e = mod_embed("🔄 Bot Restarting", discord.Color.orange(),
                  By=interaction.user.mention)
    await send_log(interaction.guild, e)
    await interaction.response.send_message("🔄 Restarting...", ephemeral=True)
    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)

# ================================================================
#  RUN
# ================================================================
# ================================================================
#  BETA TESTING SYSTEM
# ================================================================

TESTER_REVIEW_ROLES = [
    1465887675260338367,
    1429329903987458141,
    1429329903287013428
]

# Add this to your cursor.executescript() tables
cursor.executescript("""
CREATE TABLE IF NOT EXISTS bug_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    reporter_id INTEGER,
    reporter_name TEXT,
    report_type TEXT,
    report_link TEXT,
    f9_logs BOOLEAN,
    evidence BOOLEAN,
    accepted BOOLEAN DEFAULT 0,
    accepted_by INTEGER,
    timestamp INTEGER
);
""")
conn.commit()

def can_review(member: discord.Member):
    return any(role.id in TESTER_REVIEW_ROLES for role in member.roles)

# ================================================================
#  BUG REVIEW VIEW
# ================================================================

class BugReviewView(discord.ui.View):
    def __init__(self, report_id):
        super().__init__(timeout=None)
        self.report_id = report_id

    @discord.ui.button(label="Accept Report", style=discord.ButtonStyle.green)
    async def accept_report(self, interaction: discord.Interaction, button: discord.ui.Button):

        if not can_review(interaction.user):
            return await interaction.response.send_message(
                "❌ You don't have permission to review reports.",
                ephemeral=True
            )

        cursor.execute(
            "SELECT accepted, reporter_name FROM bug_reports WHERE id=?",
            (self.report_id,)
        )

        row = cursor.fetchone()

        if not row:
            return await interaction.response.send_message(
                "❌ Report not found.",
                ephemeral=True
            )

        accepted, reporter_name = row

        if accepted == 1:
            return await interaction.response.send_message(
                "⚠️ This report was already accepted.",
                ephemeral=True
            )

        cursor.execute("""
            UPDATE bug_reports
            SET accepted=1, accepted_by=?
            WHERE id=?
        """, (
            interaction.user.id,
            self.report_id
        ))

        conn.commit()

        button.disabled = True
        button.label = f"Accepted by {interaction.user.name}"

        await interaction.response.edit_message(view=self)

        await interaction.followup.send(
            f"✅ Report #{self.report_id} accepted.",
            ephemeral=True
        )

# ================================================================
#  FILEPOINT COMMAND
# ================================================================

@bot.tree.command(name="filepoint", description="Submit a beta testing bug report", guild=GUILD)
@app_commands.describe(
    report_type="Type of testing report",
    report_link="Link to report or evidence",
    f9_logs="Were F9 logs included?",
    evidence="Was evidence included?"
)
@app_commands.choices(report_type=[
    app_commands.Choice(name="General Testing", value="General Testing"),
    app_commands.Choice(name="Bug Verification", value="Bug Verification"),
    app_commands.Choice(name="Testing Session", value="Testing Session"),
    app_commands.Choice(name="Vehicle Testing", value="Vehicle Testing"),
])
async def filepoint(
    interaction: discord.Interaction,
    report_type: app_commands.Choice[str],
    report_link: str,
    f9_logs: bool,
    evidence: bool
):

    cursor.execute("""
        INSERT INTO bug_reports
        (guild_id, reporter_id, reporter_name, report_type, report_link, f9_logs, evidence, timestamp)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        interaction.guild.id,
        interaction.user.id,
        str(interaction.user),
        report_type.value,
        report_link,
        f9_logs,
        evidence,
        int(time.time())
    ))

    conn.commit()

    report_id = cursor.lastrowid

    embed = discord.Embed(
        title="🐞 New Beta Testing Report",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(name="Report ID", value=report_id, inline=True)
    embed.add_field(name="Tester", value=interaction.user.mention, inline=True)
    embed.add_field(name="Type", value=report_type.value, inline=True)

    embed.add_field(
        name="Report Link / Message",
        value=report_link,
        inline=False
    )

    embed.add_field(
        name="F9 Logs Included",
        value="✅ Yes" if f9_logs else "❌ No",
        inline=True
    )

    embed.add_field(
        name="Evidence Included",
        value="✅ Yes" if evidence else "❌ No",
        inline=True
    )

    view = BugReviewView(report_id)

    await interaction.response.send_message(
        "✅ Your bug report was submitted successfully.",
        ephemeral=True
    )

    log_ch = log_channel(interaction.guild)

    if log_ch:
        await log_ch.send(
            content="<@&1465887675260338367> <@&1429329903987458141> <@&1429329903287013428>",
            embed=embed,
            view=view
        )

# ================================================================
#  BUG LEADERBOARD
# ================================================================

@bot.tree.command(name="bugleaderboard", description="View bug report leaderboard", guild=GUILD)
async def bugleaderboard(interaction: discord.Interaction):

    cursor.execute("""
        SELECT reporter_name, COUNT(*)
        FROM bug_reports
        WHERE accepted=1
        GROUP BY reporter_name
        ORDER BY COUNT(*) DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()

    embed = discord.Embed(
        title="🏆 Bug Report Leaderboard",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow()
    )

    if not rows:
        embed.description = "No accepted reports yet."
    else:
        leaderboard = []

        for i, (name, total) in enumerate(rows, start=1):
            leaderboard.append(
                f"**#{i}** {name} — `{total}` accepted reports"
            )

        embed.description = "\n".join(leaderboard)

    await interaction.response.send_message(embed=embed)

# ================================================================
#  POINTS COMMAND
# ================================================================

@bot.tree.command(name="points", description="Check a tester's accepted reports", guild=GUILD)
@app_commands.describe(member="Member to check")
async def points(interaction: discord.Interaction, member: discord.Member):

    cursor.execute("""
        SELECT COUNT(*)
        FROM bug_reports
        WHERE reporter_id=? AND accepted=1
    """, (member.id,))

    points_total = cursor.fetchone()[0]

    embed = discord.Embed(
        title="📊 Tester Points",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="Tester",
        value=member.mention,
        inline=True
    )

    embed.add_field(
        name="Accepted Reports",
        value=f"{points_total}",
        inline=True
    )

    await interaction.response.send_message(embed=embed)

# ================================================================
#  WEEKLY REPORTS
# ================================================================

@bot.tree.command(name="weeklyreports", description="View weekly tester performance", guild=GUILD)
async def weeklyreports(interaction: discord.Interaction):

    week_ago = int(time.time()) - 604800

    cursor.execute("""
        SELECT reporter_name, COUNT(*)
        FROM bug_reports
        WHERE accepted=1 AND timestamp >= ?
        GROUP BY reporter_name
        ORDER BY COUNT(*) DESC
        LIMIT 10
    """, (week_ago,))

    rows = cursor.fetchall()

    embed = discord.Embed(
        title="📅 Weekly Testing Performance",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )

    if not rows:
        embed.description = "No accepted reports this week."
    else:
        lines = []

        for i, (name, total) in enumerate(rows, start=1):
            lines.append(
                f"**#{i}** {name} — `{total}` accepted reports this week"
            )

        embed.description = "\n".join(lines)

    await interaction.response.send_message(embed=embed)

# ================================================================
#  RUN
# ================================================================
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise ValueError("DISCORD_TOKEN environment variable is not set.")

bot.run(token)
