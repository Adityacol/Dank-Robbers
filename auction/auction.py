import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import aiohttp
import asyncio
import time
import logging
import traceback

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
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await interaction.response.send_message("Error fetching item value from API. Please try again later.", ephemeral=True)
                        return False
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    if not item_data:
                        await interaction.response.send_message("Item not found. Please enter a valid item name.", ephemeral=True)
                        return False
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    if total_value < 1:
                        await interaction.response.send_message("The total donation value must be over 100 million.", ephemeral=True)
                        return False
            except Exception as e:
                await interaction.response.send_message(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                return False
        return True

    def get_next_auction_id(self):
        auctions = self.bot.loop.run_until_complete(self.config.auctions())
        return str(max(map(int, auctions.keys()), default=0) + 1)

    class AuctionModal(discord.ui.Modal):
        def __init__(self, cog):
            self.cog = cog
            super().__init__(title="Request An Auction")

        item_name = discord.ui.TextInput(
            label="What are you going to donate?",
            placeholder="e.g., Blob",
            required=True,
            min_length=1,
            max_length=100,
            style=discord.TextStyle.short
        )
        item_count = discord.ui.TextInput(
            label="How many of those items will you donate?",
            placeholder="e.g., 5",
            required=True,
            max_length=10
        )
        minimum_bid = discord.ui.TextInput(
            label="What should the minimum bid be?",
            placeholder="e.g., 1,000,000",
            required=False,
            style=discord.TextStyle.short
        )
        message = discord.ui.TextInput(
            label="What is your message?",
            placeholder="e.g., I love DR!",
            required=False,
            max_length=200,
            style=discord.TextStyle.short
        )

        async def on_submit(self, interaction: discord.Interaction):
            logging.info("Modal submitted.")
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

    class AuctionView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green)
        async def request_auction_button(self, button: discord.ui.Button, interaction: discord.Interaction):
            logging.info("Request Auction button clicked.")
            modal = self.cog.AuctionModal(self.cog)
            try:
                await interaction.response.send_modal(modal)
                logging.info("Modal sent successfully.")
            except Exception as e:
                logging.error(f"Failed to send modal: {e}")
                await interaction.response.send_message("There was an error while sending the modal. Please try again later.", ephemeral=True)

    @commands.command()
    async def requestauction(self, ctx: commands.Context):
        """Request a new auction."""
        view = self.AuctionView(self)
        embed = discord.Embed(
            title="Request an Auction",
            description="Click the button below to request an auction.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await ctx.send(embed=embed, view=view)
        logging.info("Auction request initiated.")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
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
                        await self.config.auctions.set_raw(auction_id, value=auction)
                        user = await self.bot.fetch_user(auction["user_id"])
                        await after.channel.send(f"item_dank = {item_dank}, amount_dank= {amount_dank}")
                        await user.send("Thank you for your donation! Your auction will start shortly.")
                        ticket_channel = after.guild.get_channel(auction["ticket_channel_id"])
                        if ticket_channel:
                            await ticket_channel.delete()
                        await self.start_auction_announcement(after.guild, auction, auction["user_id"], auction["item"], auction["amount"])
                        return
                await after.channel.send("The donated item or amount does not match the saved auction details.")
                logging.info("Mismatch in donated item or amount.")
        except Exception as e:
            print(f"An error occurred in on_message_edit: {e}")
            traceback.print_exc()

    async def start_auction_announcement(self, guild, auction, user_id, item, amount):
        auction_channel = guild.get_channel(1250501101615190066)  # Replace with your auction channel ID
        if not auction_channel:
            auction_channel = await guild.create_text_channel("auction-channel")
        user = await self.bot.fetch_user(user_id)
        embed = discord.Embed(
            title="Auction Started!",
            description=f"Item: {item}\nAmount: {amount}\nStarting Bid: {auction['min_bid']}\nDonated by {user.mention}\nAuction ID: {auction['auction_id']}\nMessage: {auction['message']}",
            color=discord.Color.blue()
        )
        await auction_channel.send(embed=embed)
        await asyncio.create_task(self.run_auction(auction))

    async def run_auction(self, auction):
        await asyncio.sleep(30 * 60)  # 30 minutes auction duration
        current_time = int(time.time())
        if current_time >= auction["end_time"]:
            await self.end_auction(auction)

    async def end_auction(self, auction):
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
            highest_bidder = self.bot.get_user(highest_bid["user_id"])
            if highest_bidder:
                await auction_channel.send(f"Congratulations {highest_bidder.mention}! You won the auction for {auction['item']} with a bid of {highest_bid['amount']}!")
            else:
                await auction_channel.send(f"No bids for {auction['item']} were placed.")
        else:
            await auction_channel.send(f"No bids were placed for the auction of {auction['item']}.")

async def setup(bot):
    await bot.add_cog(Auction(bot))
