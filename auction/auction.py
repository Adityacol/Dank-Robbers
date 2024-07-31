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

    async def api_check(self, interaction: discord.Interaction, item_count, item_name) -> bool:
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
                    
                    if total_value < 20000000:  # Changed to 20 million
                        await interaction.response.send_message("The total donation value must be over 20 million.", ephemeral=True)
                        return False

            except Exception as e:
                await interaction.response.send_message(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                logging.error(f"Exception in API check: {e}")
                return False
        return True

    async def get_next_auction_id(self):
        """Generate the next auction ID."""
        auctions = await self.config.auctions()
        return str(max(map(int, auctions.keys()), default=0) + 1)

    class AuctionModal(Modal):
        def __init__(self, cog):
            self.cog = cog
            super().__init__(title="Request An Auction")

            self.item_name = TextInput(
                label="What are you going to donate?",
                placeholder="e.g., Blob",
                required=True,
                min_length=1,
                max_length=100,
            )
            self.item_count = TextInput(
                label="How many of those items will you donate?",
                placeholder="e.g., 5",
                required=True,
                max_length=10,
            )
            self.minimum_bid = TextInput(
                label="What should the minimum bid be?",
                placeholder="e.g., 1,000,000",
                required=False,
            )
            self.message = TextInput(
                label="What is your message?",
                placeholder="e.g., I love DR!",
                required=False,
                max_length=200,
            )

            self.add_item(self.item_name)
            self.add_item(self.item_count)
            self.add_item(self.minimum_bid)
            self.add_item(self.message)

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

                auction_id = await self.cog.get_next_auction_id()

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
        async def request_auction_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Open the auction request modal."""
            try:
                modal = Auction.AuctionModal(self.cog)
                await interaction.response.send_modal(modal)
            except Exception as e:
                logging.error(f"An error occurred while sending the modal: {e}")
                await interaction.response.send_message(f"An error occurred while sending the modal: {str(e)}", ephemeral=True)

    @commands.command()
    async def requestauction(self, ctx: commands.Context):
        """Request a new auction."""
        view = Auction.AuctionView(self)
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
            if (
                after.author.id == 270904126974590976  # Dank Memer Bot ID
                and hasattr(after, "interaction_metadata") 
                and after.interaction_metadata.name == "serverevents donate" 
                and before.embeds != after.embeds
            ):
                auctions = await self.config.auctions()
                title = after.embeds[0].title
                desc = after.embeds[0].description
                
                if title != "Action Confirmed":
                    return
                
                parts = desc.split("**")
                item_dank = str(parts[1].split(">")[1])
                amount_dank = int(parts[1].split("<")[0])
                
                for auction_id, auction in auctions.items():
                    if (
                        auction["item"].strip().lower() == item_dank.strip().lower() 
                        and int(auction["amount"]) == amount_dank 
                        and auction["status"] == "pending" 
                        and after.channel.id == auction["ticket_channel_id"]
                    ):
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
        """Send an announcement for a new auction."""
        auction_channel = self.bot.get_channel(1257926928300769290)  # Replace with your auction channel ID
        embed = discord.Embed(
            title="🎉 New Auction! 🎉",
            description=f"**Item:** {item}\n**Amount:** {amount}\n**Minimum Bid:** {auction['min_bid']}\n**Auction End Time:** <t:{auction['end_time']}:R>",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Auction ID: {auction['auction_id']}")

        auction_message = await auction_channel.send(embed=embed)
        
        # Save the auction message ID for tracking bids
        async with self.config.auctions() as auctions:
            auctions[auction["auction_id"]]["message_id"] = auction_message.id

        # Update auction status
        async with self.config.auctions() as auctions:
            auctions[auction["auction_id"]]["status"] = "ongoing"

        logging.info(f"Started auction: {auction['auction_id']}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Track bids and donations."""
        if message.author.bot:
            return

        auctions = await self.config.auctions()
        for auction_id, auction in auctions.items():
            if auction.get("message_id") == message.id:
                bid = int(message.content.replace(",", "").replace(" ", "").replace("k", "000").replace("m", "000000").replace("b", "000000000"))
                min_bid = int(auction["min_bid"].replace(",", "").replace("k", "000").replace("m", "000000").replace("b", "000000000"))
                
                if bid >= min_bid:
                    async with self.config.bids() as bids:
                        bids[auction_id] = {"user_id": message.author.id, "amount": bid}
                    
                    await message.channel.send(f"{message.author.mention} has placed a bid of {bid:,}!")
                    logging.info(f"Bid placed: {bid} by {message.author.id} on auction {auction_id}")
                else:
                    await message.channel.send(f"{message.author.mention}, your bid of {bid:,} is below the minimum bid of {min_bid:,}. Please bid higher.")

# Add the cog to the bot
def setup(bot: Red):
    bot.add_cog(Auction(bot))
