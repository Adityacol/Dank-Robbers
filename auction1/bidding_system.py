import discord
from discord.ui import Button, View
from datetime import datetime

class BiddingSystem:
    def __init__(self, bot, data_handler, notification_system, reputation_system):
        self.bot = bot
        self.data_handler = data_handler
        self.notification_system = notification_system
        self.reputation_system = reputation_system

    async def place_bid(self, interaction: discord.Interaction, amount: int):
        guild_id = interaction.guild_id
        auction_data = await self.data_handler.get_current_auction(guild_id)
        if not auction_data:
            await interaction.response.send_message("No active auction found.", ephemeral=True)
            return

        if amount <= auction_data['current_bid']:
            await interaction.response.send_message(f"Your bid must be higher than the current bid of ${auction_data['current_bid']:,}.", ephemeral=True)
            return

        # Check bidder's reputation
        reputation = await self.reputation_system.get_reputation(interaction.user.id)
        if reputation['score'] < 50:  # Example threshold
            await interaction.response.send_message("Your reputation score is too low to place bids.", ephemeral=True)
            return

        await self.data_handler.update_bid(guild_id, auction_data['id'], interaction.user.id, amount)
        await interaction.response.send_message(f"Your bid of ${amount:,} has been placed!", ephemeral=True)

        # Update auction embed
        channel = self.bot.get_channel(auction_data['channel_id'])
        message = await channel.fetch_message(auction_data['message_id'])
        embed = message.embeds[0]
        embed.set_field_at(1, name="Current Bid", value=f"${amount:,}")
        embed.set_field_at(2, name="Top Bidder", value=interaction.user.mention)
        await message.edit(embed=embed)

        # Notify previous top bidder
        if auction_data['top_bidder']:
            await self.notification_system.notify_outbid(auction_data['top_bidder'], auction_data['id'], amount)

        # Check for auction extension
        if (datetime.utcnow().timestamp() - auction_data['end_time']) <= 60:
            await self.extend_auction(guild_id, auction_data['id'])

    async def get_bid_history(self, guild_id: int, auction_id: int):
        auction_data = await self.data_handler.get_auction(guild_id, auction_id)
        if not auction_data:
            return None

        bid_history = auction_data.get('bid_history', [])
        embed = discord.Embed(title=f"Bid History for Auction #{auction_id}", color=discord.Color.blue())
        
        for idx, bid in enumerate(bid_history, start=1):
            user = self.bot.get_user(bid['user_id'])
            user_name = user.name if user else f"User {bid['user_id']}"
            embed.add_field(name=f"Bid #{idx}", value=f"{user_name}: ${bid['amount']:,}", inline=False)

        return embed

    async def extend_auction(self, guild_id: int, auction_id: int):
        auction_data = await self.data_handler.get_auction(guild_id, auction_id)
        if not auction_data:
            return

        new_end_time = datetime.utcnow().timestamp() + 120  # Extend by 2 minutes
        auction_data['end_time'] = new_end_time
        await self.data_handler.update_auction(guild_id, auction_id, auction_data)

        channel = self.bot.get_channel(auction_data['channel_id'])
        if channel:
            await channel.send("A bid was placed in the last minute! The auction has been extended by 2 minutes.")

    async def check_bid_validity(self, guild_id: int, user_id: int, amount: int):
        settings = await self.data_handler.get_settings(guild_id)
        blacklist_role = settings.get('blacklist_role')
        
        if blacklist_role:
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id)
            if blacklist_role in [role.id for role in member.roles]:
                return False, "You are blacklisted from participating in auctions."

        current_auction = await self.data_handler.get_current_auction(guild_id)
        if not current_auction:
            return False, "There is no active auction at the moment."

        if amount <= current_auction['current_bid']:
            return False, f"Your bid must be higher than the current bid of ${current_auction['current_bid']:,}."

        return True, ""