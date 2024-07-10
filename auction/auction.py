import discord
from redbot.core import commands, Config, data_manager
import json
import aiohttp
import asyncio
import time
import logging
import traceback

logging.basicConfig(filename="auction.log", filemode="w", level=logging.INFO, format="%(asctime)s : %(levelname)s - %(message)s")

DANK_MEMER_ID = 270904126974590976  

class Auction(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_global(auctions={}, bids={})
        self.auction_data_file = data_manager.cog_data_path(self) / "auctions.json"
        self.bid_data_file = data_manager.cog_data_path(self) / "bids.json"

    async def api_check(self, interaction, item_count, item_name) -> None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await interaction.response.send_message("Error fetching item value from API. Please try again later.", ephemeral=True)
                        return
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    if not item_data:
                        await interaction.response.send_message("Item not found. Please enter a valid item name.", ephemeral=True)
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    if total_value < 100000000:
                        await interaction.response.send_message("The total donation value must be over 100 million.", ephemeral=True)
                        return
            except Exception as e:
                await interaction.response.send_message(f"An error occurred while fetching item value: {str(e)}", ephemeral=True)
                return

    def update_json(self, file_name, identifier_key, update_data):
        try:
            with open(file_name, 'r') as file:
                file_data = json.load(file)
            
            print(f"Loaded file data: {file_data}") 

            for item in file_data.items():
                if int(item[0]) == int(identifier_key):
                    print(f"Found matching item: {item}")
                    item[1].update(update_data)
                    break
            
            print(f"Updated file data: {file_data}")

            with open(file_name, 'w') as file:
                json.dump(file_data, file, indent=4)
                
        except FileNotFoundError:
            print(f"File {file_name} not found.")
        except json.JSONDecodeError as je:
            print(f"JSON decoding error in {file_name}: {je}")
        except Exception as e:
            print(f"Error updating JSON in {file_name}: {e}")
            traceback.print_exc()

    def append_to_json(self, file_path, key, new_entry):
        try:
            data = self.load_json(file_path)
            data[key] = new_entry
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error appending to JSON file {file_path}: {e}")

    def load_json(self, file_path):
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON in {file_path}: {e}")
            data = {}
        return data

    def save_json(self, file_path, data):
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4)

    class AuctionModal(discord.ui.Modal, title="Request An Auction"):
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

        def __init__(self, cog):
            super().__init__()
            self.cog = cog

        async def on_submit(self, interaction: discord.Interaction):
            logging.info("Modal submitted.")

            item_name = self.item_name.value
            item_count = self.item_count.value

            if not item_count.isdigit():
                await interaction.response.send_message("Item count must be a number.", ephemeral=True)
                return

            item_count = int(item_count)

            await self.cog.api_check(interaction, item_count, item_name)

            guild = interaction.guild
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                interaction.client.user: discord.PermissionOverwrite(read_messages=True),
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

            self.cog.append_to_json(self.cog.auction_data_file, str(auction_id), auction_data[auction_id])

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        try:
            if after.author.id == DANK_MEMER_ID and hasattr(after.interaction, "name") and after.interaction.name == "serverevents donate" and before.embeds != after.embeds:
                auctions = self.load_json(self.auction_data_file)
                title = after.embeds[0].title
                desc = after.embeds[0].description
                if title != "Action Confirmed":
                    return
                parts = desc.split("**")
                item_dank = str(parts[1].split(">")[1])
                amount_dank = int(parts[1].split("<")[0])
                for auction_id, auction in auctions.items():
                   if auction["item"].strip().lower() == item_dank.strip().lower() and int(auction["amount"]) == amount_dank and auction["status"]== "pending" and after.channel.id == auction["ticket_channel_id"]:
                        auction["status"] = "active"
                        auction["end_time"] = int(time.time()) + 30 * 60  # Change to 30 minutes
                        update_data = {
                            "status": "active",
                            "end_time": auction["end_time"]
                        }
                        print(f"Updating auction {auction['auction_id']} with {update_data}")  
                        self.update_json(self.auction_data_file, auction["auction_id"], update_data)
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

    async def generate_auction_id(self) -> str:
        auctions = self.load_json(self.auction_data_file)
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
            auction_channel = await guild.create_text_channel("auction-channel-name")  # this line does nothing at all if u have a channel
        user = await self.bot.fetch_user(user_id)
        embed = discord.Embed(
            title="Auction Started!",
            description=f"Item: {item}\nAmount: {amount}\nStarting Bid: {amount}\n donated by {user.mention}\n auction id: {auction['auction_id']}\n donor message: {auction['message']}",
            color=discord.Color.blue()
        )
        await auction_channel.send(embed=embed)
        await asyncio.create_task(self.run_auction(auction))
        await auction_channel.set_permissions(auction_channel.guild.default_role, read_messages=True, send_messages=True)

    async def end_auction(self, auction_channel, auction):
        auction["status"] = "ended"
        self.update_json(self.auction_data_file, auction["auction_id"], {"status": "ended"})
        
        bids = self.load_json(self.bid_data_file)
        highest_bid = max(
            bids.get(auction["auction_id"], {}).values(), 
            key=lambda x: x.get("amount", 0), 
            default=None
        )
        
        if highest_bid:
            highest_bidder_id = highest_bid["user_id"]
            highest_bid_amount = highest_bid["amount"]
            
            if highest_bidder_id is not None:
                await auction_channel.send(f"Auction ended! The highest bid was {highest_bid_amount} by <@{highest_bidder_id}>. They have won the auction with the id:{auction['auction_id']}!")
                winner = await self.bot.fetch_user(highest_bidder_id)
                await winner.send(f"Congratulations! You won the auction with a bid of {highest_bid_amount}. Please donate the amount within 30 minutes.")
            else:
                await auction_channel.send("Auction ended with no valid bids.")
        else:
            await auction_channel.send("Auction ended with no bids.")
        self.clear_auction(auction["auction_id"])

    def clear_auction(self, auction_id):
        auctions = self.load_json(self.auction_data_file)
        if auction_id in auctions:
            del auctions[auction_id]
        self.save_json(self.auction_data_file, auctions)

        bids = self.load_json(self.bid_data_file)
        if auction_id in bids:
            del bids[auction_id]
        self.save_json(self.bid_data_file, bids)

    @commands.command()
    async def auction(self, ctx):
        logging.info("Received /auction command.")
        view = discord.ui.View()
        view.add_item(AuctionButton(self))
        await ctx.send("Click the button below to start an auction:", view=view)

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
    async def bid(self, ctx, bid_amount: int, auction_id: int):
        if bid_amount <= 0:
            await ctx.send("Bid amount must be greater than 0.", ephemeral=True)
            return

        bidder_id = ctx.author.id

        auctions = self.load_json(self.auction_data_file)
        active_auctions = {auction_id: auction_data for auction_id, auction_data in auctions.items() if auction_data['status'] == 'active'}
        if not active_auctions:
            return await ctx.send("No active auctions found.", ephemeral=True)

        auction_id = str(auction_id) or list(active_auctions.keys())[0]
        if auction_id not in active_auctions:
            return await ctx.send("Invalid auction ID.", ephemeral=True)
        else:
            await ctx.send(f"A bid of {bid_amount} has successfully been placed.")

        bids_data = self.edit_bid(self.load_json(self.bid_data_file), auction_id, bidder_id, bid_amount)
        self.save_json(self.bid_data_file, bids_data)


class AuctionButton(discord.ui.Button):
    def __init__(self, cog):
        super().__init__(label="Start Auction", style=discord.ButtonStyle.green)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(self.cog.AuctionModal(cog=self.cog))
