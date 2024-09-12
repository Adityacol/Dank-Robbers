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

class EnhancedAdvancedAuction(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        default_guild: Dict[str, Any] = {
            "active_auction": None,
            "auction_queue": [],
            "auction_channel": None,
            "log_channel": None,
            "queue_channel": None,
            "auction_role": None,
            "blacklist_role": None,
            "auction_ping_role": None,
            "massive_auction_ping_role": None,
            "scheduled_auctions": {},
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
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.auction_task: Optional[asyncio.Task] = None
        self.queue_lock = asyncio.Lock()
        self.donation_locks: Dict[str, asyncio.Lock] = {}

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
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in auction loop: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def process_auction_queue(self) -> None:
        async with self.queue_lock:
            for guild in self.bot.guilds:
                active_auction = await self.config.guild(guild).active_auction()
                if active_auction is None:
                    queue = await self.config.guild(guild).auction_queue()
                    if queue:
                        next_auction = queue.pop(0)
                        await self.start_auction(guild, next_auction)
                        await self.config.guild(guild).auction_queue.set(queue)

    async def check_auction_end(self) -> None:
        for guild in self.bot.guilds:
            active_auction = await self.config.guild(guild).active_auction()
            if active_auction and active_auction['end_time'] <= datetime.utcnow().timestamp():
                await self.end_auction(guild)

    async def start_auction(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        auction['start_time'] = datetime.utcnow().timestamp()
        auction['end_time'] = auction['start_time'] + await self.config.guild(guild).auction_duration()
        auction['status'] = 'active'
        await self.config.guild(guild).active_auction.set(auction)

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            embed = self.create_auction_embed(auction)
            await channel.send("New auction started!", embed=embed)

        # Notify subscribers
        await self.notify_subscribers(guild, auction)

    async def end_auction(self, guild: discord.Guild) -> None:
        auction = await self.config.guild(guild).active_auction()
        if auction is None:
            return

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            if auction['current_bidder']:
                winner = guild.get_member(auction['current_bidder'])
                await channel.send(f"Auction ended! The winner is {winner.mention} with a bid of {auction['current_bid']:,}.")
                await self.handle_auction_completion(guild, auction, winner, auction['current_bid'])
            else:
                await channel.send("Auction ended with no bids.")

        await self.config.guild(guild).active_auction.set(None)
        await self.update_auction_history(guild, auction)

        # Start next auction in queue
        await self.process_auction_queue()

    async def handle_auction_completion(self, guild: discord.Guild, auction: Dict[str, Any], winner: discord.Member, winning_bid: int) -> None:
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = self.bot.get_channel(log_channel_id)

        if log_channel:
            await log_channel.send(f"Auction completed. Winner: {winner.mention}, Amount: {winning_bid:,}")
            await log_channel.send(f"/serverevents payout user:{winner.id} quantity:{auction['amount']} item:{auction['item']}")
            await log_channel.send(f"/serverevents payout user:{auction['user_id']} quantity:{winning_bid}")

        await self.update_user_stats(guild, winner.id, winning_bid, 'won')
        await self.update_user_stats(guild, auction['user_id'], winning_bid, 'sold')

        try:
            await winner.send(f"Congratulations! You won the auction for {auction['amount']}x {auction['item']} with a bid of {winning_bid:,}. The item will be delivered to you shortly.")
        except discord.HTTPException:
            pass

    async def update_auction_history(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        async with self.config.guild(guild).auction_history() as history:
            history.append(auction)

    async def notify_subscribers(self, guild: discord.Guild, auction: Dict[str, Any]) -> None:
        for member in guild.members:
            async with self.config.member(member).notification_settings() as settings:
                if settings.get('auction_start', True):
                    try:
                        await member.send(f"New auction started: {auction['amount']}x {auction['item']} (Category: {auction['category']})")
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

    @auctionset.command(name="pingduration")
    async def set_ping_duration(self, ctx: commands.Context, duration: int):
        """Set the duration (in seconds) for which users can be pinged after an auction starts."""
        await self.config.guild(ctx.guild).auction_ping_duration.set(duration)
        await ctx.send(f"Auction ping duration set to {duration} seconds.")

    @auctionset.command(name="pingrole")
    async def set_ping_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be pinged when a new auction starts."""
        await self.config.guild(ctx.guild).auction_ping_role.set(role.id)
        await ctx.send(f"Auction ping role set to {role.name}.")

    @auctionset.command(name="massivepingrole")
    async def set_massive_ping_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be pinged when a new massive auction starts."""
        await self.config.guild(ctx.guild).massive_auction_ping_role.set(role.id)
        await ctx.send(f"Massive auction ping role set to {role.name}.")

    @auctionset.command(name="blacklistrole")
    async def set_blacklist_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be assigned to blacklisted users."""
        await self.config.guild(ctx.guild).blacklist_role.set(role.id)
        await ctx.send(f"Blacklist role set to {role.name}.")

    @auctionset.command(name="duration")
    async def set_auction_duration(self, ctx: commands.Context, duration: int):
        """Set the default duration for auctions (in hours)."""
        await self.config.guild(ctx.guild).auction_duration.set(duration * 3600)
        await ctx.send(f"Default auction duration set to {duration} hours.")

    @auctionset.command(name="minincrement")
    async def set_minimum_increment(self, ctx: commands.Context, amount: int):
        """Set the minimum bid increment."""
        await self.config.guild(ctx.guild).minimum_bid_increment.set(amount)
        await ctx.send(f"Minimum bid increment set to {amount:,}.")

    @auctionset.command(name="extensiontime")
    async def set_extension_time(self, ctx: commands.Context, seconds: int):
        """Set the auction extension time (in seconds) for last-minute bids."""
        await self.config.guild(ctx.guild).auction_extension_time.set(seconds)
        await ctx.send(f"Auction extension time set to {seconds} seconds.")

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
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. Your auction will be added to the queue.", inline=False)
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
        message = TextInput(label="What is your message?", placeholder="e.g., I love DR!", required=False, max_length=200)
        category = TextInput(label="Category", placeholder="e.g., Rare", required=False)

        async def on_submit(self, interaction: discord.Interaction):
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
        auction_data = {
            "user_id": interaction.user.id,
            "item": item_name,
            "amount": item_count,
            "min_bid": min_bid,
            "message": message,
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
                scheduled[str(len(scheduled))] = auction_data
            await interaction.followup.send(f"Auction scheduled for {schedule_time}")
        else:
            async with self.config.guild(guild).auction_queue() as queue:
                queue.append(auction_data)
            await interaction.followup.send("Your auction has been added to the queue.")

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
            embed.add_field(name="Status", value="In Queue", inline=True)
        
        await interaction.followup.send(content=interaction.user.mention, embed=embed)

        # Assign the auction role
        auction_role_id = await self.config.guild(guild).auction_role()
        if auction_role_id:
            auction_role = guild.get_role(auction_role_id)
            if auction_role:
                await interaction.user.add_roles(auction_role)

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

        if message.author.id == 270904126974590976:  # Dank Memer bot ID
            await self.handle_dank_memer_message(message)
        else:
            await self.handle_potential_bid(message)

    async def handle_dank_memer_message(self, message: discord.Message):
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
        guild = message.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            log.info("No active auction found")
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

            if is_tax_payment:
                active_auction["donated_tax"] = active_auction.get("donated_tax", 0) + donated_amount
                remaining_tax = active_auction["tax"] - active_auction["donated_tax"]
                remaining_amount = active_auction["amount"] - active_auction.get("donated_amount", 0)
            else:
                cleaned_donated_item = ' '.join(word for word in donated_item.split() if not word.startswith('<') and not word.endswith('>')).lower()
                cleaned_auction_item = active_auction["item"].lower()

                log.info(f"Cleaned item names - Donated: {cleaned_donated_item}, Auction: {cleaned_auction_item}")

                if cleaned_donated_item != cleaned_auction_item:
                    await message.channel.send(f"This item doesn't match the auction item. Expected {active_auction['item']}, but received {donated_item}.")
                    return

                active_auction["donated_amount"] = active_auction.get("donated_amount", 0) + donated_amount
                remaining_amount = active_auction["amount"] - active_auction["donated_amount"]
                remaining_tax = active_auction["tax"] - active_auction.get("donated_tax", 0)

            log.info(f"Updated auction: {active_auction}")

            if remaining_amount <= 0 and remaining_tax <= 0:
                active_auction["status"] = "active"
                active_auction["start_time"] = datetime.utcnow().timestamp()
                active_auction["end_time"] = active_auction["start_time"] + await self.config.guild(guild).auction_duration()
                await self.config.guild(guild).active_auction.set(active_auction)
                await self.announce_auction_start(guild, active_auction)
            else:
                embed = discord.Embed(
                    title="Donation Received",
                    description="Thank you for your donation. Here's what's left:",
                    color=discord.Color.green()
                )
                if remaining_amount > 0:
                    embed.add_field(name="Remaining Items", value=f"{remaining_amount}x {active_auction['item']}", inline=False)
                if remaining_tax > 0:
                    embed.add_field(name="Remaining Tax", value=f"⏣ {remaining_tax:,}", inline=False)
                await message.channel.send(embed=embed)

            await self.config.guild(guild).active_auction.set(active_auction)

        except Exception as e:
            log.error(f"Error processing donation: {e}", exc_info=True)
            await message.channel.send(f"An error occurred while processing the donation: {str(e)}. Please contact an administrator.")

    async def announce_auction_start(self, guild: discord.Guild, auction: Dict[str, Any]):
        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            embed = self.create_auction_embed(auction)
            
            # Determine which role to ping
            ping_role_id = await self.config.guild(guild).auction_ping_role()
            massive_ping_role_id = await self.config.guild(guild).massive_auction_ping_role()
            
            if auction['total_value'] >= 500_000_000 and massive_ping_role_id:  # 500 million threshold for massive auctions
                ping_role = guild.get_role(massive_ping_role_id)
            elif ping_role_id:
                ping_role = guild.get_role(ping_role_id)
            else:
                ping_role = None

            content = "New auction started!"
            if ping_role:
                content = f"{ping_role.mention} {content}"

            await channel.send(content, embed=embed)

    def create_auction_embed(self, auction: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(
            title=f"Auction: {auction['amount']}x {auction['item']}",
            description=auction['message'],
            color=discord.Color.gold()
        )
        embed.add_field(name="Category", value=auction['category'], inline=True)
        embed.add_field(name="Starting Bid", value=f"{auction['min_bid']:,}", inline=True)
        embed.add_field(name="Current Bid", value=f"{auction['current_bid']:,}", inline=True)
        embed.add_field(name="Ends At", value=f"<t:{int(auction['end_time'])}:R>", inline=True)
        embed.set_footer(text=f"Total Value: {auction['total_value']:,}")
        return embed

    async def handle_potential_bid(self, message: discord.Message):
        if not self.is_valid_bid_format(message.content):
            return

        guild = message.guild
        active_auction = await self.config.guild(guild).active_auction()

        if not active_auction or active_auction['status'] != 'active':
            return

        bid_amount = self.parse_bid_amount(message.content)
        if bid_amount <= active_auction['current_bid']:
            await message.channel.send(f"Your bid must be higher than the current bid of {active_auction['current_bid']:,}.")
            return

        min_increment = await self.config.guild(guild).minimum_bid_increment()
        if bid_amount < active_auction['current_bid'] + min_increment:
            await message.channel.send(f"Your bid must be at least {min_increment:,} higher than the current bid.")
            return

        # Check bidding cooldown
        last_bid_time = await self.config.member(message.author).last_bid_time()
        current_time = datetime.utcnow().timestamp()
        cooldown = await self.config.guild(guild).global_auction_settings.bidding_cooldown()
        if current_time - last_bid_time.get(str(active_auction['auction_id']), 0) < cooldown:
            await message.channel.send(f"You must wait {cooldown} seconds between bids.")
            return

        active_auction['current_bid'] = bid_amount
        active_auction['current_bidder'] = message.author.id
        active_auction['bid_history'].append({
            'user_id': message.author.id,
            'amount': bid_amount,
            'timestamp': int(current_time)
        })

        # Update last bid time
        last_bid_time[str(active_auction['auction_id'])] = current_time
        await self.config.member(message.author).last_bid_time.set(last_bid_time)

        # Check for auction extension
        extension_time = await self.config.guild(guild).auction_extension_time()
        if current_time + extension_time > active_auction['end_time']:
            active_auction['end_time'] = current_time + extension_time
            await message.channel.send(f"Auction extended by {extension_time // 60} minutes due to last-minute bid!")

        await self.config.guild(guild).active_auction.set(active_auction)
        await message.add_reaction("✅")
        
        embed = discord.Embed(title="New Highest Bid", color=discord.Color.green())
        embed.add_field(name="Bidder", value=message.author.mention, inline=True)
        embed.add_field(name="Amount", value=f"{bid_amount:,}", inline=True)
        await message.channel.send(embed=embed)

        # Notify outbid users
        await self.notify_outbid_users(guild, active_auction, message.author.id, bid_amount)

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

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionqueue(self, ctx: commands.Context):
        """Display the current auction queue."""
        queue = await self.config.guild(ctx.guild).auction_queue()
        if not queue:
            await ctx.send("The auction queue is currently empty.")
            return

        embed = discord.Embed(title="Auction Queue", color=discord.Color.blue())
        for i, auction in enumerate(queue, start=1):
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
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction to skip.")
            return

        await self.end_auction(guild)
        await ctx.send("The current auction has been skipped. Starting the next auction in the queue.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def cancelauction(self, ctx: commands.Context):
        """Cancel the current auction without starting the next one."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction to cancel.")
            return

        await self.config.guild(guild).active_auction.set(None)
        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send("The current auction has been cancelled by an administrator.")
        await ctx.send("The current auction has been cancelled.")

    @commands.command()
    async def auctioninfo(self, ctx: commands.Context):
        """Display information about the current auction."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction at the moment.")
            return

        embed = self.create_auction_embed(active_auction)
        await ctx.send(embed=embed)

    @commands.command()
    async def bid(self, ctx: commands.Context, amount: int):
        """Place a bid on the current auction."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction to bid on.")
            return

        if amount <= active_auction['current_bid']:
            await ctx.send(f"Your bid must be higher than the current bid of {active_auction['current_bid']:,}.")
            return

        min_increment = await self.config.guild(guild).minimum_bid_increment()
        if amount < active_auction['current_bid'] + min_increment:
            await ctx.send(f"Your bid must be at least {min_increment:,} higher than the current bid.")
            return

        # Check bidding cooldown
        last_bid_time = await self.config.member(ctx.author).last_bid_time()
        current_time = datetime.utcnow().timestamp()
        cooldown = await self.config.guild(guild).global_auction_settings.bidding_cooldown()
        if current_time - last_bid_time.get(str(active_auction['auction_id']), 0) < cooldown:
            await ctx.send(f"You must wait {cooldown} seconds between bids.")
            return

        active_auction['current_bid'] = amount
        active_auction['current_bidder'] = ctx.author.id
        active_auction['bid_history'].append({
            'user_id': ctx.author.id,
            'amount': amount,
            'timestamp': int(current_time)
        })

        # Update last bid time
        last_bid_time[str(active_auction['auction_id'])] = current_time
        await self.config.member(ctx.author).last_bid_time.set(last_bid_time)

        # Check for auction extension
        extension_time = await self.config.guild(guild).auction_extension_time()
        if current_time + extension_time > active_auction['end_time']:
            active_auction['end_time'] = current_time + extension_time
            await ctx.send(f"Auction extended by {extension_time // 60} minutes due to last-minute bid!")

        await self.config.guild(guild).active_auction.set(active_auction)
        
        embed = discord.Embed(title="New Highest Bid", color=discord.Color.green())
        embed.add_field(name="Bidder", value=ctx.author.mention, inline=True)
        embed.add_field(name="Amount", value=f"{amount:,}", inline=True)
        await ctx.send(embed=embed)

        # Notify outbid users
        await self.notify_outbid_users(guild, active_auction, ctx.author.id, amount)

    @commands.command()
    async def mybids(self, ctx: commands.Context):
        """View your bid history for the current auction."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction at the moment.")
            return

        user_bids = [bid for bid in active_auction['bid_history'] if bid['user_id'] == ctx.author.id]
        if not user_bids:
            await ctx.send("You haven't placed any bids in the current auction.")
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
    @checks.admin_or_permissions(manage_guild=True)
    async def blacklistuser(self, ctx: commands.Context, user: discord.Member, *, reason: str = "No reason provided"):
        """Blacklist a user from participating in auctions."""
        async with self.config.guild(ctx.guild).banned_users() as banned_users:
            if user.id in banned_users:
                await ctx.send(f"{user.name} is already blacklisted from auctions.")
            else:
                banned_users.append(user.id)
                await ctx.send(f"{user.name} has been blacklisted from auctions. Reason: {reason}")

        blacklist_role_id = await self.config.guild(ctx.guild).blacklist_role()
        if blacklist_role_id:
            blacklist_role = ctx.guild.get_role(blacklist_role_id)
            if blacklist_role:
                await user.add_roles(blacklist_role, reason=f"Blacklisted from auctions: {reason}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def unblacklistuser(self, ctx: commands.Context, user: discord.Member):
        """Remove a user from the auction blacklist."""
        async with self.config.guild(ctx.guild).banned_users() as banned_users:
            if user.id in banned_users:
                banned_users.remove(user.id)
                await ctx.send(f"{user.name} has been removed from the auction blacklist.")
            else:
                await ctx.send(f"{user.name} is not blacklisted from auctions.")

        blacklist_role_id = await self.config.guild(ctx.guild).blacklist_role()
        if blacklist_role_id:
            blacklist_role = ctx.guild.get_role(blacklist_role_id)
            if blacklist_role and blacklist_role in user.roles:
                await user.remove_roles(blacklist_role, reason="Removed from auction blacklist")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def listblacklist(self, ctx: commands.Context):
        """List all users blacklisted from auctions."""
        banned_users = await self.config.guild(ctx.guild).banned_users()
        if not banned_users:
            await ctx.send("There are no users blacklisted from auctions.")
        else:
            banned_list = [ctx.guild.get_member(user_id).name for user_id in banned_users if ctx.guild.get_member(user_id)]
            await ctx.send(f"Users blacklisted from auctions: {', '.join(banned_list)}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def clearbids(self, ctx: commands.Context):
        """Clear all bids from the current auction."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction.")
            return

        active_auction['current_bid'] = int(active_auction['min_bid'].replace(',', ''))
        active_auction['current_bidder'] = None
        active_auction['bid_history'] = []

        await self.config.guild(guild).active_auction.set(active_auction)
        await ctx.send("All bids have been cleared from the current auction.")

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send("All bids have been cleared by an administrator. The auction will continue with the starting bid.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def extendauction(self, ctx: commands.Context, minutes: int):
        """Extend the current auction by a specified number of minutes."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction.")
            return

        active_auction['end_time'] += minutes * 60
        await self.config.guild(guild).active_auction.set(active_auction)
        
        new_end_time = datetime.fromtimestamp(active_auction['end_time'])
        await ctx.send(f"The auction has been extended. New end time: {new_end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(f"The auction has been extended by {minutes} minutes by an administrator.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setcurrentbid(self, ctx: commands.Context, amount: int):
        """Set the current bid for the active auction."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction.")
            return

        old_bid = active_auction['current_bid']
        active_auction['current_bid'] = amount
        await self.config.guild(guild).active_auction.set(active_auction)
        
        await ctx.send(f"The current bid has been updated from {old_bid:,} to {amount:,}.")

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(f"The current bid has been updated to {amount:,} by an administrator.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def removebid(self, ctx: commands.Context, user: discord.Member):
        """Remove the last bid of a specific user from the current auction."""
        guild = ctx.guild
        active_auction = await self.config.guild(guild).active_auction()
        if active_auction is None:
            await ctx.send("There is no active auction.")
            return

        user_bids = [bid for bid in active_auction['bid_history'] if bid['user_id'] == user.id]
        if not user_bids:
            await ctx.send(f"{user.name} has not placed any bids in the current auction.")
            return

        last_bid = user_bids[-1]
        active_auction['bid_history'].remove(last_bid)

        if active_auction['current_bidder'] == user.id:
            if len(active_auction['bid_history']) > 0:
                new_highest_bid = max(active_auction['bid_history'], key=lambda x: x['amount'])
                active_auction['current_bid'] = new_highest_bid['amount']
                active_auction['current_bidder'] = new_highest_bid['user_id']
            else:
                active_auction['current_bid'] = int(active_auction['min_bid'].replace(',', ''))
                active_auction['current_bidder'] = None

        await self.config.guild(guild).active_auction.set(active_auction)
        await ctx.send(f"The last bid of {user.name} has been removed from the current auction.")

        channel_id = await self.config.guild(guild).auction_channel()
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(f"The last bid of {user.mention} has been removed by an administrator. "
                               f"The current highest bid is now {active_auction['current_bid']:,}.")

    @commands.command()
    async def auctionhistory(self, ctx: commands.Context, page: int = 1):
        """View your auction participation history."""
        user_id = ctx.author.id
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            user_auctions = [a for a in history if a['user_id'] == user_id or user_id in [b['user_id'] for b in a['bid_history']]]

        if not user_auctions:
            await ctx.send("You haven't participated in any auctions yet.")
            return

        items_per_page = 5
        pages = math.ceil(len(user_auctions) / items_per_page)
        if page < 1 or page > pages:
            await ctx.send(f"Invalid page number. Please choose a page between 1 and {pages}.")
            return

        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        current_auctions = user_auctions[start_idx:end_idx]

        embed = discord.Embed(title=f"Your Auction History (Page {page}/{pages})", color=discord.Color.blue())
        for auction in current_auctions:
            if auction['user_id'] == user_id:
                role = "Seller"
                result = f"Sold for {auction['current_bid']:,}" if auction['current_bidder'] else "No bids"
            else:
                role = "Bidder"
                if auction['current_bidder'] == user_id:
                    result = f"Won for {auction['current_bid']:,}"
                else:
                    user_max_bid = max([b['amount'] for b in auction['bid_history'] if b['user_id'] == user_id])
                    result = f"Outbid (Your max: {user_max_bid:,})"

            embed.add_field(
                name=f"{auction['amount']}x {auction['item']}",
                value=f"Role: {role}\nResult: {result}\nDate: <t:{int(auction['end_time'])}:F>",
                inline=False
            )

        await ctx.send(embed=embed)

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

    @commands.command()
    async def leaderboard(self, ctx: commands.Context, category: str = "all"):
        """View the auction leaderboard."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("There is no auction data available for a leaderboard.")
                return

            if category.lower() not in ["all", "sellers", "buyers"]:
                await ctx.send("Invalid category. Please choose 'all', 'sellers', or 'buyers'.")
                return

            seller_stats = {}
            buyer_stats = {}

            for auction in history:
                seller_id = auction['user_id']
                if seller_id not in seller_stats:
                    seller_stats[seller_id] = {"auctions": 0, "value": 0}
                seller_stats[seller_id]["auctions"] += 1
                seller_stats[seller_id]["value"] += auction['current_bid']

                if auction['current_bidder']:
                    buyer_id = auction['current_bidder']
                    if buyer_id not in buyer_stats:
                        buyer_stats[buyer_id] = {"auctions": 0, "value": 0}
                    buyer_stats[buyer_id]["auctions"] += 1
                    buyer_stats[buyer_id]["value"] += auction['current_bid']

            embed = discord.Embed(title=f"Auction Leaderboard - {category.capitalize()}", color=discord.Color.gold())

            if category.lower() in ["all", "sellers"]:
                top_sellers = sorted(seller_stats.items(), key=lambda x: x[1]["value"], reverse=True)[:5]
                seller_list = "\n".join([f"{ctx.guild.get_member(user_id).name}: {stats['auctions']} auctions, {stats['value']:,} total value" for user_id, stats in top_sellers])
                embed.add_field(name="Top Sellers", value=seller_list or "No data", inline=False)

            if category.lower() in ["all", "buyers"]:
                top_buyers = sorted(buyer_stats.items(), key=lambda x: x[1]["value"], reverse=True)[:5]
                buyer_list = "\n".join([f"{ctx.guild.get_member(user_id).name}: {stats['auctions']} auctions, {stats['value']:,} total value" for user_id, stats in top_buyers])
                embed.add_field(name="Top Buyers", value=buyer_list or "No data", inline=False)

            await ctx.send(embed=embed)

    @commands.command()
    async def mystats(self, ctx: commands.Context):
        """View your personal auction statistics."""
        user_id = ctx.author.id
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            user_auctions_sold = [a for a in history if a['user_id'] == user_id]
            user_auctions_bought = [a for a in history if a['current_bidder'] == user_id]

        total_sold = sum(a['current_bid'] for a in user_auctions_sold)
        total_bought = sum(a['current_bid'] for a in user_auctions_bought)
        total_bids = sum(len([b for b in a['bid_history'] if b['user_id'] == user_id]) for a in history)

        embed = discord.Embed(title=f"Auction Statistics for {ctx.author.name}", color=discord.Color.blue())
        embed.add_field(name="Auctions Sold", value=f"{len(user_auctions_sold)} (Total value: {total_sold:,})", inline=False)
        embed.add_field(name="Auctions Won", value=f"{len(user_auctions_bought)} (Total value: {total_bought:,})", inline=False)
        embed.add_field(name="Total Bids Placed", value=str(total_bids), inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionanalytics(self, ctx: commands.Context, days: int = 30):
        """View analytics for auctions in the past number of days."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            now = datetime.utcnow().timestamp()
            recent_auctions = [a for a in history if now - a['end_time'] <= days * 86400]

        if not recent_auctions:
            await ctx.send(f"No auctions have been completed in the last {days} days.")
            return

        total_auctions = len(recent_auctions)
        total_value = sum(a['current_bid'] for a in recent_auctions)
        average_value = total_value / total_auctions
        total_bids = sum(len(a['bid_history']) for a in recent_auctions)
        average_bids = total_bids / total_auctions

        categories = {}
        for auction in recent_auctions:
            category = auction['category']
            if category not in categories:
                categories[category] = {"count": 0, "value": 0}
            categories[category]["count"] += 1
            categories[category]["value"] += auction['current_bid']

        embed = discord.Embed(title=f"Auction Analytics (Last {days} days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=str(total_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"{total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"{average_value:,.2f}", inline=True)
        embed.add_field(name="Total Bids", value=str(total_bids), inline=True)
        embed.add_field(name="Average Bids per Auction", value=f"{average_bids:.2f}", inline=True)

        for category, stats in categories.items():
            embed.add_field(name=f"Category: {category}", value=f"Count: {stats['count']}, Value: {stats['value']:,}", inline=False)

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

    async def initialize(self):
        """Initialize the cog. This method is called when the cog is loaded."""
        await self.migrate_data()
        await self.cleanup_auctions()

    async def migrate_data(self):
        """Migrate data from old format to new format if necessary."""
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auction_history() as history:
                for auction in history:
                    if 'category' not in auction:
                        auction['category'] = 'General'
                    if 'bid_history' not in auction:
                        auction['bid_history'] = []

    async def cleanup_auctions(self):
        """Clean up any auctions that might have been left in an inconsistent state."""
        for guild in self.bot.guilds:
            active_auction = await self.config.guild(guild).active_auction()
            if active_auction and active_auction['end_time'] < datetime.utcnow().timestamp():
                await self.end_auction(guild)

    @commands.command()
    async def auctionhelp(self, ctx: commands.Context):
        """Display help information for the auction system."""
        embed = discord.Embed(title="Auction System Help", color=discord.Color.blue())
        embed.add_field(name="General Commands", value="""
        • `[p]bid <amount>`: Place a bid on the current auction
        • `[p]auctioninfo`: Display information about the current auction
        • `[p]mybids`: View your bid history for the current auction
        • `[p]togglenotifications <setting>`: Toggle notification settings
        • `[p]notificationsettings`: View your current notification settings
        • `[p]auctionhistory [page]`: View your auction participation history
        • `[p]mystats`: View your personal auction statistics
        • `[p]leaderboard [category]`: View the auction leaderboard
        """, inline=False)
        
        embed.add_field(name="Admin Commands", value="""
        • `[p]auctionset`: Configure auction settings
        • `[p]spawnauction`: Create a new auction request button
        • `[p]auctionqueue`: Display the current auction queue
        • `[p]skipauction`: Skip the current auction
        • `[p]cancelauction`: Cancel the current auction
        • `[p]auctionreport [days]`: Generate an auction report
        • `[p]setauctionmoderator <user>`: Set a user as auction moderator
        • `[p]removeauctionmoderator <user>`: Remove auction moderator status
        • `[p]listauctionmoderators`: List all auction moderators
        • `[p]blacklistuser <user> [reason]`: Blacklist a user from auctions
        • `[p]unblacklistuser <user>`: Remove a user from the blacklist
        • `[p]listblacklist`: List all blacklisted users
        • `[p]clearbids`: Clear all bids from the current auction
        • `[p]extendauction <minutes>`: Extend the current auction
        • `[p]setcurrentbid <amount>`: Set the current bid for the active auction
        • `[p]removebid <user>`: Remove the last bid of a specific user
        • `[p]exportauctiondata [format]`: Export auction data
        • `[p]auctionanalytics [days]`: View auction analytics
        • `[p]pruneauctionhistory <days>`: Remove old auction history
        """, inline=False)
        
        await ctx.send(embed=embed)

    def cog_unload(self):
        """Clean up when cog is unloaded."""
        self.bot.loop.create_task(self._unload())

    async def _unload(self):
        """Cancel any ongoing tasks."""
        if self.auction_task:
            self.auction_task.cancel()

async def setup(bot: Red):
    """Add the cog to the bot."""
    cog = EnhancedAdvancedAuction(bot)
    await cog.initialize()
    await bot.add_cog(cog)