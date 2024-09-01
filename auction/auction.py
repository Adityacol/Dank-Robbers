import discord
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config
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
        }
        self.config.register_guild(**default_guild)

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
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

    async def api_check(self, interaction: discord.Interaction, item_count: int, item_name: str) -> bool:
        """Check if the donated item meets the value requirements."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await interaction.response.send_message("Error fetching item value from API. Please try again later.", ephemeral=True)
                        logging.error(f"API response status: {response.status}")
                        return False
                    
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    
                    if not item_data:
                        await interaction.response.send_message("Item not found. Please enter a valid item name.", ephemeral=True)
                        return False
                    
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    
                    if total_value < 50000000:  # Changed to 50 million
                        await interaction.response.send_message("The total donation value must be over 50 million.", ephemeral=True)
                        return False

            except Exception as e:
                await interaction.response.send_message(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                logging.error(f"Exception in API check: {e}")
                return False
        return True

    def get_next_auction_id(self, guild: discord.Guild):
        """Generate the next auction ID."""
        auctions = self.bot.loop.run_until_complete(self.config.guild(guild).auctions())
        return str(max(map(int, auctions.keys()), default=0) + 1)

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
                item_name = self.item_name.value
                item_count = int(self.item_count.value)
                valid = await self.cog.api_check(interaction, item_count, item_name)
                
                if not valid:
                    return

                guild = interaction.guild
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    self.cog.bot.user: discord.PermissionOverwrite(read_messages=True),
                }
                
                ticket_channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", overwrites=overwrites)
                await ticket_channel.send(f"{interaction.user.mention}, please donate {item_count} of {item_name} as you have mentioned in the modal or you will get blacklisted.")
                await interaction.response.send_message("Auction details submitted! Please donate the items within 30 minutes.", ephemeral=True)

                auction_id = self.cog.get_next_auction_id(guild)

                auction_data = {
                    "auction_id": auction_id,
                    "user_id": interaction.user.id,
                    "item": item_name,
                    "amount": item_count,
                    "min_bid": self.minimum_bid.value or "1,000,000",
                    "message": self.message.value,
                    "status": "pending",
                    "ticket_channel_id": ticket_channel.id,
                    "start_time": time.time(),
                    "end_time": None
                }

                async with self.cog.config.guild(guild).auctions() as auctions:
                    auctions[auction_id] = auction_data

                # Send auction details with lock button
                item_value = item_count * 16375000  # Example calculation for item value
                fee = item_value * 0.02  # 2% fee
                embed = discord.Embed(
                    title="Your Auction Detail",
                    description=f"**{item_count}x {item_name}**\n"
                                f"**Minimum bid:** {self.minimum_bid.value or '1,000,000'}\n"
                                f"**Channeltype:** NORMAL\n"
                                f"Total worth: {item_value:,}\n"  
                                f"Your fee (2%): {fee:,}\n"
                                "Type `/auction makechanges` to make changes",
                    color=discord.Color.blue()
                )
                await ticket_channel.send(embed=embed, view=self.cog.TicketView(ticket_channel))

                # Assign the auction role
                auction_role = guild.get_role(1269319784688779416)
                if auction_role:
                    await interaction.user.add_roles(auction_role)

                # Schedule the auction end
                await self.cog.schedule_auction_end(auction_id, 7200)  # 2 hours

            except Exception as e:
                logging.error(f"An error occurred in modal submission: {e}")
                await interaction.response.send_message(f"An error occurred while processing your submission: {str(e)}", ephemeral=True)

    class TicketView(View):
        def __init__(self, channel):
            super().__init__(timeout=None)
            self.channel = channel

        @discord.ui.button(label="", style=discord.ButtonStyle.secondary, emoji="🔒")
        async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self.channel.delete()
            await interaction.response.send_message("Ticket closed.", ephemeral=True)

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
                await interaction.response.send_message(f"An error occurred while sending the modal: {str(e)}", ephemeral=True)

    @commands.command()
    async def requestauction(self, ctx: commands.Context):
        """Request a new auction."""
        view = self.AuctionView(self)
        embed = discord.Embed(
            title="🎉 Request an Auction 🎉",
            description="Click the button below to request an auction and submit your donation details.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. Await further instructions in your private channel.", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        await ctx.send(embed=embed, view=view)
        logging.info("Auction request initiated.")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Handle message edits related to auction donations."""
        try:
            if after.author.id == 270904126974590976 and before.embeds != after.embeds:
                guild = after.guild
                auctions = await self.config.guild(guild).auctions()
                title = after.embeds[0].title
                desc = after.embeds[0].description
                
                if title != "Action Confirmed":
                    return
                
                parts = desc.split("**")
                item_dank = str(parts[1].split(">")[1])
                amount_dank = int(parts[1].split("<")[0])
                
                for auction_id, auction in auctions.items():
                    if auction["status"] == "pending" and auction["item"] == item_dank and auction["amount"] == amount_dank:
                        auction["status"] = "active"
                        auction["end_time"] = int(time.time()) + 1800
                        await self.config.guild(guild).auctions.set_raw(auction_id, value=auction)
                        
                        user = self.bot.get_user(auction["user_id"])
                        ticket_channel = self.bot.get_channel(auction["ticket_channel_id"])
                        
                        if ticket_channel:
                            await ticket_channel.send("Donation confirmed. Your auction is now active.")

                        # Remove the auction role and add the blacklist role if the donation is confirmed
                        auction_role = guild.get_role(1269319784688779416)
                        blacklist_role = guild.get_role(904174609300095027)
                        member = guild.get_member(auction["user_id"])

                        if member:
                            await member.remove_roles(auction_role)
                            await member.add_roles(blacklist_role)

                        # Announce the auction in the queue channel
                        queue_channel_id = await self.config.guild(guild).queue_channel()
                        queue_channel = self.bot.get_channel(queue_channel_id)
                        if queue_channel:
                            embed = discord.Embed(
                                title="New Auction Available!",
                                description=f"**Item:** {amount_dank}x {item_dank}\n**Starting Bid:** {auction['min_bid']}\n**Ends:** <t:{auction['end_time']}:R>",
                                color=discord.Color.green()
                            )
                            await queue_channel.send(embed=embed)

        except Exception as e:
            logging.error(f"An error occurred in on_message_edit listener: {e}")

    async def schedule_auction_end(self, auction_id, delay):
        """Schedule the end of an auction."""
        await asyncio.sleep(delay)
        await self.end_auction(auction_id)

    async def end_auction(self, auction_id):
        """End the auction and announce the winner."""
        guild = self.bot.guilds[0]  # Assuming the bot is only in one guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction["status"] != "active":
                return
            
            # Set auction status to ended
            auction["status"] = "ended"

            # Announce the end of the auction
            channel = self.bot.get_channel(auction["ticket_channel_id"])
            if channel:
                embed = discord.Embed(
                    title="Auction Ended",
                    description=f"The auction for **{auction['amount']}x {auction['item']}** has ended!",
                    color=discord.Color.red()
                )
                await channel.send(embed=embed)

            # Handle the winner and the closing of the auction
            await self.handle_auction_closing(guild, auction)

    async def handle_auction_closing(self, guild, auction):
        """Handle the closing of an auction, including winner announcement and role management."""
        auction_role = guild.get_role(1269319784688779416)
        blacklist_role = guild.get_role(904174609300095027)
        member = guild.get_member(auction["user_id"])

        if member:
            await member.remove_roles(auction_role)
            await member.add_roles(blacklist_role)

        ticket_channel = guild.get_channel(auction["ticket_channel_id"])
        if ticket_channel:
            await ticket_channel.delete()

        # Announce the winner in the log channel
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = self.bot.get_channel(log_channel_id)
        if log_channel:
            bids = await self.config.guild(guild).bids()
            auction_bids = bids.get(auction["auction_id"], [])
            if auction_bids:
                winner = max(auction_bids, key=lambda x: x["amount"])
                embed = discord.Embed(
                    title="Auction Completed",
                    description=f"**Item:** {auction['amount']}x {auction['item']}\n**Winner:** {winner['user'].mention}\n**Winning Bid:** {winner['amount']:,}",
                    color=discord.Color.gold()
                )
                await log_channel.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="Auction Ended - No Bids",
                    description=f"**Item:** {auction['amount']}x {auction['item']}\n**No bids were placed.**",
                    color=discord.Color.red()
                )
                await log_channel.send(embed=embed)

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
            description=f"**Current Highest Bid:** {highest_bid:,}\n**Ends:** <t:{auction['end_time']}:R>",
            color=discord.Color.blue()
        )

        # Try to find and update the existing message, or send a new one if not found
        async for message in queue_channel.history(limit=100):
            if message.embeds and message.embeds[0].title.startswith(f"Auction: {auction['amount']}x {auction['item']}"):
                await message.edit(embed=embed)
                return

        await queue_channel.send(embed=embed)

    @commands.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def cancelauction(self, ctx: commands.Context, auction_id: str):
        """Cancel an active auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction["status"] != "active":
                await ctx.send("This auction is not active or does not exist.")
                return

            auction["status"] = "cancelled"
            await ctx.send(f"Auction {auction_id} has been cancelled.")

        # Remove the auction from the queue channel
        await self.remove_queue_auction(guild, auction_id)

        # Log the cancellation
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = self.bot.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="Auction Cancelled",
                description=f"**Item:** {auction['amount']}x {auction['item']}\n**Cancelled by:** {ctx.author.mention}",
                color=discord.Color.orange()
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
            if message.embeds and message.embeds[0].title.startswith(f"Auction: {auction['amount']}x {auction['item']}"):
                await message.delete()
                return

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
            embed.add_field(name="End Time", value=f"<t:{auction['end_time']}:F>", inline=True)
        
        if auction_bids:
            highest_bid = max(auction_bids, key=lambda x: x["amount"])
            highest_bidder = ctx.guild.get_member(highest_bid["user_id"])
            embed.add_field(name="Highest Bid", value=f"{highest_bid['amount']:,} by {highest_bidder.mention if highest_bidder else 'Unknown User'}", inline=False)
        else:
            embed.add_field(name="Highest Bid", value="No bids yet", inline=False)

        await ctx.send(embed=embed)

async def setup(bot: Red):
    await bot.add_cog(AdvancedAuction(bot))