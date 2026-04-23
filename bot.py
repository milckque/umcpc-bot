import random
import re
import aiohttp
import discord
from discord.ext import commands, tasks
from datetime import datetime, time, timedelta, timezone
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

COMMITTEE = [
    {"role": "President",      "name": "Qirui (David) Wang"},
    {"role": "Vice President", "name": "Honey Raut"},
    {"role": "Secretary",      "name": "Yunnuo (Lionel) Liu"},
    {"role": "Treasurer",      "name": "Jummana Shim"},
]

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

def load_meeting() -> dict | None:
    """Load meeting config from disk. Returns None if not set."""
    if not os.path.exists(DATA_FILE):
        return None
    with open(DATA_FILE) as f:
        return json.load(f)


def save_meeting(day: str, time_str: str):
    with open(DATA_FILE, "w") as f:
        json.dump({"day": day, "time": time_str}, f)


def load_strikes() -> dict:
    if not os.path.exists(STRIKES_FILE):
        return {}
    with open(STRIKES_FILE) as f:
        return json.load(f)


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

def parse_ping_time(time_str: str) -> time:
    h, m = map(int, time_str.split(":"))
    tz = pytz.timezone(TIMEZONE)
    utc_offset = datetime.now(tz).utcoffset()
    return time(h, m, tzinfo=timezone(utc_offset))


def next_occurrence(day: str, time_str: str) -> datetime:
    """Return the next datetime (tz-aware) for the given weekday + time."""
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    target_weekday = DAY_MAP[day]
    h, m = map(int, time_str.split(":"))

    days_ahead = (target_weekday - now.weekday()) % 7
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=days_ahead)

    # If that moment has already passed today, push to next week
    if candidate <= now:
        candidate += timedelta(weeks=1)

    return candidate


def restart_loop(day: str, time_str: str):
    """Stop the running loop (if any) and restart it with new timing."""
    if weekly_ping.is_running():
        weekly_ping.cancel()
    weekly_ping.change_interval(time=parse_ping_time(time_str))
    weekly_ping.start()


# ── Background task ────────────────────────────────────────────────────────────

@tasks.loop(hours=24)   # placeholder — overridden on startup / set
async def weekly_ping():
    meeting = load_meeting()
    if meeting is None:
        return

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    if now.weekday() != DAY_MAP.get(meeting["day"], -1):
        return

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"[ERROR] Could not find channel {CHANNEL_ID}")
        return

    role = channel.guild.get_role(ROLE_ID)
    if role is None:
        print(f"[ERROR] Could not find role {ROLE_ID}")
        return

    await channel.send(f"{role.mention} {MESSAGE}")
    print(f"[{now.strftime('%Y-%m-%d %H:%M')}] Pinged @{role.name} in #{channel.name}")


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
        strikes[uid] = strikes.get(uid, 0) + 1
        save_strikes(strikes)

        screep = discord.utils.get(message.guild.emojis, name="screep")
        screep_str = str(screep) if screep else "⚠️"

        if strikes[uid] >= 3:
            await message.channel.send(f"FINAL WARNING {screep_str} {screep_str} {screep_str}")
        else:
            await message.channel.send(f"{screep_str} Strike #{strikes[uid]}")

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
    meeting = load_meeting()
    if meeting:
        restart_loop(meeting["day"], meeting["time"])
        print(f"Resuming weekly ping: {meeting['day'].capitalize()} at {meeting['time']} ({TIMEZONE})")
    else:
        print("No meeting scheduled yet. Use '!cp meeting set' to configure one.")


# ── Commands ───────────────────────────────────────────────────────────────────

@bot.group(name="meeting", invoke_without_command=True)
async def meeting(ctx):
    await ctx.send("Usage: `@segmund meeting reminder` or `@segmund meeting set <day> <HH:MM>`")


@meeting.command(name="reminder")
async def meeting_reminder(ctx):
    """Show when the next meeting ping will fire."""
    data = load_meeting()
    if data is None:
        await ctx.send("❌ No meeting is scheduled yet. Use `@segmund meeting set <day> <HH:MM>` to set one.")
        return

    nxt = next_occurrence(data["day"], data["time"])
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    delta = nxt - now

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
    countdown = " ".join(parts)

    await ctx.send(
        f"📅 Next meeting ping: **{data['day'].capitalize()}** at **{data['time']}** ({TIMEZONE})\n"
        f"⏰ That's in **{countdown}** ({nxt.strftime('%a %d %b %Y, %H:%M')})"
    )


def has_committee_role():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        if any(r.id == COMMITTEE_ROLE_ID for r in ctx.author.roles):
            return True
        raise commands.CheckFailure("You need the @committee role to set the meeting time.")
    return commands.check(predicate)


@meeting.command(name="set")
@has_committee_role()
async def meeting_set(ctx, day: str = None, meeting_time: str = None):
    """Set the weekly meeting ping. Requires @committee role.
    Usage: @segmund meeting set <day> <HH:MM>
    Example: @segmund meeting set monday 18:00
    """
    if day is None or meeting_time is None:
        await ctx.send("Usage: `@segmund meeting set <day> <HH:MM>`\nExample: `@segmund meeting set monday 18:00`")
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

    save_meeting(day, meeting_time)
    restart_loop(day, meeting_time)

    nxt = next_occurrence(day, meeting_time)
    await ctx.send(
        f"✅ Meeting ping set to every **{day.capitalize()}** at **{meeting_time}** ({TIMEZONE}).\n"
        f"Next ping: {nxt.strftime('%a %d %b %Y, %H:%M')}"
    )


@meeting_set.error
async def meeting_set_error(ctx, error):
    await ctx.send(f"❌ {error}")


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
    embed = discord.Embed(title="UMCPC Committee", color=0x1D82B5)
    for member in COMMITTEE:
        embed.add_field(name=member["role"], value=member["name"], inline=False)
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
            "`meeting reminder` — Time until the next meeting ping\n"
            "`meeting set <day> <HH:MM>` — Set the weekly ping *(committee only)*"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔧  Admin",
        value="`testping` — Manually fire the meeting ping *(admin only)*",
        inline=False,
    )

    embed.set_footer(text="umcpc.club  •  @segmund help")
    await ctx.send(embed=embed)


bot.run(TOKEN)