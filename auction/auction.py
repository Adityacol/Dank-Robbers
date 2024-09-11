import discord
from discord.ext import commands
from redbot.core import Config, checks
import aiohttp
import asyncio
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, Any, List, Union


log = logging.getLogger("red.economy.AdvancedAuction")

class AdvancedAuction(commands.Cog):
    """
    A comprehensive auction system for Discord servers.
    This cog allows users to create, manage, and participate in auctions.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        # Default settings for guilds
        default_guild: Dict[str, Any] = {
            "auctions": {},
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
            "categories": [],  # Categories will be fetched from API
            "max_active_auctions": 10,
            "auction_cooldown": 86400,  # 24 hours in seconds
            "minimum_bid_increment": 1000,
            "auction_duration": 21600,  # 6 hours in seconds
        }
        
        # Default settings for members
        default_member: Dict[str, Any] = {
            "auction_reminders": [],
            "auction_subscriptions": []
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.auction_tasks: Dict[str, asyncio.Task] = {}
        self.queue_lock = asyncio.Lock()
        self.queue_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Initialize the cog by starting the queue manager."""
        self.queue_task = self.bot.loop.create_task(self.queue_manager())

    async def cog_unload(self) -> None:
        """Clean up tasks when the cog is unloaded."""
        if self.queue_task:
            self.queue_task.cancel()
        for task in self.auction_tasks.values():
            task.cancel()
        await asyncio.gather(*self.auction_tasks.values(), return_exceptions=True)

    async def queue_manager(self) -> None:
        """
        Manage the auction queue by processing it at regular intervals.
        This function runs indefinitely until the cog is unloaded.
        """
        while True:
            try:
                await self.process_queue()
                await asyncio.sleep(60)  # Check every minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in queue manager: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait a bit before retrying

    async def process_queue(self) -> None:
        """
        Process the auction queue for each guild.
        This function handles scheduled auctions and starts pending auctions.
        """
        async with self.queue_lock:
            for guild in self.bot.guilds:
                await self.process_scheduled_auctions(guild)
                await self.process_auction_queue(guild)

    async def process_scheduled_auctions(self, guild: discord.Guild) -> None:
        """Process scheduled auctions for a guild."""
        scheduled_auctions = await self.config.guild(guild).scheduled_auctions()
        current_time = datetime.utcnow().timestamp()
        
        for auction_id, scheduled_time in list(scheduled_auctions.items()):
            if current_time >= scheduled_time:
                await self.start_scheduled_auction(guild, auction_id)
                del scheduled_auctions[auction_id]
        
        await self.config.guild(guild).scheduled_auctions.set(scheduled_auctions)

    async def process_auction_queue(self, guild: discord.Guild) -> None:
        """Process the auction queue for a guild."""
        queue = await self.config.guild(guild).auction_queue()
        if queue:
            auction_id = queue[0]
            auctions = await self.config.guild(guild).auctions()
            auction = auctions.get(auction_id)
            if auction and auction['status'] == 'pending':
                await self.start_auction(guild, auction_id)
            elif auction and auction['status'] in ['completed', 'cancelled']:
                await self.cleanup_auction(guild, auction_id)
            
            await self.config.guild(guild).auctions.set(auctions)
            await self.config.guild(guild).auction_queue.set(queue)
