import discord
from redbot.core import commands
import re

class EmbedTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tracked_channel_ids = [1186946772568915988, 1186946772568915988, 1166668987032150026, 1167108764617424918, 1166669120977240125]  # IDs of the channels to track
        self.target_channel_id = 1169204863524155453  # ID of the channel where you want to send the winner ID
        self.bot_user_id = 693167035068317736  # ID of the bot that sends the Rumble Royale messages
        self.payment_role_id = 1018578013140566137  # ID of the role that can confirm payment
        self.loading_emoji = '⌛'  # Loading emoji
        self.thumbs_up_emoji = '👍'  # Thumbs up emoji
        self.sent_embeds = {}  # Dictionary to keep track of sent embeds

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id in self.tracked_channel_ids and message.author.id == self.bot_user_id:
            winner_id = self.extract_winner_id(message.content)
            if winner_id:
                quantity = self.get_payout_quantity(message.channel.id)
                await self.send_winner_id(winner_id, quantity, message.jump_url, message.created_at)
                await self.reply_to_tracked_message(message, winner_id, quantity)

    def extract_winner_id(self, content):
        mention_pattern = r'<@!?(\d+)>'
        match = re.search(mention_pattern, content)
        if match:
            return match.group(1)
        return None

    def get_payout_quantity(self, channel_id):
        # Define the payout quantity based on the tracked channel ID
        if channel_id == 1186946772568915988:
            return "1m"
        elif channel_id == 1166668859097501706:
            return "2m"
        elif channel_id == 1166668987032150026:
            return "3m"
        elif channel_id == 1167108764617424918:
            return "5m"
        elif channel_id == 1166669120977240125:
            return "50m"
        else:
            return "Unknown"

    async def send_winner_id(self, winner_id, quantity, message_url, message_timestamp):
        target_channel = self.bot.get_channel(self.target_channel_id)
        if target_channel:
            user = await self.bot.fetch_user(winner_id)
            embed = discord.Embed(
                title=" 🏆 Rumble Royale Winner! ",
                description=f"Congratulations <@{winner_id}>!",
                color=discord.Color.gold(),
                timestamp=message_timestamp
            )
            embed.set_thumbnail(url=user.avatar.url if user.avatar else discord.Embed.Empty)
            embed.add_field(name="Payout Command", value=f"```/serverevents payout user:{winner_id} quantity:{quantity}```")
            embed.set_footer(text="Rumble Royale • Keep on battling!")
            message = await target_channel.send(embed=embed)
            await message.add_reaction(self.loading_emoji)
            self.sent_embeds[message.id] = {"winner_id": winner_id, "payer_id": None}

    async def reply_to_tracked_message(self, message, winner_id, quantity):
        user = await self.bot.fetch_user(winner_id)
        reply_embed = discord.Embed(
            title=" 🎉 Congratulations! ",
            description=f"Congratulations {user.mention} for winning {quantity}!\n\n"
                        "Please turn on your passive or use fake id and padlock.\n\n"
                        "Payouts will be automatic, please wait patiently.",
            color=discord.Color.gold()
        )
        reply_embed.set_footer(text="Rumble Royale • Keep on battling!")
        await message.reply(embed=reply_embed)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.channel_id == self.target_channel_id and str(payload.emoji) == self.loading_emoji:
            message_id = payload.message_id
            if message_id in self.sent_embeds:
                guild = self.bot.get_guild(payload.guild_id)
                member = guild.get_member(payload.user_id)
                if member and discord.utils.get(member.roles, id=self.payment_role_id):
                    await self.process_payment(payload.guild_id, message_id, member.id)
                else:
                    channel = self.bot.get_channel(payload.channel_id)
                    message = await channel.fetch_message(message_id)
                    await message.remove_reaction(self.loading_emoji, member)

    async def process_payment(self, guild_id, message_id, payer_id):
        target_channel = self.bot.get_channel(self.target_channel_id)
        if target_channel:
            embed_info = self.sent_embeds.get(message_id)
            if embed_info:
                winner_id = embed_info["winner_id"]
                payer_user = await self.bot.fetch_user(payer_id)
                winner_user = await self.bot.fetch_user(winner_id)
                embed_message = await target_channel.fetch_message(message_id)
                embed = embed_message.embeds[0]
                embed.title = "Payment Confirmed!"
                embed.description = f"{winner_user.mention} has been paid by {payer_user.mention} for their Rumble Royale victory!"
                embed.remove_field(0)  # Remove the payout command field
                embed.set_footer(text="Rumble Royale • Payment confirmed!")
                await embed_message.edit(embed=embed)
                await embed_message.clear_reaction(self.loading_emoji)
                await embed_message.add_reaction(self.thumbs_up_emoji)
                del self.sent_embeds[message_id]

def setup(bot):
    bot.add_cog(EmbedTracker(bot))
