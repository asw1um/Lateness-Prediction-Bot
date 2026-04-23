import discord
from discord.ext import commands, tasks
from discord import app_commands, Interaction
import json
import time
from datetime import datetime, timedelta

# --- SETUP ---
intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True 

bot = commands.Bot(command_prefix="!", intents=intents)
DATA_FILE = "event_data.dat"

# --- DATA PERSISTENCE ---
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

def get_user(user_id):
    if user_id not in data:
        data[user_id] = {"events": [], "lateness": []}
    return data[user_id]

auto_timers = {} 

# --- THE ALL-IN-ONE EVENT COMMAND ---
@bot.tree.command(name="event", description="Main Event Manager")
@app_commands.choices(action=[
    app_commands.Choice(name="Create Manual", value="create"),
    app_commands.Choice(name="Create Quick (Custom Mins)", value="quick"),
    app_commands.Choice(name="List Events & Lateness", value="list"),
    app_commands.Choice(name="Stop Timer", value="stop"),
    app_commands.Choice(name="Delete Event", value="delete"),
    app_commands.Choice(name="Clear All", value="clear")
])
async def event_manager(
    interaction: Interaction, 
    action: str, 
    name: str = None, 
    minutes: int = 15, # Default is 15, but can be changed in UI
    year: int = None,
    month: int = None,
    day: int = None,
    time_24h: str = None,
    member: discord.Member = None
):
    target_user = member or interaction.user
    user_id = str(target_user.id)
    user_data = get_user(user_id)

    # 1. CREATE MANUAL
    if action == "create":
        if not all([name, year, month, day, time_24h]):
            return await interaction.response.send_message("❌ Manual creation needs: name, year, month, day, time_24h", ephemeral=True)
        try:
            date_str = f"{year}-{month:02d}-{day:02d}"
            dt_obj = datetime.strptime(f"{date_str} {time_24h}", "%Y-%m-%d %H:%M")
            event = {"name": name, "datetime": dt_obj.strftime("%Y-%m-%d %H:%M"), "lateness": None, "started": False}
            user_data["events"].append(event)
            save_data()
            await interaction.response.send_message(f"✅ Event **{name}** created for {dt_obj.strftime('%Y-%m-%d %H:%M')}", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Invalid date/time.", ephemeral=True)

    # 2. CREATE QUICK (With custom minutes)
    elif action == "quick":
        if not name:
            return await interaction.response.send_message("❌ Name is required for quick events!", ephemeral=True)
        
        future_time = datetime.now() + timedelta(minutes=minutes)
        event = {"name": name, "datetime": future_time.strftime("%Y-%m-%d %H:%M"), "lateness": None, "started": False}
        user_data["events"].append(event)
        save_data()
        
        await interaction.response.send_message(
            f"⚡ **Quick Event:** {name}\n⏰ **Starts in:** {minutes}m ({future_time.strftime('%I:%M %p')})", 
            ephemeral=True
        )

    # 3. LIST (Now includes detailed lateness)
    elif action == "list":
        if not user_data["events"]:
            return await interaction.response.send_message(f"📅 {target_user.display_name} has no events.", ephemeral=True)
        
        msg = f"📅 **{target_user.display_name}'s Events & Lateness:**\n"
        for i, e in enumerate(user_data["events"], 1):
            if e.get("lateness") is not None:
                m, s = divmod(e["lateness"], 60)
                status = f"✅ **Finished | Late: {m}m {s}s**"
            elif e.get("started"):
                status = "⏳ **Ongoing (Clock Running)**"
            else:
                status = f"📅 Scheduled for {e['datetime']}"
            
            msg += f"{i}. **{e['name']}** — {status}\n"
        
        await interaction.response.send_message(msg, ephemeral=True)

    # 4. STOP
    elif action == "stop":
        timers = auto_timers.get(user_id, [])
        timer = next((t for t in timers if t["event_name"] == name), timers[0]) if (timers and name) else (timers[0] if timers else None)
        
        target_event = None
        late_sec = 0

        if timer:
            late_sec = int(time.time() - timer["start"])
            target_event = next((e for e in user_data["events"] if e["name"] == timer["event_name"] and e.get("lateness") is None), None)
            auto_timers[user_id] = [t for t in timers if t != timer]
        else:
            # Fallback for bot restarts
            for e in user_data["events"]:
                if (name is None or e["name"] == name) and e.get("started") and e.get("lateness") is None:
                    target_event = e
                    start_dt = datetime.strptime(e["datetime"], "%Y-%m-%d %H:%M")
                    late_sec = int((datetime.now() - start_dt).total_seconds())
                    break
        
        if not target_event:
            return await interaction.response.send_message("❌ No active event found to stop.", ephemeral=True)
        
        target_event["lateness"] = late_sec
        save_data()
        m, s = divmod(late_sec, 60)
        await interaction.response.send_message(f"🛑 Stopped '{target_event['name']}'. Recorded lateness: {m}m {s}s", ephemeral=True)

    # 5. DELETE
    elif action == "delete":
        if not name: return await interaction.response.send_message("❌ Provide event name.", ephemeral=True)
        user_data["events"] = [e for e in user_data["events"] if e["name"] != name]
        save_data()
        await interaction.response.send_message(f"Deleted '{name}'.", ephemeral=True)

    # 6. CLEAR ALL
    elif action == "clear":
        user_data["events"] = []
        auto_timers.pop(user_id, None)
        save_data()
        await interaction.response.send_message(f"🧹 Cleared all data for {target_user.display_name}.", ephemeral=True)

# --- ADMIN GROUP ---
class AdminGroup(app_commands.Group, name="admin"):
    """Staff only commands"""
    pass

admin_menu = AdminGroup()
bot.tree.add_command(admin_menu)

@admin_menu.command(name="delete", description="Force delete a specific event for a user")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_delete(interaction: Interaction, member: discord.Member, event_name: str):
    uid = str(member.id)
    if uid in data:
        # Filter out the event by name
        original_count = len(data[uid]["events"])
        data[uid]["events"] = [e for e in data[uid]["events"] if e["name"] != event_name]
        
        if len(data[uid]["events"]) < original_count:
            save_data()
            await interaction.response.send_message(f"✅ Admin: Deleted event '{event_name}' for {member.display_name}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Admin: No event named '{event_name}' found for {member.display_name}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Admin: {member.display_name} has no event data.", ephemeral=True)

@admin_menu.command(name="clear", description="Wipe user data")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_clear(interaction: Interaction, member: discord.Member):
    uid = str(member.id)
    if uid in data:
        data[uid]["events"] = []
        save_data()
        await interaction.response.send_message(f"Admin: Cleared {member.name}", ephemeral=True)

# --- LOOP & VOICE ---
@tasks.loop(seconds=10)
async def auto_start_events():
    now = datetime.now()
    for uid, udata in data.items():
        for e in udata["events"]:
            if e.get("started") or e.get("lateness") is not None: continue
            try:
                if now >= datetime.strptime(e["datetime"], "%Y-%m-%d %H:%M"):
                    e["started"] = True
                    auto_timers.setdefault(uid, []).append({"event_name": e["name"], "start": time.time()})
                    user_obj = await bot.fetch_user(int(uid))
                    await user_obj.send(f"⚠️ **{e['name']}** started! Join VC.")
            except: continue
    save_data()

@bot.event
async def on_voice_state_update(member, before, after):
    uid = str(member.id)
    if before.channel is None and after.channel is not None:
        timers = auto_timers.get(uid, [])
        if timers:
            chan = discord.utils.get(member.guild.text_channels, name="general")
            for t in timers:
                late = int(time.time() - t["start"])
                user = get_user(uid)
                for e in user["events"]:
                    if e["name"] == t["event_name"] and e.get("lateness") is None:
                        e["lateness"] = late
                if chan: await chan.send(f"🏁 {member.mention} joined {after.channel.name}! Late for **{t['event_name']}**: {late//60}m {late%60}s")
            auto_timers[uid] = []
            save_data()

@bot.event
async def on_ready():
    await bot.tree.sync() 
    if not auto_start_events.is_running():
        auto_start_events.start()
    print(f"✅ {bot.user} is online with Choice Menu. Lateness tracking enabled.")

bot.run("token")