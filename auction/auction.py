import discord
from redbot.core import commands, Config, checks
import aiohttp
import asyncio
import logging
import time
import traceback

default_global = {
    "auctions": {},
    "bids": {}
}

class AuctionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_global(**default_global)
        self.dank_memer_id = 270904126974590976
        logging.basicConfig(filename="test.log", filemode="w", level=logging.INFO, format="%(asctime)s : %(levelname)s - %(message)s")

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info(f'Logged in as {self.bot.user}')

    async def api_check(self, ctx, item_count, item_name):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await ctx.send("Error fetching item value from API. Please try again later.")
                        return False
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    if not item_data:
                        await ctx.send("Item not found. Please enter a valid item name.")
                        return False
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    if total_value < 100000000:
                        await ctx.send("The total donation value must be over 100 million.")
                        return False
                return True
            except Exception as e:
                await ctx.send(f"An error occurred while fetching item value: {str(e)}")
                return False

    async def update_json(self, identifier_key, update_data):
        try:
            file_data = await self.config.global().auctions()
            for item in file_data.items():
                if int(item[0]) == int(identifier_key):
                    item[1].update(update_data)
                    break
            await self.config.global().auctions.set(file_data)
        except Exception as e:
            logging.error(f"Error updating JSON: {e}")

    async def append_to_json(self, key, new_entry):
        try:
            data = await self.config.global().auctions()
            data[key] = new_entry
            await self.config.global().auctions.set(data)
        except Exception as e:
            logging.error(f"Error appending to JSON: {e}")

    async def load_json(self, file):
        try:
            return await self.config.global().get_raw(file, default={})
        except Exception as e:
            logging.error(f"Error loading JSON: {e}")
            return {}

    async def save_json(self, file, data):
        try:
            await self.config.global().set_raw(file, value=data)
        except Exception as e:
            logging.error(f"Error saving JSON: {e}")

    class AuctionModal(discord.ui.Modal):
        def __init__(self, cog):
            self.cog = cog
            super().__init__(title="Request An Auction")

            self.item_name = discord.ui.TextInput(
                label="What are you going to donate?",
                placeholder="e.g., Blob",
                required=True,
                min_length=1,
                max_length=100,
                style=discord.TextStyle.short
            )
            self.item_count = discord.ui.TextInput(
                label="How many of those items will you donate?",
                placeholder="e.g., 5",
                required=True,
                max_length=10
            )
            self.minimum_bid = discord.ui.TextInput(
                label="What should the minimum bid be?",
                placeholder="e.g., 1,000,000",
                required=False,
                style=discord.TextStyle.short
            )
            self.message = discord.ui.TextInput(
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

            if not await self.cog.api_check(interaction, item_count, item_name):
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

            auction_id = await self.cog.generate_auction_id()

            auction_data = {
                auction_id: {
                    "auction_id": auction_id,
                    "user_id": interaction.user.id,
                    "item": item_name,
                    "amount": item_count,
                    "min_bid": self.minimum_bid.value or "1,000,000",
                    "message": self.message.value,
                    "status": "pending",
                    "ticket_channel_id": ticket_channel.id
                }
            }

            await self.cog.append_to_json(str(auction_id), auction_data[auction_id])

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        try:
            if after.author.id == self.dank_memer_id and hasattr(after.interaction, "name") and after.interaction.name == "serverevents donate" and before.embeds != after.embeds:
                auctions = await self.load_json('auctions')
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
                        update_data = {
                            "status": "active",
                            "end_time": auction["end_time"]
                        }
                        await self.update_json(auction["auction_id"], update_data)
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
            logging.error(f"An error occurred in on_message_edit: {e}")

    async def generate_auction_id(self) -> str:
        auctions = await self.load_json('auctions')
        max_id = max((int(auction_id) for auction_id in auctions.keys()), default=0)
        return str(max_id + 1)

    async def run_auction(self, auction):
        auction_channel = self.bot.get_channel(1250501101615190066)
        await asyncio.sleep(30 * 60)  # 30 minutes auction duration
        current_time = int(time.time())
        if current_time >= auction["end_time"]:
            await self.end_auction(auction_channel, auction)

    async def start_auction_announcement(self, guild, auction, user_id, item, amount):
        auction_channel = guild.get_channel(1250501101615190066)  # Replace with the auction channel ID
        if not auction_channel:
            auction_channel = await guild.create_text_channel("auction-channel-name")
        user = await self.bot.fetch_user(user_id)
        embed = discord.Embed(
            title="Auction Started!",
            description=f"Item: {item}\nAmount: {amount}\nStarting Bid: {amount}\nDonated by {user.mention}\nAuction ID: {auction['auction_id']}\nDonor Message: {auction['message']}",
            color=discord.Color.blue()
        )
        await auction_channel.send(embed=embed)
        await asyncio.create_task(self.run_auction(auction))
        await auction_channel.set_permissions(auction_channel.guild.default_role, read_messages=True, send_messages=True)

    async def end_auction(self, auction_channel, auction):
        auction["status"] = "ended"
        await self.update_json(auction["auction_id"], {"status": "ended"})

        bids = await self.load_json('bids')
        highest_bid = max(
            bids.get(auction["auction_id"], {}).values(),
            key=lambda x: x.get("amount", 0),
            default=None
        )

        if highest_bid:
            highest_bidder_id = highest_bid["user_id"]
            highest_bid_amount = highest_bid["amount"]

            if highest_bidder_id is not None:
                await auction_channel.send(f"Auction ended! The highest bid was {highest_bid_amount} by <@{highest_bidder_id}>. They have won the auction with the ID: {auction['auction_id']}!")
                winner = await self.bot.fetch_user(highest_bidder_id)
                await winner.send(f"Congratulations! You won the auction with a bid of {highest_bid_amount}. Please donate the amount within 30 minutes.")
            else:
                await auction_channel.send("Auction ended with no valid bids.")
        else:
            await auction_channel.send("Auction ended with no bids.")
        await self.clear_auction(auction["auction_id"])

    async def clear_auction(self, auction_id):
        auctions = await self.load_json('auctions')
        if auction_id in auctions:
            del auctions[auction_id]
        await self.save_json('auctions', auctions)

        bids = await self.load_json('bids')
        if auction_id in bids:
            del bids[auction_id]
        await self.save_json('bids', bids)

    @commands.command()
    @checks.admin_or_permissions(manage_channels=True)
    async def auction(self, ctx):
        await ctx.send_modal(self.AuctionModal(self))

    def edit_bid(self, bids_data, auction_id, bidder_id, bid_amount):
        auction_bids = bids_data.setdefault(auction_id, {})

        if bidder_id in auction_bids:
            auction_bids[bidder_id]["amount"] = bid_amount
        else:
            auction_bids[bidder_id] = {
                "user_id": bidder_id,
                "amount": bid_amount
            }

        return bids_data

    @commands.command()
    @checks.admin_or_permissions(manage_channels=True)
    async def bid(self, ctx, bid_amount: int, auction_id: int):
        if bid_amount <= 0:
            await ctx.send("Bid amount must be greater than 0.")
            return

        bidder_id = ctx.author.id

        auctions = await self.load_json('auctions')
        active_auctions = {auction_id: auction_data for auction_id, auction_data in auctions.items() if auction_data['status'] == 'active'}
        if not active_auctions:
            return await ctx.send("No active auctions found.")

        auction_id = str(auction_id) or list(active_auctions.keys())[0]
        if auction_id not in active_auctions:
            return await ctx.send("Invalid auction ID.")
        else:
            await ctx.send(f"A bid of {bid_amount} has successfully been placed.")

        bids_data = self.edit_bid(await self.load_json('bids'), auction_id, bidder_id, bid_amount)
        await self.save_json('bids', bids_data)
