import discord
from discord import app_commands
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import aiohttp
import asyncio
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, Any, List, Union, Tuple
import json
import io
import csv
import random
import math
import re

log = logging.getLogger("red.economy.AdvancedAuction")

class AuctionScheduleView(discord.ui.View):
    def __init__(self, cog: "EnhancedAdvancedAuction"):
        super().__init__(timeout=300)
        self.cog = cog
        self.schedule_time: Optional[datetime] = None
        self.message = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def schedule_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = self.ScheduleModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.schedule_time = modal.schedule_time
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def schedule_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Auction will be queued immediately.", ephemeral=True)
        self.stop()

    class ScheduleModal(discord.ui.Modal, title="Schedule Auction"):
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

class DonationTracker:
    def __init__(self, cog: "EnhancedAdvancedAuction"):
        self.cog = cog
        self.donation_locks: Dict[str, asyncio.Lock] = {}

    async def process_donation(self, guild: discord.Guild, auction: Dict[str, Any], donor: discord.Member, amount: int, item: str) -> Tuple[bool, str]:
        auction_id = auction['auction_id']
        if auction_id not in self.donation_locks:
            self.donation_locks[auction_id] = asyncio.Lock()

        async with self.donation_locks[auction_id]:
            if item == "Tax Payment":
                auction["donated_tax"] = auction.get("donated_tax", 0) + amount
                remaining_tax = auction["tax"] - auction["donated_tax"]
                remaining_amount = auction["amount"] - auction.get("donated_amount", 0)
                message = f"Tax payment of {amount:,} received. Remaining tax: {remaining_tax:,}"
            else:
                cleaned_donated_item = ' '.join(word for word in item.split() if not word.startswith('<') and not word.endswith('>')).lower()
                cleaned_auction_item = auction["item"].lower()

                if cleaned_donated_item != cleaned_auction_item:
                    return False, f"This item doesn't match the auction item. Expected {auction['item']}, but received {item}."

                auction["donated_amount"] = auction.get("donated_amount", 0) + amount
                remaining_amount = auction["amount"] - auction["donated_amount"]
                remaining_tax = auction["tax"] - auction.get("donated_tax", 0)
                message = f"Donation of {amount}x {item} received. Remaining items: {remaining_amount}x {auction['item']}"

            if remaining_amount <= 0 and remaining_tax <= 0:
                auction["status"] = "ready"
                message += "\nAll items and tax have been donated. The auction is ready to be queued."

            await self.cog.config.guild(guild).auctions.set({auction_id: auction})

            return True, message

