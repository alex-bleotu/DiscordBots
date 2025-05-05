import os
import re
from dotenv import load_dotenv
import discord
from discord.ext import commands
import yt_dlp
import asyncio

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='.', intents=intents)

song_queues = {}

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
    'options': '-vn'
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
                options=ffmpeg_options['options']
            ),
            data=data
        )

async def ensure_voice(ctx):
    vc = ctx.voice_client
    if not vc or not vc.is_connected():
        if ctx.author.voice and ctx.author.voice.channel:
            return await ctx.author.voice.channel.connect()
        else:
            raise RuntimeError("Voice channel required")
    return vc

def play_next(ctx, vc):
    guild_id = ctx.guild.id
    queue = song_queues.get(guild_id, [])
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

@bot.command(name='play')
async def play(ctx, *, query: str):
    try:
        vc = await ensure_voice(ctx)
        song_queues.setdefault(ctx.guild.id, [])
        if re.match(r'https?://', query):
            search = query
        else:
            search = f"ytsearch1:{query}"
        info = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
        if 'entries' in info:
            info = info['entries'][0]
        video_url = info.get('webpage_url')
        title = info.get('title')
        if vc.is_playing():
            song_queues[ctx.guild.id].append({'query': video_url, 'title': title})
            await ctx.send(f"✅ Added **{title}** to queue.")
        else:
            source = await YTDLSource.from_url(video_url, loop=bot.loop, stream=True)
            vc.play(source, after=lambda e: play_next(ctx, vc))
            await ctx.send(f"▶ Now playing: **{title}**")
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command(name='stop')
async def stop(ctx):
    try:
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.stop()
        song_queues[ctx.guild.id] = []
        await ctx.send("⏹ Stopped playback and cleared queue.")
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command(name='skip')
async def skip(ctx):
    try:
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await ctx.send("⏭ Skipped track.")
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command(name='queue')
async def queue(ctx):
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

@bot.command(name='clear')
async def clear(ctx):
    try:
        song_queues[ctx.guild.id] = []
        await ctx.send("🗑️ Cleared the queue.")
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.command(name='leave')
async def leave(ctx):
    try:
        vc = ctx.voice_client
        if vc and vc.is_connected():
            await vc.disconnect()
        song_queues[ctx.guild.id] = []
        await ctx.send("👋 Left voice channel and cleared queue.")
    except Exception as e:
        print(e)
        await ctx.send("🚫 An error occurred.")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

bot.run(os.getenv('DISCORD_BOT_TOKEN'))