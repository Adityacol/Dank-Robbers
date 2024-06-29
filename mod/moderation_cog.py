import discord
from redbot.core import commands, Config, checks
from redbot.core.data_manager import basic_config, cog_data_path
import aiohttp
import asyncio
import logging
import json
import os

logger = logging.getLogger("red.MessageModeration")

class MessageModeration(commands.Cog):
    """Cog for tracking and moderating messages."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.register_defaults()
        self.session = None
        self.data_path = cog_data_path(self) / "ai_data.json"
        self.load_data()
        self.storage_limit = 2 * 1024 * 1024 * 1024  # 2GB

    def register_defaults(self):
        default_global = {
            "track_channel": None,
            "log_channel": None,
            "api_key": None,
            "leniency_thresholds": {
                "HateAndExtremism": 1.0,
                "HateAndExtremism/threatening": 0.7,
                "Harassment": 1.0,
                "Harassment/threatening": 0.7,
                "Violence": 0.7,
                "Violence/graphic": 0.7,
                "Self-harm": 0.7,
                "Self-harm/intent": 0.7,
                "Self-harm/instructions": 0.7,
                "Sexual": 1.1,
                "Sexual/minors": 0.5
            }
        }
        self.config.register_global(**default_global)

    def load_data(self):
        if os.path.exists(self.data_path):
            with open(self.data_path, "r") as file:
                self.ai_data = json.load(file)
        else:
            self.ai_data = {"messages": [], "analysis": []}

    def save_data(self):
        with open(self.data_path, "w") as file:
            json.dump(self.ai_data, file)

    async def initialize(self):
        await self.bot.wait_until_ready()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        logger.info("MessageModeration cog initialized.")
        self.bot.loop.create_task(self.periodic_training())

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
                self.store_message(message)
                self.bot.loop.create_task(self.process_message(message, cleaned_content))

    def store_message(self, message):
        """Store the message content in ai_data.json."""
        self.ai_data["messages"].append({
            "id": message.id,
            "author": str(message.author),
            "content": message.content,
            "timestamp": message.created_at.isoformat()
        })
        self.save_data()

        # Check storage size and delete data if it exceeds the limit
        if self.get_storage_size() > self.storage_limit:
            self.delete_data()

    def get_storage_size(self):
        """Calculate the total size of the ai_data.json file."""
        return os.path.getsize(self.data_path)

    def delete_data(self):
        """Delete all data in the ai_data.json file."""
        self.ai_data = {"messages": [], "analysis": []}
        self.save_data()

    async def periodic_training(self):
        """Periodically train the bot and clear data."""
        while True:
            await asyncio.sleep(86400)  # Run once a day
            await self.train_bot()

    async def train_bot(self):
        """Train the bot on stored messages."""
        messages = self.ai_data["messages"]

        # Analyze and adjust thresholds based on messages
        for message in messages:
            analysis = await self.analyze_message(message["content"], await self.config.api_key())
            self.ai_data["analysis"].append(analysis)
            for item in analysis["items"]:
                category = item["category"]
                leniency_thresholds = await self.config.leniency_thresholds()
                if category in leniency_thresholds:
                    leniency_thresholds[category] += 0.01  # Adjust leniency slightly
                    await self.config.leniency_thresholds.set(leniency_thresholds)
                    logger.info(f"Adjusted leniency threshold for {category} to {leniency_thresholds[category]}")

        # Clear data after training
        self.delete_data()

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
