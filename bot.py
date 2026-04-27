import random
import re
import aiohttp
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import os
import json
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
ROLE_ID = int(os.getenv("ROLE_ID"))
TIMEZONE = os.getenv("TIMEZONE", "Australia/Melbourne")
MESSAGE = os.getenv("MESSAGE", "Weekly reminder!")

DATA_FILE = "meetings.json"
CLUB_FILE = "club_info.json"
STRIKES_FILE = "strikes.json"
BAD_WORDS_URL = "https://raw.githubusercontent.com/awdev1/better-profane-words/main/words.json"

_bad_words_pattern: re.Pattern | None = None

COMMITTEE_URL = "https://umcpc.club/api/committee"

COMMITTEE_FALLBACK = {
    "executives": [
        {"title": "President",      "name": "Qirui (David) Wang"},
        {"title": "Vice President", "name": "Honey Raut"},
        {"title": "Secretary",      "name": "Yunnuo (Lionel) Liu"},
        {"title": "Treasurer",      "name": "Jummana Shim"},
    ],
    "general": [],
}

ABOUT_TEXT = (
    "Our club is home to all of the University of Melbourne's competitive programming endeavours! "
    "We aim to impart a strong understanding of algorithms and data structures that are both fun "
    "and key to a successful future in the tech industry!"
)

DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# ── Persistence ────────────────────────────────────────────────────────────────

def load_meetings() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE) as f:
        data = json.load(f)
    # Migrate old single-meeting format
    if isinstance(data, dict):
        return [{"id": 1, "day": data["day"], "time": data["time"], "repeat": True, "role_id": ROLE_ID}]
    return data


def save_meetings(meetings: list):
    with open(DATA_FILE, "w") as f:
        json.dump(meetings, f, indent=2)


def next_meeting_id(meetings: list) -> int:
    return max((m["id"] for m in meetings), default=0) + 1


def load_strikes() -> dict:
    if not os.path.exists(STRIKES_FILE):
        return {}
    with open(STRIKES_FILE) as f:
        data = json.load(f)
    # Migrate old {uid: int} format
    return {
        uid: val if isinstance(val, dict) else {"count": val, "timeout_until": None}
        for uid, val in data.items()
    }


