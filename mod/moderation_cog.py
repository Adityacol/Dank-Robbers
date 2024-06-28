import discord
from discord.ext import tasks
from redbot.core import commands, Config, checks
import aiohttp
import logging

logger = logging.getLogger("red.MessageModeration")

class MessageModeration(commands.Cog):
    """Cog for tracking and moderating messages."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.register_defaults()
        self.session = aiohttp.ClientSession()

    def register_defaults(self):
        default_global = {
            "track_channel": None,
            "log_channel": None,
            "api_key": None,
        }
        self.config.register_global(**default_global)

    async def initialize(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):
        self.bot.loop.create_task(self.session.close())

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def set_track_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel to track messages."""
        await self.config.track_channel.set(channel.id)
        await ctx.send(f"Tracking messages in {channel.mention}.")

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel to log moderated messages."""
        await self.config.log_channel.set(channel.id)
        await ctx.send(f"Logging moderated messages in {channel.mention}.")

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def set_api_key(self, ctx, api_key: str):
        """Set the Eden AI API key."""
        await self.config.api_key.set(api_key)
        await ctx.send("API key set.")

    @commands.Cog.listener()
    async def on_message(self, message):
        self.bot.loop.create_task(self.process_message(message))

    async def process_message(self, message):
        track_channel_id = await self.config.track_channel()
        log_channel_id = await self.config.log_channel()
        api_key = await self.config.api_key()

        if not track_channel_id or not log_channel_id or not api_key:
            logger.debug("Track channel, log channel, or API key not set.")
            return

        track_channel = self.bot.get_channel(track_channel_id)
        log_channel = self.bot.get_channel(log_channel_id)

        if not track_channel or not log_channel:
            logger.debug("Track channel or log channel not found.")
            return

        if message.channel.id != track_channel.id or message.author.bot:
            return

        cleaned_content = self.clean_content(message.content)
        if not cleaned_content:
            return

        logger.debug(f"Processing message: {message.content}")
        analysis = await self.analyze_message(cleaned_content, api_key)
        logger.debug(f"Analysis result: {analysis}")
        if analysis.get("flagged"):
            categories = ', '.join([item['category'] for item in analysis['items'] if item['likelihood'] >= 2])
            if categories:
                await log_channel.send(
                    f"Message from {message.author.mention} flagged for moderation:\n"
                    f"Content: {message.content}\n"
                    f"Categories: {categories}"
                )
                await message.delete()

    def clean_content(self, content):
        return ' '.join(word for word in content.split() if not word.startswith(':'))

    async def analyze_message(self, content, api_key):
        url = "https://api.edenai.run/v2/text/moderation"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "providers": ["openai"],
            "language": "en",
            "text": content,
        }

        async with self.session.post(url, headers=headers, json=payload) as response:
            data = await response.json()
            logger.debug(f"API Response: {data}")
            flagged = any(item['likelihood'] >= 2 for item in data['openai']['items'])  # Lower likelihood threshold for leniency
            return {
                "flagged": flagged,
                "items": data['openai']['items']
            }

async def setup(bot):
    cog = MessageModeration(bot)
    await bot.add_cog(cog)
    await cog.initialize()
