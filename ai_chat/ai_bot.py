from redbot.core import commands, Config
import discord
import aiohttp
import random

class AdvancedAIChatBotCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.session = aiohttp.ClientSession()
        self.conversations = {}
        
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
        # Ignore messages from bots or messages not in the set channel
        if message.author.bot:
            return

        ai_channel_id = await self.config.guild(message.guild).ai_channel()
        if message.channel.id != ai_channel_id:
            return

        # Process the message
        response = await self.process_message(message.author.id, message.content)
        await message.channel.send(response)

    async def process_message(self, user_id, message):
        # Retrieve or create conversation state for the user
        if user_id not in self.conversations:
            self.conversations[user_id] = {
                'user_id': user_id,
                'context': [],
                'mood': 'neutral',
                'bot_name': 'salmon bhai',
                'developer_name': 'aditya kaushal'
            }
        conversation = self.conversations[user_id]

        # Update conversation context
        conversation['context'].append({'role': 'user', 'message': message})

        # Detect user mood
        mood = self.detect_mood(message)
        conversation['mood'] = mood

        # Generate response
        response = await self.generate_response(conversation)

        # Add bot turn to conversation context
        conversation['context'].append({'role': 'bot', 'message': response})

        # Learn from user interaction (placeholder for actual logic)
        self.learn_from_interaction(conversation)

        return response

    def detect_mood(self, message: str) -> str:
        # Convert the message to lowercase for case-insensitive matching
        message = message.lower()

        # Define keyword lists for different moods
        happy_keywords = ['happy', 'joyful', 'excited', 'delighted']
        sad_keywords = ['sad', 'depressed', 'unhappy', 'heartbroken']
        angry_keywords = ['angry', 'frustrated', 'mad', 'irritated']
        confused_keywords = ['confused', 'puzzled', 'bewildered', 'uncertain']
        neutral_keywords = ['neutral', 'okay', 'fine', 'normal']

        # Check if any of the mood keywords are present in the message
        if any(keyword in message for keyword in happy_keywords):
            return 'happy'
        elif any(keyword in message for keyword in sad_keywords):
            return 'sad'
        elif any(keyword in message for keyword in angry_keywords):
            return 'angry'
        elif any(keyword in message for keyword in confused_keywords):
            return 'confused'
        elif any(keyword in message for keyword in neutral_keywords):
            return 'neutral'
        else:
            return 'neutral'  # Default to neutral mood if no keywords match

    async def generate_response(self, conversation):
        # Get the current user mood
        mood = conversation['mood']

        # Get the mood-based response template
        response_template = self.mood_responses[mood]['template']

        # Generate response from Eden AI
        user_messages = [turn['message'] for turn in conversation['context'] if turn['role'] == 'user']
        prompt = '\n'.join(user_messages)
        response = await self.chat_completion(prompt, str(conversation['user_id']), language='en')

        return response

    async def chat_completion(self, prompt, user_id, language='en'):
        # Replace with your Eden AI service's API call
        # Here we simulate a call with a dummy response
        # Replace the URL and headers with your actual API details
        api_url = "https://api.edenai.run/v1/openai/chat/completions"
        headers = {
            "Authorization": f"Bearer YOUR_EDEN_AI_API_KEY",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "user_id": user_id,
            "language": language
        }
        async with self.session.post(api_url, headers=headers, json=payload) as resp:
            result = await resp.json()
            return result.get("choices", [{}])[0].get("message", "I couldn't generate a response.")

    def learn_from_interaction(self, conversation):
        # TODO: Implement learning logic based on user interaction
        pass

    def generate_savage_reply(self):
        replies = [
            "Oh, did you think I'd get offended by that? Nice try!",
            "You must be a keyboard warrior with that language!",
            "My developer programmed me to ignore bad words. Better luck next time!",
            "Is that the best insult you can come up with? I'm disappointed!",
            "Sorry, I don't speak bad word language. Try again with something creative!", 
            "Oh Really You dumb human you thought you will abuse me you are really dumb "
            "Accha bete baap ko sikah raha hai"
        ]
        return random.choice(replies)
# Setup function for loading the cog
async def setup(bot):
    await bot.add_cog(AdvancedAIChatBotCog(bot))