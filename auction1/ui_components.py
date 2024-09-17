import discord
import asyncio
from datetime import datetime, timedelta
from .ui_components import AuctionEmbed, BiddingButtons

class AuctionManager:
    def __init__(self, bot, data_handler, notification_system, reputation_system):
        self.bot = bot
        self.data_handler = data_handler
        self.notification_system = notification_system
        self.reputation_system = reputation_system
        self.auction_queue = asyncio.Queue()
        self.current_auction = None
        self.bot.loop.create_task(self.auction_loop())

    async def create_auction(self, interaction: discord.Interaction, auction_data: dict):
        if not self.validate_auction_data(auction_data):
            await interaction.response.send_message("Invalid auction data. Please check your inputs.", ephemeral=True)
            return

        channel = await self.create_auction_channel(interaction.guild, auction_data)
        await self.track_donation(channel, interaction.user, auction_data)
        auction_data['channel_id'] = channel.id
        auction_id = await self.data_handler.create_auction(interaction.guild.id, auction_data)
        await self.auction_queue.put(auction_id)

        await interaction.response.send_message(f"Auction created and added to queue. Channel: {channel.mention}", ephemeral=True)

    def validate_auction_data(self, auction_data):
        if auction_data['min_bid'] <= 0 or auction_data['quantity'] <= 0:
            return False
        return True

    async def create_auction_channel(self, guild, auction_data):
        category = discord.utils.get(guild.categories, name="Auctions")
        if not category:
            category = await guild.create_category("Auctions")

        channel = await category.create_text_channel(f"auction-{auction_data['item_name']}")
        await channel.set_permissions(guild.default_role, read_messages=True, send_messages=False)
        return channel

    async def track_donation(self, channel: discord.TextChannel, user: discord.Member, auction_data: dict):
        await channel.send(f"{user.mention} Please confirm your donation of {auction_data['quantity']}x {auction_data['item_name']} by typing 'confirm'.")

        def check(m):
            return m.author == user and m.content.lower() == 'confirm' and m.channel == channel

        try:
            await self.bot.wait_for('message', check=check, timeout=300.0)
        except asyncio.TimeoutError:
            await channel.send("Donation not confirmed. Auction cancelled.")
            await channel.delete()
            return

        await channel.send("Donation confirmed. Auction will start soon.")

    async def auction_loop(self):
        while True:
            if self.current_auction is None and not self.auction_queue.empty():
                auction_id = await self.auction_queue.get()
                self.current_auction = await self.data_handler.get_auction(self.bot.guilds[0].id, auction_id)
                await self.start_auction(self.current_auction)
            await asyncio.sleep(60)

    async def start_auction(self, auction_data):
        channel = self.bot.get_channel(auction_data['channel_id'])
        if channel is None:
            return

        await channel.set_permissions(channel.guild.default_role, send_messages=True)

        embed = AuctionEmbed(auction_data)
        buttons = BiddingButtons(self.bot, self.data_handler)
        message = await channel.send(embed=embed, view=buttons)

        auction_data['message_id'] = message.id
        await self.data_handler.update_auction(channel.guild.id, auction_data['id'], auction_data)

        duration = await self.data_handler.get_setting(channel.guild.id, 'auction_duration')
        end_time = datetime.utcnow() + timedelta(seconds=duration)
        
        while datetime.utcnow() < end_time:
            time_left = (end_time - datetime.utcnow()).total_seconds()
            if time_left <= 60:  # Last minute
                await channel.send("⏰ Less than 1 minute remaining in the auction!")
            elif time_left <= 300:  # Last 5 minutes
                await channel.send("⏰ Less than 5 minutes remaining in the auction!")
            
            await asyncio.sleep(60)  # Check every minute

            # Check for last-minute bids and extend if necessary
            if time_left <= 60 and await self.check_recent_bids(auction_data['id']):
                end_time += timedelta(minutes=2)
                await channel.send("🕒 A bid was placed in the last minute! Auction extended by 2 minutes.")

        await self.end_auction(channel, message, auction_data)

    async def check_recent_bids(self, auction_id):
        auction = await self.data_handler.get_auction(self.bot.guilds[0].id, auction_id)
        if not auction['bid_history']:
            return False
        last_bid_time = auction['bid_history'][-1]['timestamp']
        return (datetime.utcnow() - last_bid_time).total_seconds() <= 60

    async def end_auction(self, channel, message, auction_data):
        await channel.set_permissions(channel.guild.default_role, send_messages=False)

        winner_id = auction_data['top_bidder']
        if winner_id:
            winner = channel.guild.get_member(winner_id)
            await channel.set_permissions(winner, send_messages=True)
            await self.process_winner(channel, winner, auction_data)
        else:
            await self.cancel_auction(channel, auction_data)

        self.current_auction = None

    async def process_winner(self, channel, winner, auction_data):
        await channel.send(f"Congratulations {winner.mention}! You've won the auction for {auction_data['quantity']}x {auction_data['item_name']} with a bid of ${auction_data['current_bid']:,}.")
        await channel.send("Please confirm your payment by typing 'pay'.")

        def check(m):
            return m.author == winner and m.content.lower() == 'pay' and m.channel == channel

        try:
            await self.bot.wait_for('message', check=check, timeout=180.0)
        except asyncio.TimeoutError:
            await self.handle_non_payment(channel, winner, auction_data)
            return

        await self.complete_auction(channel, winner, auction_data)

    async def handle_non_payment(self, channel, user, auction_data):
        await self.reputation_system.decrease_reputation(user.id, reason="Non-payment")
        blacklist_role_id = await self.data_handler.get_setting(channel.guild.id, 'blacklist_role')
        blacklist_role = channel.guild.get_role(blacklist_role_id)
        if blacklist_role:
            await user.add_roles(blacklist_role)

        await channel.send(f"{user.mention} has been penalized for failing to pay.")
        await self.move_to_next_bidder(channel, auction_data)

    async def move_to_next_bidder(self, channel, auction_data):
        bid_history = auction_data['bid_history']
        if len(bid_history) < 2:
            await self.cancel_auction(channel, auction_data)
            return

        next_bidder_id = bid_history[-2]['user_id']
        next_bidder = channel.guild.get_member(next_bidder_id)
        auction_data['top_bidder'] = next_bidder_id
        auction_data['current_bid'] = bid_history[-2]['amount']

        await self.data_handler.update_auction(channel.guild.id, auction_data['id'], auction_data)
        await self.process_winner(channel, next_bidder, auction_data)

    async def complete_auction(self, channel, winner, auction_data):
        await self.reputation_system.increase_reputation(winner.id, reason="Successful auction purchase")
        await self.reputation_system.increase_reputation(auction_data['creator_id'], reason="Successful auction sale")

        payout_channel_id = await self.data_handler.get_setting(channel.guild.id, 'payout_channel')
        payout_channel = self.bot.get_channel(payout_channel_id)

        if payout_channel:
            await payout_channel.send(f"Payout for Auction #{auction_data['id']}:\n"
                                      f"Winner: {winner.mention}\n"
                                      f"Item: {auction_data['quantity']}x {auction_data['item_name']}\n"
                                      f"Amount: ${auction_data['current_bid']:,}")

        log_channel_id = await self.data_handler.get_setting(channel.guild.id, 'log_channel')
        log_channel = self.bot.get_channel(log_channel_id)

        if log_channel:
            await log_channel.send(f"Auction #{auction_data['id']} completed successfully.\n"
                                   f"Item: {auction_data['quantity']}x {auction_data['item_name']}\n"
                                   f"Winner: {winner.mention}\n"
                                   f"Final Bid: ${auction_data['current_bid']:,}")

        await channel.send("Thank you for participating in this auction!")
        await self.notification_system.notify_auction_end(auction_data)
        await asyncio.sleep(60)
        await channel.delete()

    async def cancel_auction(self, channel, auction_data):
        await channel.send("The auction has been cancelled due to lack of bids.")
        
        log_channel_id = await self.data_handler.get_setting(channel.guild.id, 'log_channel')
        log_channel = self.bot.get_channel(log_channel_id)

        if log_channel:
            await log_channel.send(f"Auction #{auction_data['id']} cancelled.\n"
                                   f"Item: {auction_data['quantity']}x {auction_data['item_name']}\n"
                                   f"Reason: No bids received")

        await self.notification_system.notify_auction_cancelled(auction_data)
        await asyncio.sleep(60)
        await channel.delete()

    async def extend_auction(self, auction_id: int, extension_time: int):
        auction = await self.data_handler.get_auction(self.bot.guilds[0].id, auction_id)
        if not auction or auction['status'] != 'active':
            return False

        new_end_time = datetime.utcnow() + timedelta(seconds=extension_time)
        auction['end_time'] = new_end_time.timestamp()
        await self.data_handler.update_auction(self.bot.guilds[0].id, auction_id, auction)

        channel = self.bot.get_channel(auction['channel_id'])
        if channel:
            await channel.send(f"The auction has been extended by {extension_time // 60} minutes.")

        return True

    async def warn_participants(self, auction_id: int, warning_message: str):
        auction = await self.data_handler.get_auction(self.bot.guilds[0].id, auction_id)
        if not auction:
            return False

        channel = self.bot.get_channel(auction['channel_id'])
        if channel:
            await channel.send(f"⚠️ Warning: {warning_message}")

        return True