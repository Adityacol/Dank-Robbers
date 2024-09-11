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

# Set up logging for the cog
log = logging.getLogger("red.economy.AdvancedAuction")

class AuctionScheduleView(discord.ui.View):
    """
    A view for scheduling auctions. This class creates a button that, when clicked,
    opens a modal for the user to input a scheduled time for the auction.
    """
    def __init__(self, cog: "AdvancedAuction"):
        super().__init__(timeout=300)  # Set a 5-minute timeout for the view
        self.cog = cog
        self.schedule_time: Optional[datetime] = None
        self.message = None

    @discord.ui.button(label="Schedule Auction", style=discord.ButtonStyle.primary)
    async def schedule_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle the button click to schedule an auction."""
        modal = self.ScheduleModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.schedule_time = modal.schedule_time
        self.stop()

    class ScheduleModal(discord.ui.Modal, title="Schedule Auction"):
        """A modal for inputting the scheduled time for an auction."""
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
            """Process the submitted time and validate it."""
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

    async def on_timeout(self):
        """Handle the timeout of the view by disabling all buttons."""
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(view=self)

class AdvancedAuction(commands.Cog):
    """
    A comprehensive auction system for Discord servers.
    This cog allows users to create, manage, and participate in auctions.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        # Define default settings for guilds
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
            "categories": ["General", "Rare", "Limited Edition"],
            "leaderboard": {},
            "max_active_auctions": 10,
            "auction_cooldown": 86400,  # 24 hours in seconds
            "featured_auction": None,
            "banned_users": [],
            "auction_moderators": [],
            "minimum_bid_increment": 1000,
            "auction_extension_time": 300,
        }
        
        # Define default settings for members
        default_member: Dict[str, Any] = {
            "auction_reminders": [],
            "auction_subscriptions": []
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.active_auctions: Dict[str, asyncio.Task] = {}
        self.auction_tasks: Dict[str, asyncio.Task] = {}
        self.queue_lock = asyncio.Lock()
        self.queue_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        """Initialize the cog by starting the queue manager."""
        self.queue_task = asyncio.create_task(self.queue_manager())

    async def cog_unload(self) -> None:
        """Clean up tasks when the cog is unloaded."""
        if self.queue_task:
            self.queue_task.cancel()
        for task in self.auction_tasks.values():
            task.cancel()
        for task in self.active_auctions.values():
            task.cancel()

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
                # Process scheduled auctions
                scheduled_auctions = await self.config.guild(guild).scheduled_auctions()
                current_time = int(datetime.utcnow().timestamp())
                
                for auction_id, scheduled_time in list(scheduled_auctions.items()):
                    if current_time >= scheduled_time:
                        await self.start_scheduled_auction(guild, auction_id)
                        del scheduled_auctions[auction_id]
                
                await self.config.guild(guild).scheduled_auctions.set(scheduled_auctions)
                
                # Process the auction queue
                queue = await self.config.guild(guild).auction_queue()
                if queue and queue[0]:
                    auction_id = queue[0]
                    auctions = await self.config.guild(guild).auctions()
                    auction = auctions.get(auction_id)
                    if auction['status'] == 'pending':
                        await self.start_auction(guild, auction_id)
                    elif auction['status'] in ['completed', 'cancelled']:
                        await self.cleanup_auction(guild, auction_id)
                
                # Save changes
                await self.config.guild(guild).auctions.set(auctions)
                await self.config.guild(guild).scheduled_auctions.set(scheduled_auctions)
                await self.config.guild(guild).auction_queue.set(queue)

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

    # Additional auctionset commands would be implemented here...

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
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. Await further instructions in your private channel.", inline=False)
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
                    
                    if total_value < 500000000:  # 500 million
                        await interaction.followup.send("The total donation value must be over 500 million.", ephemeral=True)
                        return None, None, None

                    return item_value, total_value, tax

            except aiohttp.ClientError as e:
                await interaction.followup.send(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                log.error(f"API check error: {e}", exc_info=True)
                return None, None, None

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
            await interaction.response.defer()
            await interaction.followup.send("Processing your auction request...", ephemeral=True)
            return

        item_value, total_value, tax = await self.api_check(interaction, item_count, item_name)
        if not item_value:
            return

        if total_value < 500000000:  # 500 million
            await interaction.followup.send("The total donation value must be over 500 million.", ephemeral=True)
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
            "end_time": int((datetime.utcnow() + timedelta(hours=6)).timestamp()),  # 6 hours
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
        self.auction_tasks[auction_id] = self.bot.loop.create_task(self.schedule_auction_end(auction_id, 21600))  # 6 hours

    class AuctionControlView(View):
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

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
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

    class AuctionView(View):
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
                if auction["status"] == "pending":
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

        if 'buy_out_price' in active_auction and bid_amount >= active_auction['buy_out_price']:
            await self.end_auction(guild, active_auction['auction_id'], message.author, bid_amount)
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

    async def process_auto_bids(self, guild: discord.Guild, auction: Dict[str, Any]):
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
                    channel = self.bot.get_channel(auction['auction_channel_id'])
                    if channel:
                        await channel.send(f"Auto-bid: {user.mention} has bid {new_bid:,}")
            else:
                break  # No need to check lower auto-bids

        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction['auction_id']] = auction

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

    async def finalize_auction_request(self, guild: discord.Guild, auction: Dict[str, Any]):
        auction["status"] = "active"
        auction['auction_channel_id'] = await self.config.guild(guild).auction_channel()
        auction_channel = self.bot.get_channel(auction['auction_channel_id'])
        if auction_channel:
            await auction_channel.send("All items and tax have been donated. Your auction will be queued shortly!")

        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction["auction_id"]] = auction

        async with self.config.guild(guild).auction_queue() as queue:
            queue.append(auction["auction_id"])

        self.bot.loop.create_task(self.process_queue())

    async def start_scheduled_auction(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions[auction_id]
            auction['status'] = 'pending'
            auctions[auction_id] = auction
        await self.start_auction(guild, auction_id)

    async def start_auction(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions[auction_id]
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            log.error(f"Queue channel not found for guild {guild.id}")
            return

        ping_role_id = await self.config.guild(guild).auction_ping_role()
        massive_ping_role_id = await self.config.guild(guild).massive_auction_ping_role()
        
        ping_role = guild.get_role(ping_role_id) if ping_role_id else None
        massive_ping_role = guild.get_role(massive_ping_role_id) if massive_ping_role_id else None

        if auction['total_value'] >= 1_000_000_000 and massive_ping_role:  # 1 billion
            ping = massive_ping_role.mention
        elif ping_role:
            ping = ping_role.mention
        else:
            ping = ""

        embed = discord.Embed(
            title="Auction is about to begin!",
            color=discord.Color.green()
        )
        embed.add_field(name="User", value=f"<@{auction['user_id']}>", inline=False)
        embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=False)
        embed.add_field(name="Beginning Bid", value=auction['min_bid'], inline=True)
        embed.add_field(name="Auction Worth", value=f"{auction['total_value']:,}", inline=True)
        embed.add_field(name="Category", value=auction['category'], inline=True)
        if 'reserve_price' in auction:
            embed.add_field(name="Reserve Price", value=f"{auction['reserve_price']:,}", inline=True)
        if 'buy_out_price' in auction:
            embed.add_field(name="Buy-out Price", value=f"{auction['buy_out_price']:,}", inline=True)
        embed.add_field(name="Reactions Needed", value="5", inline=True)

        message = await queue_channel.send(content=f"{ping} Auction is about to begin!", embed=embed)
        await message.add_reaction("✅")

        def reaction_check(reaction: discord.Reaction, user: Union[discord.Member, discord.User]):
            return str(reaction.emoji) == "✅" and reaction.message.id == message.id and not user.bot

        try:
            for _ in range(5):
                await self.bot.wait_for('reaction_add', timeout=60.0, check=reaction_check)
        except asyncio.TimeoutError:
            pass

        message = await queue_channel.fetch_message(message.id)
        reactions = message.reactions
        check_count = next((r.count for r in reactions if str(r.emoji) == "✅"), 0) - 1  # Subtract 1 to exclude the bot's reaction

        if check_count >= 1:
            await self.run_auction(guild, auction_id)
        else:
            await queue_channel.send("Not enough interest. Auction cancelled.")
            await self.cancel_auction(guild, auction_id, "Not enough interest")

    async def run_auction(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions[auction_id]
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            log.error(f"Queue channel not found for guild {guild.id}")
            return

        await queue_channel.send("Auction Started!")

        auction['status'] = 'active'
        auction['end_time'] = int(datetime.utcnow().timestamp()) + 300  # 5 minutes from now

        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction_id] = auction

        while datetime.utcnow().timestamp() < auction['end_time']:
            embed = discord.Embed(
                title=f"Auction: {auction['amount']}x {auction['item']}",
                description=f"**Current Highest Bid:** {auction['current_bid']:,}\n**Ends:** <t:{auction['end_time']}:R>",
                color=discord.Color.blue()
            )
            await queue_channel.send(embed=embed)

            try:
                def check_bid(m: discord.Message):
                    return (m.channel == queue_channel and 
                            not m.author.bot and 
                            self.is_valid_bid_format(m.content) and
                            self.parse_bid_amount(m.content) > auction['current_bid'])

                bid_msg = await self.bot.wait_for('message', check=check_bid, timeout=30.0)
                
                new_bid = self.parse_bid_amount(bid_msg.content)
                auction['current_bid'] = new_bid
                auction['current_bidder'] = bid_msg.author.id
                auction['end_time'] = int(datetime.utcnow().timestamp()) + 60  # Extend by 1 minute

                async with self.config.guild(guild).auctions() as auctions:
                    auctions[auction_id] = auction
                await queue_channel.send(f"Accepted bid of {new_bid:,} from {bid_msg.author.mention}")

                # Process auto-bids
                await self.process_auto_bids(guild, auction)

            except asyncio.TimeoutError:
                # No new bids, start the countdown
                for stage in ["Going Once", "Going Twice", "Final Call"]:
                    await queue_channel.send(f"{stage} at {auction['current_bid']:,}")
                    try:
                        bid_msg = await self.bot.wait_for('message', check=check_bid, timeout=10.0)
                        new_bid = self.parse_bid_amount(bid_msg.content)
                        auction['current_bid'] = new_bid
                        auction['current_bidder'] = bid_msg.author.id
                        auction['end_time'] = int(datetime.utcnow().timestamp()) + 60  # Extend by 1 minute
                        async with self.config.guild(guild).auctions() as auctions:
                            auctions[auction_id] = auction
                        await queue_channel.send(f"Accepted bid of {new_bid:,} from {bid_msg.author.mention}")
                        await self.process_auto_bids(guild, auction)
                        break
                    except asyncio.TimeoutError:
                        continue

            if auction['current_bidder'] and auction['current_bid'] >= auction.get('buy_out_price', float('inf')):
                await queue_channel.send("Buy-out price reached! Ending auction.")
                break

        await self.end_auction(guild, auction_id)

    async def end_auction(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions[auction_id]
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = self.bot.get_channel(log_channel_id)

        if not queue_channel or not log_channel:
            log.error(f"Required channels not found for guild {guild.id}")
            return

        winner = guild.get_member(auction['current_bidder'])
        winning_bid = auction['current_bid']

        if winning_bid < auction.get('reserve_price', 0):
            await queue_channel.send("Reserve price not met. Auction cancelled.")
            await self.cancel_auction(guild, auction_id, "Reserve price not met")
            return

        embed = discord.Embed(title="Auction Ended", color=discord.Color.gold())
        embed.add_field(name="Winner", value=winner.mention if winner else "Unknown User", inline=False)
        embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=True)
        embed.add_field(name="Winning Bid", value=f"{winning_bid:,}", inline=True)
        await queue_channel.send(embed=embed)

        await queue_channel.send(f"{winner.mention}, send the money in this channel within 3 minutes!")
        await queue_channel.send(f"/serverevents donate quantity:{winning_bid}")

        try:
            await self.bot.wait_for(
                'message',
                check=lambda m: m.author.id == 270904126974590976 and "Successfully donated" in m.content,
                timeout=180.0
            )
            await queue_channel.send("Successfully donated!")
            await queue_channel.send("Your items will be delivered to you shortly!")
            
            # Log the payout commands
            await log_channel.send(f"/serverevents payout user:{winner.id} quantity:{auction['amount']} item:{auction['item']}")
            await log_channel.send(f"/serverevents payout user:{auction['user_id']} quantity:{winning_bid}")

            # Update user statistics
            await self.update_user_stats(guild, winner.id, winning_bid, 'won')
            await self.update_user_stats(guild, auction['user_id'], winning_bid, 'sold')

        except asyncio.TimeoutError:
            await queue_channel.send("Payment not received in time. Auction cancelled.")
            await self.handle_payment_failure(guild, auction, winner)

        # Remove the auction from the queue
        async with self.config.guild(guild).auction_queue() as queue:
            if auction_id in queue:
                queue.remove(auction_id)

        # Clean up the auction channel
        await self.clean_auction_channel(guild, auction['auction_channel_id'])
        async with self.config.guild(guild).auctions() as auctions:
            auctions.pop(auction_id, None)

        # Start the next auction if there's one in the queue
        if queue:
            await self.start_next_auction(guild)
        else:
            await self.display_upcoming_auctions(guild)

    async def handle_payment_failure(self, guild: discord.Guild, auction: Dict[str, Any], failed_bidder: Optional[discord.Member]):
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            log.error(f"Queue channel not found for guild {guild.id}")
            return

        # Blacklist the failed bidder
        blacklist_role_id = await self.config.guild(guild).blacklist_role()
        if blacklist_role_id and failed_bidder:
            blacklist_role = guild.get_role(blacklist_role_id)
            if blacklist_role:
                await failed_bidder.add_roles(blacklist_role)
                await queue_channel.send(f"{failed_bidder.mention} has been blacklisted for failing to pay.")

        # Get all bids for this auction
        bids = auction['bid_history']

        # Sort bids by amount in descending order
        sorted_bids = sorted(bids, key=lambda x: x['amount'], reverse=True)

        for bid in sorted_bids[1:6]:  # Try the next 5 highest bidders
            bidder = guild.get_member(bid['user_id'])
            if not bidder:
                continue

            await queue_channel.send(f"{bidder.mention}, the previous bidder failed to pay. You have the chance to win this auction for {bid['amount']:,}!")
            await queue_channel.send(f"Please send the money in this channel within 3 minutes!")
            await queue_channel.send(f"/serverevents donate quantity:{bid['amount']}")

            try:
                await self.bot.wait_for(
                    'message',
                    check=lambda m: m.author.id == 270904126974590976 and "Successfully donated" in m.content,
                    timeout=180.0
                )
                await queue_channel.send("Successfully donated!")
                await queue_channel.send("Your items will be delivered to you shortly!")
                return
            except asyncio.TimeoutError:
                await queue_channel.send(f"{bidder.mention} failed to pay in time.")

        # If we've reached this point, all potential winners have failed to pay
        await queue_channel.send("All potential winners failed to pay. Auction cancelled.")

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
        embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=False)
        embed.add_field(name="Current Bid", value=f"{auction['current_bid']:,}", inline=True)
        embed.add_field(name="Minimum Bid", value=auction['min_bid'], inline=True)
        embed.add_field(name="Total Value", value=f"{auction['total_value']:,}", inline=True)
        embed.add_field(name="Status", value=auction['status'].capitalize(), inline=True)
        embed.add_field(name="Start Time", value=f"<t:{auction['start_time']}:F>", inline=True)
        embed.add_field(name="End Time", value=f"<t:{auction['end_time']}:F>", inline=True)
        
        if auction['current_bidder']:
            bidder = guild.get_member(auction['current_bidder'])
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
    async def cancelauction(self, ctx: commands.Context, auction_id: str):
        """Cancel an active or pending auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction or auction['status'] not in ['active', 'pending']:
            await ctx.send("This auction cannot be cancelled.")
            return
        
        await self.cancel_auction(guild, auction_id, "Cancelled by admin")
        await ctx.send(f"Auction {auction_id} has been cancelled.")

    async def cancel_auction(self, guild: discord.Guild, auction_id: str, reason: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if auction:
                auction['status'] = 'cancelled'
                auction['end_time'] = int(datetime.utcnow().timestamp())
                auction['cancel_reason'] = reason
                auctions[auction_id] = auction
        
        # Remove the auction from the queue if it's there
        async with self.config.guild(guild).auction_queue() as queue:
            if auction_id in queue:
                queue.remove(auction_id)

        # Clean up the auction channel
        await self.clean_auction_channel(guild, auction['auction_channel_id'])
        
        # Notify users
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)
        if queue_channel:
            await queue_channel.send(f"Auction {auction_id} has been cancelled. Reason: {reason}")

        # Cancel any running tasks for this auction
        if auction_id in self.auction_tasks:
            self.auction_tasks[auction_id].cancel()
            del self.auction_tasks[auction_id]

    @commands.command()
    async def myauctions(self, ctx: commands.Context):
        """View your active and pending auctions."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            user_auctions = [a for a in auctions.values() if a['user_id'] == ctx.author.id and a['status'] in ['active', 'pending']]
        
        if not user_auctions:
            await ctx.send("You don't have any active or pending auctions.")
            return
        
        embed = discord.Embed(title="Your Auctions", color=discord.Color.blue())
        for auction in user_auctions:
            embed.add_field(
                name=f"{auction['amount']}x {auction['item']} ({auction['status'].capitalize()})",
                value=f"ID: {auction['auction_id']}\nCurrent Bid: {auction['current_bid']:,}\nEnds: <t:{auction['end_time']}:R>",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @commands.command()
    async def auctionhistory(self, ctx: commands.Context, page: int = 1):
        """View your auction history."""
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

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionreport(self, ctx: commands.Context, days: int = 7):
        """Generate a report of auction activity for the specified number of days."""
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
        
        embed = discord.Embed(title=f"Auction Report (Last {days} Days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=len(relevant_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"{total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"{avg_value:,.2f}", inline=True)
        embed.add_field(name="Most Valuable Auction", value=f"{most_valuable['amount']}x {most_valuable['item']} ({most_valuable['current_bid']:,})", inline=False)
        embed.add_field(name="Most Bids", value=f"{most_bids['amount']}x {most_bids['item']} ({len(most_bids['bid_history'])} bids)", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setfeaturedauction(self, ctx: commands.Context, auction_id: str):
        """Set an auction as featured, displaying it prominently in the auction channel."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction or auction['status'] != 'active':
            await ctx.send("Only active auctions can be featured.")
            return
        
        await self.config.guild(guild).featured_auction.set(auction_id)
        await ctx.send(f"Auction {auction_id} is now featured.")
        
        await self.update_featured_auction(guild)

    async def update_featured_auction(self, guild: discord.Guild):
        """Update the featured auction message in the auction channel."""
        auction_channel_id = await self.config.guild(guild).auction_channel()
        auction_channel = self.bot.get_channel(auction_channel_id)
        
        if not auction_channel:
            return
        
        featured_auction_id = await self.config.guild(guild).featured_auction()
        if not featured_auction_id:
            return
        
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(featured_auction_id)
        
        if not auction:
            return
        
        embed = discord.Embed(title="Featured Auction", color=discord.Color.gold())
        embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=False)
        embed.add_field(name="Current Bid", value=f"{auction['current_bid']:,}", inline=True)
        embed.add_field(name="Time Left", value=f"<t:{auction['end_time']}:R>", inline=True)
        embed.set_footer(text=f"Auction ID: {featured_auction_id}")

        # Find and update the featured auction message, or send a new one
        async for message in auction_channel.history(limit=100):
            if message.author == self.bot.user and message.embeds and message.embeds[0].title == "Featured Auction":
                await message.edit(embed=embed)
                break
        else:
            await auction_channel.send(embed=embed)

    @commands.command()
    async def previewauction(self, ctx: commands.Context, auction_id: str):
        """Preview an upcoming auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction or auction['status'] != 'scheduled':
            await ctx.send("This auction is not scheduled for preview.")
            return
        
        embed = discord.Embed(title="Auction Preview", color=discord.Color.blue())
        embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=False)
        embed.add_field(name="Starting Bid", value=auction['min_bid'], inline=True)
        embed.add_field(name="Estimated Value", value=f"{auction['total_value']:,}", inline=True)
        embed.add_field(name="Scheduled Start", value=f"<t:{int(auction['scheduled_time'])}:F>", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    async def remindme(self, ctx: commands.Context, auction_id: str):
        """Set a reminder for an upcoming auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction or auction['status'] != 'scheduled':
            await ctx.send("This auction is not scheduled.")
            return
        
        async with self.config.member(ctx.author).auction_reminders() as reminders:
            reminders.append(auction_id)
        
        await ctx.send(f"You will be reminded 10 minutes before auction {auction_id} starts.")
        
        # Schedule the reminder
        reminder_time = auction['scheduled_time'] - 600  # 10 minutes before
        self.bot.loop.create_task(self.send_reminder(ctx.author.id, guild.id, auction_id, reminder_time))

    async def send_reminder(self, user_id: int, guild_id: int, auction_id: str, reminder_time: float):
        await asyncio.sleep(max(0, reminder_time - datetime.utcnow().timestamp()))
        guild = self.bot.get_guild(guild_id)
        user = guild.get_member(user_id)
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
        
        if not auction or auction['status'] != 'scheduled':
            return
        
        embed = discord.Embed(title="Auction Reminder", color=discord.Color.orange())
        embed.description = f"The auction for {auction['amount']}x {auction['item']} starts in 10 minutes!"
        
        try:
            await user.send(embed=embed)
        except discord.HTTPException:
            pass  # Unable to send DM to the user

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
        It features scheduled auctions, auto-bidding, featured auctions, and detailed analytics.
        """, inline=False)
        
        help_embed.add_field(name="Key Features", value="""
        • Create and manage auctions
        • Schedule auctions for future dates
        • Auto-bidding system
        • Featured auctions
        • Auction reminders
        • Detailed auction information and history
        • Admin controls and analytics
        """, inline=False)
        
        help_embed.add_field(name="User Commands", value="""
        • `spawnauction`: Start a new auction
        • `myauctions`: View your active and pending auctions
        • `auctionhistory`: View your completed auctions
        • `previewauction <auction_id>`: Preview an upcoming auction
        • `remindme <auction_id>`: Set a reminder for an auction
        • `autobid <auction_id> <max_bid>`: Set up an auto-bid
        • `cancelautobid <auction_id>`: Cancel your auto-bid
        • `auctioninfo <auction_id>`: Get detailed information about an auction
        """, inline=False)
        
        help_embed.add_field(name="Admin Commands", value="""
        • `auctionset`: Configure auction settings (use `auctionset` to see subcommands)
        • `forcestartauction <auction_id>`: Force start a pending auction
        • `cancelauction <auction_id>`: Cancel an active or pending auction
        • `auctionreport [days]`: Generate a report of auction activity
        • `setfeaturedauction <auction_id>`: Set an auction as featured
        • `cleanupauctions`: Remove old completed and cancelled auctions
        """, inline=False)
        
        help_embed.add_field(name="How It Works", value="""
        1. Admins set up the cog using `auctionset` commands.
        2. Users create auctions using the auction request button or command.
        3. Auctions can be scheduled or start immediately after approval.
        4. Users bid on active auctions in the designated channel.
        5. Auto-bidding can be set up to bid automatically up to a max amount.
        6. Auctions end after a set time, with possible extensions for last-minute bids.
        7. Winners must pay within a time limit or face consequences.
        8. Admins can view reports and manage auctions as needed.
        """, inline=False)
        
        help_embed.add_field(name="Tips", value="""
        • Use reminders to never miss an auction you're interested in.
        • Set up auto-bids to compete even when you're not actively watching.
        • Keep an eye on featured auctions for high-value or special items.
        • Admins should regularly check auction reports for insights.
        """, inline=False)
        
        help_embed.set_footer(text="For more detailed help on specific commands, use [p]help command")
        
        await ctx.send(embed=help_embed)

    async def update_user_stats(self, guild: discord.Guild, user_id: int, amount: int, action: str):
        """Update user statistics for auctions won or sold."""
        async with self.config.guild(guild).user_stats() as stats:
            if str(user_id) not in stats:
                stats[str(user_id)] = {'won': 0, 'sold': 0}
            
            stats[str(user_id)][action] += amount

    async def clean_auction_channel(self, guild: discord.Guild, channel_id: int):
        """Clean up the auction channel by removing the AuctionControlView."""
        channel = guild.get_channel(channel_id)
        if channel:
            async for message in channel.history(limit=100):
                if message.author == self.bot.user and isinstance(message.components[0], self.AuctionControlView):
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass

    async def schedule_auction_end(self, auction_id: str, delay: int):
        """Schedule an auction to end after a specified delay."""
        await asyncio.sleep(delay)
        guild_id = int(auction_id.split('-')[0])
        guild = self.bot.get_guild(guild_id)
        if guild:
            await self.end_auction(guild, auction_id)

    async def start_next_auction(self, guild: discord.Guild):
        """Start the next auction in the queue."""
        async with self.config.guild(guild).auction_queue() as queue:
            if queue:
                next_auction_id = queue[0]
                await self.start_auction(guild, next_auction_id)

    async def display_upcoming_auctions(self, guild: discord.Guild):
        """Display a list of upcoming scheduled auctions."""
        async with self.config.guild(guild).auctions() as auctions:
            upcoming_auctions = [a for a in auctions.values() if a['status'] == 'scheduled']
        upcoming_auctions.sort(key=lambda x: x['scheduled_time'])

        if not upcoming_auctions:
            return

        embed = discord.Embed(title="Upcoming Auctions", color=discord.Color.blue())
        for auction in upcoming_auctions[:5]:  # Display up to 5 upcoming auctions
            embed.add_field(
                name=f"{auction['amount']}x {auction['item']}",
                value=f"ID: {auction['auction_id']}\nStarts: <t:{int(auction['scheduled_time'])}:F>",
                inline=False
            )

        auction_channel_id = await self.config.guild(guild).auction_channel()
        auction_channel = guild.get_channel(auction_channel_id)
        if auction_channel:
            await auction_channel.send(embed=embed)

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
        await interaction.channel.send(f"Auction {auction_id} has been paused by a moderator.")

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
        await interaction.channel.send(f"Auction {auction_id} has been resumed by a moderator.")
        
        # Schedule the auction end with the adjusted end time
        remaining_time = auction['end_time'] - int(datetime.utcnow().timestamp())
        if remaining_time > 0:
            self.auction_tasks[auction_id] = self.bot.loop.create_task(self.schedule_auction_end(auction_id, remaining_time))

async def setup(bot: Red):
    await bot.add_cog(AdvancedAuction(bot))