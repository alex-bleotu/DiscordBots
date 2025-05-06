import os
import re
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands
import yt_dlp

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='.', intents=intents)

song_queues = {}
idle_timers = {}

ytdl_format_options = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}
ffmpeg_options = {
    'executable': os.getenv('FFMPEG_EXEC', 'ffmpeg'),
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(
            discord.FFmpegPCMAudio(
                filename,
                executable=ffmpeg_options['executable'],
                before_options=ffmpeg_options['before_options'],
                options=ffmpeg_options['options']
            ),
            data=data
        )

    @classmethod
    def from_info(cls, info):
        filename = info['url']
        return cls(
            discord.FFmpegPCMAudio(
                filename,
                executable=ffmpeg_options['executable'],
                before_options=ffmpeg_options['before_options'],
                options=ffmpeg_options['options']
            ),
            data=info
        )

async def ensure_voice(ctx):
    vc = ctx.voice_client
    if not vc or not vc.is_connected():
        if ctx.author.voice and ctx.author.voice.channel:
            vc = await ctx.author.voice.channel.connect()
        else:
            raise RuntimeError("Voice channel required")
    cancel_idle_timer(ctx.guild.id)
    return vc

def cancel_idle_timer(guild_id):
    task = idle_timers.pop(guild_id, None)
    if task and not task.done():
        task.cancel()

def schedule_idle_disconnect(ctx):
    guild_id = ctx.guild.id
    cancel_idle_timer(guild_id)
    async def disconnect_if_idle():
        await asyncio.sleep(60)
        vc = ctx.voice_client
        if not vc or not vc.is_playing():
            if vc and vc.is_connected():
                await vc.disconnect()
            song_queues.pop(guild_id, None)
            await ctx.send("👋 Disconnected due to 60s of inactivity.")
    idle_timers[guild_id] = bot.loop.create_task(disconnect_if_idle())

def play_next(ctx, vc):
    guild_id = ctx.guild.id
    queue = song_queues.get(guild_id)
    if queue:
        item = queue.pop(0)
        coro = YTDLSource.from_url(item['query'], loop=bot.loop, stream=True)
        future = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        source = future.result()
        vc.play(source, after=lambda e: play_next(ctx, vc))
        asyncio.run_coroutine_threadsafe(
            ctx.send(f"▶ Now playing: **{item['title']}**"),
            bot.loop
        )
    else:
        schedule_idle_disconnect(ctx)

@bot.command()
async def play(ctx, *, query: str):
    """Play a song or add to queue."""
    try:
        vc = await ensure_voice(ctx)
        song_queues.setdefault(ctx.guild.id, [])
        search = query if re.match(r'https?://', query) else f"ytsearch1:{query}"
        info = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        if 'entries' in info:
            info = info['entries'][0]
        title = info.get('title')
        url = info.get('url') or info.get('webpage_url')

        if vc.is_playing():
            song_queues[ctx.guild.id].append({'query': url, 'title': title})
            await ctx.send(f"✅ Added **{title}** to queue.")
        else:
            source = YTDLSource.from_info(info)
            vc.play(source, after=lambda e: play_next(ctx, vc))
            await ctx.send(f"▶ Now playing: **{title}**")
        cancel_idle_timer(ctx.guild.id)
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@play.error
async def play_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("🚫 Usage: .play <search terms or URL>")

@bot.command()
async def stop(ctx):
    """Stop playback and clear queue."""
    try:
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.stop()
        song_queues.pop(ctx.guild.id, None)
        await ctx.send("⏹ Stopped playback and cleared queue.")
        schedule_idle_disconnect(ctx)
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command()
async def skip(ctx):
    """Skip current track."""
    try:
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await ctx.send("⏭ Skipped track.")
        schedule_idle_disconnect(ctx)
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command()
async def queue(ctx):
    """Show song queue."""
    try:
        q = song_queues.get(ctx.guild.id, [])
        if not q:
            await ctx.send("📭 The queue is empty.")
        else:
            lines = [f"{i+1}. {item['title']}" for i, item in enumerate(q)]
            await ctx.send("🎶 Queue:\n" + "\n".join(lines))
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command()
async def clear(ctx):
    """Clear song queue."""
    try:
        song_queues.pop(ctx.guild.id, None)
        await ctx.send("🗑️ Cleared the queue.")
        schedule_idle_disconnect(ctx)
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command()
async def leave(ctx):
    """Disconnect bot and clear queue."""
    try:
        vc = ctx.voice_client
        if vc and vc.is_connected():
            await vc.disconnect()
        song_queues.pop(ctx.guild.id, None)
        cancel_idle_timer(ctx.guild.id)
        await ctx.send("👋 Left voice channel and cleared queue.")
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

bot.run(os.getenv('DISCORD_BOT_TOKEN'))
