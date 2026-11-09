import json
import os
import asyncio
from threading import Thread
import time
import discord
from discord.ext import commands, tasks
from flask import Flask

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
TOTAL_CHANNELS = 40
DEFAULT_MINUTES = 98
HELPER_ROLE_NAME = "B2 Helper"
DATA_FILE = "b2_monitor_data.json"
# ---------------------

# --- KEEP-ALIVE WEB SERVER ---
app = Flask("")

@app.route("/")
def home():
    return "B2 Subway Monitor Bot is online!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.start()
# -----------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

channel_data = {}
tracker_message_info = {"channel_id": None, "message_id": None}
current_page = 1
data_lock = asyncio.Lock()


def is_helper(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.name == HELPER_ROLE_NAME for role in member.roles)


def save_data_sync():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(
                {
                    "channels": channel_data,
                    "tracker": tracker_message_info,
                    "current_page": current_page,
                },
                f,
            )
    except Exception as e:
        print(f"[save_data] Failed to save: {e}")


async def save_data():
    await asyncio.to_thread(save_data_sync)


def load_data():
    global channel_data, tracker_message_info, current_page
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                channel_data = {int(k): v for k, v in data.get("channels", {}).items()}
                tracker_message_info = data.get(
                    "tracker", {"channel_id": None, "message_id": None}
                )
                current_page = data.get("current_page", 1)
        except Exception as e:
            print(f"[load_data] Failed to load: {e}")


def format_duration(minutes: int) -> str:
    hours = minutes // 60
    rem = minutes % 60
    if hours and rem:
        return f"{hours}h {rem}m"
    if hours:
        return f"{hours}h"
    return f"{rem}m"


def build_embed():
    now = int(time.time())
    embed = discord.Embed(
        title="🚇 B2 Subway Line — Open Channel Monitor",
        description=(
            "Use the **Dropdown** to switch pages (**1–15**, **16–30**, **31–40**).\n"
            "Click **any Channel Button** to update or set its remaining time for anyone!"
        ),
        color=discord.Color.blue(),
    )

    lines_left = []
    lines_right = []

    for ch in range(1, TOTAL_CHANNELS + 1):
        info = channel_data.get(ch)
        if info and info["end_time"] > now:
            entry = (
                f"**Ch {ch}:** <@{info['user_id']}> (Updated by) — expires <t:{info['end_time']}:R>"
            )
        else:
            if ch in channel_data:
                del channel_data[ch]
            entry = f"**Ch {ch}:** 🟢 *Open*"

        if ch <= 20:
            lines_left.append(entry)
        else:
            lines_right.append(entry)

    embed.add_field(
        name="Channels 1 – 20", value="\n".join(lines_left) or "—", inline=True
    )
    embed.add_field(
        name="Channels 21 – 40", value="\n".join(lines_right) or "—", inline=True
    )
    embed.set_footer(text="Anyone can update any channel timer. Timers auto-clear at 0.")
    return embed


