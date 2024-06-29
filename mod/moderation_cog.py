import discord
from redbot.core import commands, Config, checks
import aiohttp
import asyncio
import logging
import json
from datetime import datetime, timedelta

logger = logging.getLogger("red.MessageModeration")

class MessageModeration(commands.Cog):
    """Cog for tracking and moderating messages."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.register_defaults()
        self.session = None
        self.cache = {}
        self.user_history = {}

    def register_defaults(self):
        default_global = {
            "track_channel": None,
            "log_channel": None,
            "api_key": None,
            "sensitivity": 1.0,
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

    @commands.command()
    @checks.admin_or_permissions(administrator=True)
    async def set_sensitivity(self, ctx, sensitivity: float):
        """Set the sensitivity level for moderation (e.g., 1.0 for default, higher for more lenient)."""
        await self.config.sensitivity.set(sensitivity)
        await ctx.send(f"Sensitivity set to {sensitivity}.")
        logger.info(f"Sensitivity set to {sensitivity}.")

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
        sensitivity = await self.config.sensitivity()

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
            "HateAndExtremism": 0.99998 * sensitivity,
            "HateAndExtremism/threatening": 0.99989 * sensitivity,
            "Harassment": 0.99989 * sensitivity,
            "Harassment/threatening": 0.7 * sensitivity,
            "Violence": 0.999989 * sensitivity,
            "Violence/graphic": 0.999989 * sensitivity,
            "Self-harm": 0.9998989 * sensitivity,
            "Self-harm/intent": 0.99986253432 * sensitivity,
            "Self-harm/instructions": 0.9998263526 * sensitivity,
            "Sexual": 0.99998273522123 * sensitivity,
            "Sexual/minors": 0.5 * sensitivity
        }

        flagged_categories = [
            item['category'] for item in analysis['items']
            if item['likelihood_score'] >= leniency_thresholds.get(item['category'], 1.1 * sensitivity)
        ]

        if flagged_categories:
            user_history = self.user_history.get(message.author.id, [])
            user_history.append((datetime.utcnow(), cleaned_content))
            self.user_history[message.author.id] = user_history

            previous_message = await self.get_previous_message(message)
            previous_message_link = self.get_message_link(previous_message) if previous_message else "No previous message"

            await log_channel.send(
                f"Message from {message.author.mention} flagged for moderation:\n"
                f"Content: {message.content}\n"
                f"Categories: {', '.join(flagged_categories)}\n"
                f"Previous message link: {previous_message_link}"
            )
            await message.delete()

    async def get_previous_message(self, message):
        try:
            history = await message.channel.history(limit=2, before=message).flatten()
            if history:
                return history[0]
        except Exception as e:
            logger.error(f"Failed to fetch previous message: {e}")
        return None

    def get_message_link(self, message):
        return f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"

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
