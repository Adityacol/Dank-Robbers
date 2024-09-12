import discord
from discord import app_commands
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import aiohttp
import asyncio
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, Any, List, Union
import json
import io
import csv
import random
import math

log = logging.getLogger("red.economy.AdvancedAuction")

class AuctionScheduleView(discord.ui.View):
    """
    A view for scheduling auctions.
    This view provides buttons for users to choose whether they want to schedule an auction or not.
    """
    def __init__(self, cog: "EnhancedAdvancedAuction"):
        super().__init__(timeout=300)
        self.cog = cog
        self.schedule_time: Optional[datetime] = None
        self.message = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def schedule_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handler for the 'Yes' button. Opens a modal for auction scheduling."""
        modal = self.ScheduleModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.schedule_time = modal.schedule_time
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def schedule_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handler for the 'No' button. Informs the user that the auction will be queued immediately."""
        await interaction.response.send_message("Auction will be queued immediately.", ephemeral=True)
        self.stop()

    class ScheduleModal(discord.ui.Modal, title="Schedule Auction"):
        """
        A modal for inputting the scheduled time for an auction.
        """
        def __init__(self):
            super().__init__()
            self.schedule_time: Optional[datetime] = None

        time_input = discord.ui.TextInput(
            label="Enter time (YYYY-MM-DD HH:MM)",
            placeholder="e.g. 2024-09-15 14:30",
            required=True,
            style=discord.TextStyle.short
        )

        async def on_submit(self, interaction: discord.Interaction):
            """Handles the submission of the scheduling modal."""
            try:
                self.schedule_time = datetime.strptime(self.time_input.value, "%Y-%m-%d %H:%M")
                if self.schedule_time < datetime.utcnow():
                    await interaction.response.send_message("Scheduled time must be in the future.", ephemeral=True)
                    self.schedule_time = None
                else:
                    await interaction.response.send_message(f"Auction scheduled for {self.schedule_time}", ephemeral=True)
            except ValueError:
                await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD HH:MM", ephemeral=True)
                self.schedule_time = None

