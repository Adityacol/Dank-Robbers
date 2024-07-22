import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import aiohttp
import asyncio
import time
import logging
from discord.ui import Modal, TextInput, View, Button

# Set up logging
logging.basicConfig(level=logging.INFO)

class Auction(commands.Cog):
    """A cog to handle auctions with bidding and donations."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_global(auctions={})
        self.config.register_global(bids={})

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info(f'Logged in as {self.bot.user}')

    async def api_check(self, interaction, item_count, item_name) -> bool:
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
                    
                    if total_value < 100000000:  # Changed to 100 million
                        await interaction.response.send_message("The total donation value must be over 100 million.", ephemeral=True)
                        return False

            except Exception as e:
                await interaction.response.send_message(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                logging.error(f"Exception in API check: {e}")
                return False
        return True

    def get_next_auction_id(self):
        """Generate the next auction ID."""
        auctions = self.bot.loop.run_until_complete(self.config.auctions())
        return str(max(map(int, auctions.keys()), default=0) + 1)

    class AuctionModal(Modal):
        def __init__(self, cog):
            self.cog = cog
            super().__init__(title="Request An Auction")

        item_name = TextInput(
            label="What are you going to donate?",
            placeholder="e.g., Blob",
            required=True,
            min_length=1,
            max_length=100,
            style=discord.TextStyle.short
        )
        item_count = TextInput(
            label="How many of those items will you donate?",
            placeholder="e.g., 5",
            required=True,
            max_length=10
        )
        minimum_bid = TextInput(
            label="What should the minimum bid be?",
            placeholder="e.g., 1,000,000",
            required=False,
            style=discord.TextStyle.short
        )
        message = TextInput(
            label="What is your message?",
            placeholder="e.g., I love DR!",
            required=False,
            max_length=200,
            style=discord.TextStyle.short
        )

        async def on_submit(self, interaction: discord.Interaction):
            """Handle the form submission."""
            try:
                item_name = self.item_name.value
                item_count = self.item_count.value

                if not item_count.isdigit():
                    await interaction.response.send_message("Item count must be a number.", ephemeral=True)
                    return

                item_count = int(item_count)
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

                auction_id = self.cog.get_next_auction_id()

                auction_data = {
                    "auction_id": auction_id,
                    "user_id": interaction.user.id,
                    "item": item_name,
                    "amount": item_count,
                    "min_bid": self.minimum_bid.value or "1,000,000",
                    "message": self.message.value,
                    "status": "pending",
                    "ticket_channel_id": ticket_channel.id
                }

                async with self.cog.config.auctions() as auctions:
                    auctions[auction_id] = auction_data

            except Exception as e:
                logging.error(f"An error occurred in modal submission: {e}")
                await interaction.response.send_message(f"An error occurred while processing your submission: {str(e)}", ephemeral=True)

    class AuctionView(View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green)
        async def request_auction_button(self, button: discord.ui.Button, interaction: discord.Interaction):
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
            if after.author.id == 270904126974590976 and hasattr(after.interaction, "name") and after.interaction.name == "serverevents donate" and before.embeds != after.embeds:
                auctions = await self.config.auctions()
                title = after.embeds[0].title
                desc = after.embeds[0].description
                
                if title != "Action Confirmed":
                    return
                
                parts = desc.split("**")
                item_dank = str(parts[1].split(">")[1])
                amount_dank = int(parts[1].split("<")[0])
                
                for auction_id, auction in auctions.items():
                    if auction["item"].strip().lower() == item_dank.strip().lower() and int(auction["amount"]) == amount_dank and auction["status"] == "pending" and after.channel.id == auction["ticket_channel_id"]:
                        auction["status"] = "active"
                        auction["end_time"] = int(time.time()) + 30 * 60
                        async with self.config.auctions() as auctions:
                            auctions[auction_id] = auction
                        
                        user = await self.bot.fetch_user(auction["user_id"])
                        await after.channel.send(f"Item: {item_dank}, Amount: {amount_dank}")
                        await user.send("Thank you for your donation! Your auction will start shortly.")
                        ticket_channel = after.guild.get_channel(auction["ticket_channel_id"])
                        
                        if ticket_channel:
                            await ticket_channel.delete()
                        
                        await self.start_auction_announcement(after.guild, auction, auction["user_id"], auction["item"], auction["amount"])
                        return
                
                await after.channel.send("The donated item or amount does not match the saved auction details.")
                logging.info("Mismatch in donated item or amount.")
                
        except Exception as e:
            logging.error(f"An error occurred in on_message_edit: {e}")

    async def start_auction_announcement(self, guild, auction, user_id, item, amount):
        """Announce the start of the auction."""
        auction_channel = guild.get_channel(1250501101615190066)  # Replace with your auction channel ID
        if not auction_channel:
            auction_channel = await guild.create_text_channel("auction-channel")
        
        user = await self.bot.fetch_user(user_id)
        embed = discord.Embed(
            title="🎉 Auction Started! 🎉",
            description=f"**Item:** {item}\n**Amount:** {amount}\n**Starting Bid:** {auction['min_bid']}\n**Auction ID:** {auction['auction_id']}\n**Message:** {auction['message']}",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="Good luck to all bidders!")
        await auction_channel.send(embed=embed)
        await self.run_auction(auction)

    async def run_auction(self, auction):
        """Run the auction timer."""
        await asyncio.sleep(30 * 60)  # 30 minutes auction duration
        current_time = int(time.time())
        if current_time >= auction["end_time"]:
            await self.end_auction(auction)

    async def end_auction(self, auction):
        """End the auction and announce results."""
        auction_channel = self.bot.get_channel(1250501101615190066)  # Replace with your auction channel ID
        auction["status"] = "ended"
        async with self.config.auctions() as auctions:
            auctions[auction["auction_id"]] = auction
        
        bids = await self.config.bids()
        highest_bid = max(
            bids.get(auction["auction_id"], {}).values(),
            key=lambda x: x.get("amount", 0),
            default=None,
        )

        if highest_bid:
            user = await self.bot.fetch_user(highest_bid["user_id"])
            embed = discord.Embed(
                title="🏆 Auction Ended! 🏆",
                description=f"**Item:** {auction['item']}\n**Amount:** {auction['amount']}\n**Winner:** {user.mention}\n**Winning Bid:** {highest_bid['amount']}\n**Auction ID:** {auction['auction_id']}\n**Donated by:** {await self.bot.fetch_user(auction['user_id']).mention}",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            embed.set_footer(text="Congratulations to the winner!")
            await auction_channel.send(embed=embed)
        else:
            embed = discord.Embed(
                title="🛑 Auction Ended! 🛑",
                description=f"**Item:** {auction['item']}\n**Amount:** {auction['amount']}\n**No valid bids received.**\n**Auction ID:** {auction['auction_id']}\n**Donated by:** {await self.bot.fetch_user(auction['user_id']).mention}",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            embed.set_footer(text="Thank you for participating!")
            await auction_channel.send(embed=embed)

    @commands.command()
    async def bid(self, ctx: commands.Context, auction_id: int, bid_amount: int):
        """Place a bid on an auction."""
        auctions = await self.config.auctions()
        auction = auctions.get(str(auction_id))

        if not auction:
            await ctx.send("Invalid auction ID.")
            return

        if auction["status"] != "active":
            await ctx.send("Auction is not active.")
            return

        if bid_amount <= int(auction["min_bid"].replace(",", "")):
            await ctx.send("Bid amount is less than the minimum bid.")
            return

        async with self.config.bids() as bids:
            if str(auction_id) not in bids:
                bids[str(auction_id)] = {}

            bids[str(auction_id)][str(ctx.author.id)] = {
                "user_id": ctx.author.id,
                "amount": bid_amount
            }

        await ctx.send(f"Your bid of {bid_amount} has been placed for auction ID {auction_id}.")
        logging.info(f"Bid placed: {ctx.author} bid {bid_amount} on auction {auction_id}.")

    @commands.command()
    async def cancelauction(self, ctx: commands.Context, auction_id: int):
        """Cancel an auction."""
        auctions = await self.config.auctions()
        auction = auctions.get(str(auction_id))

        if not auction:
            await ctx.send("Invalid auction ID.")
            return

        if auction["status"] != "pending":
            await ctx.send("Only pending auctions can be cancelled.")
            return

        async with self.config.auctions() as auctions:
            del auctions[str(auction_id)]

        await ctx.send(f"Auction ID {auction_id} has been cancelled.")
        logging.info(f"Auction {auction_id} cancelled by {ctx.author}.")

def setup(bot: Red):
    bot.add_cog(Auction(bot))

