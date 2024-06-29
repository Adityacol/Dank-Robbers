import discord
from redbot.core import commands, Config, checks
import aiohttp
import asyncio
import logging
import json

logger = logging.getLogger("red.MessageModeration")

class MessageModeration(commands.Cog):
    """Cog for tracking and moderating messages."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.register_defaults()
        self.session = None
        self.cache = {}

    def register_defaults(self):
        default_global = {
            "track_channel": None,
            "log_channel": None,
            "api_key": None,
        }
        self.config.register_global(**default_global)

    async def initialize(self):
        await self.bot.wait_until_ready()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        logger.info("MessageModeration cog initialized.")

    def cog_unload(self):
        if self.session:
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
        logger.info("API key set.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        track_channel_id = await self.config.track_channel()
        if message.channel.id == track_channel_id:
            cleaned_content = self.clean_content(message.content)
            if cleaned_content:
                self.bot.loop.create_task(self.process_message(message, cleaned_content))

    async def process_message(self, message, cleaned_content):
        log_channel_id = await self.config.log_channel()
        api_key = await self.config.api_key()

        if not log_channel_id or not api_key:
            logger.error("Log channel or API key not set.")
            return

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            logger.error("Log channel not found.")
            return

        analysis = self.cache.get(cleaned_content)
        if not analysis:
            analysis = await self.analyze_message(cleaned_content, api_key)
            self.cache[cleaned_content] = analysis

        leniency_thresholds = {
            "HateAndExtremism": 1.1,
            "HateAndExtremism/threatening": 1.1,
            "Harassment": 1.1,
            "Harassment/threatening": 1.1,
            "Violence": 1.1,
            "Violence/graphic": 1.1,
            "Self-harm": 1.1,
            "Self-harm/intent": 1.1,
            "Self-harm/instructions": 1.1,
            "Sexual": 1.1,
            "Sexual/minors": 1.1
        }

        flagged_categories = [
            item['category'] for item in analysis['items']
            if item['likelihood_score'] >= leniency_thresholds.get(item['category'], 1.1)
        ]

        if flagged_categories:
            await log_channel.send(
                f"Message from {message.author.mention} flagged for moderation:\n"
                f"Content: {message.content}\n"
                f"Categories: {', '.join(flagged_categories)}"
            )
            await message.delete()

    def clean_content(self, content):
        # Ignore words that start and end with ':' (typically emojis)
        return ' '.join(word for word in content.split() if not (word.startswith(':') and word.endswith(':')))

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

        for _ in range(3):  # Retry up to 3 times
            try:
                async with self.session.post(url, headers=headers, json=payload) as response:
                    data = await response.json()
                    logger.debug(f"API Response: {data}")
                    if 'openai' in data and isinstance(data['openai']['items'], list) and data['openai']['items']:
                        return {'flagged': True, 'items': data['openai']['items']}
                    return {'flagged': False, 'items': []}
            except aiohttp.ClientError as e:
                logger.error(f"HTTP request failed: {e}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode JSON response: {e}")
            except asyncio.TimeoutError:
                logger.error("Request timed out.")
            except RuntimeError as e:
                logger.error(f"Runtime error: {e}")

        return {'flagged': False, 'items': []}  # Return a default value if all retries fail

async def setup(bot):
    cog = MessageModeration(bot)
    await bot.add_cog(cog)
    await cog.initialize()
