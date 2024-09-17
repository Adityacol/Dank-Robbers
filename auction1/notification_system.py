import discord

class NotificationSystem:
    def __init__(self, bot, data_handler):
        self.bot = bot
        self.data_handler = data_handler

    async def notify_outbid(self, user_id: int, auction_id: int, new_bid: int):
        user = self.bot.get_user(user_id)
        if user:
            await user.send(f"You've been outbid on Auction #{auction_id}. The new highest bid is ${new_bid:,}.")

    async def notify_auction_start(self, auction_data: dict):
        guild = self.bot.get_guild(auction_data['guild_id'])
        if guild:
            channel = guild.get_channel(await self.data_handler.get_setting(guild.id, 'auction_channel'))
            if channel:
                await channel.send(f"🎉 New auction started! Auction #{auction_data['id']} for {auction_data['quantity']}x {auction_data['item_name']}. Starting bid: ${auction_data['min_bid']:,}")

    async def notify_auction_end(self, auction_data: dict):
        guild = self.bot.get_guild(auction_data['guild_id'])
        if guild:
            channel = guild.get_channel(await self.data_handler.get_setting(guild.id, 'auction_channel'))
            if channel:
                if auction_data['top_bidder']:
                    await channel.send(f"🏁 Auction #{auction_data['id']} has ended. Winner: <@{auction_data['top_bidder']}> with a bid of ${auction_data['current_bid']:,}")
                else:
                    await channel.send(f"🏁 Auction #{auction_data['id']} has ended with no bids.")

    async def notify_auction_cancelled(self, auction_data: dict):
        guild = self.bot.get_guild(auction_data['guild_id'])
        if guild:
            channel = guild.get_channel(await self.data_handler.get_setting(guild.id, 'auction_channel'))
            if channel:
                await channel.send(f"❌ Auction #{auction_data['id']} has been cancelled.")

    async def add_to_watchlist(self, user_id: int, auction_id: int):
        async with self.data_handler.config.member_from_ids(user_id).watchlist() as watchlist:
            if auction_id not in watchlist:
                watchlist.append(auction_id)
                return True
        return False

    async def remove_from_watchlist(self, user_id: int, auction_id: int):
        async with self.data_handler.config.member_from_ids(user_id).watchlist() as watchlist:
            if auction_id in watchlist:
                watchlist.remove(auction_id)
                return True
        return False

    async def get_watchlist(self, user_id: int):
        return await self.data_handler.config.member_from_ids(user_id).watchlist()

    async def notify_watchlist(self, auction_data: dict, event: str):
        guild = self.bot.get_guild(auction_data['guild_id'])
        if not guild:
            return

        async for user_id, user_data in self.data_handler.config.all_members(guild.id):
            if auction_data['id'] in user_data.get('watchlist', []):
                user = guild.get_member(user_id)
                if user:
                    if event == 'bid':
                        await user.send(f"New bid on watched Auction #{auction_data['id']}. Current bid: ${auction_data['current_bid']:,}")
                    elif event == 'end':
                        await user.send(f"Watched Auction #{auction_data['id']} has ended. Final bid: ${auction_data['current_bid']:,}")