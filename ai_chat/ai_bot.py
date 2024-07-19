import discord
from redbot.core import commands, Config
import aiohttp
import random

class AdvancedAIChatBotCog(commands.Cog):
    """Advanced AI Chatbot Cog with mood detection and state management."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, 1234567890)
        self.config.register_global(ai_api_key=None, chat_channel_id=None, conversations={})

    async def send_to_ai(self, prompt: str) -> str:
        """Send a prompt to the AI chatbot API and return the response."""
        api_key = await self.config.ai_api_key()
        if not api_key:
            raise ValueError("API key is not set.")
        
        url = "https://api.edenai.run/v2/text/generation"  # Replace with your actual API URL
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": "gpt-3.5-turbo",  # Replace with your model of choice
            "prompt": prompt,
            "temperature": 0.5
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                response_data = await response.json()
                return response_data.get("text", "Sorry, I couldn't process your request.")

    async def detect_mood(self, message: str) -> str:
        """Detect mood based on the message content."""
        message = message.lower()
        mood_keywords = {
            'happy': ['happy', 'joyful', 'excited', 'delighted'],
            'sad': ['sad', 'depressed', 'unhappy', 'heartbroken'],
            'angry': ['angry', 'frustrated', 'mad', 'irritated'],
            'confused': ['confused', 'puzzled', 'bewildered', 'uncertain'],
            'neutral': ['neutral', 'okay', 'fine', 'normal']
        }
        for mood, keywords in mood_keywords.items():
            if any(keyword in message for keyword in keywords):
                return mood
        return 'neutral'

    async def handle_message(self, message: discord.Message):
        """Handle incoming messages and respond using AI."""
        if message.author.bot:
            return
        
        chat_channel_id = await self.config.chat_channel_id()
        if chat_channel_id is None or message.channel.id != chat_channel_id:
            return

        sender_id = str(message.author.id)
        conversations = await self.config.conversations()

        if sender_id not in conversations:
            conversations[sender_id] = {
                'context': [],
                'mood': 'neutral'
            }
        
        conversation = conversations[sender_id]

        # Detect mood
        mood = await self.detect_mood(message.content)
        conversation['mood'] = mood

        # Add user message to context
        conversation['context'].append({
            'role': 'user',
            'message': message.content
        })

        # Generate AI response
        prompt = '\n'.join(turn['message'] for turn in conversation['context'] if turn['role'] == 'user')
        ai_response = await self.send_to_ai(prompt)
        ai_response = self.generate_mood_response(mood) + "\n" + ai_response

        # Add bot message to context
        conversation['context'].append({
            'role': 'bot',
            'message': ai_response
        })

        # Save updated conversation state
        conversations[sender_id] = conversation
        await self.config.conversations.set(conversations)

        # Send response back to the channel
        await message.channel.send(ai_response)

    def generate_mood_response(self, mood: str) -> str:
        """Generate a mood-based response."""
        mood_responses = {
            'happy': "I'm glad to hear that you're feeling happy!",
            'sad': "I'm sorry to hear that you're feeling sad. Is there anything I can do to help?",
            'angry': "I understand that you're feeling angry. Take a deep breath and let's work through it together.",
            'confused': "I can sense your confusion. Don't worry, I'm here to provide clarity and answers.",
            'neutral': "It seems like you're in a neutral mood. How can I assist you today?",
            'excited': "Wow! Your excitement is contagious. What's got you so thrilled?",
            'grateful': "Expressing gratitude is a beautiful thing. I'm grateful to have this conversation with you.",
            'frustrated': "I can sense your frustration. Let's work together to find a solution.",
            'curious': "Your curiosity is admirable. Feel free to ask me anything you'd like to know.",
            'tired': "I understand that you're feeling tired. Take a break and recharge. I'll be here when you're ready."
        }
        return mood_responses.get(mood, "")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setaiapi(self, ctx: commands.Context, api_key: str):
        """Set the API key for the AI chatbot."""
        await self.config.ai_api_key.set(api_key)
        await ctx.send("API key has been set.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def setchatchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where messages will be sent to the AI chatbot."""
        await self.config.chat_channel_id.set(channel.id)
        await ctx.send(f"Chat channel has been set to {channel.mention}.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Process incoming messages."""
        await self.handle_message(message)

    @commands.Cog.listener()
    async def on_ready(self):
        """Notify when the cog is ready."""
        print("AdvancedAIChatBotCog is ready!")

# Setup function for loading the cog
async def setup(bot):
    await bot.add_cog(AdvancedAIChatBotCog(bot))
