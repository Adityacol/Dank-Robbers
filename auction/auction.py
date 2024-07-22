import discord
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

    async def api_check(self, user: discord.User, item_count: int, item_name: str) -> bool:
        """Check if the donated item meets the value requirements."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        await user.send("Error fetching item value from API. Please try again later.")
                        logging.error(f"API response status: {response.status}")
                        return False
                    
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    
                    if not item_data:
                        await user.send("Item not found. Please enter a valid item name.")
                        return False
                    
                    item_value = item_data.get("value", 0)
                    total_value = item_value * item_count
                    
                    if total_value < 100000000:  # Changed to 100 million
                        await user.send("The total donation value must be over 100 million.")
                        return False

            except Exception as e:
                await user.send(f"An error occurred while fetching item value: {str(e)}")
                logging.error(f"Exception in API check: {e}")
                return False
        return True

    def get_next_auction_id(self):
        """Generate the next auction ID."""
        auctions = self.bot.loop.run_until_complete(self.config.auctions())
        return str(max(map(int, auctions.keys()), default=0) + 1)

    async def open_auction_modal(self, ctx: commands.Context):
        """Open the auction request modal."""
        item_name = await self.prompt_for_input(ctx, "What are you going to donate?")
        item_count = await self.prompt_for_input(ctx, "How many of those items will you donate?")
        minimum_bid = await self.prompt_for_input(ctx, "What should the minimum bid be?", optional=True)
        message = await self.prompt_for_input(ctx, "What is your message?", optional=True)

        if not item_count.isdigit():
            await ctx.send("Item count must be a number.")
            return

        item_count = int(item_count)
        valid = await self.api_check(ctx.author, item_count, item_name)
        
        if not valid:
            return

        guild = ctx.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            self.bot.user: discord.PermissionOverwrite(read_messages=True),
        }
        
        ticket_channel = await guild.create_text_channel(f"ticket-{ctx.author.name}", overwrites=overwrites)
        await ticket_channel.send(f"{ctx.author.mention}, please donate {item_count} of {item_name} as you have mentioned in the modal or you will get blacklisted.")
        await ctx.send("Auction details submitted! Please donate the items within 30 minutes.")

        auction_id = self.get_next_auction_id()

        auction_data = {
            "auction_id": auction_id,
            "user_id": ctx.author.id,
            "item": item_name,
            "amount": item_count,
            "min_bid": minimum_bid or "1,000,000",
            "message": message or "",
            "status": "pending",
            "ticket_channel_id": ticket_channel.id
        }

        async with self.config.auctions() as auctions:
            auctions[auction_id] = auction_data

    async def prompt_for_input(self, ctx: commands.Context, question: str, optional: bool = False) -> str:
        """Prompt the user for input."""
        await ctx.send(question)
        def check(msg):
            return msg.author == ctx.author and msg.channel == ctx.channel

        try:
            msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            return msg.content
        except asyncio.TimeoutError:
            await ctx.send('You took too long to respond. Please try again.')
            return None

    @commands.command()
    async def requestauction(self, ctx: commands.Context):
        """Request a new auction."""
        embed = discord.Embed(
            title="🎉 Request an Auction 🎉",
            description="Reply with your donation details to request an auction.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="How it works", value="1. Reply with your donation details.\n2. Await further instructions in your private channel.", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        try:
            await ctx.send(embed=embed)
            logging.info("Auction request initiated.")
            await self.open_auction_modal(ctx)
        except Exception as e:
            logging.error(f"An error occurred while sending the auction request message: {e}")
            await ctx.send(f"An error occurred while initiating the auction request: {str(e)}")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Handle message edits related to auction donations."""
        try:
            if after.author.id == 270904126974590976 and hasattr(after, "embeds") and after.embeds:
                embed = after.embeds[0]
                title = embed.title
                desc = embed.description

                if title != "Action Confirmed":
                    return

                parts = desc.split("**")
                item_dank = str(parts[1].split(">")[1])
                amount_dank = int(parts[1].split("<")[0])
                
                auctions = await self.config.auctions()

                for auction_id, auction in auctions.items():
                    if auction["item"].strip().lower() == item_dank.strip().lower() and int(auction["amount"]) == amount_dank and auction["status"] == "pending" and after.channel.id == auction["ticket_channel_id"]:
                        auction["status"] = "active"
                        auction["end_time"] = int(time.time()) + 30 * 60
                        async with self.config.auctions() as auctions:
                            auctions[auction_id] = auction
                        
                        user = await self.bot.fetch_user(auction["user_id"])
                        await after.channel.send(f"Item: {item_dank}, Amount: {amount_dank}")
                        
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
        auction_channel = self.bot.get_channel(1250501101615190066)  # Replace with your auction channel ID
        embed = discord.Embed(
            title="🎉 New Auction! 🎉",
            description=f"**Item:** {item}\n**Amount:** {amount}\n**Starting Bid:** {auction['min_bid']}\n**Auction ID:** {auction['auction_id']}\n**Message:** {auction['message']}",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="Good luck to all bidders!")
        try:
            await auction_channel.send(embed=embed)
            await self.run_auction(auction)
        except Exception as e:
            logging.error(f"An error occurred while sending the auction announcement: {e}")

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
            bids.get(str(auction["auction_id"]), {}).values(),
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
            try:
                await auction_channel.send(embed=embed)
            except Exception as e:
                logging.error(f"An error occurred while sending the auction end announcement: {e}")
        else:
            embed = discord.Embed(
                title="🛑 Auction Ended! 🛑",
                description=f"**Item:** {auction['item']}\n**Amount:** {auction['amount']}\n**No valid bids received.**\n**Auction ID:** {auction['auction_id']}\n**Donated by:** {await self.bot.fetch_user(auction['user_id']).mention}",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            embed.set_footer(text="Thank you for participating!")
            try:
                await auction_channel.send(embed=embed)
            except Exception as e:
                logging.error(f"An error occurred while sending the auction end announcement: {e}")

def setup(bot: Red):
    bot.add_cog(Auction(bot))
