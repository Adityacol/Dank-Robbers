import discord
from redbot.core import commands, Config, checks
from redbot.core.data_manager import basic_config, cog_data_path
import aiohttp
import asyncio
import logging
import json
import os
import re
import joblib
from datetime import datetime, timedelta
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

logger = logging.getLogger("red.MessageModeration")

class MessageModeration(commands.Cog):
    """Cog for tracking and moderating messages."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.register_defaults()
        self.session = None
        self.data_path = cog_data_path(self) / "ai_data.json"
        self.model_path = cog_data_path(self) / "moderation_model.pkl"
        self.load_data()
        self.load_model()
        self.storage_limit = 2 * 1024 * 1024 * 1024  # 2GB
        self.vectorizer = TfidfVectorizer()

    def register_defaults(self):
        default_global = {
            "track_channel": None,
            "log_channel": None,
            "api_key": None,
            "leniency_thresholds": {
                "HateAndExtremism": 0.5,
                "HateAndExtremism/threatening": 0.4,
                "Harassment": 0.8,
                "Harassment/threatening": 0.4,
                "Violence": 0.4,
                "Violence/graphic": 0.4,
                "Self-harm": 0.4,
                "Self-harm/intent": 0.4,
                "Self-harm/instructions": 0.4,
                "Sexual": 0.8,
                "Sexual/minors": 0.2
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

    def load_model(self):
        if os.path.exists(self.model_path):
            self.model = joblib.load(self.model_path)
        else:
            self.model = None

    def save_model(self):
        if self.model:
            joblib.dump(self.model, self.model_path)

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

        if not messages:
            return

        # Preprocess messages
        contents = [self.clean_content(msg["content"]) for msg in messages]
        labels = [msg.get("flagged", False) for msg in messages]

        if contents and labels:
            # Train a simple logistic regression model
            X = self.vectorizer.fit_transform(contents)
            self.model = LogisticRegression()
            self.model.fit(X, labels)
            self.save_model()
            logger.info("Trained new moderation model.")

        # Clear data after training
        self.delete_data()

    def clean_content(self, content):
        # Ignore words that start and end with ':' (typically emojis)
        content = re.sub(r'\s*:\w+:\s*', ' ', content)
        return content

    async def process_message(self, message, cleaned_content):
        log_channel_id = await self.config.log_channel()
        api_key = await self.config.api_key()
        leniency_thresholds = await self.config.leniency_thresholds()

        if not log_channel_id or not api_key:
            logger.error("Log channel or API key not set.")
            return

        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            logger.error("Log channel not found.")
            return

        analysis = await self.analyze_message(cleaned_content, api_key)
        if analysis.get("flagged"):
            categories = [
                item["category"] for item in analysis["items"]
                if item["likelihood_score"] >= leniency_thresholds.get(item["category"], 1.0)
            ]
            if categories:
                await self.moderate_message(message, categories, log_channel)
        else:
            if self.model:
                X = self.vectorizer.transform([cleaned_content])
                prediction = self.model.predict(X)
                if prediction:
                    await self.moderate_message(message, ["Model Prediction"], log_channel)

    async def moderate_message(self, message, categories, log_channel):
        previous_message = await self.get_previous_message(message)
        previous_message_link = self.get_message_link(previous_message) if previous_message else "No previous message"

        await log_channel.send(
            f"Message from {message.author.mention} flagged for moderation:\n"
            f"Content: {message.content}\n"
            f"Categories: {', '.join(categories)}\n"
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