async def update_tracker_message():
    await save_data()

    ch_id = tracker_message_info.get("channel_id")
    msg_id = tracker_message_info.get("message_id")
    if not ch_id or not msg_id:
        return

    channel = bot.get_channel(ch_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(msg_id)
        await msg.edit(embed=build_embed(), view=TrackerView(page=current_page))
    except discord.NotFound:
        tracker_message_info["channel_id"] = None
        tracker_message_info["message_id"] = None
        await save_data()
    except discord.HTTPException as e:
        print(f"[update_tracker] HTTPException: {e}")


class SetChannelTimeModal(discord.ui.Modal, title="Update Channel Timer"):
    minutes_input = discord.ui.TextInput(
        label="Minutes Remaining",
        placeholder="e.g., 45 or 98",
        min_length=1,
        max_length=3,
        required=True,
    )

    def __init__(self, ch_num: int):
        super().__init__()
        self.ch_num = ch_num

    async def on_submit(self, interaction: discord.Interaction):
        try:
            mins = int(self.minutes_input.value)
            if mins < 1:
                await interaction.response.send_message(
                    "❌ Please enter a positive number of minutes.", ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number.", ephemeral=True
            )
            return

        async with data_lock:
            channel_data[self.ch_num] = {
                "user_id": interaction.user.id,
                "end_time": int(time.time()) + (mins * 60),
            }

        await interaction.response.send_message(
            f"✅ Updated **Ch {self.ch_num}** timer to **{mins} minutes** ({format_duration(mins)}).",
            ephemeral=True,
        )
        await update_tracker_message()


class ChannelPageSelect(discord.ui.Select):
    def __init__(self, current_page_num: int):
        options = [
            discord.SelectOption(
                label="Channels 1 – 15",
                value="1",
                description="Show buttons for Ch 1 through Ch 15",
                default=(current_page_num == 1),
            ),
            discord.SelectOption(
                label="Channels 16 – 30",
                value="2",
                description="Show buttons for Ch 16 through Ch 30",
                default=(current_page_num == 2),
            ),
            discord.SelectOption(
                label="Channels 31 – 40",
                value="3",
                description="Show buttons for Ch 31 through Ch 40",
                default=(current_page_num == 3),
            ),
        ]
        super().__init__(
            placeholder="Select Channel Group...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="monitor_page_select",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        global current_page
        current_page = int(self.values[0])
        await interaction.response.edit_message(
            embed=build_embed(), view=TrackerView(page=current_page)
        )
        await save_data()


class ChannelButton(discord.ui.Button):
    def __init__(self, ch_num: int, is_taken: bool, row_num: int):
        style = (
            discord.ButtonStyle.secondary
            if is_taken
            else discord.ButtonStyle.success
        )
        super().__init__(
            label=f"Ch {ch_num}",
            style=style,
            custom_id=f"monitor_ch_{ch_num}",
            row=row_num,
        )
        self.ch_num = ch_num

    async def callback(self, interaction: discord.Interaction):
        # Open the modal immediately for any user to set/edit the time
        await interaction.response.send_modal(SetChannelTimeModal(self.ch_num))


class ClearChannelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🗑️ Clear a Channel",
            style=discord.ButtonStyle.danger,
            custom_id="monitor_clear_btn",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ClearChannelModal())


class ClearChannelModal(discord.ui.Modal, title="Clear Channel Timer"):
    ch_input = discord.ui.TextInput(
        label=f"Channel Number to Clear (1 to {TOTAL_CHANNELS})",
        placeholder="e.g., 14",
        min_length=1,
        max_length=2,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ch = int(self.ch_input.value)
            if ch < 1 or ch > TOTAL_CHANNELS:
                await interaction.response.send_message(
                    f"❌ Enter a valid channel between **1** and **{TOTAL_CHANNELS}**.",
                    ephemeral=True,
                )
                return
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number.", ephemeral=True
            )
            return

        cleared = False
        async with data_lock:
            if ch in channel_data:
                del channel_data[ch]
                cleared = True

        if cleared:
            await interaction.response.send_message(
                f"🛡️ Cleared **Ch {ch}** back to open.", ephemeral=True
            )
            await update_tracker_message()
        else:
            await interaction.response.send_message(
                f"ℹ️ **Ch {ch}** is already open.", ephemeral=True
            )


class TrackerView(discord.ui.View):
    def __init__(self, page: int = 1):
        super().__init__(timeout=None)
        now = int(time.time())
        self.add_item(ChannelPageSelect(page))

        start_ch = (page - 1) * 15 + 1
        end_ch = min(page * 15, TOTAL_CHANNELS)

        for idx, ch in enumerate(range(start_ch, end_ch + 1)):
            row_num = 1 + (idx // 5)
            info = channel_data.get(ch)
            is_taken = bool(info and info["end_time"] > now)
            self.add_item(ChannelButton(ch, is_taken, row_num))

        self.add_item(ClearChannelButton())


@tasks.loop(seconds=45)
async def cleanup_expired():
    now = int(time.time())
    expired = []
    async with data_lock:
        expired = [
            ch for ch, info in channel_data.items() if info["end_time"] <= now
        ]
        for ch in expired:
            del channel_data[ch]

    if expired:
        await update_tracker_message()


@bot.event
async def on_ready():
    load_data()
    bot.add_view(TrackerView(page=current_page))
    if not cleanup_expired.is_running():
        cleanup_expired.start()
    print(f"Monitor Bot logged in as {bot.user}!")
    await update_tracker_message()


@bot.command(name="b2monitor")
@commands.has_permissions(manage_messages=True)
async def setup_b2_monitor(ctx):
    await ctx.message.delete()

    old_ch_id = tracker_message_info.get("channel_id")
    old_msg_id = tracker_message_info.get("message_id")
    if old_ch_id and old_msg_id:
        old_channel = bot.get_channel(old_ch_id)
        if old_channel:
            try:
                old_msg = await old_channel.fetch_message(old_msg_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    msg = await ctx.send(embed=build_embed(), view=TrackerView(page=1))
    tracker_message_info["channel_id"] = msg.channel.id
    tracker_message_info["message_id"] = msg.id
    global current_page
    current_page = 1
    await save_data()


if __name__ == "__main__":
    keep_alive()
    bot.run(BOT_TOKEN)