def save_strikes(data: dict):
    with open(STRIKES_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def fetch_bad_words():
    global _bad_words_pattern
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BAD_WORDS_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    words = [entry["word"] for entry in data if "word" in entry]
                    pattern = "|".join(re.escape(w) for w in words)
                    _bad_words_pattern = re.compile(rf"\b({pattern})\b", re.IGNORECASE)
                    print(f"[moderation] Loaded {len(words)} bad words.")
    except Exception as e:
        print(f"[moderation] Failed to fetch bad words: {e}")


def load_club_info() -> dict:
    if not os.path.exists(CLUB_FILE):
        return {"events": [], "sponsors": []}
    with open(CLUB_FILE) as f:
        return json.load(f)


def save_club_info(data: dict):
    with open(CLUB_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Scheduling helpers ─────────────────────────────────────────────────────────

def next_occurrence(day: str, time_str: str) -> datetime:
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    target_weekday = DAY_MAP[day]
    h, m = map(int, time_str.split(":"))

    days_ahead = (target_weekday - now.weekday()) % 7
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=days_ahead)

    if candidate <= now:
        candidate += timedelta(weeks=1)

    return candidate


# ── Background task ────────────────────────────────────────────────────────────

_reminder_day: str | None = None
_sent_reminders: set = set()

@tasks.loop(minutes=1)
async def reminder_loop():
    global _reminder_day, _sent_reminders

    meetings = load_meetings()
    if not meetings:
        return

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")

    if today != _reminder_day:
        _reminder_day = today
        _sent_reminders = set()

    to_remove = []
    for meeting in meetings:
        if now.weekday() != DAY_MAP.get(meeting["day"], -1):
            continue

        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            continue

        role = channel.guild.get_role(meeting.get("role_id", ROLE_ID))
        mention = role.mention if role else ""

        h, m = map(int, meeting["time"].split(":"))
        meeting_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        early_dt = meeting_dt - timedelta(minutes=5)
        mid = meeting["id"]

        if now.hour == early_dt.hour and now.minute == early_dt.minute and f"{mid}_5min" not in _sent_reminders:
            _sent_reminders.add(f"{mid}_5min")
            await channel.send(f"{mention} ⏰ Meeting starts in **5 minutes**!")
            print(f"[{today}] Sent 5-minute warning for meeting {mid}")

        if now.hour == meeting_dt.hour and now.minute == meeting_dt.minute and f"{mid}_now" not in _sent_reminders:
            _sent_reminders.add(f"{mid}_now")
            await channel.send(f"{mention} {MESSAGE}")
            print(f"[{today}] Sent meeting ping for meeting {mid}")
            if not meeting.get("repeat", True):
                to_remove.append(mid)

    if to_remove:
        save_meetings([m for m in meetings if m["id"] not in to_remove])


# ── Bot setup ──────────────────────────────────────────────────────────────────

REACT_USER_IDS = {436283361988837398, 1476536749168787600}
RICHARD_USER_ID = 444756368398876673

# Channels where every message gets reacted with all the listed emojis
REACT_CHANNEL_IDS = {625932784665624606, 1070933255790022747}
REACT_CHANNEL_EMOJI_NAMES = [
    "approval", "disapproval", "ditto", "salute", "segment_tree",
    "segmund", "segmund_cool", "segmund_wow", "umcpc", "bleh", "honest_reaction",
]

WELCOME_CHANNEL_ID = 625922671598764035
WELCOME_EMOJI_NAMES = [
    "approval", "ditto", "salute", "segment_tree",
    "segmund", "segmund_cool", "segmund_wow", "bleh", "honest_reaction",
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # required for on_member_join

HONEY_USER_ID = 813660527791177728
COMMITTEE_ROLE_ID = 625923345942052864

async def get_prefix(bot, message):
    if message.author.id == HONEY_USER_ID and bot.user in message.mentions:
        await message.channel.send("eeeewwwww i hate honey")
    return commands.when_mentioned(bot, message)

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # React to every message in specific channels
    if message.channel.id in REACT_CHANNEL_IDS:
        for name in REACT_CHANNEL_EMOJI_NAMES:
            emoji = discord.utils.get(message.guild.emojis, name=name)
            try:
                if emoji:
                    await message.add_reaction(emoji)
            except discord.NotFound:
                pass

    if message.author.id in REACT_USER_IDS:
        await message.add_reaction("🆗")

    if message.author.id == RICHARD_USER_ID:
        await message.add_reaction("🐈")

    content_lower = message.content.lower()

    if _bad_words_pattern and _bad_words_pattern.search(message.content):
        strikes = load_strikes()
        uid = str(message.author.id)
        entry = strikes.get(uid, {"count": 0, "timeout_until": None})
        entry["count"] += 1
        strikes[uid] = entry
        save_strikes(strikes)

        count = entry["count"]
        screep = discord.utils.get(message.guild.emojis, name="screep")
        screep_str = str(screep) if screep else "⚠️"
        is_committee = any(r.id == COMMITTEE_ROLE_ID for r in message.author.roles)

        if count >= 5 and not is_committee:
            today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d")
            if entry.get("timeout_until") == today:
                await message.channel.send(
                    f"{message.author.mention} has been kicked for repeated offences. {screep_str}"
                )
                try:
                    await message.author.kick(reason="Struck again on the day of unban")
                except discord.Forbidden:
                    pass
            else:
                timeout_until = (datetime.now(pytz.timezone(TIMEZONE)) + timedelta(days=3)).strftime("%Y-%m-%d")
                entry["timeout_until"] = timeout_until
                save_strikes(strikes)
                await message.channel.send(
                    f"{message.author.mention} has been timed out for 3 days. {screep_str} {screep_str} {screep_str}"
                )
                try:
                    await message.author.timeout(timedelta(days=3), reason="5+ strikes for bad words")
                except discord.Forbidden:
                    pass
        elif count == 4:
            await message.channel.send(f"FINAL WARNING {screep_str} {screep_str} {screep_str}")
        else:
            await message.channel.send(f"{screep_str} Strike #{count}")

    if "67" in content_lower or "sixseven" in content_lower or "six seven" in content_lower:
        try:
            await message.add_reaction("6️⃣")
            await message.add_reaction("7️⃣")
        except discord.NotFound:
            pass

    if "cp" in message.content.lower():
        cp = discord.utils.get(message.guild.emojis, name="umcpc")
        try:
            if cp:
                await message.add_reaction(cp)
        except discord.NotFound:
            pass

    if "dw" in message.content.lower():
        dw1 = discord.utils.get(message.guild.emojis, name="dw1")
        dw2 = discord.utils.get(message.guild.emojis, name="dw2")
        try:
            if dw1:
                await message.add_reaction(dw1)
            if dw2:
                await message.add_reaction(dw2)
        except discord.NotFound:
            pass
        await message.channel.send("dw reference")

    if "edge" in message.content.lower():
        await message.channel.send("edge reference")

    if "bryan" in message.content.lower():
        try:
            await message.add_reaction("🆗")
        except discord.NotFound:
            pass

    if "sean" in message.content.lower():
        try:
            await message.add_reaction("🐐")
        except discord.NotFound:
            pass

    if "honey" in message.content.lower():
        try:
            await message.add_reaction("🐝🍯")
        except discord.NotFound:
            pass
        await message.channel.send("honey reference")

    if "mobile" in message.content.lower():
        await message.channel.send("mobile reference")

    if "lion" in message.content.lower():
        try:
            await message.add_reaction("🍇")
        except discord.NotFound:
            pass
        await message.channel.send("lionel reference")

    if "maps" in message.content.lower():
        try:
            await message.add_reaction("👎")
        except discord.NotFound:
            pass
        await message.channel.send("ewwwww maps 🤢")

    if "unsw" in message.content.lower():
        try:
            await message.add_reaction("👎")
        except discord.NotFound:
            pass
        await message.channel.send("ewwwww unsw 🤢")

    if "cissa" in message.content.lower():
        try:
            await message.add_reaction("👎")
        except discord.NotFound:
            pass
        await message.channel.send("ewwwww cisrael 🤢")


    if "richard" in message.content.lower():
        try:
            await message.add_reaction("😼")
        except discord.NotFound:
            pass

    if "seg" in message.content.lower():
        approval = discord.utils.get(message.guild.emojis, name="approval")
        disapproval = discord.utils.get(message.guild.emojis, name="disapproval")
        ditto = discord.utils.get(message.guild.emojis, name="ditto")
        segment_tree = discord.utils.get(message.guild.emojis, name="s_tree")
        salute = discord.utils.get(message.guild.emojis, name="salute")
        segmund = discord.utils.get(message.guild.emojis, name="segmund")
        segmund_cool = discord.utils.get(message.guild.emojis, name="s_cool")
        segmund_wow = discord.utils.get(message.guild.emojis, name="s_wow")

        try:
            if approval:
                await message.add_reaction(approval)
            if disapproval:
                await message.add_reaction(disapproval)
            if ditto:
                await message.add_reaction(ditto)
            if segment_tree:
                await message.add_reaction(segment_tree)
            if salute:
                await message.add_reaction(salute)
            if segmund:
                await message.add_reaction(segmund)
            if segmund_cool:
                await message.add_reaction(segmund_cool)
            if segmund_wow:
                await message.add_reaction(segmund_wow)
        except discord.NotFound:
            pass
        await message.channel.send("segmund reference")

    await bot.process_commands(message)  # still handle commands normally


@bot.event
async def on_member_join(member):
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        return

    emoji_name = random.choice(WELCOME_EMOJI_NAMES)
    emoji = discord.utils.get(member.guild.emojis, name=emoji_name)
    emoji_str = str(emoji) if emoji else "👋"

    await channel.send(f"{member.mention} welcome to UMCPC {emoji_str}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await fetch_bad_words()
    reminder_loop.start()
    meetings = load_meetings()
    print(f"Loaded {len(meetings)} meeting(s).")


# ── Commands ───────────────────────────────────────────────────────────────────

def has_committee_role():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        if any(r.id == COMMITTEE_ROLE_ID for r in ctx.author.roles):
            return True
        raise commands.CheckFailure("You need the @committee role to manage meetings.")
    return commands.check(predicate)


def _countdown(delta: timedelta) -> str:
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


@bot.group(name="meeting", invoke_without_command=True)
async def meeting(ctx):
    await ctx.send(
        "📅 **Meeting commands:**\n"
        "`@segmund meeting add <day> <HH:MM> <yes/no> [@role]` — add a meeting\n"
        "`@segmund meeting remove <id>` — remove a meeting\n"
        "`@segmund meeting list` — list all scheduled meetings\n"
        "`@segmund meeting reminder` — show countdowns to next occurrences"
    )


@meeting.command(name="add")
@has_committee_role()
async def meeting_add(ctx, day: str = None, meeting_time: str = None, repeat: str = "yes", role: discord.Role = None):
    if day is None or meeting_time is None:
        await ctx.send("Usage: `@segmund meeting add <day> <HH:MM> <yes/no> [@role]`")
        return

    day = day.lower()
    if day not in DAY_MAP:
        await ctx.send(f"❌ Invalid day `{day}`. Use: monday, tuesday, wednesday, thursday, friday, saturday, sunday.")
        return

    try:
        h, m = map(int, meeting_time.split(":"))
        assert 0 <= h <= 23 and 0 <= m <= 59
    except (ValueError, AssertionError):
        await ctx.send("❌ Invalid time format. Use 24h `HH:MM`, e.g. `18:00`.")
        return

    if repeat.lower() not in ("yes", "no"):
        await ctx.send("❌ Repeat must be `yes` or `no`.")
        return

    role_id = role.id if role else ROLE_ID
    meetings = load_meetings()
    new_meeting = {
        "id": next_meeting_id(meetings),
        "day": day,
        "time": meeting_time,
        "repeat": repeat.lower() == "yes",
        "role_id": role_id,
    }
    meetings.append(new_meeting)
    save_meetings(meetings)

    role_mention = role.mention if role else f"<@&{ROLE_ID}>"
    repeat_str = "weekly" if new_meeting["repeat"] else "one-time"
    nxt = next_occurrence(day, meeting_time)
    await ctx.send(
        f"✅ Meeting added (ID **{new_meeting['id']}**) — "
        f"**{day.capitalize()}** at **{meeting_time}** ({TIMEZONE}), "
        f"{repeat_str}, pinging {role_mention}\n"
        f"Next ping: {nxt.strftime('%a %d %b %Y, %H:%M')}"
    )


@meeting_add.error
async def meeting_add_error(ctx, error):
    await ctx.send(f"❌ {error}")


@meeting.command(name="remove")
@has_committee_role()
async def meeting_remove(ctx, meeting_id: int = None):
    if meeting_id is None:
        await ctx.send("Usage: `@segmund meeting remove <id>`")
        return

    meetings = load_meetings()
    updated = [m for m in meetings if m["id"] != meeting_id]
    if len(updated) == len(meetings):
        await ctx.send(f"❌ No meeting with ID **{meeting_id}** found.")
        return

    save_meetings(updated)
    await ctx.send(f"✅ Meeting **{meeting_id}** removed.")


@meeting_remove.error
async def meeting_remove_error(ctx, error):
    await ctx.send(f"❌ {error}")


@meeting.command(name="list")
async def meeting_list(ctx):
    meetings = load_meetings()
    if not meetings:
        await ctx.send("No meetings scheduled. Use `@segmund meeting add` to add one.")
        return

    embed = discord.Embed(title="Scheduled Meetings", color=0x1D82B5)
    for m in meetings:
        role_mention = f"<@&{m['role_id']}>"
        repeat_str = "weekly" if m.get("repeat", True) else "one-time"
        embed.add_field(
            name=f"ID {m['id']} — {m['day'].capitalize()} at {m['time']}",
            value=f"{repeat_str} · {role_mention}",
            inline=False,
        )
    await ctx.send(embed=embed)


@meeting.command(name="reminder")
async def meeting_reminder(ctx):
    meetings = load_meetings()
    if not meetings:
        await ctx.send("No meetings scheduled.")
        return

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    embed = discord.Embed(title="Upcoming Meeting Pings", color=0x1D82B5)
    for m in meetings:
        nxt = next_occurrence(m["day"], m["time"])
        delta = nxt - now
        role_mention = f"<@&{m['role_id']}>"
        embed.add_field(
            name=f"ID {m['id']} — {m['day'].capitalize()} at {m['time']}",
            value=f"⏰ In **{_countdown(delta)}** ({nxt.strftime('%a %d %b %Y, %H:%M')})\n{role_mention}",
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.command(name="testping")
@commands.has_permissions(administrator=True)
async def test_ping(ctx):
    """Manually trigger the ping right now (admin only)."""
    channel = bot.get_channel(CHANNEL_ID)
    role = ctx.guild.get_role(ROLE_ID)

    if channel is None:
        await ctx.send(f"❌ Could not find channel with ID `{CHANNEL_ID}`.")
        return
    if role is None:
        await ctx.send(f"❌ Could not find role with ID `{ROLE_ID}`.")
        return

    await ctx.send(f"📣 Sending ping to #{channel.name} for @{role.name}...")
    await channel.send(f"{role.mention} {MESSAGE} *(test ping)*")


@bot.command(name="about")
async def about(ctx):
    """About UMCPC."""
    embed = discord.Embed(title="About UMCPC", description=ABOUT_TEXT, color=0x1D82B5)
    await ctx.send(embed=embed)


@bot.command(name="committee")
async def committee(ctx):
    """List the current committee members."""
    data = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(COMMITTEE_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
    except Exception:
        pass

    if data is None:
        data = COMMITTEE_FALLBACK

    embed = discord.Embed(title="UMCPC Committee", color=0x1D82B5)

    executives = data.get("executives", [])
    if executives:
        embed.add_field(
            name="__Executives__",
            value="\n".join(f"**{m['title']}** — {m['name']}" for m in executives),
            inline=False,
        )

    general = data.get("general", [])
    if general:
        embed.add_field(
            name="__General Committee__",
            value="\n".join(f"**{m['title']}** — {m['name']}" for m in general),
            inline=False,
        )

    await ctx.send(embed=embed)


@bot.command(name="events")
async def events(ctx):
    """List upcoming events."""
    data = load_club_info()
    upcoming = data.get("events", [])
    if not upcoming:
        await ctx.send("No upcoming events at the moment. Check back soon!")
        return
    embed = discord.Embed(title="Upcoming Events", color=0x1D82B5)
    for event in upcoming:
        value = event.get("description", "")
        if event.get("date"):
            value = f"📅 {event['date']}" + (f"\n{value}" if value else "")
        embed.add_field(name=event["name"], value=value or "​", inline=False)
    await ctx.send(embed=embed)


SPONSORS_URL = "https://umcpc.club/sponsors/sponsors.json"
TIER_ORDER = ["Diamond", "Gold", "Silver", "Bronze", "Community Partner"]

@bot.command(name="sponsors")
async def sponsors(ctx):
    """List club sponsors, fetched live from umcpc.club."""
    sponsor_list = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SPONSORS_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    sponsor_list = await resp.json(content_type=None)
    except Exception:
        pass

    if sponsor_list is None:
        data = load_club_info()
        sponsor_list = [
            {"name": s["name"], "tier": s.get("description", ""), "blurb": ""}
            for s in data.get("sponsors", [])
        ]

    if not sponsor_list:
        await ctx.send("No sponsors listed yet.")
        return

    by_tier = {}
    for s in sponsor_list:
        tier = s.get("tier", "Other")
        by_tier.setdefault(tier, []).append(s)

    embed = discord.Embed(
        title="UMCPC Sponsors",
        description="Thank you to all our sponsors! 🎉",
        color=0x1D82B5,
    )
    for tier in TIER_ORDER + [t for t in by_tier if t not in TIER_ORDER]:
        if tier not in by_tier:
            continue
        names = "\n".join(
            f"[{s['name']}]({s['url']})" if s.get("url") else s["name"]
            for s in by_tier[tier]
        )
        embed.add_field(name=f"__{tier}__", value=names, inline=False)

    embed.set_footer(text="umcpc.club/sponsors")
    await ctx.send(embed=embed)


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="Segmund — UMCPC Bot",
        description="Here's what I can do! Use `@segmund <command>`.",
        color=0x1D82B5,
    )

    embed.add_field(
        name="📖  Club Info",
        value=(
            "`about` — What is UMCPC?\n"
            "`committee` — Meet the current committee\n"
            "`events` — Upcoming events\n"
            "`sponsors` — Our sponsors"
        ),
        inline=False,
    )
    embed.add_field(
        name="📅  Meetings",
        value=(
            "`meeting list` — Show all scheduled meetings\n"
            "`meeting reminder` — Countdowns to next pings\n"
            "`meeting add <day> <HH:MM> <yes/no> [@role]` — Add a meeting *(committee only)*\n"
            "`meeting remove <id>` — Remove a meeting *(committee only)*"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔧  Admin",
        value=(
            "`testping` — Manually fire the meeting ping *(admin only)*\n"
            "`strikes` — Show all members with strikes *(committee only)*"
        ),
        inline=False,
    )

    embed.set_footer(text="umcpc.club  •  @segmund help")
    await ctx.send(embed=embed)


@bot.command(name="strikes")
@has_committee_role()
async def strikes_command(ctx):
    """Show all members with strikes."""
    data = load_strikes()
    if not data:
        await ctx.send("No strikes on record.")
        return

    embed = discord.Embed(title="Strike Records", color=0xFF4444)
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    for uid, entry in sorted(data.items(), key=lambda x: x[1]["count"], reverse=True):
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else f"Unknown ({uid})"
        count = entry["count"]
        timeout_until = entry.get("timeout_until")

        if timeout_until and timeout_until >= today:
            status = f"🔇 Timed out until {timeout_until}"
        elif timeout_until:
            status = f"✅ Unban day was {timeout_until}"
        else:
            status = ""

        value = f"**{count}** strike{'s' if count != 1 else ''}"
        if status:
            value += f"\n{status}"
        embed.add_field(name=name, value=value, inline=True)

    await ctx.send(embed=embed)


@strikes_command.error
async def strikes_error(ctx, error):
    await ctx.send(f"❌ {error}")


bot.run(TOKEN)