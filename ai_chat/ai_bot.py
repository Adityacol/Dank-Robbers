from redbot.core import commands, Config
import discord
import openai
import random
import requests

# Ensure you set your OpenAI API key in the environment variables
openai.api_key = 'sk-None-TJqi2r1Hg2VXNrJZ2uq4T3BlbkFJyuXKwxzQxYMIcqb61tut'

class AdvancedAIChatBotCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(ai_channel=None)

        # Define mood responses
        self.mood_responses = {
            'happy': {
                'template': "I'm glad to hear that you're feeling happy!",
                'followup': [
                    "Keep spreading the positivity! 😄",
                    "What's making you feel happy today?",
                    "Happiness is contagious. Have a fantastic day! 🌞"
                ]
            },
            'sad': {
                'template': "I'm sorry to hear that you're feeling sad. Is there anything I can do to help?",
                'followup': [
                    "Remember, you're not alone. I'm here to listen.",
                    "Take some time for self-care and do something that brings you joy.",
                    "Sending you virtual hugs. Stay STRONG! 🤗"
                ]
            },
            'angry': {
                'template': "I understand that you're feeling angry. Take a deep breath and let's work through it together.",
                'followup': [
                    "Anger is a natural emotion. Let's find a constructive way to channel it.",
                    "It's okay to be angry. Let's talk it out and find a solution.",
                    "Take a moment to pause and reflect. We'll address the anger together. 😊"
                ]
            },
            'confused': {
                'template': "I can sense your confusion. Don't worry, I'm here to provide clarity and answers.",
                'followup': [
                    "Confusion is an opportunity for growth. Let's explore and find answers together.",
                    "What specifically are you confused about? Let's break it down step by step.",
                    "Curiosity and confusion often go hand in hand. Embrace the journey of discovery! 🚀"
                ]
            },
            'neutral': {
                'template': "It seems like you're in a neutral mood. How can I assist you today?",
                'followup': [
                    "Feel free to ask me anything you'd like to know.",
                    "I'm here to help. What can I do for you?",
                    "Let's make the most of this conversation. How can I make your day better? 😊"
                ]
            },
            'excited': {
                'template': "Wow! Your excitement is contagious. What's got you so thrilled?",
                'followup': [
                    "Your enthusiasm is inspiring. Share your excitement with me!",
                    "I love seeing your excitement. What's the best part about it?",
                    "Embrace the thrill and enjoy the ride! 🎉"
                ]
            },
            'grateful': {
                'template': "Expressing gratitude is a beautiful thing. I'm grateful to have this conversation with you.",
                'followup': [
                    "Gratitude uplifts the spirit. What are you grateful for today?",
                    "Gratefulness brings joy. Share something you're thankful for!",
                    "Your positive outlook is admirable. Keep the gratitude flowing! 🙏"
                ]
            },
            'frustrated': {
                'template': "I can sense your frustration. Let's work together to find a solution.",
                'followup': [
                    "Frustration can be an opportunity for growth. How can I assist you in overcoming your frustrations?",
                    "Let's break down the source of your frustration and brainstorm potential solutions.",
                    "Remember, challenges are stepping stones to success! 💪"
                ]
            },
            'curious': {
                'template': "Your curiosity is admirable. Feel free to ask me anything you'd like to know.",
                'followup': [
                    "Curiosity is the key to learning. What knowledge are you seeking today?",
                    "I'm here to satisfy your curiosity. Ask me any question!",
                    "Keep the curiosity alive. The pursuit of knowledge knows no bounds! 🧠"
                ]
            },
            'tired': {
                'template': "I understand that you're feeling tired. Take a break and recharge. I'll be here when you're ready.",
                'followup': [
                    "Self-care is important. Take some time to relax and rejuvenate.",
                    "Rest is crucial for well-being. Make sure to take care of yourself.",
                    "Remember, a refreshed mind and body perform at their best! 💤"
                ]
            }
        }

    @commands.command()
    async def set_channel_ai(self, ctx, channel: discord.TextChannel):
        """Set the channel for AI interaction."""
        await self.config.guild(ctx.guild).ai_channel.set(channel.id)
        await ctx.send(f"AI interaction channel has been set to {channel.mention}.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        ai_channel_id = await self.config.guild(message.guild).ai_channel()
        if message.channel.id != ai_channel_id:
            return

        # Show typing status while generating the response
        async with message.channel.typing():
            # Process the message and generate a response
            response = await self.process_message(message.author.id, message.content)
            await message.channel.send(response)

    async def process_message(self, user_id, message):
        # Retrieve or create conversation state for the user
        conversation = await self.get_or_create_conversation(user_id)

        # Update conversation context
        conversation['context'].append({'role': 'user', 'message': message})

        # Detect user mood
        mood = self.detect_mood(message)
        conversation['mood'] = mood

        # Generate response
        response = await self.generate_response(conversation)

        # Add bot turn to conversation context
        conversation['context'].append({'role': 'bot', 'message': response})

        # Save conversation state
        await self.save_conversation(user_id, conversation)

        return response

    async def get_or_create_conversation(self, user_id):
        """Retrieve or create conversation state for the user."""
        conversations = await self.config.guild(user_id).conversations()
        if user_id not in conversations:
            conversations[user_id] = {
                'user_id': user_id,
                'context': [],
                'mood': 'neutral'
            }
        return conversations[user_id]

    async def save_conversation(self, user_id, conversation):
        """Save the conversation state."""
        conversations = await self.config.guild(user_id).conversations()
        conversations[user_id] = conversation
        await self.config.guild(user_id).conversations.set(conversations)

    def detect_mood(self, message: str) -> str:
        """Detect the mood from the message."""
        message = message.lower()
        mood_keywords = {
            'happy': ['happy', 'joyful', 'excited', 'delighted'],
            'sad': ['sad', 'depressed', 'unhappy', 'heartbroken'],
            'angry': ['angry', 'frustrated', 'mad', 'irritated'],
            'confused': ['confused', 'baffled', 'perplexed', 'uncertain'],
            'excited': ['excited', 'thrilled', 'enthusiastic', 'eager'],
            'grateful': ['grateful', 'thankful', 'appreciative', 'blessed'],
            'curious': ['curious', 'inquisitive', 'interested', 'intrigued'],
            'tired': ['tired', 'exhausted', 'weary', 'fatigued']
        }
        for mood, keywords in mood_keywords.items():
            if any(keyword in message for keyword in keywords):
                return mood
        return 'neutral'

    async def generate_response(self, conversation):
        """Generate a response based on the conversation and mood."""
        mood = conversation['mood']
        response_template = self.mood_responses[mood]['template']
        followup_response = random.choice(self.mood_responses[mood]['followup'])

        user_messages = [turn['message'] for turn in conversation['context'] if turn['role'] == 'user']
        prompt = '\n'.join(user_messages)
        completion = await self.chat_completion(prompt)

        response = f"{response_template}\n\n{followup_response}\n\n{completion}"
        return response

    async def chat_completion(self, prompt: str) -> str:
        """Call the OpenAI API for chat completion."""
        try:
            response = await self.bot.loop.run_in_executor(None, openai.Completion.create, {
                'engine': 'text-davinci-003',
                'prompt': prompt,
                'max_tokens': 150,
                'temperature': 0.7
            })
            return response.choices[0].text.strip()
        except Exception as e:
            print(f"OpenAI API error: {e}")
            return "Sorry, I encountered an error while generating a response."

    async def get_latest_news(self) -> str:
        """Retrieve the latest news headlines."""
        try:
            response = requests.get('https://newsapi.org/v2/top-headlines', params={
                'apiKey': 'YOUR_NEWS_API_KEY',
                'country': 'us'
            })
            data = response.json()
            headlines = [article['title'] for article in data['articles'][:5]]
            return '\n'.join(headlines) if headlines else "No news available."
        except Exception as e:
            print(f"News API error: {e}")
            return "Sorry, I couldn't fetch the latest news."

# Setup function for loading the cog
async def setup(bot):
    await bot.add_cog(AdvancedAIChatBotCog(bot))
