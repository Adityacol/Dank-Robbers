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
        self.auction_data_file = data_manager.cog_data_path(self) / "auction.json"
        self.bid_data_file = data_manager.cog_data_path(self) / "bid.json"

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
                        return
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
            file_data[identifier_key].update(update_data)
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
                item_dank = parts[1].split(">")[1].strip()
                amount_dank = int(parts[2].split("<")[0].strip())
                
                for auction_id, auction in auctions.items():
                    if (auction["item"].strip().lower() == item_dank.lower() and
                        int(auction["amount"]) == amount_dank and
                        auction["status"] == "pending" and
                        after.channel.id == auction["ticket_channel_id"]):
                        
                        auction["status"] = "active"
                        auction["end_time"] = int(time.time()) + 30 * 60  
                        update_data = {
                            "status": "active",
                            "end_time": auction["end_time"]
                        }
                        self.update_json(self.auction_data_file, auction["auction_id"], update_data)
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
            print(f"An error occurred in on_message_edit: {e}")
            traceback.print_exc()

    async def generate_auction_id(self) -> str:
        auctions = self.load_json(self.auction_data_file)
        max_id = max((int(auction_id) for auction_id in auctions.keys()), default=0)
        return str(max_id + 1)

    async def run_auction(self, auction):
        auction_channel = self.bot.get_channel(1250501101615190066)
        await asyncio.sleep(30 * 60)  
        current_time = int(time.time())
        if current_time >= auction["end_time"]:
            await self.end_auction(auction_channel, auction)

    async def start_auction_announcement(self, guild, auction, user_id, item, amount):
        auction_channel = guild.get_channel(1250501101615190066)
        if not auction_channel:
            auction_channel = await guild.create_text_channel("auction-channel-name")
        user = await self.bot.fetch_user(user_id)
        embed = discord.Embed(
            title="🎉 Auction Started! 🎉",
            description=f"**Item**: {item}\n**Amount**: {amount}\n**Starting Bid**: {auction['min_bid']}\n**Donated by**: {user.mention}\n\nPlace your bids by typing them in this channel!",
            color=discord.Color.blue()
        )
        await auction_channel.send(embed=embed)
        asyncio.create_task(self.run_auction(auction))

    async def end_auction(self, auction_channel, auction):
        try:
            bids = self.load_json(self.bid_data_file).get(auction["auction_id"], [])
            if not bids:
                await auction_channel.send("No bids were placed for this auction. The auction has ended with no winner.")
                return
            highest_bid = max(bids, key=lambda bid: int(bid["bid_amount"]))
            winner_id = highest_bid["user_id"]
            winner = await self.bot.fetch_user(winner_id)
            embed = discord.Embed(
                title="🎉 Auction Ended! 🎉",
                description=f"The auction for {auction['amount']} {auction['item']} has ended.\n\n**Winner**: {winner.mention}\n**Winning Bid**: {highest_bid['bid_amount']}",
                color=discord.Color.gold()
            )
            await auction_channel.send(embed=embed)
        except Exception as e:
            await auction_channel.send(f"An error occurred while ending the auction: {str(e)}")
            traceback.print_exc()

    @commands.command()
    async def submit_auction(self, ctx):
        try:
            modal = self.AuctionModal(self)
            await ctx.send_modal(modal)
        except Exception as e:
            print(f"An error occurred in submit_auction: {e}")
            traceback.print_exc()

    @commands.command()
    async def bid(self, ctx, auction_id: str, amount: str):
        try:
            if not amount.isdigit() or int(amount) <= 0:
                await ctx.send("Please enter a valid bid amount.")
                return
            bids = self.load_json(self.bid_data_file)
            auction_bids = bids.get(auction_id, [])
            auction_bids.append({
                "user_id": ctx.author.id,
                "bid_amount": amount
            })
            bids[auction_id] = auction_bids
            self.save_json(self.bid_data_file, bids)
            await ctx.send(f"Bid of {amount} placed by {ctx.author.mention} for auction {auction_id}!")
        except Exception as e:
            await ctx.send(f"An error occurred while placing the bid: {str(e)}")
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(Auction(bot))