class EnhancedAdvancedAuction(commands.Cog):
    """
    A comprehensive auction system for Discord servers.
    This cog provides functionality for creating, managing, and participating in auctions.
    """
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        default_guild: Dict[str, Any] = {
            "auctions": {},
            "bids": {},
            "auction_channel": None,
            "log_channel": None,
            "queue_channel": None,
            "auction_role": None,
            "blacklist_role": None,
            "auction_ping_role": None,
            "massive_auction_ping_role": None,
            "auction_queue": [],
            "scheduled_auctions": {},
            "user_stats": {},
            "categories": ["General", "Rare", "Limited Edition", "Exclusive", "Event"],
            "leaderboard": {},
            "max_active_auctions": 10,
            "auction_cooldown": 86400,
            "featured_auction": None,
            "banned_users": [],
            "auction_moderators": [],
            "minimum_bid_increment": 1000,
            "auction_extension_time": 300,
            "auction_tiers": {
                "standard": {"min_value": 0, "duration": 6 * 3600},
                "premium": {"min_value": 100000000, "duration": 12 * 3600},
                "exclusive": {"min_value": 500000000, "duration": 24 * 3600}
            },
            "donation_tracking": {},
            "bid_confirmations": {},
            "auction_history": [],
            "global_auction_settings": {
                "max_auction_duration": 7 * 24 * 3600,  # 7 days
                "min_auction_duration": 1 * 3600,  # 1 hour
                "max_auctions_per_user": 3,
                "bidding_cooldown": 30,  # 30 seconds between bids
                "snipe_protection_time": 300,  # 5 minutes
            },
        }
        
        default_member: Dict[str, Any] = {
            "auction_reminders": [],
            "auction_subscriptions": [],
            "auto_bids": {},
            "notification_settings": {
                "outbid": True,
                "auction_start": True,
                "auction_end": True,
                "won_auction": True,
            },
            "last_bid_time": {},
            "auction_history": [],
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.active_auctions: Dict[str, asyncio.Task] = {}
        self.auction_tasks: Dict[str, asyncio.Task] = {}
        self.queue_lock = asyncio.Lock()
        self.queue_task: Optional[asyncio.Task] = None
        self.donation_locks: Dict[str, asyncio.Lock] = {}
        self.bid_locks: Dict[str, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        """
        Initializes the cog when it's loaded.
        Starts the queue manager task.
        """
        self.queue_task = asyncio.create_task(self.enhanced_queue_manager())

    async def cog_unload(self) -> None:
        """
        Cleans up when the cog is unloaded.
        Cancels all running tasks to ensure a clean shutdown.
        """
        if self.queue_task:
            self.queue_task.cancel()
        for task in self.auction_tasks.values():
            task.cancel()
        for task in self.active_auctions.values():
            task.cancel()

    async def enhanced_queue_manager(self) -> None:
        """
        Manages the auction queue and related tasks.
        This method runs continuously, processing the queue, checking auction tiers, and handling auto-bids.
        """
        while True:
            try:
                await self.process_queue()
                await self.check_auction_tiers()
                await self.process_auto_bids()
                await self.check_ending_auctions()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in enhanced queue manager: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def process_queue(self) -> None:
        """
        Processes the auction queue.
        Starts scheduled auctions and balances auction start times.
        """
        async with self.queue_lock:
            for guild in self.bot.guilds:
                scheduled_auctions = await self.config.guild(guild).scheduled_auctions()
                current_time = int(datetime.utcnow().timestamp())
                
                for auction_id, scheduled_time in list(scheduled_auctions.items()):
                    if current_time >= scheduled_time:
                        await self.start_scheduled_auction(guild, auction_id)
                        del scheduled_auctions[auction_id]
                
                await self.config.guild(guild).scheduled_auctions.set(scheduled_auctions)
                
                queue = await self.config.guild(guild).auction_queue()
                if queue:
                    await self.balance_auction_start_times(guild, queue)

    async def balance_auction_start_times(self, guild: discord.Guild, queue: List[str]) -> None:
        """
        Balances the start times of auctions in the queue to prevent overlaps.
        
        Args:
            guild (discord.Guild): The guild for which to balance auctions.
            queue (List[str]): The list of auction IDs in the queue.
        """
        auctions = await self.config.guild(guild).auctions()
        current_time = int(datetime.utcnow().timestamp())
        last_end_time = current_time

        for auction_id in queue:
            auction = auctions.get(auction_id)
            if auction and auction['status'] == 'pending':
                auction['start_time'] = last_end_time + 300  # 5-minute gap between auctions
                auction['end_time'] = auction['start_time'] + self.get_auction_duration(guild, auction)
                last_end_time = auction['end_time']
                auctions[auction_id] = auction

        await self.config.guild(guild).auctions.set(auctions)

    def get_auction_duration(self, guild: discord.Guild, auction: Dict[str, Any]) -> int:
        """
        Determines the duration of an auction based on its tier.
        
        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction (Dict[str, Any]): The auction data.
        
        Returns:
            int: The duration of the auction in seconds.
        """
        tiers = self.config.guild(guild).auction_tiers()
        for tier, details in reversed(sorted(tiers.items(), key=lambda x: x[1]['min_value'])):
            if auction['total_value'] >= details['min_value']:
                return details['duration']
        return 6 * 3600  # Default to 6 hours if no tier matches

    async def check_auction_tiers(self) -> None:
        """
        Checks and updates the tiers of all active auctions.
        This method is called periodically to ensure auctions are in the correct tier based on their current value.
        """
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in auctions.items():
                    if auction['status'] == 'active':
                        await self.update_auction_tier(guild, auction)
                        auctions[auction_id] = auction

    async def update_auction_tier(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        """
        Updates the tier of an auction based on its current value.
        
        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction (Dict[str, Any]): The auction data to update.
        """
        tiers = await self.config.guild(guild).auction_tiers()
        current_value = auction['current_bid']
        new_tier = max((tier for tier, details in tiers.items() if current_value >= details['min_value']), key=lambda t: tiers[t]['min_value'])
        
        if new_tier != auction.get('tier'):
            auction['tier'] = new_tier
            new_duration = tiers[new_tier]['duration']
            time_left = auction['end_time'] - int(datetime.utcnow().timestamp())
            if new_duration > time_left:
                auction['end_time'] = int(datetime.utcnow().timestamp()) + new_duration
                await self.notify_tier_change(guild, auction, new_tier)

    async def notify_tier_change(self, guild: discord.Guild, auction: Dict[str, Any], new_tier: str) -> None:
        """
        Notifies users about a change in an auction's tier.
        
        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction (Dict[str, Any]): The auction data.
            new_tier (str): The new tier of the auction.
        """
        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(f"Auction {auction['auction_id']} has been upgraded to {new_tier} tier! The auction duration has been extended.")

    async def process_auto_bids(self) -> None:
        """
        Processes auto-bids for all active auctions.
        This method is called periodically to handle automatic bidding.
        """
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in auctions.items():
                    if auction['status'] == 'active':
                        await self.process_auto_bids_for_auction(guild, auction)
                        auctions[auction_id] = auction

    async def process_auto_bids_for_auction(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        """
        Processes auto-bids for a specific auction.
        
        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction (Dict[str, Any]): The auction data.
        """
        auto_bids = await self.config.guild(guild).auto_bids()
        auction_auto_bids = auto_bids.get(auction['auction_id'], {})
        
        if not auction_auto_bids:
            return

        current_bid = auction['current_bid']
        min_increment = await self.config.guild(guild).minimum_bid_increment()

        for user_id, max_bid in sorted(auction_auto_bids.items(), key=lambda x: x[1], reverse=True):
            if int(user_id) != auction['current_bidder'] and max_bid > current_bid + min_increment:
                new_bid = min(max_bid, current_bid + min_increment)
                await self.place_bid(guild, auction, int(user_id), new_bid, is_auto_bid=True)
                current_bid = new_bid

    async def place_bid(self, guild: discord.Guild, auction: Dict[str, Any], user_id: int, bid_amount: int, is_auto_bid: bool = False) -> None:
        """
        Places a bid on an auction.
        
        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction (Dict[str, Any]): The auction data.
            user_id (int): The ID of the user placing the bid.
            bid_amount (int): The amount of the bid.
            is_auto_bid (bool): Whether this is an automatic bid.
        """
        if not self.bid_locks.get(auction['auction_id']):
            self.bid_locks[auction['auction_id']] = asyncio.Lock()
        async with self.bid_locks[auction['auction_id']]:
            if bid_amount <= auction['current_bid']:
                return

            # Check if the user is on cooldown
            user_last_bid_time = await self.config.member_from_ids(guild.id, user_id).last_bid_time()
            current_time = int(datetime.utcnow().timestamp())
            cooldown = await self.config.guild(guild).global_auction_settings.bidding_cooldown()
            
            if user_last_bid_time.get(auction['auction_id'], 0) + cooldown > current_time:
                remaining_cooldown = user_last_bid_time[auction['auction_id']] + cooldown - current_time
                raise commands.UserFeedbackCheckFailure(f"You're on cooldown. Please wait {remaining_cooldown} seconds before bidding again.")

            auction['current_bid'] = bid_amount
            auction['current_bidder'] = user_id
            auction['bid_history'].append({
                'user_id': user_id,
                'amount': bid_amount,
                'timestamp': current_time,
                'is_auto_bid': is_auto_bid
            })

            # Update user's last bid time
            async with self.config.member_from_ids(guild.id, user_id).last_bid_time() as last_bid_time:
                last_bid_time[auction['auction_id']] = current_time

            channel_id = await self.config.guild(guild).auction_channel()
            channel = self.bot.get_channel(channel_id)
            if channel:
                user = guild.get_member(user_id)
                await channel.send(f"New {'auto-' if is_auto_bid else ''}bid of {bid_amount:,} by {user.mention if user else 'Unknown User'}")

            # Check for auction extension
            extension_time = await self.config.guild(guild).auction_extension_time()
            if current_time + extension_time > auction['end_time']:
                auction['end_time'] = current_time + extension_time
                if channel:
                    await channel.send(f"Auction extended by {extension_time // 60} minutes due to last-minute bid!")

            # Notify outbid users
            await self.notify_outbid_users(guild, auction, user_id, bid_amount)

            # Update auction in config
            async with self.config.guild(guild).auctions() as auctions:
                auctions[auction['auction_id']] = auction

    async def notify_outbid_users(self, guild: discord.Guild, auction: Dict[str, Any], new_bidder_id: int, new_bid_amount: int) -> None:
        """
        Notifies users who have been outbid in an auction.

        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction (Dict[str, Any]): The auction data.
            new_bidder_id (int): The ID of the user who placed the new highest bid.
            new_bid_amount (int): The amount of the new highest bid.
        """
        outbid_users = set(bid['user_id'] for bid in auction['bid_history'] if bid['user_id'] != new_bidder_id)
        for user_id in outbid_users:
            user = guild.get_member(user_id)
            if user:
                try:
                    user_settings = await self.config.member(user).notification_settings()
                    if user_settings['outbid']:
                        await user.send(f"You've been outbid on auction {auction['auction_id']} for {auction['amount']}x {auction['item']}. The new highest bid is {new_bid_amount:,}.")
                except discord.HTTPException:
                    pass  # Unable to send DM to the user

    async def check_ending_auctions(self) -> None:
        """
        Checks for auctions that are about to end and handles the ending process.
        This method is called periodically to manage auction closings.
        """
        current_time = int(datetime.utcnow().timestamp())
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in list(auctions.items()):
                    if auction['status'] == 'active' and auction['end_time'] <= current_time:
                        await self.end_auction(guild, auction_id)
                        del auctions[auction_id]

    async def end_auction(self, guild: discord.Guild, auction_id: str) -> None:
        """
        Ends an auction and handles the post-auction process.

        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction_id (str): The ID of the auction to end.
        """
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        
        if auction['current_bidder']:
            winner = guild.get_member(auction['current_bidder'])
            winning_bid = auction['current_bid']
            
            if channel:
                await channel.send(f"Auction {auction_id} has ended! The winner is {winner.mention} with a bid of {winning_bid:,}.")
            
            # Handle item transfer and payment
            await self.handle_auction_completion(guild, auction, winner, winning_bid)
        else:
            if channel:
                await channel.send(f"Auction {auction_id} has ended with no bids.")

        # Update auction history
        await self.update_auction_history(guild, auction)

        # Remove from active auctions
        if auction_id in self.active_auctions:
            self.active_auctions[auction_id].cancel()
            del self.active_auctions[auction_id]

        # Start next auction in queue
        await self.start_next_auction(guild)

    async def handle_auction_completion(self, guild: discord.Guild, auction: Dict[str, Any], winner: discord.Member, winning_bid: int) -> None:
        """
        Handles the completion of an auction, including item transfer and payment.

        Args:
            guild (discord.Guild): The guild where the auction took place.
            auction (Dict[str, Any]): The auction data.
            winner (discord.Member): The member who won the auction.
            winning_bid (int): The winning bid amount.
        """
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = self.bot.get_channel(log_channel_id)

        if log_channel:
            await log_channel.send(f"Auction {auction['auction_id']} completed. Winner: {winner.mention}, Amount: {winning_bid:,}")
            await log_channel.send(f"/serverevents payout user:{winner.id} quantity:{auction['amount']} item:{auction['item']}")
            await log_channel.send(f"/serverevents payout user:{auction['user_id']} quantity:{winning_bid}")

        # Update user statistics
        await self.update_user_stats(guild, winner.id, winning_bid, 'won')
        await self.update_user_stats(guild, auction['user_id'], winning_bid, 'sold')

        # Notify the winner
        try:
            await winner.send(f"Congratulations! You won the auction for {auction['amount']}x {auction['item']} with a bid of {winning_bid:,}. The item will be delivered to you shortly.")
        except discord.HTTPException:
            pass  # Unable to send DM to the winner

    async def update_auction_history(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        """
        Updates the auction history with the completed auction data.

        Args:
            guild (discord.Guild): The guild where the auction took place.
            auction (Dict[str, Any]): The completed auction data.
        """
        async with self.config.guild(guild).auction_history() as history:
            history.append({
                'auction_id': auction['auction_id'],
                'item': auction['item'],
                'amount': auction['amount'],
                'start_time': auction['start_time'],
                'end_time': auction['end_time'],
                'winner': auction['current_bidder'],
                'winning_bid': auction['current_bid'],
                'seller': auction['user_id']
            })

    async def start_next_auction(self, guild: discord.Guild) -> None:
        """
        Starts the next auction in the queue.

        Args:
            guild (discord.Guild): The guild where to start the next auction.
        """
        async with self.config.guild(guild).auction_queue() as queue:
            if queue:
                next_auction_id = queue.pop(0)
                await self.start_auction(guild, next_auction_id)

    async def start_auction(self, guild: discord.Guild, auction_id: str) -> None:
        """
        Starts an auction.

        Args:
            guild (discord.Guild): The guild where to start the auction.
            auction_id (str): The ID of the auction to start.
        """
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

        auction['status'] = 'active'
        auction['start_time'] = int(datetime.utcnow().timestamp())
        auction['end_time'] = auction['start_time'] + self.get_auction_duration(guild, auction)

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)

        if channel:
            embed = discord.Embed(title="New Auction Started!", color=discord.Color.green())
            embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=False)
            embed.add_field(name="Starting Bid", value=f"{auction['min_bid']:,}", inline=True)
            embed.add_field(name="Ends At", value=f"<t:{auction['end_time']}:F>", inline=True)
            await channel.send(embed=embed)

        # Notify subscribers
        await self.notify_subscribers(guild, auction)

        # Schedule auction end
        self.active_auctions[auction_id] = asyncio.create_task(self.schedule_auction_end(guild, auction_id, auction['end_time'] - auction['start_time']))

    async def schedule_auction_end(self, guild: discord.Guild, auction_id: str, duration: int) -> None:
        """
        Schedules the end of an auction.

        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction_id (str): The ID of the auction.
            duration (int): The duration of the auction in seconds.
        """
        await asyncio.sleep(duration)
        await self.end_auction(guild, auction_id)

    async def notify_subscribers(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        """
        Notifies subscribers about a new auction.

        Args:
            guild (discord.Guild): The guild where the auction is starting.
            auction (Dict[str, Any]): The auction data.
        """
        for member in guild.members:
            async with self.config.member(member).auction_subscriptions() as subscriptions:
                if 'all' in subscriptions or auction['category'] in subscriptions:
                    try:
                        user_settings = await self.config.member(member).notification_settings()
                        if user_settings['auction_start']:
                            await member.send(f"New auction started: {auction['amount']}x {auction['item']} in the {auction['category']} category.")
                    except discord.HTTPException:
                        pass  # Unable to send DM to the user

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionset(self, ctx: commands.Context):
        """Configure the auction system."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @auctionset.command(name="auctionchannels")
    async def set_auction_channels(self, ctx: commands.Context, auction: discord.TextChannel, queue: discord.TextChannel, log: discord.TextChannel):
        """
        Set the channels for auctions, queue, and logging.

        Args:
            ctx (commands.Context): The command context.
            auction (discord.TextChannel): The channel for active auctions.
            queue (discord.TextChannel): The channel for the auction queue.
            log (discord.TextChannel): The channel for auction logs.
        """
        await self.config.guild(ctx.guild).auction_channel.set(auction.id)
        await self.config.guild(ctx.guild).queue_channel.set(queue.id)
        await self.config.guild(ctx.guild).log_channel.set(log.id)
        await ctx.send(f"Auction channel set to {auction.mention}, queue channel set to {queue.mention}, and log channel set to {log.mention}.")

    @auctionset.command(name="auctionrole")
    async def set_auction_role(self, ctx: commands.Context, role: discord.Role):
        """
        Set the role to be assigned to users when they open an auction channel.

        Args:
            ctx (commands.Context): The command context.
            role (discord.Role): The role to assign to auction participants.
        """
        await self.config.guild(ctx.guild).auction_role.set(role.id)
        await ctx.send(f"Auction role set to {role.name}.")

    @auctionset.command(name="tiers")
    async def set_auction_tiers(self, ctx: commands.Context, tier: str, min_value: int, duration: int):
        """
        Set or update an auction tier.

        Args:
            ctx (commands.Context): The command context.
            tier (str): The name of the tier.
            min_value (int): The minimum value for an auction to be in this tier.
            duration (int): The duration of auctions in this tier (in hours).
        """
        async with self.config.guild(ctx.guild).auction_tiers() as tiers:
            tiers[tier] = {"min_value": min_value, "duration": duration * 3600}
        await ctx.send(f"Auction tier {tier} set with minimum value {min_value} and duration {duration} hours.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def spawnauction(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """
        Spawn the auction request embed with button in the specified channel or the current channel.

        Args:
            ctx (commands.Context): The command context.
            channel (Optional[discord.TextChannel]): The channel to spawn the auction request in. Defaults to the current channel.
        """
        channel = channel or ctx.channel
        view = self.AuctionView(self)
        embed = discord.Embed(
            title="🎉 Request an Auction 🎉",
            description="Click the button below to request an auction and submit your donation details.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. Await further instructions in your private channel.", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        message = await channel.send(embed=embed, view=view)
        view.message = message
        await ctx.send(f"Auction request embed spawned in {channel.mention}")

    class AuctionView(View):
        """
        A view for the auction request button.
        """
        def __init__(self, cog: "EnhancedAdvancedAuction"):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green)
        async def request_auction_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """
            Handler for the auction request button.
            Opens the auction request modal when clicked.
            """
            try:
                async with self.cog.config.guild(interaction.guild).banned_users() as banned_users:
                    if interaction.user.id in banned_users:
                        await interaction.response.send_message("You are banned from participating in auctions.", ephemeral=True)
                        return

                modal = self.cog.AuctionModal(self.cog)
                await interaction.response.send_modal(modal)
            except Exception as e:
                log.error(f"An error occurred while sending the modal: {e}")
                await interaction.followup.send(f"An error occurred while sending the modal: {str(e)}", ephemeral=True)

    class AuctionModal(discord.ui.Modal, title="Request An Auction"):
        """
        A modal for submitting auction requests.
        """
        def __init__(self, cog: "EnhancedAdvancedAuction"):
            super().__init__()
            self.cog = cog

        item_name = TextInput(label="What are you going to donate?", placeholder="e.g., Blob", required=True, min_length=1, max_length=100)
        item_count = TextInput(label="How many of those items will you donate?", placeholder="e.g., 5", required=True, max_length=10)
        minimum_bid = TextInput(label="What should the minimum bid be?", placeholder="e.g., 1,000,000", required=False)
        message = TextInput(label="What is your message?", placeholder="e.g., I love DR!", required=False, max_length=200)
        category = TextInput(label="Category", placeholder="e.g., Rare", required=False)

        async def on_submit(self, interaction: discord.Interaction):
            """
            Handles the submission of the auction request modal.
            """
            try:
                log.info(f"Auction modal submitted by {interaction.user.name}")
                item_name = self.item_name.value
                item_count = self.item_count.value
                min_bid = self.minimum_bid.value or "1,000,000"
                message = self.message.value
                category = self.category.value or "General"

                log.info(f"Submitted values: item={item_name}, count={item_count}, min_bid={min_bid}, category={category}")

                await interaction.response.send_message("Processing your auction request...", ephemeral=True)

                view = AuctionScheduleView(self.cog)
                await interaction.followup.send("Would you like to schedule this auction?", view=view, ephemeral=True)
                await view.wait()

                await self.cog.process_auction_request(interaction, item_name, item_count, min_bid, message, category, view.schedule_time)

            except Exception as e:
                log.error(f"An error occurred in modal submission: {e}", exc_info=True)
                await interaction.followup.send(f"An error occurred while processing your submission. Please try again or contact an administrator.", ephemeral=True)

    async def process_auction_request(self, interaction: discord.Interaction, item_name: str, item_count: str, min_bid: str, message: str, category: str, schedule_time: Optional[datetime]):
        """
        Processes an auction request submitted through the modal.

        Args:
            interaction (discord.Interaction): The interaction that triggered the request.
            item_name (str): The name of the item being auctioned.
            item_count (str): The quantity of the item.
            min_bid (str): The minimum bid for the auction.
            message (str): Any additional message from the requester.
            category (str): The category of the auction.
            schedule_time (Optional[datetime]): The scheduled time for the auction, if any.
        """
        try:
            item_count = int(item_count)
            if item_count <= 0:
                raise ValueError("Item count must be positive")
        except ValueError as e:
            await interaction.followup.send(f"Invalid item count: {e}", ephemeral=True)
            return

        item_value, total_value, tax = await self.api_check(interaction, item_count, item_name)
        if not item_value:
            return

        guild = interaction.guild
        auction_id = await self.get_next_auction_id(guild)

        auction_data = {
            "auction_id": auction_id,
            "guild_id": guild.id,
            "user_id": interaction.user.id,
            "item": item_name,
            "amount": item_count,
            "min_bid": min_bid,
            "message": message,
            "category": category,
            "status": "pending",
            "start_time": int(datetime.utcnow().timestamp()),
            "end_time": int((datetime.utcnow() + timedelta(hours=6)).timestamp()),
            "item_value": item_value,
            "total_value": total_value,
            "tax": tax,
            "donated_amount": 0,
            "donated_tax": 0,
            "current_bid": int(min_bid.replace(',', '')),
            "current_bidder": None,
            "bid_history": [],
            "auto_bids": {},
        }

        if schedule_time:
            auction_data['scheduled_time'] = int(schedule_time.timestamp())
            auction_data['status'] = 'scheduled'
            async with self.config.guild(guild).scheduled_auctions() as scheduled:
                scheduled[auction_id] = auction_data['scheduled_time']
            await interaction.followup.send(f"Auction scheduled for {schedule_time}")
        else:
            await self.finalize_auction_request(guild, auction_data)

        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction_id] = auction_data

        embed = discord.Embed(
            title="Your Auction Details",
            description=f"Please donate {item_count} of {item_name} as you have mentioned in the modal or you will get blacklisted.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Item", value=f"{item_count}x {item_name}", inline=False)
        embed.add_field(name="Minimum Bid", value=min_bid, inline=True)
        embed.add_field(name="Market Price (each)", value=f"{item_value:,}", inline=True)
        embed.add_field(name="Total Value", value=f"{total_value:,}", inline=True)
        embed.add_field(name="Tax (10%)", value=f"{tax:,}", inline=True)
        if schedule_time:
            embed.add_field(name="Scheduled Time", value=f"<t:{int(schedule_time.timestamp())}:F>", inline=False)
        else:
            embed.add_field(name="Channel closes in", value="6 hours", inline=True)
        embed.set_footer(text="This channel will be deleted after 6 hours.")

        view = self.AuctionControlView(self, auction_id)
        await interaction.followup.send(content=interaction.user.mention, embed=embed, view=view)

        # Assign the auction role
        auction_role_id = await self.config.guild(guild).auction_role()
        if auction_role_id:
            auction_role = guild.get_role(auction_role_id)
            if auction_role:
                await interaction.user.add_roles(auction_role)

        # Schedule the auction end
        self.auction_tasks[auction_id] = self.bot.loop.create_task(self.schedule_auction_end(guild, auction_id, 21600))  # 6 hours

    async def api_check(self, interaction: discord.Interaction, item_count: int, item_name: str) -> tuple:
        """
        Checks the item value using an external API.

        Args:
            interaction (discord.Interaction): The interaction that triggered the request.
            item_count (int): The quantity of the item.
            item_name (str): The name of the item.

        Returns:
            tuple: A tuple containing the item value, total value, and tax, or (None, None, None) if the check fails.
        """
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await interaction.followup.send("Error fetching item value from API. Please try again later.", ephemeral=True)
                        log.error(f"API response status: {response.status}")
                        return None, None, None
                    
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    
                    if not item_data:
                        await interaction.followup.send("Item not found. Please enter a valid item name.", ephemeral=True)
                        return None, None, None
                    
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    tax = total_value * 0.10  # 10% tax
                    
                    if total_value < 50_000_000:  # 50 million
                        await interaction.followup.send("The total donation value must be over 50 million.", ephemeral=True)
                        return None, None, None

                    return item_value, total_value, tax

            except aiohttp.ClientError as e:
                await interaction.followup.send(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                log.error(f"API check error: {e}", exc_info=True)
                return None, None, None

    async def get_next_auction_id(self, guild: discord.Guild) -> str:
        """
        Generates the next available auction ID for a guild.

        Args:
            guild (discord.Guild): The guild for which to generate the auction ID.

        Returns:
            str: The next available auction ID.
        """
        async with self.config.guild(guild).auctions() as auctions:
            existing_ids = [int(aid.split('-')[1]) for aid in auctions.keys() if '-' in aid]
            next_id = max(existing_ids, default=0) + 1
            return f"{guild.id}-{next_id}"

    class AuctionControlView(View):
        """
        A view for controlling an active auction.
        """
        def __init__(self, cog: "EnhancedAdvancedAuction", auction_id: str):
            super().__init__(timeout=None)
            self.cog = cog
            self.auction_id = auction_id

        @discord.ui.button(label="Close Auction", style=discord.ButtonStyle.danger)
        async def close_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Handles the closing of an auction."""
            await self.cog.close_auction(interaction, self.auction_id, "User closed the auction")

        @discord.ui.button(label="Pause Auction", style=discord.ButtonStyle.secondary)
        async def pause_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Handles the pausing of an auction."""
            await self.cog.pause_auction(interaction, self.auction_id)

        @discord.ui.button(label="Resume Auction", style=discord.ButtonStyle.success)
        async def resume_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Handles the resuming of a paused auction."""
            await self.cog.resume_auction(interaction, self.auction_id)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            """Checks if the user has permission to interact with the auction controls."""
            user = interaction.user
            async with self.cog.config.guild(interaction.guild).auctions() as auctions:
                auction = auctions.get(self.auction_id)
                if not auction:
                    await interaction.response.send_message("This auction no longer exists.", ephemeral=True)
                    return False
                is_owner = auction["user_id"] == user.id
            is_admin = interaction.user.guild_permissions.administrator
            async with self.cog.config.guild(interaction.guild).auction_moderators() as moderators:
                is_moderator = user.id in moderators
            return is_owner or is_admin or is_moderator

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listens for messages to handle potential bids and Dank Memer donations.

        Args:
            message (discord.Message): The message to process.
        """
        if message.author.bot and message.author.id != 270904126974590976:  # Ignore all bots except Dank Memer
            return

        if message.author.id == 270904126974590976:  # Dank Memer bot ID
            await self.handle_dank_memer_message(message)
        else:
            await self.handle_potential_bid(message)

    async def handle_dank_memer_message(self, message: discord.Message):
        """
        Handles messages from the Dank Memer bot, specifically for donations.

        Args:
            message (discord.Message): The message from Dank Memer to process.
        """
        log.info(f"Received message from Dank Memer: {message.content}")

        if not message.embeds:
            log.info("No embeds in the message")
            return

        embed = message.embeds[0]
        log.info(f"Embed title: {embed.title}")
        log.info(f"Embed description: {embed.description}")

        if "Successfully donated" in embed.description:
            await self.handle_donation(message)
        elif embed.title == "Pending Confirmation":
            try:
                def check(before: discord.Message, after: discord.Message):
                    return (before.id == message.id and 
                            after.embeds and 
                            "Successfully donated" in after.embeds[0].description)

                _, edited_message = await self.bot.wait_for('message_edit', check=check, timeout=60.0)
                await self.handle_donation(edited_message)
            except asyncio.TimeoutError:
                log.info("Donation confirmation timed out")
        else:
            log.info("Not a donation message")

    async def handle_donation(self, message: discord.Message):
        """
        Processes a donation message from Dank Memer.

        Args:
            message (discord.Message): The donation message to process.
        """
        guild = message.guild
        async with self.config.guild(guild).auctions() as auctions:
            log.info(f"Current auctions: {auctions}")
            log.info(f"Current channel ID: {message.channel.id}")

            for auction_id, auction in auctions.items():
                log.info(f"Checking auction {auction_id}: {auction}")
                if auction["status"] == "pending":
                    log.info(f"Found matching auction: {auction_id}")
                    await self.process_donation(message, guild, auction)
                    break
            else:
                log.info("No matching auction found")

    async def process_donation(self, message: discord.Message, guild: discord.Guild, auction: Dict[str, Any]):
        """
        Processes a donation for a specific auction.

        Args:
            message (discord.Message): The donation message.
            guild (discord.Guild): The guild where the donation was made.
            auction (Dict[str, Any]): The auction data.
        """
        embed = message.embeds[0]
        description = embed.description
        log.info(f"Processing donation: {description}")

        try:
            parts = description.split("**")
            log.info(f"Split parts: {parts}")
        
            if len(parts) < 3:
                raise ValueError("Unexpected donation message format")

            donation_info = parts[1].strip()
            log.info(f"Donation info: {donation_info}")

            if '⏣' in donation_info:
                amount_str = ''.join(filter(str.isdigit, donation_info))
                log.info(f"Parsed amount string: {amount_str}")
            
                if not amount_str:
                    raise ValueError(f"Unable to parse amount from: {donation_info}")
            
                donated_amount = int(amount_str)
                is_tax_payment = True
                donated_item = "Tax Payment"
            else:
                amount_and_item = donation_info.split(' ', 1)
                log.info(f"Amount and item split: {amount_and_item}")
            
                if len(amount_and_item) < 2:
                    raise ValueError(f"Unable to split amount and item from: {donation_info}")
            
                amount_str = amount_and_item[0].replace(',', '')
                donated_amount = int(amount_str)
                donated_item = amount_and_item[1]
                is_tax_payment = False

            log.info(f"Parsed donation: {donated_amount} {donated_item}")

            if is_tax_payment:
                auction["donated_tax"] += donated_amount
                remaining_tax = auction["tax"] - auction["donated_tax"]
                remaining_amount = auction["amount"] - auction["donated_amount"]
            else:
                cleaned_donated_item = ' '.join(word for word in donated_item.split() if not word.startswith('<') and not word.endswith('>')).lower()
                cleaned_auction_item = auction["item"].lower()

                log.info(f"Cleaned item names - Donated: {cleaned_donated_item}, Auction: {cleaned_auction_item}")

                if cleaned_donated_item != cleaned_auction_item:
                    await message.channel.send(f"This item doesn't match the auction item. Expected {auction['item']}, but received {donated_item}.")
                    return

                auction["donated_amount"] += donated_amount
                remaining_amount = auction["amount"] - auction["donated_amount"]
                remaining_tax = auction["tax"] - auction["donated_tax"]

            log.info(f"Updated auction: {auction}")

            if remaining_amount <= 0 and remaining_tax <= 0:
                await self.finalize_auction_request(guild, auction)
            else:
                embed = discord.Embed(
                    title="Donation Received",
                    description="Thank you for your donation. Here's what's left:",
                    color=discord.Color.green()
                )
                if remaining_amount > 0:
                    embed.add_field(name="Remaining Items", value=f"{remaining_amount}x {auction['item']}", inline=False)
                if remaining_tax > 0:
                    embed.add_field(name="Remaining Tax", value=f"⏣ {remaining_tax:,}", inline=False)
                await message.channel.send(embed=embed)

            async with self.config.guild(guild).auctions() as auctions:
                auctions[auction["auction_id"]] = auction

        except Exception as e:
            log.error(f"Error processing donation: {e}", exc_info=True)
            await message.channel.send(f"An error occurred while processing the donation: {str(e)}. Please contact an administrator.")

    async def handle_potential_bid(self, message: discord.Message):
        """
        Handles a potential bid message.

        Args:
            message (discord.Message): The message that might contain a bid.
        """
        if not self.is_valid_bid_format(message.content):
            return

        guild = message.guild
        async with self.config.guild(guild).auctions() as auctions:
            active_auction = next((a for a in auctions.values() if a['status'] == 'active' and a['auction_channel_id'] == message.channel.id), None)

        if not active_auction:
            return

        bid_amount = self.parse_bid_amount(message.content)
        if bid_amount <= active_auction['current_bid']:
            await message.channel.send(f"Your bid must be higher than the current bid of {active_auction['current_bid']:,}.")
            return

        min_increment = await self.config.guild(guild).minimum_bid_increment()
        if bid_amount < active_auction['current_bid'] + min_increment:
            await message.channel.send(f"Your bid must be at least {min_increment:,} higher than the current bid.")
            return

        # Bid confirmation
        confirm_view = self.BidConfirmationView(self, guild, active_auction, message.author, bid_amount)
        confirm_msg = await message.channel.send(f"{message.author.mention}, please confirm your bid of {bid_amount:,}.", view=confirm_view)
        await confirm_view.wait()

        if not confirm_view.value:
            await confirm_msg.edit(content="Bid cancelled.", view=None)
            return

        await self.place_bid(guild, active_auction, message.author.id, bid_amount)

    class BidConfirmationView(discord.ui.View):
        """
        A view for confirming bids.
        """
        def __init__(self, cog, guild, auction, user, bid_amount):
            super().__init__(timeout=30)
            self.cog = cog
            self.guild = guild
            self.auction = auction
            self.user = user
            self.bid_amount = bid_amount
            self.value = None

        @discord.ui.button(label="Confirm Bid", style=discord.ButtonStyle.green)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Handles bid confirmation."""
            if interaction.user != self.user:
                await interaction.response.send_message("You cannot confirm this bid.", ephemeral=True)
                return
            self.value = True
            self.stop()

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Handles bid cancellation."""
            if interaction.user != self.user:
                await interaction.response.send_message("You cannot cancel this bid.", ephemeral=True)
                return
            self.value = False
            self.stop()

    @commands.command()
    async def bid(self, ctx: commands.Context, auction_id: str, amount: int):
        """
        Place a bid on an active auction.

        Args:
            ctx (commands.Context): The command context.
            auction_id (str): The ID of the auction to bid on.
            amount (int): The bid amount.
        """
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)

        if not auction or auction['status'] != 'active':
            await ctx.send("This auction is not active.")
            return

        if amount <= auction['current_bid']:
            await ctx.send(f"Your bid must be higher than the current bid of {auction['current_bid']:,}.")
            return

        min_increment = await self.config.guild(guild).minimum_bid_increment()
        if amount < auction['current_bid'] + min_increment:
            await ctx.send(f"Your bid must be at least {min_increment:,} higher than the current bid.")
            return

        confirm_view = self.BidConfirmationView(self, guild, auction, ctx.author, amount)
        confirm_msg = await ctx.send(f"{ctx.author.mention}, please confirm your bid of {amount:,}.", view=confirm_view)
        await confirm_view.wait()

        if not confirm_view.value:
            await confirm_msg.edit(content="Bid cancelled.", view=None)
            return

        await self.place_bid(guild, auction, ctx.author.id, amount)
        await ctx.send(f"Your bid of {amount:,} has been placed.")

    @commands.command()
    async def autobid(self, ctx: commands.Context, auction_id: str, max_bid: int):
        """
        Set up an auto-bid for a specific auction.

        Args:
            ctx (commands.Context): The command context.
            auction_id (str): The ID of the auction to set up auto-bid for.
            max_bid (int): The maximum bid amount for auto-bidding.
        """
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)

        if not auction or auction['status'] != 'active':
            await ctx.send("This auction is not active.")
            return

        if max_bid <= auction['current_bid']:
            await ctx.send(f"Your maximum bid must be higher than the current bid of {auction['current_bid']:,}.")
            return

        async with self.config.member(ctx.author).auto_bids() as auto_bids:
            auto_bids[auction_id] = max_bid

        await ctx.send(f"Auto-bid set for auction {auction_id} up to {max_bid:,}")

    @commands.command()
    async def cancelautobid(self, ctx: commands.Context, auction_id: str):
        """
        Cancel your auto-bid for a specific auction.

        Args:
            ctx (commands.Context): The command context.
            auction_id (str): The ID of the auction to cancel auto-bid for.
        """
        async with self.config.member(ctx.author).auto_bids() as auto_bids:
            if auction_id in auto_bids:
                del auto_bids[auction_id]
                await ctx.send(f"Your auto-bid for auction {auction_id} has been cancelled.")
            else:
                await ctx.send(f"You don't have an active auto-bid for auction {auction_id}.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionreport(self, ctx: commands.Context, days: int = 7):
        """
        Generate a detailed report of auction activity for the specified number of days.

        Args:
            ctx (commands.Context): The command context.
            days (int): The number of days to include in the report. Defaults to 7.
        """
        if days <= 0:
            await ctx.send("The number of days must be greater than 0.")
            return

        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            now = int(datetime.utcnow().timestamp())
            start_time = now - (days * 86400)
            
            relevant_auctions = [a for a in auctions.values() if a['end_time'] > start_time and a['status'] == 'completed']
        
        if not relevant_auctions:
            await ctx.send(f"No completed auctions in the last {days} days.")
            return
        
        total_value = sum(a['current_bid'] for a in relevant_auctions)
        avg_value = total_value / len(relevant_auctions) if relevant_auctions else 0
        most_valuable = max(relevant_auctions, key=lambda x: x['current_bid'])
        most_bids = max(relevant_auctions, key=lambda x: len(x['bid_history']))
        
        categories = {}
        for auction in relevant_auctions:
            category = auction['category']
            if category not in categories:
                categories[category] = {'count': 0, 'value': 0}
            categories[category]['count'] += 1
            categories[category]['value'] += auction['current_bid']
        
        embed = discord.Embed(title=f"Auction Report (Last {days} Days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=len(relevant_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"{total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"{avg_value:,.2f}", inline=True)
        embed.add_field(name="Most Valuable Auction", value=f"{most_valuable['amount']}x {most_valuable['item']} ({most_valuable['current_bid']:,})", inline=False)
        embed.add_field(name="Most Bids", value=f"{most_bids['amount']}x {most_bids['item']} ({len(most_bids['bid_history'])} bids)", inline=False)
        
        for category, data in categories.items():
            embed.add_field(name=f"Category: {category}", value=f"Count: {data['count']}, Value: {data['value']:,}", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionanalytics(self, ctx: commands.Context):
        """
        Display analytics about auction performance and user engagement.

        Args:
            ctx (commands.Context): The command context.
        """
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            completed_auctions = [a for a in auctions.values() if a['status'] == 'completed']
        
        if not completed_auctions:
            await ctx.send("No completed auctions to analyze.")
            return
        
        total_auctions = len(completed_auctions)
        total_value = sum(a['current_bid'] for a in completed_auctions)
        avg_value = total_value / total_auctions if total_auctions else 0
        
        user_participation = {}
        for auction in completed_auctions:
            for bid in auction['bid_history']:
                user_id = bid['user_id']
                if user_id not in user_participation:
                    user_participation[user_id] = {'auctions': set(), 'bids': 0, 'wins': 0}
                user_participation[user_id]['auctions'].add(auction['auction_id'])
                user_participation[user_id]['bids'] += 1
            if auction['current_bidder']:
                user_participation[auction['current_bidder']]['wins'] += 1
        
        most_active_user = max(user_participation.items(), key=lambda x: len(x[1]['auctions']))
        most_wins = max(user_participation.items(), key=lambda x: x[1]['wins'])
        
        embed = discord.Embed(title="Auction Analytics", color=discord.Color.blue())
        embed.add_field(name="Total Completed Auctions", value=total_auctions, inline=True)
        embed.add_field(name="Total Value", value=f"{total_value:,}", inline=True)
        embed.add_field(name="Average Auction Value", value=f"{avg_value:,.2f}", inline=True)
        embed.add_field(name="Most Active User", value=f"<@{most_active_user[0]}> ({len(most_active_user[1]['auctions'])} auctions)", inline=False)
        embed.add_field(name="Most Auction Wins", value=f"<@{most_wins[0]}> ({most_wins[1]['wins']} wins)", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionleaderboard(self, ctx: commands.Context):
        """
        Display a leaderboard of top auction participants.

        Args:
            ctx (commands.Context): The command context.
        """
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            completed_auctions = [a for a in auctions.values() if a['status'] == 'completed']
        
        if not completed_auctions:
            await ctx.send("No completed auctions for leaderboard.")
            return
        
        user_stats = {}
        for auction in completed_auctions:
            for bid in auction['bid_history']:
                user_id = bid['user_id']
                if user_id not in user_stats:
                    user_stats[user_id] = {'bids': 0, 'wins': 0, 'spent': 0}
                user_stats[user_id]['bids'] += 1
            if auction['current_bidder']:
                user_stats[auction['current_bidder']]['wins'] += 1
                user_stats[auction['current_bidder']]['spent'] += auction['current_bid']
        
        sorted_stats = sorted(user_stats.items(), key=lambda x: x[1]['spent'], reverse=True)
        
        embed = discord.Embed(title="Auction Leaderboard", color=discord.Color.gold())
        for i, (user_id, stats) in enumerate(sorted_stats[:10], 1):
            user = guild.get_member(user_id)
            username = user.name if user else f"User {user_id}"
            embed.add_field(
                name=f"{i}. {username}",
                value=f"Wins: {stats['wins']}, Bids: {stats['bids']}, Spent: {stats['spent']:,}",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @commands.command()
    async def myauctionstats(self, ctx: commands.Context):
        """
        Display your personal auction statistics.

        Args:
            ctx (commands.Context): The command context.
        """
        user_id = ctx.author.id
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            completed_auctions = [a for a in auctions.values() if a['status'] == 'completed']
        
        user_stats = {'bids': 0, 'wins': 0, 'spent': 0, 'participated': 0}
        for auction in completed_auctions:
            participated = False
            for bid in auction['bid_history']:
                if bid['user_id'] == user_id:
                    user_stats['bids'] += 1
                    participated = True
            if participated:
                user_stats['participated'] += 1
            if auction['current_bidder'] == user_id:
                user_stats['wins'] += 1
                user_stats['spent'] += auction['current_bid']
        
        embed = discord.Embed(title="Your Auction Statistics", color=discord.Color.blue())
        embed.add_field(name="Auctions Participated", value=user_stats['participated'], inline=True)
        embed.add_field(name="Total Bids", value=user_stats['bids'], inline=True)
        embed.add_field(name="Auctions Won", value=user_stats['wins'], inline=True)
        embed.add_field(name="Total Spent", value=f"{user_stats['spent']:,}", inline=True)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionmoderator(self, ctx: commands.Context, user: discord.Member):
        """
        Set a user as an auction moderator.

        Args:
            ctx (commands.Context): The command context.
            user (discord.Member): The user to set as a moderator.
        """
        async with self.config.guild(ctx.guild).auction_moderators() as moderators:
            if user.id in moderators:
                await ctx.send(f"{user.name} is already an auction moderator.")
            else:
                moderators.append(user.id)
                await ctx.send(f"{user.name} has been set as an auction moderator.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def removeauctionmoderator(self, ctx: commands.Context, user: discord.Member):
        """
        Remove a user from being an auction moderator.

        Args:
            ctx (commands.Context): The command context.
            user (discord.Member): The user to remove as a moderator.
        """
        async with self.config.guild(ctx.guild).auction_moderators() as moderators:
            if user.id in moderators:
                moderators.remove(user.id)
                await ctx.send(f"{user.name} has been removed as an auction moderator.")
            else:
                await ctx.send(f"{user.name} is not an auction moderator.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def listauctionmoderators(self, ctx: commands.Context):
        """
        List all auction moderators.

        Args:
            ctx (commands.Context): The command context.
        """
        async with self.config.guild(ctx.guild).auction_moderators() as moderators:
            if not moderators:
                await ctx.send("There are no auction moderators set.")
            else:
                mod_list = [ctx.guild.get_member(mod_id).name for mod_id in moderators if ctx.guild.get_member(mod_id)]
                await ctx.send(f"Auction moderators: {', '.join(mod_list)}")

    @commands.command()
    async def subscribeauctions(self, ctx: commands.Context, category: str = None):
        """
        Subscribe to auction notifications, optionally for a specific category.

        Args:
            ctx (commands.Context): The command context.
            category (str, optional): The category to subscribe to. If not provided, subscribes to all categories.
        """
        async with self.config.member(ctx.author).auction_subscriptions() as subscriptions:
            if category:
                if category not in subscriptions:
                    subscriptions.append(category)
                    await ctx.send(f"You have subscribed to notifications for {category} auctions.")
                else:
                    await ctx.send(f"You are already subscribed to {category} auctions.")
            else:
                if 'all' not in subscriptions:
                    subscriptions.append('all')
                    await ctx.send("You have subscribed to notifications for all auctions.")
                else:
                    await ctx.send("You are already subscribed to all auctions.")

    @commands.command()
    async def unsubscribeauctions(self, ctx: commands.Context, category: str = None):
        """
        Unsubscribe from auction notifications, optionally for a specific category.

        Args:
            ctx (commands.Context): The command context.
            category (str, optional): The category to unsubscribe from. If not provided, unsubscribes from all categories.
        """
        async with self.config.member(ctx.author).auction_subscriptions() as subscriptions:
            if category:
                if category in subscriptions:
                    subscriptions.remove(category)
                    await ctx.send(f"You have unsubscribed from notifications for {category} auctions.")
                else:
                    await ctx.send(f"You are not subscribed to {category} auctions.")
            else:
                subscriptions.clear()
                await ctx.send("You have unsubscribed from all auction notifications.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionbackup(self, ctx: commands.Context):
        """
        Create a backup of all auction data.

        Args:
            ctx (commands.Context): The command context.
        """
        guild = ctx.guild
        data = await self.config.guild(guild).all()
        
        # Convert data to JSON string
        json_data = json.dumps(data, indent=2)
        
        # Create a file-like object in memory
        file = io.StringIO(json_data)
        
        # Send the file to the user
        await ctx.send("Here's your auction data backup:", file=discord.File(fp=file, filename="auction_backup.json"))

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionrestore(self, ctx: commands.Context):
        """
        Restore auction data from a backup file.

        Args:
            ctx (commands.Context): The command context.
        """
        if not ctx.message.attachments:
            await ctx.send("Please attach the backup file when using this command.")
            return
        
        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith('.json'):
            await ctx.send("The attached file must be a JSON file.")
            return
        
        try:
            content = await attachment.read()
            data = json.loads(content)
            await self.config.guild(ctx.guild).set(data)
            await ctx.send("Auction data has been restored from the backup.")
        except json.JSONDecodeError:
            await ctx.send("The attached file is not a valid JSON file.")
        except Exception as e:
            await ctx.send(f"An error occurred while restoring the data: {str(e)}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def pruneoldauctions(self, ctx: commands.Context, days: int = 30):
        """
        Prune completed auctions older than the specified number of days.

        Args:
            ctx (commands.Context): The command context.
            days (int, optional): The number of days to keep. Auctions older than this will be pruned. Defaults to 30.
        """
        if days <= 0:
            await ctx.send("The number of days must be greater than 0.")
            return

        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            now = int(datetime.utcnow().timestamp())
            cutoff_time = now - (days * 86400)
            
            old_auctions = [aid for aid, a in auctions.items() if a['status'] == 'completed' and a['end_time'] < cutoff_time]
            
            for auction_id in old_auctions:
                del auctions[auction_id]
        
        await ctx.send(f"Pruned {len(old_auctions)} completed auctions older than {days} days.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def exportauctiondata(self, ctx: commands.Context, format: str = "csv"):
        """
        Export auction data in CSV or JSON format.

        Args:
            ctx (commands.Context): The command context.
            format (str, optional): The format to export data in. Either "csv" or "json". Defaults to "csv".
        """
        guild = ctx.guild
        data = await self.config.guild(guild).auctions()
        
        if format.lower() == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Auction ID", "Item", "Amount", "Start Time", "End Time", "Final Bid", "Winner"])
            for auction_id, auction in data.items():
                writer.writerow([
                    auction_id,
                    auction['item'],
                    auction['amount'],
                    datetime.fromtimestamp(auction['start_time']),
                    datetime.fromtimestamp(auction['end_time']),
                    auction['current_bid'],
                    auction['current_bidder']
                ])
            file = discord.File(fp=io.BytesIO(output.getvalue().encode()), filename="auction_data.csv")
        elif format.lower() == "json":
            file = discord.File(fp=io.BytesIO(json.dumps(data, indent=2).encode()), filename="auction_data.json")
        else:
            await ctx.send("Invalid format. Please choose 'csv' or 'json'.")
            return
        
        await ctx.send("Here's your exported auction data:", file=file)

    @commands.command()
    async def auctionhelp(self, ctx: commands.Context):
        """
        Display help information for the auction system.

        Args:
            ctx (commands.Context): The command context.
        """
        embed = discord.Embed(title="Auction System Help", color=discord.Color.blue())
        embed.add_field(name="General Commands", value="""
        • `[p]myauctions`: View your active and pending auctions
        • `[p]auctionhistory`: View your completed auctions
        • `[p]bid <auction_id> <amount>`: Place a bid on an auction
        • `[p]autobid <auction_id> <max_bid>`: Set up an auto-bid
        • `[p]cancelautobid <auction_id>`: Cancel your auto-bid
        • `[p]subscribeauctions [category]`: Subscribe to auction notifications
        • `[p]unsubscribeauctions [category]`: Unsubscribe from notifications
        • `[p]myauctionstats`: View your personal auction statistics
        """, inline=False)
        
        embed.add_field(name="Admin Commands", value="""
        • `[p]auctionset`: Configure auction settings
        • `[p]spawnauction`: Create a new auction request button
        • `[p]auctionreport [days]`: Generate an auction report
        • `[p]auctionanalytics`: View auction system analytics
        • `[p]auctionleaderboard`: Display top auction participants
        • `[p]setauctionmoderator <user>`: Set a user as auction moderator
        • `[p]removeauctionmoderator <user>`: Remove auction moderator status
        • `[p]listauctionmoderators`: List all auction moderators
        • `[p]auctionbackup`: Create a backup of auction data
        • `[p]auctionrestore`: Restore auction data from a backup
        • `[p]pruneoldauctions [days]`: Remove old completed auctions
        • `[p]exportauctiondata [format]`: Export auction data
        """, inline=False)
        
        await ctx.send(embed=embed)

    def is_valid_bid_format(self, content: str) -> bool:
        """
        Check if the given content is in a valid bid format.

        Args:
            content (str): The content to check.

        Returns:
            bool: True if the content is in a valid bid format, False otherwise.
        """
        return content.replace(',', '').isdigit() or content.lower().endswith(('k', 'm', 'b'))

    def parse_bid_amount(self, content: str) -> int:
        """
        Parse the bid amount from the given content.

        Args:
            content (str): The content to parse.

        Returns:
            int: The parsed bid amount.
        """
        content = content.lower().replace(',', '')
        if content.endswith('k'):
            return int(float(content[:-1]) * 1000)
        elif content.endswith('m'):
            return int(float(content[:-1]) * 1000000)
        elif content.endswith('b'):
            return int(float(content[:-1]) * 1000000000)
        else:
            return int(content)

    async def update_user_stats(self, guild: discord.Guild, user_id: int, amount: int, action: str):
        """
        Update user statistics for auctions won or sold.

        Args:
            guild (discord.Guild): The guild where the auction took place.
            user_id (int): The ID of the user to update stats for.
            amount (int): The amount involved in the auction.
            action (str): The action performed ('won' or 'sold').
        """
        async with self.config.guild(guild).user_stats() as stats:
            if str(user_id) not in stats:
                stats[str(user_id)] = {'won': 0, 'sold': 0, 'total_value': 0}
            
            stats[str(user_id)][action] += 1
            stats[str(user_id)]['total_value'] += amount

    async def close_auction(self, interaction: discord.Interaction, auction_id: str, reason: str):
        """
        Close an auction manually.

        Args:
            interaction (discord.Interaction): The interaction that triggered the closure.
            auction_id (str): The ID of the auction to close.
            reason (str): The reason for closing the auction.
        """
        guild = interaction.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await interaction.response.send_message("Auction not found.", ephemeral=True)
                return
            
            if auction['status'] != 'active':
                await interaction.response.send_message("This auction is not active.", ephemeral=True)
                return
            
            auction['status'] = 'completed'
            auction['end_time'] = int(datetime.utcnow().timestamp())
            auction['close_reason'] = reason
            
            await self.end_auction(guild, auction_id)
            await interaction.response.send_message(f"Auction {auction_id} has been closed. Reason: {reason}")

    async def pause_auction(self, interaction: discord.Interaction, auction_id: str):
        """
        Pause an active auction.

        Args:
            interaction (discord.Interaction): The interaction that triggered the pause.
            auction_id (str): The ID of the auction to pause.
        """
        guild = interaction.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await interaction.response.send_message("Auction not found.", ephemeral=True)
                return
            
            if auction['status'] != 'active':
                await interaction.response.send_message("This auction is not active.", ephemeral=True)
                return
            
            auction['status'] = 'paused'
            auction['pause_time'] = int(datetime.utcnow().timestamp())
            
            await interaction.response.send_message(f"Auction {auction_id} has been paused.")

    async def resume_auction(self, interaction: discord.Interaction, auction_id: str):
        """
        Resume a paused auction.

        Args:
            interaction (discord.Interaction): The interaction that triggered the resume.
            auction_id (str): The ID of the auction to resume.
        """
        guild = interaction.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await interaction.response.send_message("Auction not found.", ephemeral=True)
                return
            
            if auction['status'] != 'paused':
                await interaction.response.send_message("This auction is not paused.", ephemeral=True)
                return
            
            pause_duration = int(datetime.utcnow().timestamp()) - auction['pause_time']
            auction['status'] = 'active'
            auction['end_time'] += pause_duration
            del auction['pause_time']
            
            await interaction.response.send_message(f"Auction {auction_id} has been resumed.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """
        Global error handler for the cog.

        Args:
            ctx (commands.Context): The context in which the error occurred.
            error (commands.CommandError): The error that was raised.
        """
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"This command is on cooldown. Please try again in {error.retry_after:.2f} seconds.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing required argument: {error.param}")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"Bad argument: {str(error)}")
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have the required permissions to use this command.")
        else:
            await ctx.send(f"An error occurred: {str(error)}")
            log.error(f"Unhandled error in {ctx.command}: {error}", exc_info=error)

    async def red_delete_data_for_user(self, *, requester: str, user_id: int):
        """
        Delete user data when requested.

        Args:
            requester (str): The requester of the deletion.
            user_id (int): The ID of the user whose data should be deleted.
        """
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in list(auctions.items()):
                    if auction['user_id'] == user_id:
                        del auctions[auction_id]
            
            async with self.config.guild(guild).user_stats() as stats:
                if str(user_id) in stats:
                    del stats[str(user_id)]
        
        await self.config.user_from_id(user_id).clear()

    async def initialize(self):
        """
        Initialize the cog. This method is called when the cog is loaded.
        """
        await self.migrate_data()
        await self.cleanup_auctions()

    async def migrate_data(self):
        """
        Migrate data from old format to new format if necessary.
        """
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in auctions.items():
                    if 'tier' not in auction:
                        auction['tier'] = self.get_auction_tier(guild, auction)
                    if 'bid_history' not in auction:
                        auction['bid_history'] = []

    async def cleanup_auctions(self):
        """
        Clean up any auctions that might have been left in an inconsistent state.
        """
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in list(auctions.items()):
                    if auction['status'] == 'active' and auction['end_time'] < int(datetime.utcnow().timestamp()):
                        await self.end_auction(guild, auction_id)

    def get_auction_tier(self, guild: discord.Guild, auction: Dict[str, Any]) -> str:
        """
        Determine the tier of an auction based on its value.

        Args:
            guild (discord.Guild): The guild where the auction is taking place.
            auction (Dict[str, Any]): The auction data.

        Returns:
            str: The determined tier for the auction.
        """
        tiers = self.config.guild(guild).auction_tiers()
        for tier, details in reversed(sorted(tiers.items(), key=lambda x: x[1]['min_value'])):
            if auction['total_value'] >= details['min_value']:
                return tier
        return "standard"  # Default tier if no others match

    @commands.command()
    async def auctioninfo(self, ctx: commands.Context, auction_id: str):
        """
        Get detailed information about a specific auction.

        Args:
            ctx (commands.Context): The command context.
            auction_id (str): The ID of the auction to get information about.
        """
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction:
            await ctx.send("Auction not found.")
            return
        
        embed = discord.Embed(title=f"Auction Info: {auction_id}", color=discord.Color.blue())
        embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=False)
        embed.add_field(name="Current Bid", value=f"{auction['current_bid']:,}", inline=True)
        embed.add_field(name="Tier", value=auction['tier'], inline=True)
        embed.add_field(name="Status", value=auction['status'].capitalize(), inline=True)
        embed.add_field(name="Start Time", value=f"<t:{auction['start_time']}:F>", inline=True)
        embed.add_field(name="End Time", value=f"<t:{auction['end_time']}:F>", inline=True)
        
        if auction['current_bidder']:
            bidder = ctx.guild.get_member(auction['current_bidder'])
            embed.add_field(name="Current Highest Bidder", value=bidder.mention if bidder else "Unknown User", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    async def myauctions(self, ctx: commands.Context):
        """
        View your active and pending auctions.
        """
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            user_auctions = [a for a in auctions.values() if a['user_id'] == ctx.author.id and a['status'] in ['active', 'pending']]
        
        if not user_auctions:
            await ctx.send("You don't have any active or pending auctions.")
            return
        
        embed = discord.Embed(title="Your Auctions", color=discord.Color.green())
        for auction in user_auctions:
            embed.add_field(
                name=f"{auction['amount']}x {auction['item']} ({auction['status'].capitalize()})",
                value=f"ID: {auction['auction_id']}\nCurrent Bid: {auction['current_bid']:,}\nEnds: <t:{auction['end_time']}:R>",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @commands.command()
    async def auctionhistory(self, ctx: commands.Context, page: int = 1):
        """
        View your auction history.

        Args:
            ctx (commands.Context): The command context.
            page (int, optional): The page number to view. Defaults to 1.
        """
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            user_auctions = [a for a in auctions.values() if a['user_id'] == ctx.author.id and a['status'] == 'completed']
        
        user_auctions.sort(key=lambda x: x['end_time'], reverse=True)
        
        if not user_auctions:
            await ctx.send("You don't have any completed auctions.")
            return
        
        items_per_page = 5
        pages = (len(user_auctions) + items_per_page - 1) // items_per_page
        
        if page < 1 or page > pages:
            await ctx.send(f"Invalid page number. Please choose a page between 1 and {pages}.")
            return
        
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        
        embed = discord.Embed(title=f"Your Auction History (Page {page}/{pages})", color=discord.Color.blue())
        for auction in user_auctions[start_idx:end_idx]:
            embed.add_field(
                name=f"{auction['amount']}x {auction['item']}",
                value=f"ID: {auction['auction_id']}\nFinal Bid: {auction['current_bid']:,}\nEnded: <t:{auction['end_time']}:R>",
                inline=False
            )
        
        await ctx.send(embed=embed)

async def setup(bot: Red):
    """
    Setup function to add the cog to the bot.

    Args:
        bot (Red): The Red Discord bot instance.
    """
    cog = EnhancedAdvancedAuction(bot)
    await cog.initialize()
    await bot.add_cog(cog)
    