class EnhancedAdvancedAuction(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        default_guild: Dict[str, Any] = {
            "auctions": {},
            "auction_queue": [],
            "scheduled_auctions": {},
            "auction_channel": None,
            "log_channel": None,
            "queue_channel": None,
            "auction_role": None,
            "blacklist_role": None,
            "auction_ping_role": None,
            "massive_auction_ping_role": None,
            "user_stats": {},
            "categories": ["General", "Rare", "Limited Edition", "Event"],
            "leaderboard": {},
            "auction_cooldown": 86400,
            "banned_users": [],
            "auction_moderators": [],
            "minimum_bid_increment": 1000,
            "auction_extension_time": 300,
            "auction_duration": 6 * 3600,  # 6 hours
            "auction_history": [],
            "global_auction_settings": {
                "max_auction_duration": 7 * 24 * 3600,  # 7 days
                "min_auction_duration": 1 * 3600,  # 1 hour
                "max_auctions_per_user": 3,
                "bidding_cooldown": 30,  # 30 seconds between bids
                "snipe_protection_time": 300,  # 5 minutes
            },
            "bid_increment_tiers": {
                "0": 1000,
                "10000": 5000,
                "100000": 10000,
                "1000000": 50000,
                "10000000": 100000,
            },
        }
        
        default_member: Dict[str, Any] = {
            "auction_reminders": [],
            "notification_settings": {
                "outbid": True,
                "auction_start": True,
                "auction_end": True,
                "won_auction": True,
            },
            "last_bid_time": {},
            "auction_history": [],
            "reputation_score": 100,
            "subscribed_categories": [],
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.auction_task: Optional[asyncio.Task] = None
        self.queue_lock = asyncio.Lock()
        self.donation_tracker = DonationTracker(self)

    async def cog_load(self) -> None:
        self.auction_task = self.bot.loop.create_task(self.auction_loop())

    async def cog_unload(self) -> None:
        if self.auction_task:
            self.auction_task.cancel()

    async def auction_loop(self) -> None:
        while True:
            try:
                await self.process_auction_queue()
                await self.check_auction_end()
                await self.process_scheduled_auctions()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in auction loop: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def process_auction_queue(self) -> None:
        async with self.queue_lock:
            for guild in self.bot.guilds:
                auction_channel_id = await self.config.guild(guild).auction_channel()
                if not auction_channel_id:
                    continue
                
                auction_channel = guild.get_channel(auction_channel_id)
                if not auction_channel:
                    continue

                active_threads = auction_channel.threads
                if len(active_threads) == 0:
                    queue = await self.config.guild(guild).auction_queue()
                    if queue:
                        next_auction = queue.pop(0)
                        await self.start_auction(guild, next_auction)
                        await self.config.guild(guild).auction_queue.set(queue)

    async def check_auction_end(self) -> None:
        for guild in self.bot.guilds:
            auction_channel_id = await self.config.guild(guild).auction_channel()
            if not auction_channel_id:
                continue
            
            auction_channel = guild.get_channel(auction_channel_id)
            if not auction_channel:
                continue

            for thread in auction_channel.threads:
                if thread.name.startswith("Auction #"):
                    auction_id = thread.name.split("#")[1]
                    auction = (await self.config.guild(guild).auctions()).get(auction_id)
                    if auction and auction['status'] == 'active' and auction['end_time'] <= datetime.utcnow().timestamp():
                        await self.end_auction(guild, auction_id)

    async def process_scheduled_auctions(self) -> None:
        for guild in self.bot.guilds:
            async with self.config.guild(guild).scheduled_auctions() as scheduled:
                current_time = datetime.utcnow().timestamp()
                for auction_id, auction_time in list(scheduled.items()):
                    if auction_time <= current_time:
                        auction_data = (await self.config.guild(guild).auctions()).get(auction_id)
                        if auction_data:
                            await self.queue_auction(guild, auction_data)
                            del scheduled[auction_id]

    async def start_auction(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        auction['start_time'] = datetime.utcnow().timestamp()
        auction['end_time'] = auction['start_time'] + await self.config.guild(guild).auction_duration()
        auction['status'] = 'active'
        
        auction_channel_id = await self.config.guild(guild).auction_channel()
        auction_channel = guild.get_channel(auction_channel_id)
        
        if auction_channel:
            thread = await auction_channel.create_thread(
                name=f"Auction #{auction['auction_id']}",
                type=discord.ChannelType.public_thread,
                reason="New auction started"
            )
            
            embed = self.create_auction_embed(auction)
            await thread.send("New auction started!", embed=embed)
            
            # Notify subscribers
            await self.notify_subscribers(guild, auction, thread)
        
        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction['auction_id']] = auction

    async def end_auction(self, guild: discord.Guild, auction_id: str) -> None:
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

            auction_channel_id = await self.config.guild(guild).auction_channel()
            auction_channel = guild.get_channel(auction_channel_id)
            
            if auction_channel:
                thread = discord.utils.get(auction_channel.threads, name=f"Auction #{auction_id}")
                if thread:
                    if auction['current_bidder']:
                        winner = guild.get_member(auction['current_bidder'])
                        await thread.send(f"Auction ended! The winner is {winner.mention} with a bid of {auction['current_bid']:,}.")
                        await self.handle_auction_completion(guild, auction, winner, auction['current_bid'])
                    else:
                        await thread.send("Auction ended with no bids.")
                    
                    # Archive the thread
                    await thread.edit(archived=True, locked=True)

            auction['status'] = 'completed'
            auctions[auction_id] = auction

        await self.update_auction_history(guild, auction)
        await self.process_auction_queue()

    async def handle_auction_completion(self, guild: discord.Guild, auction: Dict[str, Any], winner: discord.Member, winning_bid: int) -> None:
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = guild.get_channel(log_channel_id)

        if log_channel:
            await log_channel.send(f"Auction completed. Winner: {winner.mention}, Amount: {winning_bid:,}")
            await log_channel.send(f"/serverevents payout user:{winner.id} quantity:{auction['amount']} item:{auction['item']}")
            await log_channel.send(f"/serverevents payout user:{auction['user_id']} quantity:{winning_bid}")

        await self.update_user_stats(guild, winner.id, winning_bid, 'won')
        await self.update_user_stats(guild, auction['user_id'], winning_bid, 'sold')
        await self.update_bidder_reputation(guild, winner.id, 'increase')

        try:
            await winner.send(f"Congratulations! You won the auction for {auction['amount']}x {auction['item']} with a bid of {winning_bid:,}. The item will be delivered to you shortly.")
        except discord.HTTPException:
            pass

    async def update_auction_history(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        async with self.config.guild(guild).auction_history() as history:
            history.append(auction)

    async def notify_subscribers(self, guild: discord.Guild, auction: Dict[str, Any], thread: discord.Thread) -> None:
        async with self.config.all_members(guild)() as all_members:
            for member_id, member_data in all_members.items():
                if auction['category'] in member_data.get('subscribed_categories', []):
                    member = guild.get_member(member_id)
                    if member:
                        try:
                            await member.send(f"New auction started in your subscribed category '{auction['category']}': {auction['amount']}x {auction['item']}\n{thread.jump_url}")
                        except discord.HTTPException:
                            pass

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionset(self, ctx: commands.Context):
        """Configure the auction system."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @auctionset.command(name="auctionchannels")
    async def set_auction_channels(self, ctx: commands.Context, auction: discord.TextChannel, queue: discord.TextChannel, log: discord.TextChannel):
        """Set the channels for auctions, queue, and logging."""
        await self.config.guild(ctx.guild).auction_channel.set(auction.id)
        await self.config.guild(ctx.guild).queue_channel.set(queue.id)
        await self.config.guild(ctx.guild).log_channel.set(log.id)
        await ctx.send(f"Auction channel set to {auction.mention}, queue channel set to {queue.mention}, and log channel set to {log.mention}.")

    @auctionset.command(name="auctionrole")
    async def set_auction_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be assigned to users when they open an auction channel."""
        await self.config.guild(ctx.guild).auction_role.set(role.id)
        await ctx.send(f"Auction role set to {role.name}.")

    @auctionset.command(name="bidincrements")
    async def set_bid_increments(self, ctx: commands.Context, tier: int, increment: int):
        """Set bid increment for a specific tier."""
        async with self.config.guild(ctx.guild).bid_increment_tiers() as tiers:
            tiers[str(tier)] = increment
        await ctx.send(f"Bid increment for tier {tier} set to {increment:,}.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def spawnauction(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Spawn the auction request embed with button in the specified channel or the current channel."""
        channel = channel or ctx.channel
        view = self.AuctionView(self)
        embed = discord.Embed(
            title="🎉 Request an Auction 🎉",
            description="Click the button below to request an auction and submit your donation details.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. A new thread will be created for your auction.", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        message = await channel.send(embed=embed, view=view)
        view.message = message
        await ctx.send(f"Auction request embed spawned in {channel.mention}")

    class AuctionView(View):
        def __init__(self, cog: "EnhancedAdvancedAuction"):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green)
        async def request_auction_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        def __init__(self, cog: "EnhancedAdvancedAuction"):
            super().__init__()
            self.cog = cog

        item_name = TextInput(label="What are you going to donate?", placeholder="e.g., Blob", required=True, min_length=1, max_length=100)
        item_count = TextInput(label="How many of those items will you donate?", placeholder="e.g., 5", required=True, max_length=10)
        minimum_bid = TextInput(label="What should the minimum bid be?", placeholder="e.g., 1,000,000", required=False)
        buy_out_price = TextInput(label="Set a buy-out price (optional)", placeholder="e.g., 10,000,000", required=False)
        category = TextInput(label="Category", placeholder="e.g., Rare", required=False)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                log.info(f"Auction modal submitted by {interaction.user.name}")
                item_name = self.item_name.value
                item_count = self.item_count.value
                min_bid = self.minimum_bid.value or "1,000,000"
                buy_out_price = self.buy_out_price.value
                category = self.category.value or "General"

                log.info(f"Submitted values: item={item_name}, count={item_count}, min_bid={min_bid}, buy_out={buy_out_price}, category={category}")

                await interaction.response.send_message("Processing your auction request...", ephemeral=True)

                view = AuctionScheduleView(self.cog)
                await interaction.followup.send("Would you like to schedule this auction?", view=view, ephemeral=True)
                await view.wait()

                await self.cog.process_auction_request(interaction, item_name, item_count, min_bid, buy_out_price, category, view.schedule_time)

            except Exception as e:
                log.error(f"An error occurred in modal submission: {e}", exc_info=True)
                await interaction.followup.send(f"An error occurred while processing your submission. Please try again or contact an administrator.", ephemeral=True)

    async def process_auction_request(self, interaction: discord.Interaction, item_name: str, item_count: str, min_bid: str, buy_out_price: str, category: str, schedule_time: Optional[datetime]):
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
            "user_id": interaction.user.id,
            "item": item_name,
            "amount": item_count,
            "min_bid": int(min_bid.replace(',', '')),
            "buy_out_price": int(buy_out_price.replace(',', '')) if buy_out_price else None,
            "category": category,
            "status": "pending",
            "item_value": item_value,
            "total_value": total_value,
            "tax": tax,
            "current_bid": int(min_bid.replace(',', '')),
            "current_bidder": None,
            "bid_history": [],
            "start_time": None,
            "end_time": None,
            "donated_amount": 0,
            "donated_tax": 0,
        }

        if schedule_time:
            auction_data['scheduled_time'] = int(schedule_time.timestamp())
            auction_data['status'] = 'scheduled'
            async with self.config.guild(guild).scheduled_auctions() as scheduled:
                scheduled[auction_id] = auction_data
            await interaction.followup.send(f"Auction scheduled for {schedule_time}")
        else:
            await self.create_auction_thread(guild, auction_data, interaction.user)
            await interaction.followup.send("Your auction thread has been created.")

        # Assign the auction role
        auction_role_id = await self.config.guild(guild).auction_role()
        if auction_role_id:
            auction_role = guild.get_role(auction_role_id)
            if auction_role:
                await interaction.user.add_roles(auction_role)

    async def create_auction_thread(self, guild: discord.Guild, auction_data: Dict[str, Any], creator: discord.Member):
        auction_channel_id = await self.config.guild(guild).auction_channel()
        auction_channel = guild.get_channel(auction_channel_id)
        
        if not auction_channel:
            log.error(f"Auction channel not found for guild {guild.id}")
            return
        
        thread = await auction_channel.create_thread(
            name=f"Auction #{auction_data['auction_id']}",
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason=f"Auction request by {creator.name}"
        )
        
        await thread.add_user(creator)
        
        embed = discord.Embed(
            title="Your Auction Details",
            description=f"Please donate {auction_data['amount']} of {auction_data['item']} and the required tax.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Item", value=f"{auction_data['amount']}x {auction_data['item']}", inline=False)
        embed.add_field(name="Minimum Bid", value=f"{auction_data['min_bid']:,}", inline=True)
        embed.add_field(name="Buy-out Price", value=f"{auction_data['buy_out_price']:,}" if auction_data['buy_out_price'] else "Not set", inline=True)
        embed.add_field(name="Market Price (each)", value=f"{auction_data['item_value']:,}", inline=True)
        embed.add_field(name="Total Value", value=f"{auction_data['total_value']:,}", inline=True)
        embed.add_field(name="Tax (10%)", value=f"{auction_data['tax']:,}", inline=True)
        embed.add_field(name="Status", value="Waiting for donations", inline=True)
        
        await thread.send(content=creator.mention, embed=embed)
        
        # Store the thread ID in the auction data
        auction_data['thread_id'] = thread.id
        
        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction_data['auction_id']] = auction_data

    async def get_next_auction_id(self, guild: discord.Guild) -> str:
        async with self.config.guild(guild).auctions() as auctions:
            existing_ids = [int(aid.split('-')[1]) for aid in auctions.keys() if '-' in aid]
            next_id = max(existing_ids, default=0) + 1
            return f"{guild.id}-{next_id}"

    async def api_check(self, interaction: discord.Interaction, item_count: int, item_name: str) -> tuple:
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and message.author.id != 270904126974590976:  # Ignore all bots except Dank Memer
            return

        if isinstance(message.channel, discord.Thread) and message.channel.parent_id == await self.config.guild(message.guild).auction_channel():
            if message.author.id == 270904126974590976:  # Dank Memer bot ID
                await self.handle_dank_memer_message(message)
            else:
                await self.handle_potential_bid(message)

    async def handle_dank_memer_message(self, message: discord.Message):
        log.info(f"Received message from Dank Memer in thread {message.channel.name}: {message.content}")

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
        guild = message.guild
        thread = message.channel
        auction_id = thread.name.split('#')[1]
        
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                log.info(f"No auction found for ID {auction_id}")
                return

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

                success, message = await self.donation_tracker.process_donation(guild, auction, message.author, donated_amount, donated_item)
                await thread.send(message)

                if success and auction['status'] == 'ready':
                    await self.finalize_auction_setup(guild, auction_id)

                auctions[auction_id] = auction

            except Exception as e:
                log.error(f"Error processing donation: {e}", exc_info=True)
                await thread.send(f"An error occurred while processing the donation: {str(e)}. Please contact an administrator.")

    async def finalize_auction_setup(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

            auction['status'] = 'queued'
            auctions[auction_id] = auction

        await self.queue_auction(guild, auction)
        
        thread_id = auction.get('thread_id')
        if thread_id:
            thread = guild.get_thread(thread_id)
            if thread:
                await thread.send("All items and tax have been donated. Your auction has been queued.")
                await thread.edit(archived=True, locked=True)

        # Remove auction role from the user
        auction_role_id = await self.config.guild(guild).auction_role()
        if auction_role_id:
            auction_role = guild.get_role(auction_role_id)
            user = guild.get_member(auction['user_id'])
            if auction_role and user:
                await user.remove_roles(auction_role)

    async def queue_auction(self, guild: discord.Guild, auction: Dict[str, Any]):
        async with self.config.guild(guild).auction_queue() as queue:
            queue.append(auction['auction_id'])

    async def handle_potential_bid(self, message: discord.Message):
        if not self.is_valid_bid_format(message.content):
            return

        guild = message.guild
        thread = message.channel
        auction_id = thread.name.split('#')[1]

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                return

            bid_amount = self.parse_bid_amount(message.content)
            if bid_amount <= auction['current_bid']:
                await thread.send(f"Your bid must be higher than the current bid of {auction['current_bid']:,}.")
                return

            min_increment = self.get_bid_increment(guild, auction['current_bid'])
            if bid_amount < auction['current_bid'] + min_increment:
                await thread.send(f"Your bid must be at least {min_increment:,} higher than the current bid.")
                return

            # Check bidding cooldown
            last_bid_time = await self.config.member(message.author).last_bid_time()
            current_time = datetime.utcnow().timestamp()
            cooldown = await self.config.guild(guild).global_auction_settings.bidding_cooldown()
            if current_time - last_bid_time.get(auction_id, 0) < cooldown:
                await thread.send(f"You must wait {cooldown} seconds between bids.")
                return

            # Check for buy-out
            if auction['buy_out_price'] and bid_amount >= auction['buy_out_price']:
                await self.handle_buyout(guild, auction, message.author, bid_amount)
                return

            auction['current_bid'] = bid_amount
            auction['current_bidder'] = message.author.id
            auction['bid_history'].append({
                'user_id': message.author.id,
                'amount': bid_amount,
                'timestamp': int(current_time)
            })

            # Update last bid time
            async with self.config.member(message.author).last_bid_time() as last_bid_time:
                last_bid_time[auction_id] = current_time

            # Check for auction extension (anti-sniping)
            extension_time = await self.config.guild(guild).auction_extension_time()
            if current_time + extension_time > auction['end_time']:
                auction['end_time'] = current_time + extension_time
                await thread.send(f"Auction extended by {extension_time // 60} minutes due to last-minute bid!")

            auctions[auction_id] = auction

        await message.add_reaction("✅")
        
        embed = discord.Embed(title="New Highest Bid", color=discord.Color.green())
        embed.add_field(name="Bidder", value=message.author.mention, inline=True)
        embed.add_field(name="Amount", value=f"{bid_amount:,}", inline=True)
        await thread.send(embed=embed)

        # Notify outbid users
        await self.notify_outbid_users(guild, auction, message.author.id, bid_amount)

    async def handle_buyout(self, guild: discord.Guild, auction: Dict[str, Any], buyer: discord.Member, amount: int):
        thread_id = auction.get('thread_id')
        thread = guild.get_thread(thread_id) if thread_id else None
        
        auction['status'] = 'completed'
        auction['current_bid'] = amount
        auction['current_bidder'] = buyer.id
        auction['end_time'] = datetime.utcnow().timestamp()
        
        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction['auction_id']] = auction
        
        if thread:
            await thread.send(f"🎉 Auction ended! {buyer.mention} has bought out the auction for {amount:,}!")
            await thread.edit(archived=True, locked=True)
        
        await self.handle_auction_completion(guild, auction, buyer, amount)
        await self.process_auction_queue()

    async def notify_outbid_users(self, guild: discord.Guild, auction: Dict[str, Any], new_bidder_id: int, new_bid_amount: int):
        outbid_users = set(bid['user_id'] for bid in auction['bid_history'] if bid['user_id'] != new_bidder_id)
        for user_id in outbid_users:
            user = guild.get_member(user_id)
            if user:
                try:
                    user_settings = await self.config.member(user).notification_settings()
                    if user_settings['outbid']:
                        await user.send(f"You've been outbid on the auction for {auction['amount']}x {auction['item']}. The new highest bid is {new_bid_amount:,}.")
                except discord.HTTPException:
                    pass  # Unable to send DM to the user

    def is_valid_bid_format(self, content: str) -> bool:
        return content.replace(',', '').isdigit() or content.lower().endswith(('k', 'm', 'b'))

    def parse_bid_amount(self, content: str) -> int:
        content = content.lower().replace(',', '')
        if content.endswith('k'):
            return int(float(content[:-1]) * 1000)
        elif content.endswith('m'):
            return int(float(content[:-1]) * 1000000)
        elif content.endswith('b'):
            return int(float(content[:-1]) * 1000000000)
        else:
            return int(content)

    def get_bid_increment(self, guild: discord.Guild, current_bid: int) -> int:
        bid_increment_tiers = self.config.guild(guild).bid_increment_tiers()
        for tier, increment in sorted(bid_increment_tiers.items(), key=lambda x: int(x[0]), reverse=True):
            if current_bid >= int(tier):
                return increment
        return bid_increment_tiers['0']  # Default increment

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionqueue(self, ctx: commands.Context):
        """Display the current auction queue."""
        queue = await self.config.guild(ctx.guild).auction_queue()
        if not queue:
            await ctx.send("The auction queue is currently empty.")
            return

        embed = discord.Embed(title="Auction Queue", color=discord.Color.blue())
        async with self.config.guild(ctx.guild).auctions() as auctions:
            for i, auction_id in enumerate(queue, start=1):
                auction = auctions.get(auction_id)
                if auction:
                    embed.add_field(
                        name=f"Queue Position {i}",
                        value=f"{auction['amount']}x {auction['item']} (Category: {auction['category']})",
                        inline=False
                    )
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def skipauction(self, ctx: commands.Context):
        """Skip the current auction and start the next one in the queue."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_queue() as queue:
            if not queue:
                await ctx.send("There are no auctions in the queue to skip to.")
                return
            
            skipped_auction_id = queue.pop(0)
        
        await ctx.send(f"Skipped auction {skipped_auction_id}. Starting the next auction in the queue.")
        await self.process_auction_queue()

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def cancelauction(self, ctx: commands.Context, auction_id: str):
        """Cancel a specific auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await ctx.send(f"No auction found with ID {auction_id}.")
                return
            
            auction['status'] = 'cancelled'
            auctions[auction_id] = auction
        
        # Remove from queue if present
        async with self.config.guild(guild).auction_queue() as queue:
            if auction_id in queue:
                queue.remove(auction_id)
        
        # Close and archive the auction thread if it exists
        thread_id = auction.get('thread_id')
        if thread_id:
            thread = guild.get_thread(thread_id)
            if thread:
                await thread.send("This auction has been cancelled by an administrator.")
                await thread.edit(archived=True, locked=True)
        
        await ctx.send(f"Auction {auction_id} has been cancelled.")

    @commands.command()
    async def auctioninfo(self, ctx: commands.Context, auction_id: Optional[str] = None):
        """Display information about the current or a specific auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            if auction_id:
                auction = auctions.get(auction_id)
                if not auction:
                    await ctx.send(f"No auction found with ID {auction_id}.")
                    return
            else:
                active_auctions = [a for a in auctions.values() if a['status'] == 'active']
                if not active_auctions:
                    await ctx.send("There is no active auction at the moment.")
                    return
                auction = active_auctions[0]

        embed = self.create_auction_embed(auction)
        await ctx.send(embed=embed)

    def create_auction_embed(self, auction: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(
            title=f"Auction: {auction['amount']}x {auction['item']}",
            description=f"Category: {auction['category']}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Status", value=auction['status'].capitalize(), inline=True)
        embed.add_field(name="Current Bid", value=f"{auction['current_bid']:,}", inline=True)
        embed.add_field(name="Minimum Bid", value=f"{auction['min_bid']:,}", inline=True)
        if auction['buy_out_price']:
            embed.add_field(name="Buy-out Price", value=f"{auction['buy_out_price']:,}", inline=True)
        embed.add_field(name="Total Value", value=f"{auction['total_value']:,}", inline=True)
        if auction['status'] == 'active':
            embed.add_field(name="Ends At", value=f"<t:{int(auction['end_time'])}:R>", inline=True)
        embed.set_footer(text=f"Auction ID: {auction['auction_id']}")
        return embed

    @commands.command()
    async def bid(self, ctx: commands.Context, amount: int):
        """Place a bid on the current auction."""
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("Bids can only be placed in auction threads.")
            return

        guild = ctx.guild
        auction_id = ctx.channel.name.split('#')[1]

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("There is no active auction in this thread.")
                return

            await self.handle_potential_bid(ctx.message)

    @commands.command()
    async def mybids(self, ctx: commands.Context):
        """View your bid history for the current auction."""
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("This command can only be used in auction threads.")
            return

        guild = ctx.guild
        auction_id = ctx.channel.name.split('#')[1]

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await ctx.send("No auction found for this thread.")
                return

        user_bids = [bid for bid in auction['bid_history'] if bid['user_id'] == ctx.author.id]
        if not user_bids:
            await ctx.send("You haven't placed any bids in this auction.")
            return

        embed = discord.Embed(title="Your Bid History", color=discord.Color.blue())
        for bid in user_bids:
            embed.add_field(
                name=f"Bid at {datetime.fromtimestamp(bid['timestamp'])}",
                value=f"{bid['amount']:,}",
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionreport(self, ctx: commands.Context, days: int = 7):
        """Generate a report of auction activity for the specified number of days."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            now = datetime.utcnow().timestamp()
            relevant_auctions = [a for a in history if now - a['end_time'] <= days * 86400]

        if not relevant_auctions:
            await ctx.send(f"No completed auctions in the last {days} days.")
            return

        total_value = sum(a['current_bid'] for a in relevant_auctions)
        avg_value = total_value / len(relevant_auctions)
        most_valuable = max(relevant_auctions, key=lambda x: x['current_bid'])
        most_bids = max(relevant_auctions, key=lambda x: len(x['bid_history']))

        embed = discord.Embed(title=f"Auction Report (Last {days} Days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=len(relevant_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"{total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"{avg_value:,.2f}", inline=True)
        embed.add_field(name="Most Valuable Auction", value=f"{most_valuable['amount']}x {most_valuable['item']} ({most_valuable['current_bid']:,})", inline=False)
        embed.add_field(name="Most Bids", value=f"{most_bids['amount']}x {most_bids['item']} ({len(most_bids['bid_history'])} bids)", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionmoderator(self, ctx: commands.Context, user: discord.Member):
        """Set a user as an auction moderator."""
        async with self.config.guild(ctx.guild).auction_moderators() as moderators:
            if user.id in moderators:
                await ctx.send(f"{user.name} is already an auction moderator.")
            else:
                moderators.append(user.id)
                await ctx.send(f"{user.name} has been set as an auction moderator.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def removeauctionmoderator(self, ctx: commands.Context, user: discord.Member):
        """Remove a user from being an auction moderator."""
        async with self.config.guild(ctx.guild).auction_moderators() as moderators:
            if user.id in moderators:
                moderators.remove(user.id)
                await ctx.send(f"{user.name} has been removed as an auction moderator.")
            else:
                await ctx.send(f"{user.name} is not an auction moderator.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def listauctionmoderators(self, ctx: commands.Context):
        """List all auction moderators."""
        async with self.config.guild(ctx.guild).auction_moderators() as moderators:
            if not moderators:
                await ctx.send("There are no auction moderators set.")
            else:
                mod_list = [ctx.guild.get_member(mod_id).name for mod_id in moderators if ctx.guild.get_member(mod_id)]
                await ctx.send(f"Auction moderators: {', '.join(mod_list)}")

    @commands.command()
    async def togglenotifications(self, ctx: commands.Context, setting: str):
        """
        Toggle notification settings for auctions.
        Available settings: outbid, auction_start, auction_end, won_auction
        """
        valid_settings = ['outbid', 'auction_start', 'auction_end', 'won_auction']
        if setting not in valid_settings:
            await ctx.send(f"Invalid setting. Please choose from: {', '.join(valid_settings)}")
            return

        async with self.config.member(ctx.author).notification_settings() as settings:
            settings[setting] = not settings.get(setting, True)
            state = "enabled" if settings[setting] else "disabled"
            await ctx.send(f"{setting.replace('_', ' ').title()} notifications have been {state}.")

    @commands.command()
    async def notificationsettings(self, ctx: commands.Context):
        """View your current notification settings for auctions."""
        settings = await self.config.member(ctx.author).notification_settings()
        embed = discord.Embed(title="Your Notification Settings", color=discord.Color.blue())
        for setting, enabled in settings.items():
            embed.add_field(name=setting.replace('_', ' ').title(), value="Enabled" if enabled else "Disabled", inline=False)
        await ctx.send(embed=embed)

    @commands.command()
    async def myauctionstats(self, ctx: commands.Context):
        """View your personal auction statistics."""
        user_id = ctx.author.id
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            user_auctions_sold = [a for a in history if a['user_id'] == user_id]
            user_auctions_won = [a for a in history if a['current_bidder'] == user_id]

        total_sold = sum(a['current_bid'] for a in user_auctions_sold)
        total_bought = sum(a['current_bid'] for a in user_auctions_won)
        total_bids = sum(len([b for b in a['bid_history'] if b['user_id'] == user_id]) for a in history)

        embed = discord.Embed(title=f"Auction Statistics for {ctx.author.name}", color=discord.Color.blue())
        embed.add_field(name="Auctions Sold", value=f"{len(user_auctions_sold)} (Total value: {total_sold:,})", inline=False)
        embed.add_field(name="Auctions Won", value=f"{len(user_auctions_won)} (Total value: {total_bought:,})", inline=False)
        embed.add_field(name="Total Bids Placed", value=str(total_bids), inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def pruneauctionhistory(self, ctx: commands.Context, days: int):
        """Remove auction history older than the specified number of days."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            now = datetime.utcnow().timestamp()
            old_count = len(history)
            history = [a for a in history if now - a['end_time'] <= days * 86400]
            new_count = len(history)

        removed = old_count - new_count
        await ctx.send(f"Removed {removed} auctions from the history. {new_count} auctions remain.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def exportauctiondata(self, ctx: commands.Context, format: str = "csv"):
        """Export auction data in CSV or JSON format."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("There is no auction data to export.")
                return

            if format.lower() == "csv":
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["Auction ID", "Item", "Amount", "Seller", "Winner", "Winning Bid", "Start Time", "End Time"])
                for auction in history:
                    writer.writerow([
                        auction.get('auction_id', 'N/A'),
                        auction['item'],
                        auction['amount'],
                        auction['user_id'],
                        auction.get('current_bidder', 'N/A'),
                        auction['current_bid'],
                        datetime.fromtimestamp(auction['start_time']),
                        datetime.fromtimestamp(auction['end_time'])
                    ])
                file = discord.File(fp=io.BytesIO(output.getvalue().encode()), filename="auction_data.csv")
            elif format.lower() == "json":
                file = discord.File(fp=io.BytesIO(json.dumps(history, indent=2).encode()), filename="auction_data.json")
            else:
                await ctx.send("Invalid format. Please choose 'csv' or 'json'.")
                return

            await ctx.send("Here's your exported auction data:", file=file)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Global error handler for the cog."""
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
        """Delete user data when requested."""
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auction_history() as history:
                for auction in history:
                    if auction['user_id'] == user_id:
                        auction['user_id'] = None
                    auction['bid_history'] = [bid for bid in auction['bid_history'] if bid['user_id'] != user_id]
                    if auction['current_bidder'] == user_id:
                        auction['current_bidder'] = None
            
            async with self.config.guild(guild).banned_users() as banned_users:
                if user_id in banned_users:
                    banned_users.remove(user_id)

        await self.config.user_from_id(user_id).clear()

    @commands.command()
    async def auctionhelp(self, ctx: commands.Context):
        """Display help information for the auction system."""
        embed = discord.Embed(title="Auction System Help", color=discord.Color.blue())
        embed.add_field(name="General Commands", value="""
        • `[p]bid <amount>`: Place a bid on the current auction
        • `[p]auctioninfo [auction_id]`: Display information about the current or a specific auction
        • `[p]mybids`: View your bid history for the current auction
        • `[p]togglenotifications <setting>`: Toggle notification settings
        • `[p]notificationsettings`: View your current notification settings
        • `[p]myauctionstats`: View your personal auction statistics
        """, inline=False)
        
        embed.add_field(name="Admin Commands", value="""
        • `[p]auctionset`: Configure auction settings
        • `[p]spawnauction`: Create a new auction request button
        • `[p]auctionqueue`: Display the current auction queue
        • `[p]skipauction`: Skip the current auction
        • `[p]cancelauction <auction_id>`: Cancel a specific auction
        • `[p]auctionreport [days]`: Generate an auction report
        • `[p]setauctionmoderator <user>`: Set a user as auction moderator
        • `[p]removeauctionmoderator <user>`: Remove auction moderator status
        • `[p]listauctionmoderators`: List all auction moderators
        • `[p]pruneauctionhistory <days>`: Remove old auction history
        • `[p]exportauctiondata [format]`: Export auction data
        """, inline=False)
        
        await ctx.send(embed=embed)

    async def update_bidder_reputation(self, guild: discord.Guild, user_id: int, action: str):
        """Update bidder reputation based on their actions."""
        async with self.config.member_from_ids(guild.id, user_id).reputation_score() as reputation:
            if action == 'increase':
                reputation = min(reputation + 1, 100)
            elif action == 'decrease':
                reputation = max(reputation - 1, 0)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setreputation(self, ctx: commands.Context, user: discord.Member, score: int):
        """Set the reputation score for a user (0-100)."""
        if 0 <= score <= 100:
            await self.config.member(user).reputation_score.set(score)
            await ctx.send(f"{user.name}'s reputation score has been set to {score}.")
        else:
            await ctx.send("Reputation score must be between 0 and 100.")

    @commands.command()
    async def reputation(self, ctx: commands.Context, user: discord.Member = None):
        """View your reputation score or the score of another user."""
        target = user or ctx.author
        score = await self.config.member(target).reputation_score()
        await ctx.send(f"{target.name}'s reputation score is {score}.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def resetauctions(self, ctx: commands.Context):
        """Reset all auction data. Use with caution!"""
        confirm = await ctx.send("Are you sure you want to reset all auction data? This action cannot be undone. Reply with 'yes' to confirm.")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == 'yes'

        try:
            await self.bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send("Reset cancelled.")
            return

        await self.config.guild(ctx.guild).clear()
        await self.config.guild(ctx.guild).set(self.config.guild(ctx.guild).defaults)
        await ctx.send("All auction data has been reset.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionsettings(self, ctx: commands.Context):
        """Display current auction settings."""
        settings = await self.config.guild(ctx.guild).get_raw()
        embed = discord.Embed(title="Auction Settings", color=discord.Color.blue())
        
        for key, value in settings.items():
            if isinstance(value, dict):
                embed.add_field(name=key, value="\n".join(f"{k}: {v}" for k, v in value.items()), inline=False)
            else:
                embed.add_field(name=key, value=str(value), inline=True)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionduration(self, ctx: commands.Context, hours: int):
        """Set the default duration for auctions."""
        if hours < 1:
            await ctx.send("Auction duration must be at least 1 hour.")
            return
        
        await self.config.guild(ctx.guild).auction_duration.set(hours * 3600)
        await ctx.send(f"Default auction duration set to {hours} hours.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionextension(self, ctx: commands.Context, minutes: int):
        """Set the auction extension time for last-minute bids."""
        if minutes < 1:
            await ctx.send("Extension time must be at least 1 minute.")
            return
        
        await self.config.guild(ctx.guild).auction_extension_time.set(minutes * 60)
        await ctx.send(f"Auction extension time set to {minutes} minutes.")

    def cog_unload(self):
        """Clean up when cog is unloaded."""
        self.bot.loop.create_task(self._unload())

    async def _unload(self):
        """Cancel any ongoing tasks."""
        if self.auction_task:
            self.auction_task.cancel()

    async def initialize(self):
        """Initialize the cog. This method is called when the cog is loaded."""
        await self.migrate_data()
        await self.cleanup_auctions()

    async def migrate_data(self):
        """Migrate data from old format to new format if necessary."""
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in auctions.items():
                    if 'thread_id' not in auction:
                        auction['thread_id'] = None
                    if 'buy_out_price' not in auction:
                        auction['buy_out_price'] = None

    async def cleanup_auctions(self):
        """Clean up any auctions that might have been left in an inconsistent state."""
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in list(auctions.items()):
                    if auction['status'] == 'active' and auction['end_time'] < datetime.utcnow().timestamp():
                        await self.end_auction(guild, auction_id)

async def setup(bot: Red):
    """Add the cog to the bot."""
    cog = EnhancedAdvancedAuction(bot)
    await cog.initialize()
    await bot.add_cog(cog)