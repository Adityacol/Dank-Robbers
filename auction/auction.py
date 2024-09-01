import discord
from discord import app_commands
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import aiohttp
import asyncio
import time
import logging
from typing import Optional

logging.basicConfig(level=logging.INFO)

class AdvancedAuction(commands.Cog):
    """An advanced cog to handle auctions with bidding, donations, and Dank Memer integration."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "auctions": {},
            "bids": {},
            "auction_channel": None,
            "log_channel": None,
            "queue_channel": None,
            "auction_role": None,
            "blacklist_role": None,
        }
        self.config.register_guild(**default_guild)

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
                        await self.safe_send(interaction, "Error fetching item value from API. Please try again later.", ephemeral=True)
                        logging.error(f"API response status: {response.status}")
                        return None, None, None
                    
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    
                    if not item_data:
                        await self.safe_send(interaction, "Item not found. Please enter a valid item name.", ephemeral=True)
                        return None, None, None
                    
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    tax = total_value * 0.10  # 10% tax
                    
                    if total_value < 50000000:  # 50 million
                        await self.safe_send(interaction, "The total donation value must be over 50 million.", ephemeral=True)
                        return None, None, None

                    return item_value, total_value, tax

            except Exception as e:
                await self.safe_send(interaction, f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                logging.error(f"Exception in API check: {e}")
                return None, None, None

    async def get_next_auction_id(self, guild: discord.Guild):
        """Generate the next auction ID."""
        auctions = await self.config.guild(guild).auctions()
        existing_ids = [int(aid.split('-')[1]) for aid in auctions.keys()]
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
                logging.info(f"Auction modal submitted by {interaction.user.name}")
                item_name = self.item_name.value
                item_count = self.item_count.value
                min_bid = self.minimum_bid.value or "1,000,000"
                message = self.message.value

                logging.info(f"Submitted values: item={item_name}, count={item_count}, min_bid={min_bid}")

                # Validate input
                try:
                    item_count = int(item_count)
                    if item_count <= 0:
                        raise ValueError("Item count must be positive")
                except ValueError as e:
                    await self.cog.safe_send(interaction, f"Invalid item count: {e}", ephemeral=True)
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

                await self.cog.safe_send(interaction, f"Auction channel created: {ticket_channel.mention}", ephemeral=True)

                # Schedule the auction end
                self.cog.bot.loop.create_task(self.cog.schedule_auction_end(auction_id, 21600))  # 6 hours

            except Exception as e:
                logging.error(f"An error occurred in modal submission: {e}", exc_info=True)
                await self.cog.safe_send(interaction, f"An error occurred while processing your submission. Please try again or contact an administrator.", ephemeral=True)

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
                logging.error(f"An error occurred while sending the modal: {e}")
                await self.cog.safe_send(interaction, f"An error occurred while sending the modal: {str(e)}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle Dank Memer donation messages."""
        if message.author.id != 270904126974590976:  # Dank Memer bot ID
            return

        logging.info(f"Received message from Dank Memer: {message.content}")

        if not message.embeds:
            logging.info("No embeds in the message")
            return

        embed = message.embeds[0]
        logging.info(f"Embed title: {embed.title}")
        logging.info(f"Embed description: {embed.description}")

        if not embed.description or "Successfully donated" not in embed.description:
            logging.info("Not a donation message")
            return

        guild = message.guild
        auctions = await self.config.guild(guild).auctions()
        
        logging.info(f"Current auctions: {auctions}")
        logging.info(f"Current channel ID: {message.channel.id}")

        for auction_id, auction in auctions.items():
            logging.info(f"Checking auction {auction_id}: {auction}")
            if auction["ticket_channel_id"] == message.channel.id and auction["status"] == "pending":
                logging.info(f"Found matching auction: {auction_id}")
                await self.process_donation(message, auction)
                break
        else:
            logging.info("No matching auction found")

    async def process_donation(self, message, auction):
        """Process a donation for an auction."""
        embed = message.embeds[0]
        description = embed.description
        parts = description.split()
        logging.info(f"Donation message parts: {parts}")

        try:
            # Find the donated amount and item name
            donated_amount = None
            donated_item = None
            for i, part in enumerate(parts):
                if part.isdigit():
                    donated_amount = int(part)
                    donated_item = " ".join(parts[i+1:])
                    break

            if donated_amount is None or donated_item is None:
                raise ValueError("Could not parse donation amount or item")

            logging.info(f"Parsed donation: {donated_amount} {donated_item}")

            if donated_item.lower() != auction["item"].lower():
                await message.channel.send("This item doesn't match the auction item. Please donate the correct item.")
                return

            auction["donated_amount"] += donated_amount
            remaining_amount = auction["amount"] - auction["donated_amount"]
            remaining_tax = auction["tax"] - auction["donated_tax"]

            logging.info(f"Updated auction: {auction}")

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
                    embed.add_field(name="Remaining Tax", value=f"{remaining_tax:,}", inline=False)
                await message.channel.send(embed=embed)

            await self.config.guild(message.guild).auctions.set_raw(auction["auction_id"], value=auction)

        except Exception as e:
            logging.error(f"Error processing donation: {e}")
            await message.channel.send("An error occurred while processing the donation. Please contact an administrator.")

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
            embed = discord.Embed(
                title="New Auction Available!",
                description=f"**Item:** {auction['amount']}x {auction['item']}\n**Starting Bid:** {auction['min_bid']}\n**Ends:** <t:{int(auction['end_time'])}:R>",
                color=discord.Color.green()
            )
            await queue_channel.send(embed=embed)

        await self.config.guild(guild).auctions.set_raw(auction["auction_id"], value=auction)

    async def schedule_auction_end(self, auction_id, delay):
        """Schedule the end of an auction."""
        await asyncio.sleep(delay)
        await self.end_auction(auction_id)

    async def end_auction(self, auction_id):
        """End the auction and announce the winner."""
        guild_id, _ = auction_id.split('-')
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            logging.error(f"Could not find guild for auction {auction_id}")
            return

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction["status"] != "active":
                return
            
            # Set auction status to ended
            auction["status"] = "ended"

        await self.close_auction(None, auction_id, "Auction time limit reached")

    async def close_auction(self, interaction: Optional[discord.Interaction], auction_id: str, reason: str):
        """Close the auction channel and handle the aftermath."""
        guild_id, _ = auction_id.split('-')
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            logging.error(f"Could not find guild for auction {auction_id}")
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

            # Handle the winner if the auction was active
            if auction["status"] == "ended":
                await self.handle_auction_winner(guild, auction)

            # Remove the auction from the queue channel
            await self.remove_queue_auction(guild, auction_id)

        if interaction:
            await self.safe_send(interaction, "Auction has been closed.", ephemeral=True)

    async def handle_auction_winner(self, guild: discord.Guild, auction: dict):
        """Handle the winner of an ended auction."""
        bids = await self.config.guild(guild).bids()
        auction_bids = bids.get(auction["auction_id"], [])
        
        if not auction_bids:
            log_channel_id = await self.config.guild(guild).log_channel()
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="Auction Ended - No Bids",
                    description=f"**Item:** {auction['amount']}x {auction['item']}\n**No bids were placed.**",
                    color=discord.Color.orange()
                )
                await log_channel.send(embed=embed)
            return

        winner = max(auction_bids, key=lambda x: x["amount"])
        winner_user = guild.get_member(winner["user_id"])

        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = self.bot.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="Auction Completed",
                description=f"**Item:** {auction['amount']}x {auction['item']}\n**Winner:** {winner_user.mention if winner_user else 'Unknown User'}\n**Winning Bid:** {winner['amount']:,}",
                color=discord.Color.gold()
            )
            await log_channel.send(embed=embed)

    async def remove_queue_auction(self, guild: discord.Guild, auction_id: str):
        """Remove the auction information from the queue channel."""
        queue_channel_id = await self.config.guild(guild).queue_channel()
        queue_channel = self.bot.get_channel(queue_channel_id)
        if not queue_channel:
            return

        auctions = await self.config.guild(guild).auctions()
        auction = auctions.get(auction_id)
        if not auction:
            return

        async for message in queue_channel.history(limit=100):
            if message.embeds and message.embeds[0].title.startswith(f"New Auction Available!") and auction['item'] in message.embeds[0].description:
                await message.delete()
                return

    @commands.command()
    async def bid(self, ctx: commands.Context, auction_id: str, amount: str):
        """Place a bid on an active auction."""
        guild = ctx.guild
        auctions = await self.config.guild(guild).auctions()
        auction = auctions.get(auction_id)

        if not auction or auction["status"] != "active":
            await ctx.send("This auction is not active or does not exist.")
            return

        try:
            bid_amount = self.parse_amount(amount)
        except ValueError:
            await ctx.send("Invalid bid amount. Please use a valid number with optional k, m, or b suffix.")
            return

        min_bid = self.parse_amount(auction["min_bid"])
        if bid_amount < min_bid:
            await ctx.send(f"Your bid must be at least {auction['min_bid']}.")
            return

        async with self.config.guild(guild).bids() as bids:
            auction_bids = bids.get(auction_id, [])
            if auction_bids and bid_amount <= max(b["amount"] for b in auction_bids):
                await ctx.send("Your bid must be higher than the current highest bid.")
                return

            auction_bids.append({"user_id": ctx.author.id, "amount": bid_amount})
            bids[auction_id] = auction_bids

        await ctx.send(f"Your bid of {amount} has been placed successfully!")

        # Update the auction in the queue channel
        await self.update_queue_auction(guild, auction_id)

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
            if message.embeds and message.embeds[0].title.startswith(f"New Auction Available!") and auction['item'] in message.embeds[0].description:
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

    async def safe_send(self, interaction: discord.Interaction, content: str, **kwargs):
        """Safely send a response to an interaction, handling unknown interaction errors."""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, **kwargs)
            else:
                await interaction.response.send_message(content, **kwargs)
        except discord.errors.NotFound:
            logging.warning(f"Attempted to respond to an unknown interaction: {interaction.id}")
        except Exception as e:
            logging.error(f"Error in safe_send: {e}")

    def cog_unload(self):
        """Clean up on cog unload."""
        # Cancel any scheduled tasks here if needed

async def setup(bot):
    await bot.add_cog(AdvancedAuction(bot))