import discord
import os
from discord.ext import commands, tasks
from discord import app_commands, Interaction
import sqlite3
import json 
from datetime import datetime, timedelta
from dotenv import load_dotenv

# setup
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DB_FILE = "events.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    c = conn.cursor()
    try:
        c.execute('PRAGMA journal_mode=WAL;')
        c.execute('''CREATE TABLE IF NOT EXISTS events 
                     (guild_id TEXT, user_id TEXT, username TEXT, name TEXT, time TEXT, lateness INTEGER, started INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS schedules 
                     (guild_id TEXT, user_id TEXT, username TEXT, name TEXT, day_of_week INTEGER, time_24h TEXT)''')
        
        # Migration logic for existing DBs
        try:
            c.execute("ALTER TABLE events ADD COLUMN guild_id TEXT")
        except sqlite3.OperationalError: pass
        try:
            c.execute("ALTER TABLE schedules ADD COLUMN guild_id TEXT")
        except sqlite3.OperationalError: pass
        conn.commit()
    finally:
        conn.close()

def query_db(query, args=(), one=False):
    # Added isolation_level=None for better performance in WAL mode
    conn = sqlite3.connect(DB_FILE, timeout=20, isolation_level=None) 
    c = conn.cursor()
    try:
        if not query.strip().upper().startswith("SELECT"):
            c.execute("BEGIN IMMEDIATE") # Forces a write lock immediately to prevent mid-operation deadlocks
            c.execute(query, args)
            c.execute("COMMIT")
            rv = []
        else:
            c.execute(query, args)
            rv = c.fetchall()
    except Exception as e:
        if not query.strip().upper().startswith("SELECT"):
            c.execute("ROLLBACK")
        raise e
    finally:
        conn.close()
    return (rv[0] if rv else None) if one else rv

init_db()

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True 

bot = commands.Bot(command_prefix="!", intents=intents)

class EventGroup(app_commands.Group, name="event"): pass
class AdminGroup(app_commands.Group, name="admin"): pass

event_menu = EventGroup()
admin_menu = AdminGroup()

# --- USER COMMANDS ---

@event_menu.command(name="create", description="Manual: Set a specific date and time")
async def create(interaction: Interaction, name: str, year: int, month: int, day: int, time_24h: str, member: discord.Member = None):
    target = member or interaction.user
    try:
        dt_str = f"{year}-{month:02d}-{day:02d} {time_24h}"
        query_db("INSERT INTO events (guild_id, user_id, username, name, time, lateness, started) VALUES (?, ?, ?, ?, ?, NULL, 0)", 
                 (str(interaction.guild.id), str(target.id), str(target), name, dt_str))
        await interaction.response.send_message(f"✅ Event **{name}** created for {target.mention}")
    except:
        await interaction.response.send_message("❌ Format error. Use: YYYY MM DD HH:MM", ephemeral=True)

