import discord
from discord import app_commands
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import aiohttp
import asyncio
import time
import logging
from typing import Optional, Dict, Any, List

log = logging.getLogger("red.economy.AdvancedAuction")

class AdvancedAuction(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
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
            "auction_queue": []
        }
        self.config.register_guild(**default_guild)
        self.active_auctions = {}
        self.auction_tasks = {}

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

    @auctionset.command(name="blacklistrole")
    async def set_blacklist_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be assigned to users who fail to complete their auction donation."""
        await self.config.guild(ctx.guild).blacklist_role.set(role.id)
        await ctx.send(f"Blacklist role set to {role.name}.")

    @auctionset.command(name="auctionpingrole")
    async def set_auction_ping_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be pinged for regular auctions."""
        await self.config.guild(ctx.guild).auction_ping_role.set(role.id)
        await ctx.send(f"Auction ping role set to {role.name}.")

    @auctionset.command(name="massiveauctionpingrole")
    async def set_massive_auction_ping_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be pinged for massive auctions (worth over 1b)."""
        await self.config.guild(ctx.guild).massive_auction_ping_role.set(role.id)
        await ctx.send(f"Massive auction ping role set to {role.name}.")

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
        await channel.send(embed=embed, view=view)
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
                    
                    if total_value < 500000:  # 50 million
                        await interaction.followup.send("The total donation value must be over 50 million.", ephemeral=True)
                        return None, None, None

                    return item_value, total_value, tax

            except Exception as e:
                await interaction.followup.send(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                log.error(f"Exception in API check: {e}")
                return None, None, None

    async def get_next_auction_id(self, guild: discord.Guild):
        """Generate the next auction ID."""
        auctions = await self.config.guild(guild).auctions()
        existing_ids = [int(aid.split('-')[1]) for aid in auctions.keys() if '-' in aid]
        next_id = max(existing_ids, default=0) + 1
        return f"{guild.id}-{next_id}"

    class AuctionModal(Modal):
        def __init__(self, cog):
            self.cog = cog
            super().__init__(title="Request An Auction")

        item_name = TextInput(label="What are you going to donate?", placeholder="e.g., Blob", required=True, min_length=1, max_length=100)
        item_count = TextInput(label="How many of those items will you donate?", placeholder="e.g., 5", required=True, max_length=10)
        minimum_bid = TextInput(label="What should the minimum bid be?", placeholder="e.g., 1,000,000", required=False)
        message = TextInput(label="What is your message?", placeholder="e.g., I love DR!", required=False, max_length=200)

        async def on_submit(self, interaction: discord.Interaction):
            """Handle the form submission."""
            try:
                log.info(f"Auction modal submitted by {interaction.user.name}")
                item_name = self.item_name.value
                item_count = self.item_count.value
                min_bid = self.minimum_bid.value or "1,000,000"
                message = self.message.value

                log.info(f"Submitted values: item={item_name}, count={item_count}, min_bid={min_bid}")

                # Respond immediately to close the modal
                await interaction.response.send_message("Processing your auction request...", ephemeral=True)

                # Start a task to process the auction
                self.cog.bot.loop.create_task(self.process_auction(interaction, item_name, item_count, min_bid, message))

            except Exception as e:
                log.error(f"An error occurred in modal submission: {e}", exc_info=True)
                await interaction.followup.send(f"An error occurred while processing your submission. Please try again or contact an administrator.", ephemeral=True)

        async def process_auction(self, interaction: discord.Interaction, item_name: str, item_count: str, min_bid: str, message: str):
            try:
                # Validate input
                try:
                    item_count = int(item_count)
                    if item_count <= 0:
                        raise ValueError("Item count must be positive")
                except ValueError as e:
                    await interaction.followup.send(f"Invalid item count: {e}", ephemeral=True)
                    return

                # Check item value
                item_value, total_value, tax = await self.cog.api_check(interaction, item_count, item_name)
                if not item_value:
                    return  # api_check will have sent an error message

                guild = interaction.guild
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    self.cog.bot.user: discord.PermissionOverwrite(read_messages=True),
                }
                
                ticket_channel = await guild.create_text_channel(f"auction-{interaction.user.name}", overwrites=overwrites)
                
                auction_id = await self.cog.get_next_auction_id(guild)

                auction_data = {
                    "auction_id": auction_id,
                    "guild_id": guild.id,
                    "user_id": interaction.user.id,
                    "item": item_name,
                    "amount": item_count,
                    "min_bid": min_bid,
                    "message": message,
                    "status": "pending",
                    "ticket_channel_id": ticket_channel.id,
                    "start_time": int(time.time()),
                    "end_time": int(time.time()) + 21600,  # 6 hours
                    "item_value": item_value,
                    "total_value": total_value,
                    "tax": tax,
                    "donated_amount": 0,
                    "donated_tax": 0
                }

                async with self.cog.config.guild(guild).auctions() as auctions:
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
                embed.add_field(name="Channel closes in", value="6 hours", inline=True)
                embed.set_footer(text="This channel will be deleted after 6 hours.")

                view = self.cog.AuctionControlView(self.cog, auction_id)
                await ticket_channel.send(content=interaction.user.mention, embed=embed, view=view)

                # Assign the auction role
                auction_role_id = await self.cog.config.guild(guild).auction_role()
                if auction_role_id:
                    auction_role = guild.get_role(auction_role_id)
                    if auction_role:
                        await interaction.user.add_roles(auction_role)

                await interaction.followup.send(f"Auction channel created: {ticket_channel.mention}", ephemeral=True)

                # Schedule the auction end
                self.cog.bot.loop.create_task(self.cog.schedule_auction_end(auction_id, 21600))  # 6 hours

            except Exception as e:
                log.error(f"An error occurred while processing the auction: {e}", exc_info=True)
                await interaction.followup.send(f"An error occurred while creating your auction. Please try again or contact an administrator.", ephemeral=True)

    class AuctionControlView(View):
        def __init__(self, cog, auction_id):
            super().__init__(timeout=None)
            self.cog = cog
            self.auction_id = auction_id

        @discord.ui.button(label="Close Auction", style=discord.ButtonStyle.danger)
        async def close_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.cog.close_auction(interaction, self.auction_id, "User closed the auction")

    class AuctionView(View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green)
        async def request_auction_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Open the auction request modal."""
            try:
                modal = self.cog.AuctionModal(self.cog)
                await interaction.response.send_modal(modal)
            except Exception as e:
                log.error(f"An error occurred while sending the modal: {e}")
                await interaction.followup.send(f"An error occurred while sending the modal: {str(e)}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle Dank Memer donation messages and bids."""
        if message.author.bot and message.author.id != 270904126974590976:  # Ignore all bots except Dank Memer
            return

        if message.author.id == 270904126974590976:  # Dank Memer bot ID
            await self.handle_dank_memer_message(message)
        else:
            await self.handle_potential_bid(message)

    async def handle_dank_memer_message(self, message):
        """Handle messages from Dank Memer bot."""
        log.info(f"Received message from Dank Memer: {message.content}")

        if not message.embeds:
            log.info("No embeds in the message")
            return

        embed = message.embeds[0]
        log.info(f"Embed title: {embed.title}")
        log.info(f"Embed description: {embed.description}")

        if embed.title == "Pending Confirmation":
            # This is a confirmation message, we'll wait for it to be edited
            def check(before, after):
                return before.id == message.id and after.embeds and "Successfully donated" in after.embeds[0].description

            try:
                _, edited_message = await self.bot.wait_for('message_edit', check=check, timeout=30.0)
                await self.handle_donation(edited_message)
            except asyncio.TimeoutError:
                log.info("Donation confirmation timed out")
        elif "Successfully donated" in embed.description:
            # This is already a successful donation message
            await self.handle_donation(message)
        else:
            log.info("Not a donation message")

    async def handle_donation(self, message):
        """Handle a successful donation message."""
        guild = message.guild
        auctions = await self.config.guild(guild).auctions()
        
        log.info(f"Current auctions: {auctions}")
        log.info(f"Current channel ID: {message.channel.id}")

        for auction_id, auction in auctions.items():
            log.info(f"Checking auction {auction_id}: {auction}")
            if auction["ticket_channel_id"] == message.channel.id and auction["status"] == "pending":
                log.info(f"Found matching auction: {auction_id}")
                await self.process_donation(message, auction)
                break
        else:
            log.info("No matching auction found")

    async def process_donation(self, message, auction):
        """Process a donation for an auction."""
        embed = message.embeds[0]
        description = embed.description
        log.info(f"Processing donation: {description}")

        try:
            # Extract donation information
            parts = description.split("**")
            log.info(f"Split parts: {parts}")
        
            if len(parts) < 3:
                raise ValueError("Unexpected donation message format")

            donation_info = parts[1].strip()
            log.info(f"Donation info: {donation_info}")

            # Check if it's a currency donation (tax payment)
            if '⏣' in donation_info:
                # Remove currency symbol and commas, then convert to int
                amount_str = ''.join(filter(str.isdigit, donation_info))
                log.info(f"Parsed amount string: {amount_str}")
            
                if not amount_str:
                    raise ValueError(f"Unable to parse amount from: {donation_info}")
            
                donated_amount = int(amount_str)
                is_tax_payment = True
                donated_item = "Tax Payment"
            else:
                # Split the donation info into amount and item
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
                # Clean up item names for comparison
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

            if remaining_tax < 0:
                await message.channel.send("The tax payment exceeds the required amount. Please contact an administrator.")
                return

            if remaining_amount <= 0 and remaining_tax <= 0:
                await self.finalize_auction(message.guild, auction)
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

            await self.config.guild(message.guild).auctions.set_raw(auction["auction_id"], value=auction)

        except Exception as e:
            log.error(f"Error processing donation: {e}", exc_info=True)
            await message.channel.send(f"An error occurred while processing the donation: {str(e)}. Please contact an administrator.")

    async def handle_potential_bid(self, message):
        """Handle potential bid messages."""
        if not self.is_valid_bid_format(message.content):
            return

        guild = message.guild
        auctions = await self.config.guild(guild).auctions()
        active_auction = next((a for a in auctions.values() if a['status'] == 'active' and a['ticket_channel_id'] == message.channel.id), None)

        if not active_auction:
            return

        bid_amount = self.parse_bid_amount(message.content)
        if bid_amount <= int(active_auction['current_bid']):
            await message.channel.send(f"Your bid must be higher than the current bid of {active_auction['current_bid']:,}.")
            return

        active_auction['current_bid'] = bid_amount
        active_auction['current_bidder'] = message.author.id
        await self.config.guild(guild).auctions.set_raw(active_auction['auction_id'], value=active_auction)

        await message.add_reaction("✅")
        embed = discord.Embed(title="New Highest Bid", color=discord.Color.green())
        embed.add_field(name="Bidder", value=message.author.mention, inline=True)
        embed.add_field(name="Amount", value=f"{bid_amount:,}", inline=True)
        await message.channel.send(embed=embed)

        # Extend auction time
        active_auction['end_time'] = int(time.time()) + 60  # Extend by 1 minute
        await self.config.guild(guild).auctions.set_raw(active_auction['auction_id'], value=active_auction)

    def is_valid_bid_format(self, content: str) -> bool:
        """Check if the message content is a valid bid format."""
        return content.replace(',', '').isdigit() or content.lower().endswith(('k', 'm', 'b'))

    def parse_bid_amount(self, content: str) -> int:
        """Parse the bid amount from the message content."""
        content = content.lower().replace(',', '')
        if content.endswith('k'):
            return int(float(content[:-1]) * 1000)
        elif content.endswith('m'):
            return int(float(content[:-1]) * 1000000)
        elif content.endswith('b'):
            return int(float(content[:-1]) * 1000000000)
        else:
            return int(content)

    async def finalize_auction(self, guild, auction):
        """Finalize an auction after all items and tax have been donated."""
        auction["status"] = "active"
        channel = self.bot.get_channel(auction["ticket_channel_id"])
        if channel:
            await channel.send("All items and tax have been donated. Your auction is now active!")

        # Add to the auction queue
        async with self.config.guild(guild).auction_queue() as queue:
            queue.append(auction["auction_id"])

        # If this is the only auction in the queue, start it immediately
        if len(queue) == 1:
            await self.start_next_auction(guild)

    async def start_next_auction(self, guild):
        """Start the next auction in the queue."""
        async with self.config.guild(guild).auction_queue() as queue:
            if not queue:
                return
            auction_id = queue[0]

        auction = await self.config.guild(guild).auctions.get_raw(auction_id)
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
        embed.add_field(name="Reactions Needed", value="5", inline=True)

        message = await queue_channel.send(content=f"{ping} Auction is about to begin!", embed=embed)
        await message.add_reaction("✅")

        # Wait for 5 minutes or until 5 reactions
        def reaction_check(reaction, user):
            return str(reaction.emoji) == "✅" and reaction.message.id == message.id and not user.bot

        try:
            for _ in range(5):
                await self.bot.wait_for('reaction_add', timeout=60.0, check=reaction_check)
        except asyncio.TimeoutError:
            pass

        # Fetch the message again to get the final reaction count
        message = await queue_channel.fetch_message(message.id)
        reactions = message.reactions
        check_count = next((r.count for r in reactions if str(r.emoji) == "✅"), 0) - 1  # Subtract 1 to exclude the bot's reaction

        if check_count >= 2:
            await self.run_auction(guild, auction_id)
        else:
            await queue_channel.send("Not enough interest. Auction cancelled.")
            await self.cancel_auction(guild, auction_id, "Not enough interest")

    async def run_auction(self, guild, auction_id):
        """Run the auction process."""
        auction = await self.config.guild(guild).auctions.get_raw(auction_id)
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            log.error(f"Queue channel not found for guild {guild.id}")
            return

        await queue_channel.send("Auction Started!")

        auction['status'] = 'active'
        auction['current_bid'] = int(auction['min_bid'].replace(',', ''))
        auction['current_bidder'] = None
        auction['end_time'] = int(time.time()) + 300  # 5 minutes from now

        await self.config.guild(guild).auctions.set_raw(auction_id, value=auction)

        while time.time() < auction['end_time']:
            embed = discord.Embed(
                title=f"Auction: {auction['amount']}x {auction['item']}",
                description=f"**Current Highest Bid:** {auction['current_bid']:,}\n**Ends:** <t:{auction['end_time']}:R>",
                color=discord.Color.blue()
            )
            await queue_channel.send(embed=embed)

            await asyncio.sleep(10)  # Wait for 10 seconds before next update

        await self.end_auction(guild, auction_id)

    async def end_auction(self, guild, auction_id):
        """End the auction and handle the results."""
        auction = await self.config.guild(guild).auctions.get_raw(auction_id)
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            log.error(f"Queue channel not found for guild {guild.id}")
            return

        if auction['current_bidder']:
            winner = guild.get_member(auction['current_bidder'])
            winning_bid = auction['current_bid']

            embed = discord.Embed(title="Auction Ended", color=discord.Color.gold())
            embed.add_field(name="Winner", value=winner.mention if winner else "Unknown User", inline=False)
            embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=True)
            embed.add_field(name="Winning Bid", value=f"{winning_bid:,}", inline=True)
            await queue_channel.send(embed=embed)

            await queue_channel.send(f"{winner.mention}, send the money in this channel within 5 minutes!")
            await queue_channel.send(f"/serverevents donate quantity:{winning_bid}")

            try:
                await self.bot.wait_for(
                    'message',
                    check=lambda m: m.author.id == 270904126974590976 and "Successfully donated" in m.content,
                    timeout=300.0
                )
                await queue_channel.send("Successfully donated!")
                await queue_channel.send("Your items will be delivered to you shortly!")
            except asyncio.TimeoutError:
                await queue_channel.send("Payment not received in time. Auction cancelled.")
                await self.handle_payment_failure(guild, auction, winner)
        else:
            await queue_channel.send("Auction ended with no bids.")

        # Remove the auction from the queue
        async with self.config.guild(guild).auction_queue() as queue:
            queue.remove(auction_id)

        # Start the next auction if there's one in the queue
        if queue:
            await self.start_next_auction(guild)
        else:
            await self.display_upcoming_auctions(guild)

    async def handle_payment_failure(self, guild, auction, failed_bidder):
        """Handle payment failure by moving to the next highest bidder."""
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            log.error(f"Queue channel not found for guild {guild.id}")
            return

        # Blacklist the failed bidder
        blacklist_role_id = await self.config.guild(guild).blacklist_role()
        if blacklist_role_id:
            blacklist_role = guild.get_role(blacklist_role_id)
            if blacklist_role and failed_bidder:
                await failed_bidder.add_roles(blacklist_role)
                await queue_channel.send(f"{failed_bidder.mention} has been blacklisted for failing to pay.")

        # Get all bids for this auction
        bids = await self.config.guild(guild).bids()
        auction_bids = bids.get(auction['auction_id'], [])

        # Sort bids by amount in descending order
        sorted_bids = sorted(auction_bids, key=lambda x: x['amount'], reverse=True)

        for bid in sorted_bids[1:4]:  # Try the next 3 highest bidders
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
                
                # Assign the auction role to the winning bidder
                auction_role_id = await self.config.guild(guild).auction_role()
                if auction_role_id:
                    auction_role = guild.get_role(auction_role_id)
                    if auction_role:
                        await bidder.add_roles(auction_role)
                
                return
            except asyncio.TimeoutError:
                await queue_channel.send(f"{bidder.mention} failed to pay in time.")
                await self.handle_payment_failure(guild, auction, bidder)
                return

        # If we've reached this point, all potential winners have failed to pay
        await queue_channel.send("All potential winners failed to pay. Auction cancelled.")

    async def display_upcoming_auctions(self, guild):
        """Display the list of upcoming auctions."""
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            log.error(f"Queue channel not found for guild {guild.id}")
            return

        async with self.config.guild(guild).auction_queue() as queue:
            if not queue:
                await queue_channel.send("There are no upcoming auctions.")
                return

            embed = discord.Embed(title="Upcoming Auctions", color=discord.Color.blue())
            
            for i, auction_id in enumerate(queue, 1):
                auction = await self.config.guild(guild).auctions.get_raw(auction_id)
                user = self.bot.get_user(auction['user_id'])
                username = user.name if user else "Unknown User"
                
                embed.add_field(
                    name=f"{i}. @{username}",
                    value=f"{auction['amount']}x {auction['item']} - {auction['min_bid']} {auction.get('message', '')}",
                    inline=False
                )

            await queue_channel.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def removeprevbid(self, ctx: commands.Context):
        """Remove the previous bid from the current auction."""
        guild = ctx.guild
        auctions = await self.config.guild(guild).auctions()
        active_auction = next((a for a in auctions.values() if a['status'] == 'active'), None)

        if not active_auction:
            await ctx.send("There is no active auction.")
            return

        bids = await self.config.guild(guild).bids()
        auction_bids = bids.get(active_auction['auction_id'], [])

        if len(auction_bids) < 2:
            await ctx.send("There are not enough bids to remove the previous one.")
            return

        removed_bid = auction_bids.pop()
        new_highest_bid = auction_bids[-1]

        active_auction['current_bid'] = new_highest_bid['amount']
        active_auction['current_bidder'] = new_highest_bid['user_id']

        await self.config.guild(guild).auctions.set_raw(active_auction['auction_id'], value=active_auction)
        await self.config.guild(guild).bids.set_raw(active_auction['auction_id'], value=auction_bids)

        removed_bidder = guild.get_member(removed_bid['user_id'])
        new_highest_bidder = guild.get_member(new_highest_bid['user_id'])

        await ctx.send(f"Removed bid of {removed_bid['amount']:,} by {removed_bidder.mention if removed_bidder else 'Unknown User'}. "
                       f"New highest bid: {new_highest_bid['amount']:,} by {new_highest_bidder.mention if new_highest_bidder else 'Unknown User'}.")

    def cog_unload(self):
        """Clean up on cog unload."""
        for task in self.auction_tasks.values():
            task.cancel()

async def setup(bot):
    await bot.add_cog(AdvancedAuction(bot))