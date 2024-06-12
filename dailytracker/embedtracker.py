import discord
from redbot.core import commands
import re
from datetime import datetime, timedelta

class DailyEmbedTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracked_channel_id = 1000987277234819153  # ID of the channel to track
        self.target_channel_id = 1233422941681877126  # ID of the channel where you want to send the winner ID
        self.bot_user_id = 235148962103951360  # ID of the bot that sends the Rumble Royale messages
        self.payment_role_id = 1230167620972576836  # ID of the role that can confirm payment
        self.loading_emoji = '⌛'  # Loading emoji
        self.thumbs_up_emoji = '👍'  # Thumbs up emoji
        self.sent_embeds = {}  # Dictionary to keep track of sent embeds
        self.daily_rumble_info = {}  # Dictionary to keep track of daily rumble info
        self.rumble_count = 0  # Counter for the number of rumbles done

    @commands.command()
    async def dailyrumble(self, ctx, days: int, quantity: str, donor: str, *, message: str):
        end_date = datetime.utcnow() + timedelta(days=days)
        self.daily_rumble_info[self.tracked_channel_id] = {
            "end_date": end_date,
            "donor": donor,
            "message": message,
            "days": days,
            "quantity": quantity,
            "rumble_count": 0
        }
        await ctx.send(f"Daily Rumble set for {days} days by {donor} donating {quantity}. It will end on {end_date.strftime('%Y-%m-%d %H:%M:%S')} UTC.")

    @commands.command()
    async def clearrumble(self, ctx):
        self.daily_rumble_info = {}
        self.rumble_count = 0
        await ctx.send("All previously fed daily rumbles have been cleared.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id == self.tracked_channel_id and message.author.id == self.bot_user_id:
            if self.tracked_channel_id not in self.daily_rumble_info:
                return  # Ensure dailyrumble command has been run first

            winner_id = self.extract_winner_id(message.content)
            if winner_id:
                self.daily_rumble_info[self.tracked_channel_id]['rumble_count'] += 1
                await self.send_combined_embed(winner_id, message.jump_url, message.created_at, message.channel)

    def extract_winner_id(self, content):
        mention_pattern = r'<@!?(\d+)>'
        match = re.search(mention_pattern, content)
        if match:
            return match.group(1)
        return None

    async def send_combined_embed(self, winner_id, message_url, message_timestamp, channel):
        user = await self.bot.fetch_user(winner_id)
        info = self.daily_rumble_info[self.tracked_channel_id]
        embed = discord.Embed(
            title=f"Congratulations {user.name}! 🎉",
            description=f"You won {info['quantity']} from Daily Rumble! Copy [the link of this message]({message_url}) and follow the directions in https://discord.com/channels/895344237204369458/1036369248945193010 (Claim within 24h of winning!)",
            color=discord.Color.gold(),
            timestamp=message_timestamp
        )
        embed.set_thumbnail(url=user.avatar.url if user.avatar else discord.Embed.Empty)
        embed.add_field(name="Next Daily Rumble", value=f"{info['quantity']} {info['rumble_count']}/{info['days']}\nDonated by\n{info['donor']}")
        embed.set_footer(text="Rumble Royale • Keep on battling!")
        
async def setup(bot):
    await bot.add_cog(DailyEmbedTracker(bot))