@event_menu.command(name="quick", description="Quick: Start in X minutes from now")
async def quick(interaction: Interaction, name: str, minutes: int = 15, member: discord.Member = None):
    target = member or interaction.user
    dt_str = (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")
    query_db("INSERT INTO events (guild_id, user_id, username, name, time, lateness, started) VALUES (?, ?, ?, ?, ?, NULL, 0)", 
             (str(interaction.guild.id), str(target.id), str(target), name, dt_str))
    await interaction.response.send_message(f"**Quick Event** '{name}' starts in {minutes}m for {target.mention}")

@event_menu.command(name="list", description="List your events and recorded lateness/earliness")
async def list_events(interaction: Interaction, member: discord.Member = None):
    target = member or interaction.user
    rows = query_db("SELECT name, time, lateness, started FROM events WHERE user_id = ? AND guild_id = ?", 
                    (str(target.id), str(interaction.guild.id)))
    
    if not rows: 
        return await interaction.response.send_message(f"📅 No events found for {target.display_name}", ephemeral=True)
    
    msg = f"📅 **{target.display_name}'s Events:**\n"
    
    for i, (name, timestamp, late, started) in enumerate(rows, 1):
        if late is not None:
            # Calculate absolute minutes and seconds for display
            m, s = abs(late) // 60, abs(late) % 60
            time_str = f"{m}m {s}s"
            
            # Label based on original value
            if late < 0:
                status = f"✅ Early: {time_str}"
            elif late == 0:
                status = " Exactly on time!"
            else:
                status = f"✅ Late: {time_str}"
        else:
            status = f"{timestamp} ⏳ Ongoing" if started else f"🕒 {timestamp}"
            
        msg += f"{i}. **{name}** — {timestamp} {status}\n"
    
    await interaction.response.send_message(msg, ephemeral=True)

@event_menu.command(name="stop", description="Stop the timer (records negative if early)")
async def stop(interaction: Interaction, name: str):
    uid, gid = str(interaction.user.id), str(interaction.guild.id)
    # Removed the 'started = 1' requirement so you can stop it early
    row = query_db("SELECT time FROM events WHERE user_id = ? AND guild_id = ? AND name = ? AND lateness IS NULL", (uid, gid, name), one=True)
    
    if not row: 
        return await interaction.response.send_message("❌ No active or pending event found with that name.", ephemeral=True)
    
    event_time = datetime.strptime(row[0], "%Y-%m-%d %H:%M")
    now = datetime.now()
    
    # Calculate total seconds (Negative = Early, Positive = Late)
    late_seconds = int((now - event_time).total_seconds())
    
    query_db("UPDATE events SET lateness = ?, started = 0 WHERE user_id = ? AND guild_id = ? AND name = ?", 
             (late_seconds, uid, gid, name))
    
    if late_seconds < 0:
        abs_early = abs(late_seconds)
        await interaction.response.send_message(f" Early arrival! Recorded **-{abs_early//60}m {abs_early%60}s** for '{name}'.", ephemeral=True)
    else:
        await interaction.response.send_message(f" Stopped '{name}'. Lateness: **{late_seconds//60}m {late_seconds%60}s**.", ephemeral=True)

@event_menu.command(name="delete", description="Delete one of your events")
async def delete(interaction: Interaction, name: str):
    # 1. Tell Discord to wait (prevents 404 Unknown Interaction)
    await interaction.response.defer(ephemeral=True)
    
    try:
        query_db("DELETE FROM events WHERE user_id = ? AND guild_id = ? AND name = ?", 
                 (str(interaction.user.id), str(interaction.guild.id), name))
        # 2. Use followups for deferred messages
        await interaction.followup.send(f" Deleted event: '{name}'")
    except sqlite3.OperationalError:
        await interaction.followup.send("❌ Database is currently busy. Please try again in a few seconds.")


@event_menu.command(name="clear", description="Clear all your events in this server")
async def clear(interaction: Interaction):
    query_db("DELETE FROM events WHERE user_id = ? AND guild_id = ?", (str(interaction.user.id), str(interaction.guild.id)))
    await interaction.response.send_message(" Your events in this server cleared.", ephemeral=True)

@event_menu.command(name="list_all", description="View everyone's events in this server")
async def list_all(interaction: Interaction):
    rows = query_db("SELECT user_id, username, name, time, lateness, started FROM events WHERE guild_id = ? ORDER BY user_id ASC", (str(interaction.guild.id),))
    
    if not rows: 
        return await interaction.response.send_message("📅 No events found in this server.", ephemeral=True)
    
    msg = f" **{interaction.guild.name} Event Board**\n"
    curr = None
    
    for uid, uname, name, timestamp, late, started in rows:
        if uid != curr:
            curr = uid
            msg += f"\n👤 **{uname or f'<@{uid}>'}**\n"
        
        if late is not None:
            # 1. Always use absolute value for the numbers
            abs_late = abs(late)
            m, s = abs_late // 60, abs_late % 60
            time_str = f"{m}m {s}s"
            
            # 2. Use the original 'late' value to pick the label/emoji
            if late < 0:
                emoji = "✅ Early:"
            elif late == 0:
                emoji = " On Time:"
            else:
                emoji = "✅ Late:"
                
            status = f"{timestamp} {emoji} {time_str}"
        else:
            status = f"{timestamp} ⏳ Ongoing" if started else f"🕒 {timestamp}"
            
        msg += f" └ **{name}** — {status}\n"
    
    await interaction.response.send_message(msg)

@event_menu.command(name="add_schedule", description="Set a recurring weekly event")
async def add_schedule(interaction: Interaction, name: str, day: str, time_24h: str):
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day = day.lower().strip()
    if day not in days: return await interaction.response.send_message("❌ Invalid day.", ephemeral=True)
    query_db("INSERT INTO schedules (guild_id, user_id, username, name, day_of_week, time_24h) VALUES (?, ?, ?, ?, ?, ?)", 
             (str(interaction.guild.id), str(interaction.user.id), str(interaction.user), name, days.index(day), time_24h))
    await interaction.response.send_message(f"🗓️ Recurring: **{name}** every {day.capitalize()} at {time_24h}.")

@event_menu.command(name="delete_schedule", description="Delete a recurring schedule")
async def delete_schedule(interaction: Interaction, name: str):
    query_db("DELETE FROM schedules WHERE user_id = ? AND guild_id = ? AND name = ?", (str(interaction.user.id), str(interaction.guild.id), name))
    await interaction.response.send_message(f" Deleted schedule: {name}", ephemeral=True)

# --- ADMIN COMMANDS ---

@admin_menu.command(name="delete", description="Admin: Delete event from other user")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_delete(interaction: Interaction, member: discord.Member, event_name: str):
    query_db("DELETE FROM events WHERE user_id = ? AND guild_id = ? AND name = ?", (str(member.id), str(interaction.guild.id), event_name))
    await interaction.response.send_message(f" Admin: Deleted '{event_name}' for {member.display_name}")

@admin_menu.command(name="clear", description="Admin: Clear all user data in this server")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_clear(interaction: Interaction, member: discord.Member):
    query_db("DELETE FROM events WHERE user_id = ? AND guild_id = ?", (str(member.id), str(interaction.guild.id)))
    await interaction.response.send_message(f" Admin: Cleared data for {member.display_name}")

@admin_menu.command(name="stop", description="Admin: Force stop a user's timer (allows negative lateness)")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_stop(interaction: Interaction, member: discord.Member, name: str):
    uid, gid = str(member.id), str(interaction.guild.id)
    
    # Look for any event for this user that hasn't been finished (lateness is NULL)
    row = query_db("SELECT time FROM events WHERE user_id = ? AND guild_id = ? AND name = ? AND lateness IS NULL", (uid, gid, name), one=True)
    
    if not row: 
        return await interaction.response.send_message(f"❌ No active/pending event found for {member.display_name} with that name.", ephemeral=True)
    
    event_time = datetime.strptime(row[0], "%Y-%m-%d %H:%M")
    now = datetime.now()
    
    # Calculate difference (Negative = Early, Positive = Late)
    late_seconds = int((now - event_time).total_seconds())
    
    query_db("UPDATE events SET lateness = ?, started = 0 WHERE user_id = ? AND guild_id = ? AND name = ?", 
             (late_seconds, uid, gid, name))
    
    if late_seconds < 0:
        abs_early = abs(late_seconds)
        await interaction.response.send_message(f"🏁 Admin: Force-stopped '{name}' early for {member.mention}. Recorded **-{abs_early//60}m {abs_early%60}s**.")
    else:
        await interaction.response.send_message(f"🛑 Admin: Force-stopped '{name}' for {member.mention}. Lateness: **{late_seconds//60}m {late_seconds%60}s**.")

@admin_menu.command(name="add_record", description="Admin: Add a finished event record")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_add_record(interaction: Interaction, member: discord.Member, name: str, lateness_minutes: int, date_str: str = None):
    if not date_str: date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    query_db("INSERT INTO events (guild_id, user_id, username, name, time, lateness, started) VALUES (?, ?, ?, ?, ?, ?, 0)", 
             (str(interaction.guild.id), str(member.id), str(member), name, date_str, lateness_minutes * 60))
    await interaction.response.send_message(f"✅ Added record for {member.display_name}.")

@admin_menu.command(name="add_schedule", description="Admin: Add schedule for member")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_add_schedule(interaction: Interaction, member: discord.Member, name: str, day: str, time_24h: str):
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day = day.lower().strip()
    if day not in days: return await interaction.response.send_message("❌ Invalid day.")
    query_db("INSERT INTO schedules (guild_id, user_id, username, name, day_of_week, time_24h) VALUES (?, ?, ?, ?, ?, ?)", 
             (str(interaction.guild.id), str(member.id), str(member), name, days.index(day), time_24h))
    await interaction.response.send_message(f"🗓️ Admin set schedule for {member.display_name}")

@admin_menu.command(name="delete_user_schedule", description="Admin: Delete user schedule")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_delete_user_schedule(interaction: Interaction, member: discord.Member, name: str):
    query_db("DELETE FROM schedules WHERE user_id = ? AND guild_id = ? AND name = ?", (str(member.id), str(interaction.guild.id), name))
    await interaction.response.send_message(f" Admin deleted schedule for {member.display_name}")

# --- SYSTEM COMMANDS ---

@admin_menu.command(name="export", description="Export server data to JSON")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_export(interaction: Interaction):
    rows = query_db("SELECT * FROM events WHERE guild_id = ?", (str(interaction.guild.id),))
    data = [{"gid": r[0], "uid": r[1], "user": r[2], "name": r[3], "time": r[4], "late": r[5], "start": r[6]} for r in rows]
    with open(f"export_{interaction.guild.id}.json", "w") as f: json.dump(data, f, indent=4)
    await interaction.response.send_message("✅ Exported!", file=discord.File(f"export_{interaction.guild.id}.json"), ephemeral=True)

@admin_menu.command(name="import", description="Import from JSON string")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_import(interaction: Interaction, json_data: str):
    try:
        data = json.loads(json_data)
        for e in data:
            query_db("INSERT INTO events (guild_id, user_id, username, name, time, lateness, started) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                     (e.get('gid', str(interaction.guild.id)), e['uid'], e.get('user', 'Unknown'), e['name'], e['time'], e['late'], e['start']))
        await interaction.response.send_message("✅ Imported successfully!", ephemeral=True)
    except Exception as ex: await interaction.response.send_message(f"❌ Error: {ex}", ephemeral=True)

# --- LOOPS & AUTOMATION ---

@tasks.loop(seconds=20)
async def auto_check():
    now = datetime.now()
    now_str, day_idx, time_str, date_only = now.strftime("%Y-%m-%d %H:%M"), now.weekday(), now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
    
    recurring = query_db("SELECT guild_id, user_id, username, name FROM schedules WHERE day_of_week = ? AND time_24h = ?", (day_idx, time_str))
    if recurring:
        for gid, uid, uname, name in recurring:
            if not query_db("SELECT name FROM events WHERE guild_id = ? AND user_id = ? AND name = ? AND time LIKE ?", (gid, uid, name, f"{date_only}%"), one=True):
                query_db("INSERT INTO events (guild_id, user_id, username, name, time, lateness, started) VALUES (?, ?, ?, ?, ?, NULL, 1)", (gid, uid, uname, name, now_str))
                try: 
                    user = await bot.fetch_user(int(uid))
                    await user.send(f"⏰ **Scheduled Event Started:** {name}")
                except: pass

    pending = query_db("SELECT user_id, name, guild_id FROM events WHERE time <= ? AND started = 0 AND lateness IS NULL", (now_str,))
    if pending:
        for uid, name, gid in pending:
            query_db("UPDATE events SET started = 1 WHERE user_id = ? AND guild_id = ? AND name = ?", (uid, gid, name))
            try: 
                user = await bot.fetch_user(int(uid))
                await user.send(f"⚠️ **Event Starting Now:** {name}")
            except: pass

@bot.event
async def on_voice_state_update(member, before, after):
    # User joins a voice channel
    if before.channel is None and after.channel is not None:
        gid, uid = str(member.guild.id), str(member.id)
        
        # Look for any event for this user that hasn't been finished yet
        active = query_db("SELECT name, time FROM events WHERE user_id = ? AND guild_id = ? AND lateness IS NULL", (uid, gid))
        
        if active:
            for name, timestamp in active:
                event_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M")
                now = datetime.now()
                late_seconds = int((now - event_time).total_seconds())
                
                # Update the database
                query_db("UPDATE events SET lateness = ?, started = 0 WHERE user_id = ? AND guild_id = ? AND name = ?", 
                         (late_seconds, uid, gid, name))
                
                chan = discord.utils.get(member.guild.text_channels, name="general")
                if chan:
                    if late_seconds < 0:
                        abs_early = abs(late_seconds)
                        await chan.send(f" {member.mention} is early! Saved **-{abs_early//60}m {abs_early%60}s** for **{name}**.")
                    else:
                        await chan.send(f"✅ {member.mention} arrived! Late for **{name}**: {late_seconds//60}m {late_seconds%60}s")

@bot.event
async def on_ready():
    bot.tree.add_command(event_menu)
    bot.tree.add_command(admin_menu)
    await bot.tree.sync()
    if not auto_check.is_running(): auto_check.start()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)