class AuctionScheduleView(discord.ui.View):
    def __init__(self, cog: "AdvancedAuction"):
        super().__init__(timeout=300)  # 5-minute timeout
        self.cog = cog
        self.schedule_time: Optional[datetime] = None

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
            required=True
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
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. Await further instructions in your private thread.", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        message = await channel.send(embed=embed, view=view)
        view.message = message
        await ctx.send(f"Auction request embed spawned in {channel.mention}")

    async def api_check(self, interaction: discord.Interaction, item_count: int, item_name: str) -> tuple:
        """Check if the donated item meets the value requirements and return item details."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await interaction.followup.send("Error fetching item value from API. Please try again later.", ephemeral=True)
                        log.error(f"API response status: {response.status}")
                        return None, None, None, None
                    
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    
                    if not item_data:
                        await interaction.followup.send("Item not found. Please enter a valid item name.", ephemeral=True)
                        return None, None, None, None
                    
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    tax = total_value * 0.10  # 10% tax
                    category = item_data.get("category", "General")
                    
                    if total_value < 50_000_000:  # 50 million
                        await interaction.followup.send("The total donation value must be over 50 million.", ephemeral=True)
                        return None, None, None, None

                    return item_value, total_value, tax, category

            except aiohttp.ClientError as e:
                await interaction.followup.send(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                log.error(f"API check error: {e}", exc_info=True)
                return None, None, None, None

    async def get_next_auction_id(self, guild: discord.Guild) -> str:
        """Generate the next auction ID."""
        async with self.config.guild(guild).auctions() as auctions:
            existing_ids = [int(aid.split('-')[1]) for aid in auctions.keys() if '-' in aid]
            next_id = max(existing_ids, default=0) + 1
            return f"{guild.id}-{next_id}"

    class AuctionModal(discord.ui.Modal):
        def __init__(self, cog: "AdvancedAuction"):
            self.cog = cog
            super().__init__(title="Request An Auction")

        item_name = discord.ui.TextInput(label="What are you going to donate?", placeholder="e.g., Blob", required=True, min_length=1, max_length=100)
        item_count = discord.ui.TextInput(label="How many of those items will you donate?", placeholder="e.g., 5", required=True, max_length=10)
        minimum_bid = discord.ui.TextInput(label="What should the minimum bid be?", placeholder="e.g., 1,000,000", required=False)
        message = discord.ui.TextInput(label="What is your message?", placeholder="e.g., I love DR!", required=False, max_length=200)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                log.info(f"Auction modal submitted by {interaction.user.name}")
                item_name = self.item_name.value
                item_count = int(self.item_count.value)
                min_bid = self.minimum_bid.value or "1,000,000"
                message = self.message.value

                log.info(f"Submitted values: item={item_name}, count={item_count}, min_bid={min_bid}")

                await interaction.response.send_message("Processing your auction request...", ephemeral=True)

                item_value, total_value, tax, category = await self.cog.api_check(interaction, item_count, item_name)
                if not item_value:
                    return

                view = AuctionScheduleView(self.cog)
                await interaction.followup.send("Would you like to schedule this auction?", view=view, ephemeral=True)
                await view.wait()

                await self.cog.process_auction_request(interaction, item_name, item_count, min_bid, message, category, view.schedule_time, item_value, total_value, tax)

            except Exception as e:
                log.error(f"An error occurred in modal submission: {e}", exc_info=True)
                await interaction.followup.send(f"An error occurred while processing your submission. Please try again or contact an administrator.", ephemeral=True)

    async def process_auction_request(self, interaction: discord.Interaction, item_name: str, item_count: int, min_bid: str, message: str, category: str, schedule_time: Optional[datetime], item_value: int, total_value: int, tax: int):
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
            await self.create_auction_thread(interaction.guild, auction_data)

        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction_id] = auction_data

        embed = self.create_auction_embed(auction_data)
        view = self.AuctionControlView(self, auction_id)
        
        thread = await self.get_auction_thread(guild, auction_id)
        if thread:
            await thread.send(content=interaction.user.mention, embed=embed, view=view)

    async def create_auction_thread(self, guild: discord.Guild, auction_data: Dict[str, Any]):
        """Create a new thread for the auction and set initial permissions."""
        auction_channel_id = await self.config.guild(guild).auction_channel()
        auction_channel = guild.get_channel(auction_channel_id)
        
        if not auction_channel:
            log.error(f"Auction channel not found for guild {guild.id}")
            return
        
        thread = await auction_channel.create_thread(
            name=f"Auction: {auction_data['amount']}x {auction_data['item']}",
            type=discord.ChannelType.private_thread
        )
        
        auction_data['thread_id'] = thread.id
        
        # Set initial permissions
        await thread.add_user(guild.get_member(auction_data['user_id']))
        
        return thread

    def create_auction_embed(self, auction_data: Dict[str, Any]) -> discord.Embed:
        """Create an embed with auction details."""
        embed = discord.Embed(
            title="Your Auction Details",
            description=f"Please donate {auction_data['amount']} of {auction_data['item']} as you have mentioned or you will get blacklisted.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Item", value=f"{auction_data['amount']}x {auction_data['item']}", inline=False)
        embed.add_field(name="Minimum Bid", value=auction_data['min_bid'], inline=True)
        embed.add_field(name="Market Price (each)", value=f"{auction_data['item_value']:,}", inline=True)
        embed.add_field(name="Total Value", value=f"{auction_data['total_value']:,}", inline=True)
        embed.add_field(name="Tax (10%)", value=f"{auction_data['tax']:,}", inline=True)
        embed.add_field(name="Category", value=auction_data['category'], inline=True)
        embed.add_field(name="Channel closes in", value="6 hours", inline=True)
        embed.set_footer(text="This channel will be deleted after 6 hours.")
        return embed

    class AuctionControlView(discord.ui.View):
        def __init__(self, cog: "AdvancedAuction", auction_id: str):
            super().__init__(timeout=None)
            self.cog = cog
            self.auction_id = auction_id

        @discord.ui.button(label="Close Auction", style=discord.ButtonStyle.danger)
        async def close_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.cog.close_auction(interaction, self.auction_id, "User closed the auction")

        @discord.ui.button(label="Pause Auction", style=discord.ButtonStyle.secondary)
        async def pause_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.cog.pause_auction(interaction, self.auction_id)

        @discord.ui.button(label="Resume Auction", style=discord.ButtonStyle.success)
        async def resume_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.cog.resume_auction(interaction, self.auction_id)

        @discord.ui.button(label="Modify Auction", style=discord.ButtonStyle.primary)
        async def modify_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.cog.open_modify_auction_modal(interaction, self.auction_id)

    class AuctionView(discord.ui.View):
        def __init__(self, cog: "AdvancedAuction"):
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

    async def open_modify_auction_modal(self, interaction: discord.Interaction, auction_id: str):
        """Open a modal to modify auction details."""
        async with self.config.guild(interaction.guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await interaction.response.send_message("Auction not found.", ephemeral=True)
                return

        modal = self.ModifyAuctionModal(self, auction)
        await interaction.response.send_modal(modal)

    class ModifyAuctionModal(discord.ui.Modal):
        def __init__(self, cog: "AdvancedAuction", auction: Dict[str, Any]):
            super().__init__(title="Modify Auction")
            self.cog = cog
            self.auction = auction

        min_bid = discord.ui.TextInput(label="New Minimum Bid", required=False)
        message = discord.ui.TextInput(label="New Message", required=False)

        async def on_submit(self, interaction: discord.Interaction):
            if self.min_bid.value:
                self.auction['min_bid'] = self.min_bid.value
            if self.message.value:
                self.auction['message'] = self.message.value

            async with self.cog.config.guild(interaction.guild).auctions() as auctions:
                auctions[self.auction['auction_id']] = self.auction

            await interaction.response.send_message("Auction details updated successfully.", ephemeral=True)
            
            # Update the auction embed in the thread
            thread = await self.cog.get_auction_thread(interaction.guild, self.auction['auction_id'])
            if thread:
                embed = self.cog.create_auction_embed(self.auction)
                view = self.cog.AuctionControlView(self.cog, self.auction['auction_id'])
                await thread.send(embed=embed, view=view)

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
        async with self.config.guild(guild).auctions() as auctions:
            log.info(f"Current auctions: {auctions}")
            log.info(f"Current channel ID: {message.channel.id}")

            for auction_id, auction in auctions.items():
                log.info(f"Checking auction {auction_id}: {auction}")
                if auction["status"] == "pending" and auction.get('thread_id') == message.channel.id:
                    log.info(f"Found matching auction: {auction_id}")
                    await self.process_donation(message, guild, auction)
                    break
            else:
                log.info("No matching auction found")

    async def process_donation(self, message: discord.Message, guild: discord.Guild, auction: Dict[str, Any]):
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
                await self.finalize_auction(guild, auction)
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

    async def finalize_auction(self, guild: discord.Guild, auction: Dict[str, Any]):
        """Finalize the auction after all items and tax have been donated."""
        auction["status"] = "active"
        thread = await self.get_auction_thread(guild, auction['auction_id'])
        if thread:
            await thread.send("All items and tax have been donated. Your auction will be queued shortly!")

        # Remove permissions for all users except admins and the auction creator
        for member in thread.members:
            if not member.guild_permissions.administrator and member.id != auction['user_id']:
                await thread.remove_user(member)

        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction["auction_id"]] = auction

        async with self.config.guild(guild).auction_queue() as queue:
            queue.append(auction["auction_id"])

        self.bot.loop.create_task(self.process_queue())

    async def handle_potential_bid(self, message: discord.Message):
        if not self.is_valid_bid_format(message.content):
            return

        guild = message.guild
        async with self.config.guild(guild).auctions() as auctions:
            active_auction = next((a for a in auctions.values() if a['status'] == 'active' and a.get('thread_id') == message.channel.id), None)

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

        active_auction['current_bid'] = bid_amount
        active_auction['current_bidder'] = message.author.id
        active_auction['bid_history'].append({
            'user_id': message.author.id,
            'amount': bid_amount,
            'timestamp': int(datetime.utcnow().timestamp())
        })

        await message.add_reaction("✅")
        embed = discord.Embed(title="New Highest Bid", color=discord.Color.green())
        embed.add_field(name="Bidder", value=message.author.mention, inline=True)
        embed.add_field(name="Amount", value=f"{bid_amount:,}", inline=True)
        await message.channel.send(embed=embed)

        # Extend auction time and check for anti-sniping
        current_time = int(datetime.utcnow().timestamp())
        extension_time = await self.config.guild(guild).auction_extension_time()
        if current_time + extension_time > active_auction['end_time']:
            active_auction['end_time'] = current_time + extension_time
            await message.channel.send(f"Auction extended by {extension_time // 60} minutes due to last-minute bid!")

        async with self.config.guild(guild).auctions() as auctions:
            auctions[active_auction['auction_id']] = active_auction

        # Process auto-bids
        await self.process_auto_bids(guild, active_auction)

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

    async def process_auto_bids(self, guild: discord.Guild, auction: Dict[str, Any]):
        """Process auto-bids for an auction."""
        min_increment = await self.config.guild(guild).minimum_bid_increment()
        auto_bids = sorted(auction['auto_bids'].items(), key=lambda x: x[1], reverse=True)
        
        for user_id, max_bid in auto_bids:
            if max_bid > auction['current_bid'] + min_increment:
                new_bid = min(max_bid, auction['current_bid'] + min_increment)
                auction['current_bid'] = new_bid
                auction['current_bidder'] = int(user_id)
                auction['bid_history'].append({
                    'user_id': int(user_id),
                    'amount': new_bid,
                    'timestamp': int(datetime.utcnow().timestamp()),
                    'auto_bid': True
                })

                user = guild.get_member(int(user_id))
                if user:
                    thread = await self.get_auction_thread(guild, auction['auction_id'])
                    if thread:
                        await thread.send(f"Auto-bid: {user.mention} has bid {new_bid:,}")
            else:
                break  # No need to check lower auto-bids

        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction['auction_id']] = auction

    async def close_auction(self, interaction: discord.Interaction, auction_id: str, reason: str):
        """Close an auction manually."""
        guild = interaction.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await interaction.response.send_message("Auction not found.", ephemeral=True)
                return
            
            if auction['status'] not in ['active', 'pending']:
                await interaction.response.send_message("This auction cannot be closed.", ephemeral=True)
                return

            auction['status'] = 'cancelled'
            auction['end_time'] = int(datetime.utcnow().timestamp())
            auction['cancel_reason'] = reason
            auctions[auction_id] = auction

        await interaction.response.send_message(f"Auction {auction_id} has been closed. Reason: {reason}", ephemeral=True)
        
        thread = await self.get_auction_thread(guild, auction_id)
        if thread:
            await thread.send(f"This auction has been closed by a moderator. Reason: {reason}")
            await thread.edit(archived=True, locked=True)

        # Remove the auction from the queue if it's there
        async with self.config.guild(guild).auction_queue() as queue:
            if auction_id in queue:
                queue.remove(auction_id)

        # Cancel any running tasks for this auction
        if auction_id in self.auction_tasks:
            self.auction_tasks[auction_id].cancel()
            del self.auction_tasks[auction_id]

    async def pause_auction(self, interaction: discord.Interaction, auction_id: str):
        """Pause an active auction."""
        async with self.config.guild(interaction.guild).auctions() as auctions:
            auction = auctions.get(auction_id)

            if not auction or auction['status'] != 'active':
                await interaction.response.send_message("This auction is not active.", ephemeral=True)
                return

            auction['status'] = 'paused'
            auction['paused_time'] = int(datetime.utcnow().timestamp())
            auction['original_end_time'] = auction['end_time']  # Store the original end time
            auction['end_time'] = None  # Clear the end time while paused

            auctions[auction_id] = auction

        await interaction.response.send_message(f"Auction {auction_id} has been paused.", ephemeral=True)
        thread = await self.get_auction_thread(interaction.guild, auction_id)
        if thread:
            await thread.send(f"Auction {auction_id} has been paused by a moderator.")

    async def resume_auction(self, interaction: discord.Interaction, auction_id: str):
        """Resume a paused auction."""
        async with self.config.guild(interaction.guild).auctions() as auctions:
            auction = auctions.get(auction_id)

            if not auction or auction['status'] != 'paused':
                await interaction.response.send_message("This auction is not paused.", ephemeral=True)
                return

            pause_duration = int(datetime.utcnow().timestamp()) - auction['paused_time']
            auction['end_time'] = auction['original_end_time'] + pause_duration  # Adjust end time
            auction['status'] = 'active'
            del auction['paused_time']
            del auction['original_end_time']

            auctions[auction_id] = auction

        await interaction.response.send_message(f"Auction {auction_id} has been resumed.", ephemeral=True)
        thread = await self.get_auction_thread(interaction.guild, auction_id)
        if thread:
            await thread.send(f"Auction {auction_id} has been resumed by a moderator.")
        
        # Schedule the auction end with the adjusted end time
        remaining_time = auction['end_time'] - int(datetime.utcnow().timestamp())
        if remaining_time > 0:
            self.auction_tasks[auction_id] = self.bot.loop.create_task(self.schedule_auction_end(auction_id, remaining_time))

    async def get_auction_thread(self, guild: discord.Guild, auction_id: str) -> Optional[discord.Thread]:
        """Get the thread for a specific auction."""
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or 'thread_id' not in auction:
                return None
            
            thread = guild.get_thread(auction['thread_id'])
            return thread

    async def schedule_auction_end(self, auction_id: str, delay: int):
        """Schedule an auction to end after a specified delay."""
        await asyncio.sleep(delay)
        guild_id = int(auction_id.split('-')[0])
        guild = self.bot.get_guild(guild_id)
        if guild:
            await self.end_auction(guild, auction_id)

    async def end_auction(self, guild: discord.Guild, auction_id: str):
        """End an auction and process the results."""
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                return

            auction['status'] = 'completed'
            auctions[auction_id] = auction

        thread = await self.get_auction_thread(guild, auction_id)
        if not thread:
            return

        winner = guild.get_member(auction['current_bidder'])
        winning_bid = auction['current_bid']

        embed = discord.Embed(title="Auction Ended", color=discord.Color.gold())
        embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=False)
        embed.add_field(name="Winner", value=winner.mention if winner else "No winner", inline=True)
        embed.add_field(name="Winning Bid", value=f"{winning_bid:,}", inline=True)
        await thread.send(embed=embed)

        if winner:
            await thread.send(f"{winner.mention}, please pay {winning_bid:,} within 5 minutes.")
            await thread.send(f"/serverevents donate quantity:{winning_bid}")

            try:
                await self.bot.wait_for(
                    'message',
                    check=lambda m: m.author.id == 270904126974590976 and "Successfully donated" in m.content,
                    timeout=300.0
                )
                await thread.send("Payment received. The auction has been completed.")
                
                # Update user statistics
                await self.update_user_stats(guild, winner.id, winning_bid, 'won')
                await self.update_user_stats(guild, auction['user_id'], winning_bid, 'sold')
            except asyncio.TimeoutError:
                await thread.send("Payment not received in time. The auction has been cancelled.")
                await self.handle_payment_failure(guild, auction, winner)
                return

        await thread.edit(archived=True, locked=True)

        # Remove the auction from the queue
        async with self.config.guild(guild).auction_queue() as queue:
            if auction_id in queue:
                queue.remove(auction_id)

        # Start the next auction if there's one in the queue
        if queue:
            await self.start_next_auction(guild)

    async def handle_payment_failure(self, guild: discord.Guild, auction: Dict[str, Any], failed_bidder: Optional[discord.Member]):
        """Handle the case when a winning bidder fails to pay."""
        thread = await self.get_auction_thread(guild, auction['auction_id'])
        if not thread:
            return

        # Blacklist the failed bidder
        blacklist_role_id = await self.config.guild(guild).blacklist_role()
        if blacklist_role_id and failed_bidder:
            blacklist_role = guild.get_role(blacklist_role_id)
            if blacklist_role:
                await failed_bidder.add_roles(blacklist_role)
                await thread.send(f"{failed_bidder.mention} has been blacklisted for failing to pay.")

        # Get all bids for this auction
        bids = auction['bid_history']

        # Sort bids by amount in descending order
        sorted_bids = sorted(bids, key=lambda x: x['amount'], reverse=True)

        for bid in sorted_bids[1:6]:  # Try the next 5 highest bidders
            bidder = guild.get_member(bid['user_id'])
            if not bidder:
                continue

            await thread.send(f"{bidder.mention}, the previous bidder failed to pay. You have the chance to win this auction for {bid['amount']:,}!")
            await thread.send(f"Please pay {bid['amount']:,} within 3 minutes!")
            await thread.send(f"/serverevents donate quantity:{bid['amount']}")

            try:
                await self.bot.wait_for(
                    'message',
                    check=lambda m: m.author.id == 270904126974590976 and "Successfully donated" in m.content,
                    timeout=180.0
                )
                await thread.send("Payment received. The auction has been completed.")
                
                # Update user statistics
                await self.update_user_stats(guild, bidder.id, bid['amount'], 'won')
                await self.update_user_stats(guild, auction['user_id'], bid['amount'], 'sold')
                return
            except asyncio.TimeoutError:
                await thread.send(f"{bidder.mention} failed to pay in time.")

        # If we've reached this point, all potential winners have failed to pay
        await thread.send("All potential winners failed to pay. Auction cancelled.")

    async def update_user_stats(self, guild: discord.Guild, user_id: int, amount: int, action: str):
        """Update user statistics for auctions won or sold."""
        async with self.config.guild(guild).user_stats() as stats:
            if str(user_id) not in stats:
                stats[str(user_id)] = {'won': 0, 'sold': 0}
            
            stats[str(user_id)][action] += amount

    @commands.command()
    async def myauctions(self, ctx: commands.Context):
        """View your active and pending auctions."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            user_auctions = [a for a in auctions.values() if a.get('user_id') == ctx.author.id and a.get('status') in ['active', 'pending']]
        
        if not user_auctions:
            await ctx.send("You don't have any active or pending auctions.")
            return
        
        embed = discord.Embed(title="Your Auctions", color=discord.Color.blue())
        for auction in user_auctions:
            auction_id = auction.get('auction_id', 'Unknown')
            item = f"{auction.get('amount', 'Unknown')}x {auction.get('item', 'Unknown')}"
            status = auction.get('status', 'Unknown').capitalize()
            current_bid = auction.get('current_bid', auction.get('min_bid', 'Unknown'))
            end_time = auction.get('end_time', 'Unknown')

            value = f"ID: {auction_id}\n"
            value += f"Current Bid: {current_bid:,}\n" if isinstance(current_bid, int) else f"Current Bid: {current_bid}\n"
            value += f"Ends: <t:{end_time}:R>" if isinstance(end_time, int) else "End time: Unknown"

            embed.add_field(
                name=f"{item} ({status})",
                value=value,
                inline=False
            )
        
        await ctx.send(embed=embed)

    @commands.command()
    async def auctioninfo(self, ctx: commands.Context, auction_id: str):
        """Get detailed information about an auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction:
            await ctx.send("Auction not found.")
            return
        
        embed = discord.Embed(title=f"Auction Info: {auction_id}", color=discord.Color.blue())
        embed.add_field(name="Item", value=f"{auction.get('amount', 'Unknown')}x {auction.get('item', 'Unknown')}", inline=False)
        
        current_bid = auction.get('current_bid', auction.get('min_bid', 'Unknown'))
        embed.add_field(name="Current Bid", value=f"{current_bid:,}" if isinstance(current_bid, int) else str(current_bid), inline=True)
        
        embed.add_field(name="Minimum Bid", value=str(auction.get('min_bid', 'Unknown')), inline=True)
        embed.add_field(name="Total Value", value=f"{auction.get('total_value', 'Unknown'):,}" if isinstance(auction.get('total_value'), int) else 'Unknown', inline=True)
        embed.add_field(name="Status", value=auction.get('status', 'Unknown').capitalize(), inline=True)
        
        start_time = auction.get('start_time', 'Unknown')
        embed.add_field(name="Start Time", value=f"<t:{start_time}:F>" if isinstance(start_time, int) else str(start_time), inline=True)
        
        end_time = auction.get('end_time', 'Unknown')
        embed.add_field(name="End Time", value=f"<t:{end_time}:F>" if isinstance(end_time, int) else str(end_time), inline=True)
        end_time = auction.get('end_time', 'Unknown')
        embed.add_field(name="End Time", value=f"<t:{end_time}:F>" if isinstance(end_time, int) else str(end_time), inline=True)
        
        current_bidder = auction.get('current_bidder')
        if current_bidder:
            bidder = guild.get_member(current_bidder)
            embed.add_field(name="Current Highest Bidder", value=bidder.mention if bidder else "Unknown User", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def forcestartauction(self, ctx: commands.Context, auction_id: str):
        """Force start a pending auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction or auction['status'] != 'pending':
            await ctx.send("This auction is not in pending status.")
            return
        
        await self.start_auction(guild, auction_id)
        await ctx.send(f"Auction {auction_id} has been force started.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def cancelauction(self, ctx: commands.Context, auction_id: str, *, reason: str):
        """Cancel an active or pending auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction or auction['status'] not in ['active', 'pending']:
            await ctx.send("This auction cannot be cancelled.")
            return
        
        await self.close_auction(ctx, auction_id, reason)
        await ctx.send(f"Auction {auction_id} has been cancelled.")

    @commands.command()
    async def autobid(self, ctx: commands.Context, auction_id: str, max_bid: int):
        """Set up an auto-bid for a specific auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)

        if not auction or auction['status'] != 'active':
            await ctx.send("This auction is not active.")
            return

        if max_bid <= auction['current_bid']:
            await ctx.send(f"Your maximum bid must be higher than the current bid of {auction['current_bid']:,}.")
            return

        auction['auto_bids'][str(ctx.author.id)] = max_bid
        auctions[auction_id] = auction
        await ctx.send(f"Auto-bid set for auction {auction_id} up to {max_bid:,}")

    @commands.command()
    async def cancelautobid(self, ctx: commands.Context, auction_id: str):
        """Cancel your auto-bid for a specific auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)

        if not auction or auction['status'] != 'active':
            await ctx.send("This auction is not active.")
            return

        if str(ctx.author.id) in auction['auto_bids']:
            del auction['auto_bids'][str(ctx.author.id)]
            auctions[auction_id] = auction
            await ctx.send(f"Your auto-bid for auction {auction_id} has been cancelled.")
        else:
            await ctx.send(f"You don't have an active auto-bid for auction {auction_id}.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def cleanupauctions(self, ctx: commands.Context):
        """Clean up completed and cancelled auctions older than 7 days."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            now = int(datetime.utcnow().timestamp())
            seven_days_ago = now - (7 * 86400)
            
            cleaned_auctions = {k: v for k, v in auctions.items() if v['status'] not in ['completed', 'cancelled'] or v['end_time'] > seven_days_ago}
            
            removed_count = len(auctions) - len(cleaned_auctions)
            
            auctions.clear()
            auctions.update(cleaned_auctions)
        
        await ctx.send(f"Cleanup complete. Removed {removed_count} old auctions.")

    @commands.command()
    async def auctionhelp(self, ctx: commands.Context):
        """Provide a comprehensive explanation of the AdvancedAuction cog and its commands."""
        
        help_embed = discord.Embed(title="Auction Cog Help", 
                                   description="This cog provides a comprehensive auction system for your Discord server.",
                                   color=discord.Color.blue())
        
        help_embed.add_field(name="Overview", value="""
        The AdvancedAuction cog allows users to create, manage, and participate in auctions within your Discord server. 
        It features scheduled auctions, auto-bidding, and detailed analytics.
        """, inline=False)
        
        help_embed.add_field(name="Key Features", value="""
        • Create and manage auctions
        • Schedule auctions for future dates
        • Auto-bidding system
        • Auction reminders
        • Detailed auction information and history
        • Admin controls and analytics
        """, inline=False)
        
        help_embed.add_field(name="User Commands", value="""
        • `requestauction`: Start a new auction
        • `myauctions`: View your active and pending auctions
        • `auctioninfo <auction_id>`: Get detailed information about an auction
        • `bid <auction_id> <amount>`: Place a bid on an auction
        • `autobid <auction_id> <max_bid>`: Set up an auto-bid
        • `cancelautobid <auction_id>`: Cancel your auto-bid
        """, inline=False)
        
        help_embed.add_field(name="Admin Commands", value="""
        • `auctionset`: Configure auction settings (use `auctionset` to see subcommands)
        • `spawnauction`: Spawn an auction request embed
        • `forcestartauction <auction_id>`: Force start a pending auction
        • `cancelauction <auction_id> <reason>`: Cancel an active or pending auction
        • `cleanupauctions`: Remove old completed and cancelled auctions
        """, inline=False)
        
        help_embed.add_field(name="How It Works", value="""
        1. Admins set up the cog using `auctionset` commands.
        2. Users create auctions using the auction request button or command.
        3. Auctions can be scheduled or start immediately after approval.
        4. Users bid on active auctions in the designated thread.
        5. Auto-bidding can be set up to bid automatically up to a max amount.
        6. Auctions end after a set time, with possible extensions for last-minute bids.
        7. Winners must pay within a time limit or face consequences.
        8. Admins can view reports and manage auctions as needed.
        """, inline=False)
        
        help_embed.set_footer(text="For more detailed help on specific commands, use [p]help command")
        
        await ctx.send(embed=help_embed)

async def setup(bot):
    await bot.add_cog(AdvancedAuction(bot))