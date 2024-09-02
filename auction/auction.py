import discord
from discord import app_commands
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import aiohttp
import asyncio
import time
import logging
from typing import Optional, Dict, Any

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
        """Handle Dank Memer donation messages."""
        if message.author.id != 270904126974590976:  # Dank Memer bot ID
            return

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

    async def finalize_auction(self, guild, auction):
       """Finalize an auction after all items and tax have been donated."""
       auction["status"] = "active"
       channel = self.bot.get_channel(auction["ticket_channel_id"])
       if channel:
           await channel.send("All items and tax have been donated. Your auction is now active!")

       # Announce the auction in the queue channel
       queue_channel_id = await self.config.guild(guild).queue_channel()
       queue_channel = self.bot.get_channel(queue_channel_id)
       if queue_channel:
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

           start_time = time.time()
           end_time = start_time + 300  # 5 minutes

           while time.time() < end_time:
               # Fetch the message to get updated reaction counts
               message = await queue_channel.fetch_message(message.id)
               reactions = message.reactions
               check_count = next((r.count for r in reactions if str(r.emoji) == "✅"), 0) - 1  # Subtract 1 to exclude the bot's reaction

               if check_count >= 5:
                   await self.start_bidding(guild, auction)
                   return

               await asyncio.sleep(10)  # Check every 10 seconds

           # If we've reached here, 5 minutes have passed without enough reactions
           await queue_channel.send("Not enough interest. Auction cancelled.")
           await self.cancel_auction(guild, auction['auction_id'], "Not enough interest after 5 minutes")

       await self.config.guild(guild).auctions.set_raw(auction["auction_id"], value=auction)

    async def cancel_auction(self, guild, auction_id, reason):
       """Cancel an auction."""
       async with self.config.guild(guild).auctions() as auctions:
           if auction_id in auctions:
               del auctions[auction_id]
    
       log_channel_id = await self.config.guild(guild).log_channel()
       log_channel = self.bot.get_channel(log_channel_id)
       if log_channel:
           embed = discord.Embed(
               title="Auction Cancelled",
               description=f"Auction {auction_id} has been cancelled.\nReason: {reason}",
               color=discord.Color.red()
           )
           await log_channel.send(embed=embed)

    async def start_bidding(self, guild, auction):
        """Start the bidding process for an auction."""
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if queue_channel:
            await queue_channel.send("Auction Started!")

            auction_task = self.bot.loop.create_task(self.run_auction(guild, auction['auction_id']))
            self.auction_tasks[auction['auction_id']] = auction_task

    async def run_auction(self, guild, auction_id):
        """Run the auction process with timeouts."""
        auction = await self.config.guild(guild).auctions.get_raw(auction_id)
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            return

        current_bid = int(auction['min_bid'].replace(',', ''))
        auction_end_time = time.time() + 300  # 5 minutes from now

        while time.time() < auction_end_time:
            embed = discord.Embed(
                title=f"Auction: {auction['amount']}x {auction['item']}",
                description=f"**Current Highest Bid:** {current_bid:,}\n**Ends:** <t:{int(auction_end_time)}:R>",
                color=discord.Color.blue()
            )
            await queue_channel.send(embed=embed)

            try:
                bid_message = await self.bot.wait_for(
                    'message',
                    check=lambda m: m.channel == queue_channel and m.content.lower().startswith(('!bid', '!b')) and m.author.id != self.bot.user.id,
                    timeout=20.0
                )
            except asyncio.TimeoutError:
                continue

            try:
                bid_amount = int(bid_message.content.split()[1].replace(',', ''))
            except (IndexError, ValueError):
                await queue_channel.send("Invalid bid format. Use !bid [amount]")
                continue

            if bid_amount <= current_bid:
                await queue_channel.send(f"Your bid must be higher than {current_bid:,}")
                continue

            current_bid = bid_amount
            auction_end_time = time.time() + 60  # Extend by 1 minute

            embed = discord.Embed(title="Accepted bid", color=discord.Color.blue())
            embed.add_field(name=f"@{bid_message.author.name}", value=f"{bid_amount:,}", inline=False)
            embed.add_field(name="Item", value=f"{auction['amount']}x {auction['item']}", inline=True)
            embed.add_field(name="Market Worth", value=f"{auction['total_value']:,}", inline=True)
            embed.add_field(name="Status", value="Going Once", inline=False)
            await queue_channel.send(embed=embed)

        # Auction ended
        winner = bid_message.author if 'bid_message' in locals() else None
        await self.end_auction(guild, auction, winner, current_bid)

    async def end_auction(self, guild, auction, winner, winning_bid):
        """End the auction and handle the results."""
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)

        if not queue_channel:
            return

        if winner:
            embed = discord.Embed(title="Auction Ended", color=discord.Color.gold())
            embed.add_field(name="Winner", value=winner.mention, inline=False)
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
        else:
            await queue_channel.send("Auction ended with no bids.")

        # Clean up
        del self.auction_tasks[auction['auction_id']]
        await self.config.guild(guild).auctions.clear_raw(auction['auction_id'])

    async def schedule_auction_end(self, auction_id, delay):
        """Schedule the end of an auction."""
        await asyncio.sleep(delay)
        guild_id, _ = auction_id.split('-')
        guild = self.bot.get_guild(int(guild_id))
        if guild:
            await self.close_auction(None, auction_id, "Auction time limit reached")

    async def close_auction(self, interaction: Optional[discord.Interaction], auction_id: str, reason: str):
        """Close the auction channel and handle the aftermath."""
        guild_id, _ = auction_id.split('-')
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            log.error(f"Could not find guild for auction {auction_id}")
            return

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return
            
            channel = self.bot.get_channel(auction["ticket_channel_id"])
            if channel:
                await channel.delete()

            # Remove the auction role from the user
            auction_role_id = await self.config.guild(guild).auction_role()
            if auction_role_id:
                auction_role = guild.get_role(auction_role_id)
                member = guild.get_member(auction["user_id"])
                if auction_role and member:
                    await member.remove_roles(auction_role)

            # Assign blacklist role if the auction wasn't completed
            if auction["status"] == "pending":
                blacklist_role_id = await self.config.guild(guild).blacklist_role()
                if blacklist_role_id:
                    blacklist_role = guild.get_role(blacklist_role_id)
                    if blacklist_role and member:
                        await member.add_roles(blacklist_role)

            # Log the auction closure
            log_channel_id = await self.config.guild(guild).log_channel()
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="Auction Closed",
                    description=f"Auction for {auction['amount']}x {auction['item']} has been closed.\nReason: {reason}",
                    color=discord.Color.red()
                )
                await log_channel.send(embed=embed)

            del auctions[auction_id]

        if interaction:
            await interaction.followup.send("Auction has been closed.", ephemeral=True)

    @commands.command()
    async def bid(self, ctx: commands.Context, amount: str):
        """Place a bid on the active auction in this channel."""
        guild = ctx.guild
        auctions = await self.config.guild(guild).auctions()
        active_auction = next((a for a in auctions.values() if a['status'] == 'active' and a['ticket_channel_id'] == ctx.channel.id), None)

        if not active_auction:
            await ctx.send("There is no active auction in this channel.")
            return

        try:
            bid_amount = self.parse_amount(amount)
        except ValueError:
            await ctx.send("Invalid bid amount. Please use a valid number with optional k, m, or b suffix.")
            return

        min_bid = self.parse_amount(active_auction["min_bid"])
        if bid_amount < min_bid:
            await ctx.send(f"Your bid must be at least {active_auction['min_bid']}.")
            return

        async with self.config.guild(guild).bids() as bids:
            auction_bids = bids.get(active_auction['auction_id'], [])
            if auction_bids and bid_amount <= max(b["amount"] for b in auction_bids):
                await ctx.send("Your bid must be higher than the current highest bid.")
                return

            auction_bids.append({"user_id": ctx.author.id, "amount": bid_amount})
            bids[active_auction['auction_id']] = auction_bids

        await ctx.send(f"Your bid of {amount} has been placed successfully!")

        # Update the auction in the queue channel
        await self.update_queue_auction(guild, active_auction['auction_id'])

    def parse_amount(self, amount: str) -> int:
        """Parse a string amount with k, m, b suffixes into an integer."""
        amount = amount.lower().replace(',', '')
        if amount.endswith('k'):
            return int(float(amount[:-1]) * 1000)
        elif amount.endswith('m'):
            return int(float(amount[:-1]) * 1000000)
        elif amount.endswith('b'):
            return int(float(amount[:-1]) * 1000000000)
        else:
            return int(amount)

    async def update_queue_auction(self, guild: discord.Guild, auction_id: str):
        """Update the auction information in the queue channel."""
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)
        if not queue_channel:
            return

        auctions = await self.config.guild(guild).auctions()
        auction = auctions.get(auction_id)
        if not auction:
            return

        bids = await self.config.guild(guild).bids()
        auction_bids = bids.get(auction_id, [])
        highest_bid = max(b["amount"] for b in auction_bids) if auction_bids else 0

        embed = discord.Embed(
            title=f"Auction: {auction['amount']}x {auction['item']}",
            description=f"**Current Highest Bid:** {highest_bid:,}\n**Ends:** <t:{int(auction['end_time'])}:R>",
            color=discord.Color.blue()
        )

        async for message in queue_channel.history(limit=100):
            if message.embeds and message.embeds[0].title.startswith(f"Auction:") and auction['item'] in message.embeds[0].title:
                await message.edit(embed=embed)
                return

        await queue_channel.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def cancelauction(self, ctx: commands.Context, auction_id: str):
        """Cancel an active auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction["status"] not in ["pending", "active"]:
                await ctx.send("This auction cannot be cancelled.")
                return

        await self.close_auction(None, auction_id, f"Cancelled by {ctx.author}")
        await ctx.send(f"Auction {auction_id} has been cancelled.")

    @commands.command()
    async def auctioninfo(self, ctx: commands.Context, auction_id: str):
        """Get detailed information about an auction."""
        guild = ctx.guild
        auctions = await self.config.guild(guild).auctions()
        auction = auctions.get(auction_id)

        if not auction:
            await ctx.send("This auction does not exist.")
            return

        bids = await self.config.guild(guild).bids()
        auction_bids = bids.get(auction_id, [])

        embed = discord.Embed(
            title=f"Auction Information: {auction['amount']}x {auction['item']}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Status", value=auction["status"].capitalize(), inline=False)
        embed.add_field(name="Minimum Bid", value=auction["min_bid"], inline=True)
        embed.add_field(name="Start Time", value=f"<t:{int(auction['start_time'])}:F>", inline=True)
        
        if auction["end_time"]:
            embed.add_field(name="End Time", value=f"<t:{int(auction['end_time'])}:F>", inline=True)
        
        if auction_bids:
            highest_bid = max(auction_bids, key=lambda x: x["amount"])
            highest_bidder = ctx.guild.get_member(highest_bid["user_id"])
            embed.add_field(name="Highest Bid", value=f"{highest_bid['amount']:,} by {highest_bidder.mention if highest_bidder else 'Unknown User'}", inline=False)
        else:
            embed.add_field(name="Highest Bid", value="No bids yet", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def listauctions(self, ctx: commands.Context):
        """List all active auctions."""
        guild = ctx.guild
        auctions = await self.config.guild(guild).auctions()
        active_auctions = [a for a in auctions.values() if a['status'] == 'active']

        if not active_auctions:
            await ctx.send("There are no active auctions.")
            return

        embed = discord.Embed(title="Active Auctions", color=discord.Color.blue())
        for auction in active_auctions:
            embed.add_field(
                name=f"{auction['amount']}x {auction['item']}",
                value=f"ID: {auction['auction_id']}\nMinimum Bid: {auction['min_bid']}\nEnds: <t:{int(auction['end_time'])}:R>",
                inline=False
            )

        await ctx.send(embed=embed)

    def cog_unload(self):
        """Clean up on cog unload."""
        for task in self.auction_tasks.values():
            task.cancel()

async def setup(bot):
    await bot.add_cog(AdvancedAuction(bot))