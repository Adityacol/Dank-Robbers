import discord
from discord.ui import Modal, TextInput, View, Button
from redbot.core import commands, Config
from redbot.core.bot import Red
import aiohttp
import asyncio
import time
import logging

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
        )
        item_count = TextInput(
            label="How many of those items will you donate?",
            placeholder="e.g., 5",
            required=True,
            max_length=10,
        )
        minimum_bid = TextInput(
            label="What should the minimum bid be?",
            placeholder="e.g., 1,000,000",
            required=False,
        )
        message = TextInput(
            label="What is your message?",
            placeholder="e.g., I love DR!",
            required=False,
            max_length=200,
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

                embed = discord.Embed(
                    title="Your Auction Detail",
                    description=f"**{item_count}x {item_name}**\n\n"
                                f"**Minimum bid:** {self.minimum_bid.value or '1,000,000'}\n"
                                f"**Channelytpe:** NORMAL\n"
                                f"**Total worth:** {item_count * 16375000}\n"  # Assuming the worth of each item is 16,375,000
                                f"**Your fee (0.02%):** {item_count * 16375000 * 0.0002}\n"
                                f"type \"/auction makechanges\" to make changes",
                    color=discord.Color.blue()
                )
                await ticket_channel.send(content=f"{interaction.user.mention}, welcome to Auctions\nTo get started donate {item_count}x {item_name} into the server pool in this channel!",
                                          embed=embed)
                
                await interaction.response.send_message("Auction details submitted! Please donate the items within 30 minutes.", ephemeral=True)

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
            if (
                after.author.id == 270904126974590976 
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
                
                await after.channel.send("The donated item or amount does not match any pending auction.")
        
        except Exception as e:
            logging.error(f"An error occurred in on_message_edit: {e}")
            await after.channel.send("An error occurred while processing the donation. Please contact an admin.")
    
    async def start_auction_announcement(self, guild, auction, user_id, item, amount):
        """Start the auction announcement."""
        auction_channel = guild.get_channel(123456789012345678)  # Replace with your auction announcement channel ID
        embed = discord.Embed(
            title="🎉 New Auction 🎉",
            description=f"**{amount}x {item}** is now available for bidding!\n\n"
                        f"**Minimum bid:** {auction['min_bid']}\n\n"
                        f"To place a bid, use the `/bid` command.",
            color=discord.Color.green()
        )
        await auction_channel.send(embed=embed)
        logging.info(f"Auction {auction['auction_id']} started for {amount}x {item}.")

async def setup(bot: Red):
    cog = Auction(bot)
    await bot.add_cog(cog)
