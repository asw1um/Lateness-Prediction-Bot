import discord
from discord.ext import commands, tasks
import json
import time
from datetime import datetime

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix = "!", intents = intents)
DATA_FILE = "event_data.dat"

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f);
    except:
        return{};

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent =4)


data = load_data()

def get_user(user_id):  #just in case if we wanna log other latness
    if user_id not in data:
        data[user_id] = {
            "events" : [],
            "lateness"  : []
        }
    return data[user_id]

auto_timers={}
manual_timers={}

#create event with auto start
@bot.tree.command(name = "event_create", description = "Insert event")
async def event_create(
    interaction: discord.Interaction,
    name: str,
    date: str,  
    time_str: str,
    member: discord.Member,
    channel: discord.VoiceChannel = None
):
    user_id = str(member.id)
    user = get_user(user_id)

    datetime_str = f"{date} {time_str}"

    event = {
        "name": name,
        "datetime": datetime_str,
        "channel_id": channel.id if channel else None,
        "lateness": None,
        "started": False
    }

    user["events"].append(event)
    save_data()

    await interaction.response.send_message(
        f"Event '{name}' created for {datetime_str}"+(f" in {channel.name}" if channel else ""), ephemeral = True
    )

#manual start and stop
@bot.tree.command(name = "event_late_start", description="Start lateness stopwatch for IRL event")
async def event_late_start(interaction: discord.Interaction, event_name: str):
    user_id = str(interaction.user.id)

    if user_id in manual_timers:
        await interaction.response.send_message(
            "active timer recording lateness",
            ephemeral = True
        )
        return
    
    manual_timers[user_id] = {
        "start": time.time(),
        "event_name": event_name
    }

    await interaction.response.send_message(
        f"irl latness record created for '{event_name}'",
        ephemeral= True
    )

@bot.tree.command(name = "event_late_stop", description="Stop record for lateness")
async def event_late_stop(interaction: discord.Interaction):
    user_id = str(interaction.user.id)

    if user_id not in manual_timers:
        await interaction.response.send_message(
            "No active recording of latness",
            ephemeral= True
        )
        return
    
    timer = manual_timers.pop(user_id)
    late_seconds = int(time.time() - timer["start"])

    user = get_user(user_id)
    event_found = False
    for e in user["events"]:
        if e["name"] == timer["event_name"] and e["lateness"] is None:
            e["lateness"] = late_seconds
            event_found = True
            break
    if not event_found:
        event = {
            "name": timer["event_name"],
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "channel_id": None,
            "lateness": late_seconds
        }
        user["events"].append(event)

    save_data()

    await interaction.response.send_message(
        f" {interaction.user.mention} is late for {late_seconds} seconds for '{timer['event_name']}'",
        ephemeral=True
    )

@bot.tree.command(name = "event_list", description = "View current events for user")
async def event_list(
    interaction: discord.Interaction,
    member: discord.Member = None
):
    target = member or interaction._user
    user_id = str(target.id)
    user = get_user(user_id)

    if not user["events"]:
        await interaction.response.send_message(
            f"{target.display_name} has no events",
            ephemeral = True
        )
        return
    
    message = f"{target.display_name}'s Events:\n\n"

    for i,event in enumerate(user["events"], start = 1):
        status = "Started" if event.get("started") else "Not started"

        lateness = (
            f"late: {event['lateness']}s"
            if event.get("lateness") is not None
            else "ON time"
        )
        message += (f"{i}. {event["name"]} - {event["datetime"]} "f"({status}, {lateness})\n")

    await interaction.response.send_message(message, ephemeral = True)

#autostart
@tasks.loop(seconds = 30)
async def auto_start_events():
    now = datetime.now()
    
    for user_id, user_data in data.items():
        for event in user_data["events"]:
            if event.get("started"):
                continue
            try:
                event_time = datetime.strptime(event["datetime"], "%Y-%m-%d %H:%M")
            except:
                continue
        if now>= event_time:
            event["started"] = True
            auto_timers[user_id] = {
                "start": time.time(),
                "event_name": event["name"]
            }
            print(f"Auto-started timer for {event['name']} ({user_id})")

    save_data()

@bot.event
async def on_ready():
    await bot.tree.sync()
    auto_start_events.start()
    print(f"Logged in as {bot.user}")

bot.run("TOKEN") #replace when testing bot
