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

# load and save data
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
        data[user_id] = {"events": []}
    return data[user_id]

auto_timers = {} 

class EventGroup(app_commands.Group, name="event"):
    """All personal event commands"""
    pass

class AdminGroup(app_commands.Group, name="admin"):
    """Staff only commands"""
    pass

event_menu = EventGroup()
admin_menu = AdminGroup()

# /events

@event_menu.command(name="create", description="Manual: Set a specific date and time")
async def create(interaction: Interaction, name: str, year: int, month: int, day: int, time_24h: str, member: discord.Member = None):
    target_user = member or interaction.user
    user_data = get_user(str(target_user.id))
    try:
        date_str = f"{year}-{month:02d}-{day:02d}"
        dt_obj = datetime.strptime(f"{date_str} {time_24h}", "%Y-%m-%d %H:%M")
        event = {"name": name, "datetime": dt_obj.strftime("%Y-%m-%d %H:%M"), "lateness": None, "started": False}
        user_data["events"].append(event)
        save_data()
        await interaction.response.send_message(f"✅ Event **{name}** created for {dt_obj.strftime('%Y-%m-%d %H:%M')}", ephemeral=True)
    except:
        await interaction.response.send_message("❌ Invalid format. Use: YYYY MM DD HH:MM", ephemeral=True)

@event_menu.command(name="quick", description="Quick: Start in X minutes from now")
async def quick(interaction: Interaction, name: str, minutes: int = 15, member: discord.Member = None):
    target_user = member or interaction.user
    user_data = get_user(str(target_user.id))
    future_time = datetime.now() + timedelta(minutes=minutes)
    event = {"name": name, "datetime": future_time.strftime("%Y-%m-%d %H:%M"), "lateness": None, "started": False}
    user_data["events"].append(event)
    save_data()
    await interaction.response.send_message(f" **Quick Event:** {name} starts in {minutes}m ({future_time.strftime('%I:%M %p')})", ephemeral=True)

@event_menu.command(name="list", description="List your events and recorded lateness")
async def list_events(interaction: Interaction, member: discord.Member = None):
    target = member or interaction.user
    user = get_user(str(target.id))
    if not user["events"]:
        return await interaction.response.send_message(f"📅 {target.display_name} has no events.", ephemeral=True)

    msg = f"📅 **{target.display_name}'s Events:**\n"
    for i, e in enumerate(user["events"], 1):
        if e.get("lateness") is not None:
            m, s = divmod(e["lateness"], 60)
            status = f"Scheduled: {e['datetime']} \n ✅ **Late: {m}m {s}s**"
        elif e.get("started"):
            status = "Scheduled: {e['datetime']} ⏳ **Ongoing (Clock Running **Late: {m}m {s}s** )**"
        else:
            status = f" Scheduled: {e['datetime']}"
        msg += f"{i}. **{e['name']}** — {status}\n"
    await interaction.response.send_message(msg, ephemeral=True)

@event_menu.command(name="stop", description="Manually stop the active timer")
async def stop(interaction: Interaction, name: str = None):
    user_id = str(interaction.user.id)
    user = get_user(user_id)
    timers = auto_timers.get(user_id, [])
    timer = next((t for t in timers if t["event_name"] == name), timers[0]) if (timers and name) else (timers[0] if timers else None)
    
    target_event = None
    late_sec = 0

    if timer:
        late_sec = int(time.time() - timer["start"])
        target_event = next((e for e in user["events"] if e["name"] == timer["event_name"] and e.get("lateness") is None), None)
        auto_timers[user_id] = [t for t in timers if t != timer]
    else:
        for e in user["events"]:
            if (name is None or e["name"] == name) and e.get("started") and e.get("lateness") is None:
                target_event = e
                start_dt = datetime.strptime(e["datetime"], "%Y-%m-%d %H:%M")
                late_sec = int((datetime.now() - start_dt).total_seconds())
                break

    if not target_event:
        return await interaction.response.send_message("❌ No active event found.", ephemeral=True)

    target_event["lateness"] = late_sec
    save_data()
    m, s = divmod(late_sec, 60)
    await interaction.response.send_message(f"Stopped '{target_event['name']}'. Lateness: {m}m {s}s", ephemeral=True)

@event_menu.command(name="delete", description="Delete one of your events")
async def delete(interaction: Interaction, name: str):
    user = get_user(str(interaction.user.id))
    user["events"] = [e for e in user["events"] if e["name"] != name]
    save_data()
    await interaction.response.send_message(f" Deleted event: '{name}'", ephemeral=True)

@event_menu.command(name="clear", description="Clear all your events")
async def clear(interaction: Interaction):
    data[str(interaction.user.id)]["events"] = []
    save_data()
    await interaction.response.send_message(" All your events have been cleared.", ephemeral=True)

# /admin

@admin_menu.command(name="delete", description="Admin: Delete a specific event for a user")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_delete(interaction: Interaction, member: discord.Member, event_name: str):
    uid = str(member.id)
    if uid in data:
        original_len = len(data[uid]["events"])
        data[uid]["events"] = [e for e in data[uid]["events"] if e["name"] != event_name]
        if len(data[uid]["events"]) < original_len:
            save_data()
            return await interaction.response.send_message(f"✅ Admin: Deleted '{event_name}' for {member.display_name}.", ephemeral=True)
    await interaction.response.send_message(f"❌ Admin: Event '{event_name}' not found for {member.display_name}.", ephemeral=True)

@admin_menu.command(name="clear", description="Admin: Wipe all data for a user")
@app_commands.checks.has_permissions(manage_guild=True)
async def admin_clear(interaction: Interaction, member: discord.Member):
    uid = str(member.id)
    if uid in data:
        data[uid]["events"] = []
        auto_timers.pop(uid, None)
        save_data()
        await interaction.response.send_message(f" Admin: Cleared all data for {member.display_name}", ephemeral=True)
    else:
        await interaction.response.send_message("❌ User has no data.", ephemeral=True)


bot.tree.add_command(event_menu)
bot.tree.add_command(admin_menu)


#auto start
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
                    await user_obj.send(f"⚠️ **{e['name']}** has started! Join Voice.")
            except: continue
    save_data()

#auto stop when joining vc
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
                if chan: await chan.send(f" {member.mention} arrived! Late for **{t['event_name']}**: {late//60}m {late%60}s")
            auto_timers[uid] = []
            save_data()

@bot.event
async def on_ready():
    await bot.tree.sync() 
    if not auto_start_events.is_running():
        auto_start_events.start()
    print(f" {bot.user} is online and fully restored")

bot.run("token")