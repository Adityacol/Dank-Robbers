import discord
from discord.ext import tasks, commands
from redbot.core import Config, checks, bank, commands
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from redbot.core.bot import Red
import asyncio
import logging
from typing import Optional, Dict, Any, List, Union, Tuple
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import io
import csv
import json
import random
import seaborn as sns
import os
import math
import re
from collections import defaultdict
import aiohttp

log = logging.getLogger("red.economy.AdvancedAuctionSystem")

class AuctionAnalytics:
    def __init__(self):
        self.total_auctions = 0
        self.total_value = 0
        self.item_popularity = defaultdict(int)
        self.user_participation = defaultdict(int)
        self.category_performance = defaultdict(lambda: {"count": 0, "value": 0})

    def update(self, auction: Dict[str, Any]):
        self.total_auctions += 1
        self.total_value += auction['current_bid']
        for item in auction['items']:
            self.item_popularity[item['name']] += item['amount']
        self.user_participation[auction['user_id']] += 1
        self.user_participation[auction['current_bidder']] += 1
        self.category_performance[auction['category']]['count'] += 1
        self.category_performance[auction['category']]['value'] += auction['current_bid']

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_auctions": self.total_auctions,
            "total_value": self.total_value,
            "top_items": dict(sorted(self.item_popularity.items(), key=lambda x: x[1], reverse=True)[:5]),
            "top_users": dict(sorted(self.user_participation.items(), key=lambda x: x[1], reverse=True)[:5]),
            "category_performance": self.category_performance
        }

class AuctionVisualization:
    @staticmethod
    async def create_bid_history_chart(auction: Dict[str, Any]) -> discord.File:
        plt.figure(figsize=(10, 6))
        bids = [(datetime.fromtimestamp(bid['timestamp']), bid['amount']) for bid in auction['bid_history']]
        times, amounts = zip(*bids)
        plt.plot(times, amounts, marker='o')
        plt.title(f"Bid History for Auction #{auction['auction_id']}")
        plt.xlabel("Time")
        plt.ylabel("Bid Amount")
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename=f"auction_{auction['auction_id']}_history.png")

class AdvancedAuctionSystem(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=95932766180)
        default_guild = {
            "auctions": {},
            "auction_queue": [],
            "scheduled_auctions": {},
            "auction_category": None,
            "log_channel": None,
            "queue_channel": None,
            "auction_role": None,
            "blacklist_role": None,
            "auction_ping_role": None,
            "massive_auction_ping_role": None,
            "user_stats": {},
            "categories": ["Common", "Uncommon", "Rare", "Epic", "Legendary"],
            "leaderboard": {},
            "auction_cooldown": 86400,
            "banned_users": [],
            "auction_moderators": [],
            "minimum_bid_increment": 1000,
            "auction_extension_time": 300,
            "auction_duration": 6 * 3600,
            "auction_history": [],
            "auction_templates": {},
            "global_auction_settings": {
                "max_auction_duration": 7 * 24 * 3600,
                "min_auction_duration": 1 * 3600,
                "max_auctions_per_user": 3,
                "bidding_cooldown": 30,
                "snipe_protection_time": 300,
                "reserve_price_allowed": True,
                "proxy_bidding_allowed": True,
                "multi_item_auctions_allowed": True,
                "auction_bundle_allowed": True,
                "auction_insurance_rate": 0.05,
            },
            "bid_increment_tiers": {
                "0": 1000,
                "10000": 5000,
                "100000": 10000,
                "1000000": 50000,
                "10000000": 100000,
            },
            "reputation_system": {
                "starting_score": 100,
                "max_score": 1000,
                "min_score": 0,
                "auction_completion_bonus": 5,
                "auction_cancellation_penalty": 10,
                "successful_sale_bonus": 2,
                "successful_purchase_bonus": 1,
            },
            "moderator_role": None,
            "donation_tracking": {},
        }
        default_member = {
            "auction_reminders": [],
            "notification_settings": {
                "outbid": True,
                "auction_start": True,
                "auction_end": True,
                "won_auction": True,
                "price_threshold": True,
                "auction_extension": True,
            },
            "last_bid_time": {},
            "auction_history": [],
            "reputation_score": 100,
            "subscribed_categories": [],
            "saved_searches": [],
            "proxy_bids": {},
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.auction_task = None
        self.analytics = AuctionAnalytics()
        self.visualization = AuctionVisualization()
        self.api_cache = {}
        self.api_cache_time = {}
        self.queue_lock = asyncio.Lock()

    async def initialize(self):
        self.auction_task = self.bot.loop.create_task(self.auction_loop())
        await self.migrate_data()
        await self.load_analytics()

    async def cog_unload(self):
        if self.auction_task:
            self.auction_task.cancel()

    async def migrate_data(self):
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction_id, auction in auctions.items():
                    if 'channel_id' not in auction:
                        auction['channel_id'] = None
                    if 'buy_out_price' not in auction:
                        auction['buy_out_price'] = None
                    if 'reserve_price' not in auction:
                        auction['reserve_price'] = None
                    if 'proxy_bids' not in auction:
                        auction['proxy_bids'] = {}
                    if 'items' not in auction:
                        auction['items'] = [{"name": auction['item'], "amount": auction['amount']}]
                        del auction['item']
                        del auction['amount']
                    if 'donations' not in auction:
                        auction['donations'] = []

    async def load_analytics(self):
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auction_history() as history:
                for auction in history:
                    self.analytics.update(auction)

    @tasks.loop(minutes=1)
    async def auction_loop(self):
        try:
            await self.process_auction_queue()
            await self.check_auction_end()
            await self.process_scheduled_auctions()
            await self.update_auction_analytics()
        except Exception as e:
            log.error(f"Error in auction loop: {e}", exc_info=True)

    async def process_auction_queue(self):
        async with self.queue_lock:
            for guild in self.bot.guilds:
                auction_category_id = await self.config.guild(guild).auction_category()
                if not auction_category_id:
                    continue
                
                auction_category = guild.get_channel(auction_category_id)
                if not auction_category:
                    continue

                active_auctions = len([channel for channel in auction_category.channels if channel.name.startswith("auction-")])
                max_concurrent_auctions = await self.config.guild(guild).global_auction_settings.max_concurrent_auctions()
                
                if active_auctions < max_concurrent_auctions:
                    queue = await self.config.guild(guild).auction_queue()
                    if queue:
                        next_auction = queue.pop(0)
                        await self.start_auction(guild, next_auction)
                        await self.config.guild(guild).auction_queue.set(queue)

    async def check_auction_end(self):
        for guild in self.bot.guilds:
            auction_category_id = await self.config.guild(guild).auction_category()
            if not auction_category_id:
                continue
            
            auction_category = guild.get_channel(auction_category_id)
            if not auction_category:
                continue

            for channel in auction_category.channels:
                if channel.name.startswith("auction-"):
                    auction_id = channel.name.split("-")[1]
                    auction = (await self.config.guild(guild).auctions()).get(auction_id)
                    if auction and auction['status'] == 'active' and auction['end_time'] <= datetime.utcnow().timestamp():
                        await self.end_auction(guild, auction_id)

    async def process_scheduled_auctions(self):
        for guild in self.bot.guilds:
            async with self.config.guild(guild).scheduled_auctions() as scheduled:
                current_time = datetime.utcnow().timestamp()
                for auction_id, auction_time in list(scheduled.items()):
                    if auction_time <= current_time:
                        auction_data = (await self.config.guild(guild).auctions()).get(auction_id)
                        if auction_data:
                            await self.queue_auction(guild, auction_data)
                            del scheduled[auction_id]

    async def update_auction_analytics(self):
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auctions() as auctions:
                for auction in auctions.values():
                    if auction['status'] == 'completed':
                        self.analytics.update(auction)

    async def start_auction(self, guild: discord.Guild, auction: Dict[str, Any]):
        auction['start_time'] = datetime.utcnow().timestamp()
        auction['end_time'] = auction['start_time'] + await self.config.guild(guild).auction_duration()
        auction['status'] = 'active'
        
        auction_category_id = await self.config.guild(guild).auction_category()
        auction_category = guild.get_channel(auction_category_id)
        
        if auction_category:
            channel = await auction_category.create_text_channel(f"auction-{auction['auction_id']}")
            auction['channel_id'] = channel.id
            
            embed = await self.create_auction_embed(auction)
            message = await channel.send("New auction started!", embed=embed, view=self.AuctionControls(self, auction))
            await message.pin()
            
            # Create and send bid history chart
            chart = await self.visualization.create_bid_history_chart(auction)
            await channel.send("Current bid history:", file=chart)
            
            # Notify subscribers
            await self.notify_subscribers(guild, auction, channel)
        
        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction['auction_id']] = auction

    async def end_auction(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

            channel = guild.get_channel(auction['channel_id'])
            
            if channel:
                if auction['current_bidder']:
                    winner = guild.get_member(auction['current_bidder'])
                    await channel.send(f"Auction ended! The winner is {winner.mention} with a bid of {auction['current_bid']:,}.")
                    await self.handle_auction_completion(guild, auction, winner, auction['current_bid'])
                else:
                    await channel.send("Auction ended with no bids.")
                
                # Log channel content
                log_channel_id = await self.config.guild(guild).log_channel()
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    messages = [message async for message in channel.history(limit=None, oldest_first=True)]
                    content = "\n".join([f"{m.created_at}: {m.author}: {m.content}" for m in messages])
                    await log_channel.send(f"Auction #{auction_id} log:", file=discord.File(io.StringIO(content), filename=f"auction_{auction_id}_log.txt"))
                
                # Delete the channel
                await channel.delete()

            auction['status'] = 'completed'
            auctions[auction_id] = auction

        await self.update_auction_history(guild, auction)
        await self.process_auction_queue()

    async def handle_auction_completion(self, guild: discord.Guild, auction: Dict[str, Any], winner: discord.Member, winning_bid: int):
        log_channel_id = await self.config.guild(guild).log_channel()
        log_channel = guild.get_channel(log_channel_id)

        if log_channel:
            await log_channel.send(f"Auction completed. Winner: {winner.mention}, Amount: {winning_bid:,}")
            for item in auction['items']:
                await log_channel.send(f"/serverevents payout user:{winner.id} quantity:{item['amount']} item:{item['name']}")
            await log_channel.send(f"/serverevents payout user:{auction['user_id']} quantity:{winning_bid}")

        await self.update_user_stats(guild, winner.id, winning_bid, 'won')
        await self.update_user_stats(guild, auction['user_id'], winning_bid, 'sold')
        await self.update_reputation(guild, winner.id, 'increase', 'purchase')
        await self.update_reputation(guild, auction['user_id'], 'increase', 'sale')

        try:
            items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
            await winner.send(f"Congratulations! You won the auction for {items_str} with a bid of {winning_bid:,}. The items will be delivered to you shortly.")
        except discord.HTTPException:
            pass

    async def update_auction_history(self, guild: discord.Guild, auction: Dict[str, Any]):
        async with self.config.guild(guild).auction_history() as history:
            history.append(auction)
        self.analytics.update(auction)

    async def notify_subscribers(self, guild: discord.Guild, auction: Dict[str, Any], channel: discord.TextChannel):
        async with self.config.all_members(guild)() as all_members:
            for member_id, member_data in all_members.items():
                if auction['category'] in member_data.get('subscribed_categories', []):
                    member = guild.get_member(member_id)
                    if member:
                        try:
                            items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
                            await member.send(f"New auction started in your subscribed category '{auction['category']}': {items_str}\n{channel.jump_url}")
                        except discord.HTTPException:
                            pass

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionset(self, ctx: commands.Context):
        """Configure the advanced auction system."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @auctionset.command(name="category")
    async def set_auction_category(self, ctx: commands.Context, category: discord.CategoryChannel):
        """Set the category for auction channels."""
        await self.config.guild(ctx.guild).auction_category.set(category.id)
        await ctx.send(f"Auction category set to {category.name}.")

    @auctionset.command(name="logchannel")
    async def set_log_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel for auction logs."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}.")

    @auctionset.command(name="queuechannel")
    async def set_queue_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel for the auction queue."""
        await self.config.guild(ctx.guild).queue_channel.set(channel.id)
        await ctx.send(f"Queue channel set to {channel.mention}.")

    @auctionset.command(name="role")
    async def set_auction_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to be assigned to users when they open an auction channel."""
        await self.config.guild(ctx.guild).auction_role.set(role.id)
        await ctx.send(f"Auction role set to {role.name}.")

    @auctionset.command(name="bidincrements")
    async def set_bid_increments(self, ctx: commands.Context, tier: int, increment: int):
        """Set bid increment for a specific tier."""
        async with self.config.guild(ctx.guild).bid_increment_tiers() as tiers:
            tiers[str(tier)] = increment
        await ctx.send(f"Bid increment for tier {tier} set to {increment:,}.")

    @auctionset.command(name="categories")
    async def set_categories(self, ctx: commands.Context, *categories):
        """Set auction categories."""
        await self.config.guild(ctx.guild).categories.set(list(categories))
        await ctx.send(f"Auction categories updated: {', '.join(categories)}")

    @auctionset.command(name="duration")
    async def set_auction_duration(self, ctx: commands.Context, hours: int):
        """Set the default duration for auctions."""
        if hours < 1:
            await ctx.send("Auction duration must be at least 1 hour.")
            return
        await self.config.guild(ctx.guild).auction_duration.set(hours * 3600)
        await ctx.send(f"Default auction duration set to {hours} hours.")

    @auctionset.command(name="extension")
    async def set_auction_extension(self, ctx: commands.Context, minutes: int):
        """Set the auction extension time for last-minute bids."""
        if minutes < 1:
            await ctx.send("Extension time must be at least 1 minute.")
            return
        await self.config.guild(ctx.guild).auction_extension_time.set(minutes * 60)
        await ctx.send(f"Auction extension time set to {minutes} minutes.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def spawnauction(self, ctx: commands.Context):
        """Spawn the auction request embed with button in the current channel."""
        view = self.AuctionRequestView(self)
        embed = discord.Embed(
            title="🎉 Request an Advanced Auction 🎉",
            description="Click the button below to request an auction and submit your donation details.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. A new channel will be created for your auction.", inline=False)
        embed.add_field(name="Features", value="• Multi-item auctions\n• Automatic categorization\n• Proxy bidding\n• Buy-out option", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        message = await ctx.send(embed=embed, view=view)
        view.message = message

class AuctionDetailsModal(discord.ui.Modal, title="Auction Details"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    items = discord.ui.TextInput(label="Items (name:amount, separate with ;)", style=discord.TextStyle.long, placeholder="e.g. Rare Pepe:1;Golden Coin:5")
    minimum_bid = discord.ui.TextInput(label="Minimum Bid", style=discord.TextStyle.short, placeholder="e.g. 1000000")
    donations = discord.ui.TextInput(label="Donations (name:amount, separate with ;)", style=discord.TextStyle.long, placeholder="e.g. Rare Pepe:1;Golden Coin:5", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        items = [item.strip().split(':') for item in self.items.value.split(';')]
        items = [{"name": item[0], "amount": int(item[1])} for item in items]
        min_bid = int(self.minimum_bid.value)

        donations = []
        if self.donations.value:
            donations = [donation.strip().split(':') for donation in self.donations.value.split(';')]
            donations = [{"name": donation[0], "amount": int(donation[1])} for donation in donations]

        total_value = sum(await self.cog.get_item_value(item['name']) * item['amount'] for item in items)
        category = self.cog.determine_category(total_value)
        buy_out_price = min(int(total_value * 1.5), total_value + 1000000000)  # Max 150% or value + 1B

        auction_data = {
            "auction_id": await self.cog.get_next_auction_id(interaction.guild),
            "user_id": interaction.user.id,
            "items": items,
            "min_bid": min_bid,
            "category": category,
            "buy_out_price": buy_out_price,
            "current_bid": 0,
            "current_bidder": None,
            "status": "pending",
            "start_time": None,
            "end_time": None,
            "bid_history": [],
            "proxy_bids": {},
            "donations": donations,
        }

        channel = await self.cog.create_auction_channel(interaction.guild, auction_data, interaction.user)
        
        async with self.cog.config.guild(interaction.guild).auctions() as auctions:
            auctions[auction_data['auction_id']] = auction_data
        
        await interaction.response.send_message(f"Your auction request has been created. Please check the new channel: {channel.mention}", ephemeral=True)

    async def get_item_value(self, item_name: str) -> Optional[int]:
        current_time = datetime.utcnow().timestamp()
        if item_name in self.api_cache and current_time - self.api_cache_time[item_name] < 3600:  # Cache for 1 hour
            return self.api_cache[item_name]

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"https://api.example.com/items/{item_name}") as response:
                    if response.status == 200:
                        data = await response.json()
                        item_value = data['value']
                        self.api_cache[item_name] = item_value
                        self.api_cache_time[item_name] = current_time
                        return item_value
                    else:
                        log.error(f"Failed to fetch value for item {item_name}. Status: {response.status}")
                        return None
            except aiohttp.ClientError as e:
                log.error(f"API request error for item {item_name}: {e}")
                return None

    async def get_next_auction_id(self, guild: discord.Guild) -> str:
        async with self.config.guild(guild).auctions() as auctions:
            if not auctions:
                return "AUC0001"
            last_id = max(int(aid[3:]) for aid in auctions.keys())
            return f"AUC{last_id + 1:04d}"

    @commands.command()
    async def bid(self, ctx: commands.Context, amount: int):
        """Place a bid on the current auction."""
        if not ctx.channel.name.startswith("auction-"):
            await ctx.send("Bids can only be placed in auction channels.")
            return

        auction_id = ctx.channel.name.split('-')[1]
        async with self.config.guild(ctx.guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("There is no active auction in this channel.")
                return

            if amount <= auction['current_bid']:
                await ctx.send(f"Your bid must be higher than the current bid of ${auction['current_bid']:,}.")
                return

            total_value = sum(await self.get_item_value(item['name']) * item['amount'] for item in auction['items'])
            if amount > total_value * 1.5:
                await ctx.send(f"Your bid cannot exceed 150% of the item's value (${total_value * 1.5:,}).")
                return

            auction['current_bid'] = amount
            auction['current_bidder'] = ctx.author.id
            auction['bid_history'].append({
                'user_id': ctx.author.id,
                'amount': amount,
                'timestamp': datetime.utcnow().timestamp()
            })

            if amount >= auction['buy_out_price']:
                await self.end_auction(ctx.guild, auction_id)
            else:
                auctions[auction_id] = auction
                await ctx.send(embed=await self.create_auction_embed(auction))

    @commands.command()
    async def proxybid(self, ctx: commands.Context, amount: int):
        """Set a maximum proxy bid for the current auction."""
        if not ctx.channel.name.startswith("auction-"):
            await ctx.send("Proxy bids can only be set in auction channels.")
            return

        auction_id = ctx.channel.name.split('-')[1]
        async with self.config.guild(ctx.guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("There is no active auction in this channel.")
                return

            total_value = sum(await self.get_item_value(item['name']) * item['amount'] for item in auction['items'])
            max_proxy_bid = min(total_value * 1.5, total_value + 1000000000)  # Max 150% or value + 1B
            
            if amount > max_proxy_bid:
                await ctx.send(f"Your proxy bid cannot exceed ${max_proxy_bid:,}.")
                return

            auction['proxy_bids'][str(ctx.author.id)] = amount
            auctions[auction_id] = auction

            await ctx.send(f"Your maximum proxy bid of ${amount:,} has been set.")
            await self.process_proxy_bids(ctx.guild, auction_id)

    async def process_proxy_bids(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                return

            sorted_bids = sorted(auction['proxy_bids'].items(), key=lambda x: int(x[1]), reverse=True)
            if len(sorted_bids) < 2:
                return

            top_bidder_id, top_bid = sorted_bids[0]
            second_highest_bid = int(sorted_bids[1][1])

            if second_highest_bid >= auction['current_bid']:
                new_bid = min(second_highest_bid + 1, int(top_bid))
                auction['current_bid'] = new_bid
                auction['current_bidder'] = int(top_bidder_id)
                auction['bid_history'].append({
                    'user_id': int(top_bidder_id),
                    'amount': new_bid,
                    'timestamp': datetime.utcnow().timestamp()
                })

                auctions[auction_id] = auction

                # Notify about the new bid
                channel = guild.get_channel(auction['channel_id'])
                if channel:
                    await channel.send(embed=await self.create_auction_embed(auction))

    @commands.command()
    async def auctioninfo(self, ctx: commands.Context, auction_id: Optional[str] = None):
        """Display information about the current or a specific auction."""
        if not auction_id and ctx.channel.name.startswith("auction-"):
            auction_id = ctx.channel.name.split('-')[1]

        if not auction_id:
            await ctx.send("Please provide an auction ID or use this command in an auction channel.")
            return

        async with self.config.guild(ctx.guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await ctx.send("Invalid auction ID.")
                return

            embed = await self.create_auction_embed(auction)
            await ctx.send(embed=embed)

    @commands.command()
    async def auctionhistory(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """View auction history for yourself or another user."""
        target = user or ctx.author
        async with self.config.guild(ctx.guild).auction_history() as history:
            user_history = [a for a in history if a['user_id'] == target.id or a['current_bidder'] == target.id]

        if not user_history:
            await ctx.send(f"No auction history found for {target.name}.")
            return

        embeds = []
        for auction in user_history:
            embed = discord.Embed(title=f"Auction #{auction['auction_id']}", color=discord.Color.blue())
            embed.add_field(name="Role", value="Seller" if auction['user_id'] == target.id else "Buyer", inline=True)
            embed.add_field(name="Final Bid", value=f"${auction['current_bid']:,}", inline=True)
            embed.add_field(name="Status", value=auction['status'].capitalize(), inline=True)
            items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
            embed.add_field(name="Items", value=items_str, inline=False)
            embed.add_field(name="Date", value=f"<t:{int(auction['end_time'])}:F>", inline=False)
            embeds.append(embed)

        await menu(ctx, embeds, DEFAULT_CONTROLS)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def cancelauction(self, ctx: commands.Context, auction_id: str):
        """Cancel an ongoing auction."""
        async with self.config.guild(ctx.guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await ctx.send("Invalid auction ID.")
                return

            if auction['status'] != 'active':
                await ctx.send("This auction is not active and cannot be cancelled.")
                return

            auction['status'] = 'cancelled'
            auctions[auction_id] = auction

        channel = ctx.guild.get_channel(auction['channel_id'])
        if channel:
            await channel.send("This auction has been cancelled by an administrator.")
            await channel.delete()

        await ctx.send(f"Auction #{auction_id} has been cancelled.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setmoderatorrole(self, ctx: commands.Context, role: discord.Role):
        """Set the auction moderator role."""
        await self.config.guild(ctx.guild).moderator_role.set(role.id)
        await ctx.send(f"Auction moderator role set to {role.name}.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def listmoderatorroles(self, ctx: commands.Context):
        """List the current auction moderator role."""
        role_id = await self.config.guild(ctx.guild).moderator_role()
        role = ctx.guild.get_role(role_id)
        if role:
            await ctx.send(f"Current auction moderator role: {role.name}")
        else:
            await ctx.send("No auction moderator role set.")

    @commands.command()
    async def auctionleaderboard(self, ctx: commands.Context):
        """Display the auction leaderboard."""
        async with self.config.guild(ctx.guild).user_stats() as user_stats:
            sorted_stats = sorted(user_stats.items(), key=lambda x: x[1]['total_value'], reverse=True)[:10]

        embed = discord.Embed(title="Auction Leaderboard", color=discord.Color.gold())
        for i, (user_id, stats) in enumerate(sorted_stats, 1):
            user = ctx.guild.get_member(int(user_id))
            if user:
                embed.add_field(
                    name=f"{i}. {user.name}",
                    value=f"Total Value: ${stats['total_value']:,}\nAuctions Won: {stats['auctions_won']}",
                    inline=False
                )

        await ctx.send(embed=embed)

    @commands.command()
    async def auctionsubscribe(self, ctx: commands.Context, *categories):
        """Subscribe to auction notifications for specific categories."""
        if not categories:
            await ctx.send("Please specify at least one category to subscribe to.")
            return

        valid_categories = set(await self.config.guild(ctx.guild).categories())
        invalid_categories = set(categories) - valid_categories
        if invalid_categories:
            await ctx.send(f"Invalid categories: {', '.join(invalid_categories)}. Valid categories are: {', '.join(valid_categories)}")
            return

        async with self.config.member(ctx.author).subscribed_categories() as subscribed:
            subscribed.extend(cat for cat in categories if cat not in subscribed)

        await ctx.send(f"You have been subscribed to the following categories: {', '.join(categories)}")

    @commands.command()
    async def auctionunsubscribe(self, ctx: commands.Context, *categories):
        """Unsubscribe from auction notifications for specific categories."""
        if not categories:
            await ctx.send("Please specify at least one category to unsubscribe from.")
            return

        async with self.config.member(ctx.author).subscribed_categories() as subscribed:
            for cat in categories:
                if cat in subscribed:
                    subscribed.remove(cat)

        await ctx.send(f"You have been unsubscribed from the following categories: {', '.join(categories)}")

    @commands.command()
    async def mysubscriptions(self, ctx: commands.Context):
        """View your current auction category subscriptions."""
        subscribed = await self.config.member(ctx.author).subscribed_categories()
        if subscribed:
            await ctx.send(f"You are currently subscribed to the following categories: {', '.join(subscribed)}")
        else:
            await ctx.send("You are not currently subscribed to any auction categories.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionreport(self, ctx: commands.Context, days: int = 7):
        """Generate a detailed report of auction activity for the specified number of days."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            now = datetime.utcnow().timestamp()
            relevant_auctions = [a for a in history if now - a['end_time'] <= days * 86400]

        if not relevant_auctions:
            await ctx.send(f"No completed auctions in the last {days} days.")
            return

        total_value = sum(a['current_bid'] for a in relevant_auctions)
        avg_value = total_value / len(relevant_auctions)
        most_valuable = max(relevant_auctions, key=lambda x: x['current_bid'])
        most_bids = max(relevant_auctions, key=lambda x: len(x['bid_history']))

        category_stats = defaultdict(lambda: {"count": 0, "value": 0})
        for auction in relevant_auctions:
            category_stats[auction['category']]["count"] += 1
            category_stats[auction['category']]["value"] += auction['current_bid']

        embed = discord.Embed(title=f"Auction Report (Last {days} Days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=len(relevant_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"${total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"${avg_value:,.2f}", inline=True)
        
        most_valuable_items = ', '.join(f"{item['amount']}x {item['name']}" for item in most_valuable['items'])
        embed.add_field(name="Most Valuable Auction", value=f"${most_valuable['current_bid']:,} ({most_valuable_items})", inline=False)
        
        most_bids_items = ', '.join(f"{item['amount']}x {item['name']}" for item in most_bids['items'])
        embed.add_field(name="Most Bids", value=f"{len(most_bids['bid_history'])} bids ({most_bids_items})", inline=False)

        category_report = "\n".join(f"{cat}: {stats['count']} auctions, ${stats['value']:,} total value" for cat, stats in category_stats.items())
        embed.add_field(name="Category Performance", value=category_report, inline=False)

        await ctx.send(embed=embed)

        # Generate and send charts
        value_chart = await self.create_value_distribution_chart(relevant_auctions)
        category_chart = await self.create_category_performance_chart(category_stats)
        await ctx.send(files=[value_chart, category_chart])

    async def create_value_distribution_chart(self, auctions: List[Dict[str, Any]]) -> discord.File:
        plt.figure(figsize=(10, 6))
        values = [a['current_bid'] for a in auctions]
        plt.hist(values, bins=20, edgecolor='black')
        plt.title("Auction Value Distribution")
        plt.xlabel("Auction Value")
        plt.ylabel("Number of Auctions")
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="value_distribution.png")

    async def create_category_performance_chart(self, category_stats: Dict[str, Dict[str, int]]) -> discord.File:
        categories = list(category_stats.keys())
        values = [stats['value'] for stats in category_stats.values()]
        
        plt.figure(figsize=(10, 6))
        plt.bar(categories, values)
        plt.title("Category Performance")
        plt.xlabel("Category")
        plt.ylabel("Total Value")
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="category_performance.png")

    @commands.command()
    async def auctioninsights(self, ctx: commands.Context):
        """Display insights and analytics about the auction system."""
        summary = self.analytics.get_summary()
        
        embed = discord.Embed(title="Auction System Insights", color=discord.Color.blue())
        embed.add_field(name="Total Auctions", value=str(summary['total_auctions']), inline=True)
        embed.add_field(name="Total Value", value=f"${summary['total_value']:,}", inline=True)
        
        top_items = "\n".join(f"{item}: {count}" for item, count in summary['top_items'].items())
        embed.add_field(name="Top 5 Items", value=top_items or "No data", inline=False)
        
        top_users = "\n".join(f"<@{user_id}>: {count}" for user_id, count in summary['top_users'].items())
        embed.add_field(name="Top 5 Users", value=top_users or "No data", inline=False)
        
        category_performance = "\n".join(f"{cat}: {stats['count']} auctions, ${stats['value']:,} total value" for cat, stats in summary['category_performance'].items())
        embed.add_field(name="Category Performance", value=category_performance or "No data", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    async def auctionsearch(self, ctx: commands.Context, *, query: str):
        """Search for auctions based on item name, category, or seller."""
        auctions = await self.config.guild(ctx.guild).auctions()
        results = []
        
        for auction in auctions.values():
            if (query.lower() in [item['name'].lower() for item in auction['items']] or
                query.lower() in auction['category'].lower() or
                query == str(auction['user_id'])):
                results.append(auction)
        
        if not results:
            await ctx.send("No matching auctions found.")
            return
        
        embeds = []
        for auction in results:
            embed = await self.create_auction_embed(auction)
            embeds.append(embed)
        
        await menu(ctx, embeds, DEFAULT_CONTROLS)

    @commands.command()
    async def savesearch(self, ctx: commands.Context, name: str, *, query: str):
        """Save a search query for future use."""
        async with self.config.member(ctx.author).saved_searches() as searches:
            searches[name] = query
        
        await ctx.send(f"Search '{name}' has been saved.")

    @commands.command()
    async def runsavedsearch(self, ctx: commands.Context, name: str):
        """Run a saved search query."""
        searches = await self.config.member(ctx.author).saved_searches()
        if name not in searches:
            await ctx.send(f"No saved search found with the name '{name}'.")
            return
        
        query = searches[name]
        await ctx.invoke(self.auctionsearch, query=query)

    @commands.command()
    async def listsavedsearches(self, ctx: commands.Context):
        """List all saved search queries."""
        searches = await self.config.member(ctx.author).saved_searches()
        if not searches:
            await ctx.send("You have no saved searches.")
            return
        
        embed = discord.Embed(title="Your Saved Searches", color=discord.Color.blue())
        for name, query in searches.items():
            embed.add_field(name=name, value=query, inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    async def deletesavedsearch(self, ctx: commands.Context, name: str):
        """Delete a saved search query."""
        async with self.config.member(ctx.author).saved_searches() as searches:
            if name not in searches:
                await ctx.send(f"No saved search found with the name '{name}'.")
                return
            
            del searches[name]
        
        await ctx.send(f"Saved search '{name}' has been deleted.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def toggleauctionfeature(self, ctx: commands.Context, feature: str):
        """Toggle various auction features on or off."""
        valid_features = ['reserve_price', 'proxy_bidding', 'multi_item', 'bundles', 'insurance']
        if feature not in valid_features:
            await ctx.send(f"Invalid feature. Valid features are: {', '.join(valid_features)}")
            return
        
        async with self.config.guild(ctx.guild).global_auction_settings() as settings:
            feature_key = f"{feature}_allowed"
            settings[feature_key] = not settings.get(feature_key, True)
            state = "enabled" if settings[feature_key] else "disabled"
        
        await ctx.send(f"The {feature} feature has been {state}.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctioninsurance(self, ctx: commands.Context, rate: float):
        """Set the insurance rate for auctions."""
        if rate < 0 or rate > 1:
            await ctx.send("Insurance rate must be between 0 and 1 (0% to 100%).")
            return
        
        await self.config.guild(ctx.guild).global_auction_settings.auction_insurance_rate.set(rate)
        await ctx.send(f"Auction insurance rate has been set to {rate:.2%}")

    @commands.command()
    async def buyauctioninsurance(self, ctx: commands.Context):
        """Buy insurance for your current auction."""
        if not ctx.channel.name.startswith("auction-"):
            await ctx.send("This command can only be used in auction channels.")
            return
        
        guild = ctx.guild
        auction_id = ctx.channel.name.split("-")[1]
        
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['user_id'] != ctx.author.id:
                await ctx.send("You don't have an active auction in this channel.")
                return
            
            if auction.get('insurance_bought', False):
                await ctx.send("You've already bought insurance for this auction.")
                return
            
            settings = await self.config.guild(guild).global_auction_settings()
            if not settings.get('insurance_allowed', False):
                await ctx.send("Auction insurance is not enabled on this server.")
                return
            
            insurance_rate = settings['auction_insurance_rate']
            insurance_cost = int(auction['min_bid'] * insurance_rate)
            
            # Check if user can afford the insurance
            if not await bank.can_spend(ctx.author, insurance_cost):
                await ctx.send(f"You don't have enough funds to buy insurance. Cost: ${insurance_cost:,}")
                return
            
            # Deduct insurance cost and mark insurance as bought
            await bank.withdraw_credits(ctx.author, insurance_cost)
            auction['insurance_bought'] = True
            auctions[auction_id] = auction
        
        await ctx.send(f"You've successfully bought insurance for your auction. Cost: ${insurance_cost:,}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionpingroles(self, ctx: commands.Context, regular: discord.Role, massive: discord.Role):
        """Set roles to be pinged for regular and massive auctions."""
        await self.config.guild(ctx.guild).auction_ping_role.set(regular.id)
        await self.config.guild(ctx.guild).massive_auction_ping_role.set(massive.id)
        await ctx.send(f"Auction ping roles set. Regular: {regular.name}, Massive: {massive.name}")

    async def ping_auction_roles(self, guild: discord.Guild, auction: Dict[str, Any]):
        """Ping appropriate roles when a new auction starts."""
        channel_id = auction['channel_id']
        channel = guild.get_channel(channel_id)
        if not channel:
            return

        total_value = sum(await self.get_item_value(item['name']) * item['amount'] for item in auction['items'])
        massive_threshold = await self.config.guild(guild).massive_auction_threshold()

        if total_value >= massive_threshold:
            role_id = await self.config.guild(guild).massive_auction_ping_role()
        else:
            role_id = await self.config.guild(guild).auction_ping_role()

        role = guild.get_role(role_id)
        if role:
            await channel.send(f"{role.mention} A new auction has started!")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setmassiveauctionthreshold(self, ctx: commands.Context, value: int):
        """Set the threshold for what's considered a massive auction."""
        await self.config.guild(ctx.guild).massive_auction_threshold.set(value)
        await ctx.send(f"Massive auction threshold set to ${value:,}")

    @commands.command()
    async def topauctioneer(self, ctx: commands.Context):
        """Display the top auctioneer based on total value sold."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("No auction history available.")
                return

            seller_stats = defaultdict(lambda: {"total_value": 0, "auctions_count": 0})
            for auction in history:
                if auction['status'] == 'completed':
                    seller_stats[auction['user_id']]["total_value"] += auction['current_bid']
                    seller_stats[auction['user_id']]["auctions_count"] += 1

            if not seller_stats:
                await ctx.send("No completed auctions found.")
                return

            top_seller_id = max(seller_stats, key=lambda x: seller_stats[x]["total_value"])
            top_seller = guild.get_member(top_seller_id)
            top_seller_name = top_seller.name if top_seller else f"User ID: {top_seller_id}"

            embed = discord.Embed(title="Top Auctioneer", color=discord.Color.gold())
            embed.add_field(name="Auctioneer", value=top_seller_name, inline=False)
            embed.add_field(name="Total Value Sold", value=f"${seller_stats[top_seller_id]['total_value']:,}", inline=True)
            embed.add_field(name="Auctions Completed", value=str(seller_stats[top_seller_id]['auctions_count']), inline=True)

            await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def blacklistuser(self, ctx: commands.Context, user: discord.Member):
        """Blacklist a user from participating in auctions."""
        async with self.config.guild(ctx.guild).banned_users() as banned_users:
            if user.id in banned_users:
                await ctx.send(f"{user.name} is already blacklisted from auctions.")
                return
            banned_users.append(user.id)

        await ctx.send(f"{user.name} has been blacklisted from participating in auctions.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def unblacklistuser(self, ctx: commands.Context, user: discord.Member):
        """Remove a user from the auction blacklist."""
        async with self.config.guild(ctx.guild).banned_users() as banned_users:
            if user.id not in banned_users:
                await ctx.send(f"{user.name} is not blacklisted from auctions.")
                return
            banned_users.remove(user.id)

        await ctx.send(f"{user.name} has been removed from the auction blacklist.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def listblacklistedusers(self, ctx: commands.Context):
        """List all users blacklisted from auctions."""
        banned_users = await self.config.guild(ctx.guild).banned_users()
        if not banned_users:
            await ctx.send("No users are currently blacklisted from auctions.")
            return

        embed = discord.Embed(title="Blacklisted Users", color=discord.Color.red())
        for user_id in banned_users:
            user = ctx.guild.get_member(user_id)
            embed.add_field(name=f"User ID: {user_id}", value=user.name if user else "User not found", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def auctionextension(self, ctx: commands.Context, auction_id: str, minutes: int):
        """Request an extension for an ongoing auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("Invalid auction ID or the auction is not active.")
                return

            if ctx.author.id != auction['user_id']:
                await ctx.send("Only the auction creator can request an extension.")
                return

            max_extensions = await self.config.guild(guild).max_auction_extensions()
            if auction.get('extensions', 0) >= max_extensions:
                await ctx.send(f"This auction has already been extended the maximum number of times ({max_extensions}).")
                return

            auction['end_time'] += minutes * 60
            auction['extensions'] = auction.get('extensions', 0) + 1
            auctions[auction_id] = auction

        await ctx.send(f"Auction #{auction_id} has been extended by {minutes} minutes. New end time: <t:{int(auction['end_time'])}:F>")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setmaxauctionextensions(self, ctx: commands.Context, max_extensions: int):
        """Set the maximum number of times an auction can be extended."""
        if max_extensions < 0:
            await ctx.send("The maximum number of extensions must be a non-negative integer.")
            return

        await self.config.guild(ctx.guild).max_auction_extensions.set(max_extensions)
        await ctx.send(f"Maximum auction extensions set to {max_extensions}.")

    @commands.command()
    async def auctionbundle(self, ctx: commands.Context, *items: str):
        """Create an auction bundle from multiple items."""
        if len(items) < 2:
            await ctx.send("You need to specify at least two items to create a bundle.")
            return

        bundle_items = []
        for item in items:
            try:
                name, amount = item.split(':')
                bundle_items.append({"name": name.strip(), "amount": int(amount)})
            except ValueError:
                await ctx.send(f"Invalid format for item: {item}. Please use 'name:amount'.")
                return

        bundle_name = f"Bundle: {', '.join(item['name'] for item in bundle_items)}"
        total_value = sum(await self.get_item_value(item['name']) * item['amount'] for item in bundle_items)

        if not await self.check_auction_limits(ctx.guild, ctx.author.id):
            await ctx.send("You have reached the maximum number of active auctions or are in the cooldown period.")
            return

        auction_data = {
            "auction_id": await self.get_next_auction_id(ctx.guild),
            "user_id": ctx.author.id,
            "items": bundle_items,
            "min_bid": int(total_value * 0.8),  # Set minimum bid to 80% of total value
            "category": "Bundle",
            "status": "pending",
            "total_value": total_value,
            "current_bid": 0,
            "current_bidder": None,
            "bid_history": [],
            "start_time": None,
            "end_time": None,
            "proxy_bids": {},
        }

        await self.create_auction_channel(ctx.guild, auction_data, ctx.author)
        await ctx.send(f"Bundle auction created: {bundle_name}")

    @commands.command()
    async def auctionwatch(self, ctx: commands.Context, auction_id: str):
        """Add an auction to your watch list."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            if auction_id not in auctions:
                await ctx.send("Invalid auction ID.")
                return

            async with self.config.member(ctx.author).watched_auctions() as watched:
                if auction_id in watched:
                    await ctx.send("This auction is already in your watch list.")
                    return
                watched.append(auction_id)

        await ctx.send(f"Auction #{auction_id} has been added to your watch list.")

    @commands.command()
    async def auctionunwatch(self, ctx: commands.Context, auction_id: str):
        """Remove an auction from your watch list."""
        async with self.config.member(ctx.author).watched_auctions() as watched:
            if auction_id not in watched:
                await ctx.send("This auction is not in your watch list.")
                return
            watched.remove(auction_id)

        await ctx.send(f"Auction #{auction_id} has been removed from your watch list.")

    @commands.command()
    async def mywatchlist(self, ctx: commands.Context):
        """Display your auction watch list."""
        watched = await self.config.member(ctx.author).watched_auctions()
        if not watched:
            await ctx.send("Your auction watch list is empty.")
            return

        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            embed = discord.Embed(title="Your Auction Watch List", color=discord.Color.blue())
            for auction_id in watched:
                auction = auctions.get(auction_id)
                if auction:
                    items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
                    embed.add_field(
                        name=f"Auction #{auction_id}",
                        value=f"Items: {items_str}\nCurrent Bid: ${auction['current_bid']:,}\nEnds: <t:{int(auction['end_time'])}:R>",
                        inline=False
                    )

        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctiontemplate(self, ctx: commands.Context, name: str, *, template: str):
        """Create or update an auction template."""
        async with self.config.guild(ctx.guild).auction_templates() as templates:
            templates[name] = template
        await ctx.send(f"Auction template '{name}' has been created/updated.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def deleteauctiontemplate(self, ctx: commands.Context, name: str):
        """Delete an auction template."""
        async with self.config.guild(ctx.guild).auction_templates() as templates:
            if name not in templates:
                await ctx.send(f"Template '{name}' does not exist.")
                return
            del templates[name]
        await ctx.send(f"Auction template '{name}' has been deleted.")

    @commands.command()
    async def listauctiontemplatenames(self, ctx: commands.Context):
        """List all auction template names."""
        templates = await self.config.guild(ctx.guild).auction_templates()
        if not templates:
            await ctx.send("No auction templates have been created.")
            return
        template_list = "\n".join(templates.keys())
        await ctx.send(f"Available auction templates:\n{template_list}")

    @commands.command()
    async def viewauctiontemplate(self, ctx: commands.Context, name: str):
        """View a specific auction template."""
        templates = await self.config.guild(ctx.guild).auction_templates()
        if name not in templates:
            await ctx.send(f"Template '{name}' does not exist.")
            return
        await ctx.send(f"Template '{name}':\n{templates[name]}")

    @commands.command()
    async def useauctiontemplate(self, ctx: commands.Context, name: str, *args):
        """Use an auction template to create a new auction."""
        templates = await self.config.guild(ctx.guild).auction_templates()
        if name not in templates:
            await ctx.send(f"Template '{name}' does not exist.")
            return

        template = templates[name]
        try:
            formatted_template = template.format(*args)
        except IndexError:
            await ctx.send("Not enough arguments provided for the template.")
            return
        except KeyError:
            await ctx.send("Invalid keyword argument in the template.")
            return

        # Parse the formatted template and create the auction
        lines = formatted_template.split('\n')
        auction_data = {}
        for line in lines:
            key, value = line.split(':')
            auction_data[key.strip()] = value.strip()

        # Convert the parsed data into the format expected by create_auction_channel
        formatted_auction_data = {
            "auction_id": await self.get_next_auction_id(ctx.guild),
            "user_id": ctx.author.id,
            "items": [{"name": auction_data["item"], "amount": int(auction_data["amount"])}],
            "min_bid": int(auction_data["min_bid"]),
            "category": auction_data["category"],
            "status": "pending",
            "current_bid": 0,
            "current_bidder": None,
            "bid_history": [],
            "start_time": None,
            "end_time": None,
            "proxy_bids": {},
        }

        # Create the auction channel
        channel = await self.create_auction_channel(ctx.guild, formatted_auction_data, ctx.author)

        # Add the auction to the guild's auctions
        async with self.config.guild(ctx.guild).auctions() as auctions:
            auctions[formatted_auction_data['auction_id']] = formatted_auction_data
    
        await ctx.send(f"Auction created using the template. Please check the new channel: {channel.mention}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionbackup(self, ctx: commands.Context):
        """Create a backup of all auction data."""
        guild = ctx.guild
        backup_data = {
            "auctions": await self.config.guild(guild).auctions(),
            "auction_history": await self.config.guild(guild).auction_history(),
            "settings": await self.config.guild(guild).get_raw(),
        }

        filename = f"auction_backup_{guild.id}_{int(datetime.utcnow().timestamp())}.json"
        with open(filename, 'w') as f:
            json.dump(backup_data, f, indent=4)

        await ctx.send("Auction data backup created.", file=discord.File(filename))
        os.remove(filename)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionrestore(self, ctx: commands.Context):
        """Restore auction data from a backup file."""
        if not ctx.message.attachments:
            await ctx.send("Please attach the backup file when using this command.")
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith('.json'):
            await ctx.send("Please attach a valid JSON backup file.")
            return

        try:
            backup_content = await attachment.read()
            backup_data = json.loads(backup_content)

            guild = ctx.guild
            await self.config.guild(guild).auctions.set(backup_data["auctions"])
            await self.config.guild(guild).auction_history.set(backup_data["auction_history"])
            await self.config.guild(guild).set_raw(value=backup_data["settings"])

            await ctx.send("Auction data has been restored from the backup.")
        except json.JSONDecodeError:
            await ctx.send("The attached file is not a valid JSON file.")
        except KeyError:
            await ctx.send("The backup file is missing required data.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionmetrics(self, ctx: commands.Context, days: int = 30):
        """Display advanced auction metrics for the specified number of days."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            now = datetime.utcnow().timestamp()
            relevant_auctions = [a for a in history if now - a['end_time'] <= days * 86400]

        if not relevant_auctions:
            await ctx.send(f"No completed auctions in the last {days} days.")
            return

        total_auctions = len(relevant_auctions)
        total_value = sum(a['current_bid'] for a in relevant_auctions)
        avg_value = total_value / total_auctions
        median_value = sorted(a['current_bid'] for a in relevant_auctions)[total_auctions // 2]
        total_unique_bidders = len(set(bid['user_id'] for a in relevant_auctions for bid in a['bid_history']))
        total_unique_sellers = len(set(a['user_id'] for a in relevant_auctions))
        avg_bids_per_auction = sum(len(a['bid_history']) for a in relevant_auctions) / total_auctions
        
        category_performance = defaultdict(lambda: {"count": 0, "value": 0})
        for auction in relevant_auctions:
            category_performance[auction['category']]["count"] += 1
            category_performance[auction['category']]["value"] += auction['current_bid']

        embed = discord.Embed(title=f"Advanced Auction Metrics (Last {days} Days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=str(total_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"${total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"${avg_value:,.2f}", inline=True)
        embed.add_field(name="Median Value", value=f"${median_value:,}", inline=True)
        embed.add_field(name="Unique Bidders", value=str(total_unique_bidders), inline=True)
        embed.add_field(name="Unique Sellers", value=str(total_unique_sellers), inline=True)
        embed.add_field(name="Avg. Bids per Auction", value=f"{avg_bids_per_auction:.2f}", inline=True)

        category_stats = "\n".join(f"{cat}: {stats['count']} auctions, ${stats['value']:,} total value" for cat, stats in category_performance.items())
        embed.add_field(name="Category Performance", value=category_stats, inline=False)

        await ctx.send(embed=embed)

        # Generate and send additional charts
        value_distribution_chart = await self.create_value_distribution_chart(relevant_auctions)
        category_performance_chart = await self.create_category_performance_chart(category_performance)
        await ctx.send(files=[value_distribution_chart, category_performance_chart])

    @commands.command()
    async def auctionhelp(self, ctx: commands.Context):
        """Display help information for the  auction system."""
        embed = discord.Embed(title=" Auction System Help", color=discord.Color.blue())
        
        general_commands = [
            "`bid <amount>`: Place a bid on the current auction",
            "`proxybid <amount>`: Set a maximum proxy bid",
            "`auctioninfo [auction_id]`: Display auction information",
            "`auctionhistory [user]`: View auction history",
            "`auctionleaderboard`: View top auction participants",
            "`auctionsubscribe <categories>`: Subscribe to auction categories",
            "`auctionunsubscribe <categories>`: Unsubscribe from categories",
            "`mysubscriptions`: View your category subscriptions",
            "`auctionsearch <query>`: Search for auctions",
            "`savesearch <name> <query>`: Save a search query",
            "`runsavedsearch <name>`: Run a saved search",
            "`listsavedsearches`: List your saved searches",
            "`deletesavedsearch <name>`: Delete a saved search",
            "`buyauctioninsurance`: Buy insurance for your auction",
            "`auctionwatch <auction_id>`: Add an auction to your watch list",
            "`auctionunwatch <auction_id>`: Remove an auction from your watch list",
            "`mywatchlist`: Display your auction watch list",
            "`auctionbundle <item1:amount> <item2:amount> ...`: Create an auction bundle",
            "`auctionextension <auction_id> <minutes>`: Request an auction extension",
            "`useauctiontemplate <name> [args]`: Use an auction template",
        ]
        embed = discord.Embed(title=" Auction System Help", color=discord.Color.blue())
        admin_commands = [
            "`auctionset`: Configure auction settings",
            "`spawnauction`: Create a new auction request button",
            "`cancelauction <auction_id>`: Cancel an auction",
            "`setmoderatorrole <role>`: Set the auction moderator role",
            "`listmoderatorroles`: List auction moderator roles",
            "`auctionreport [days]`: Generate an auction report",
            "`toggleauctionfeature <feature>`: Toggle auction features",
            "`setauctioninsurance <rate>`: Set the insurance rate",
            "`setauctionpingroles <regular_role> <massive_role>`: Set roles for auction pings",
            "`setmassiveauctionthreshold <value>`: Set the threshold for massive auctions",
            "`blacklistuser <user>`: Blacklist a user from auctions",
            "`unblacklistuser <user>`: Remove a user from the auction blacklist",
            "`listblacklistedusers`: List all blacklisted users",
            "`setmaxauctionextensions <number>`: Set the maximum number of auction extensions",
            "`auctiontemplate <name> <template>`: Create or update an auction template",
            "`deleteauctiontemplate <name>`: Delete an auction template",
            "`listauctiontemplatenames`: List all auction template names",
            "`viewauctiontemplate <name>`: View a specific auction template",
            "`auctionbackup`: Create a backup of all auction data",
            "`auctionrestore`: Restore auction data from a backup file",
            "`auctionmetrics [days]`: Display advanced auction metrics",
        ]
        
        embed.add_field(name="General Commands", value="\n".join(general_commands), inline=False)
        embed.add_field(name="Admin Commands", value="\n".join(admin_commands), inline=False)
        
        await ctx.send(embed=embed)

    def cog_unload(self):
        """Clean up when cog is unloaded."""
        self.bot.loop.create_task(self._unload())

    async def _unload(self):
        """Cancel any ongoing tasks."""
        if self.auction_task:
            self.auction_task.cancel()

    async def red_delete_data_for_user(self, *, requester: str, user_id: int):
        """Delete user data when requested."""
        for guild in self.bot.guilds:
            async with self.config.guild(guild).auction_history() as history:
                for auction in history:
                    if auction['user_id'] == user_id:
                        auction['user_id'] = None
                    auction['bid_history'] = [bid for bid in auction['bid_history'] if bid['user_id'] != user_id]
                    if auction['current_bidder'] == user_id:
                        auction['current_bidder'] = None
            
            async with self.config.guild(guild).banned_users() as banned_users:
                if user_id in banned_users:
                    banned_users.remove(user_id)

            async with self.config.guild(guild).auctions() as auctions:
                for auction in auctions.values():
                    if str(user_id) in auction['proxy_bids']:
                        del auction['proxy_bids'][str(user_id)]

        await self.config.user_from_id(user_id).clear()

    # Helper methods and classes

    class AuctionControls(discord.ui.View):
        def __init__(self, cog, auction):
            super().__init__(timeout=None)
            self.cog = cog
            self.auction = auction

        @discord.ui.button(label="Bid", style=discord.ButtonStyle.primary)
        async def bid_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = self.BidModal(self.cog, self.auction)
            await interaction.response.send_modal(modal)

        @discord.ui.button(label="Buy Out", style=discord.ButtonStyle.danger)
        async def buyout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.auction.get('buy_out_price'):
                await interaction.response.send_message("This auction doesn't have a buy-out option.", ephemeral=True)
                return
            
            confirm_view = self.ConfirmBuyout(self.cog, self.auction)
            await interaction.response.send_message("Are you sure you want to buy out this auction?", view=confirm_view, ephemeral=True)

        class BidModal(discord.ui.Modal):
            def __init__(self, cog, auction):
                super().__init__(title="Place a Bid")
                self.cog = cog
                self.auction = auction

            bid_amount = discord.ui.TextInput(label="Bid Amount", placeholder="Enter your bid amount")

            async def on_submit(self, interaction: discord.Interaction):
                try:
                    amount = int(self.bid_amount.value)
                    await self.cog.handle_bid(interaction, self.auction['auction_id'], amount)
                except ValueError:
                    await interaction.response.send_message("Invalid bid amount. Please enter a number.", ephemeral=True)

        class ConfirmBuyout(discord.ui.View):
            def __init__(self, cog, auction):
                super().__init__()
                self.cog = cog
                self.auction = auction

            @discord.ui.button(label="Confirm Buy Out", style=discord.ButtonStyle.danger)
            async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.handle_buyout(interaction, self.auction['auction_id'])

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_message("Buy out cancelled.", ephemeral=True)

    async def handle_bid(self, interaction: discord.Interaction, auction_id: str, amount: int):
        guild = interaction.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await interaction.response.send_message("This auction is not active.", ephemeral=True)
                return

            if amount <= auction['current_bid']:
                await interaction.response.send_message(f"Your bid must be higher than the current bid of ${auction['current_bid']:,}.", ephemeral=True)
                return

            total_value = sum(await self.get_item_value(item['name']) * item['amount'] for item in auction['items'])
            if amount > total_value * 1.5:
                await interaction.response.send_message(f"Your bid cannot exceed 150% of the item's value (${total_value * 1.5:,}).", ephemeral=True)
                return

            auction['current_bid'] = amount
            auction['current_bidder'] = interaction.user.id
            auction['bid_history'].append({
                'user_id': interaction.user.id,
                'amount': amount,
                'timestamp': datetime.utcnow().timestamp()
            })

            auctions[auction_id] = auction
            
            await interaction.response.send_message(f"Your bid of ${amount:,} has been placed!", ephemeral=True)
            await self.update_auction_message(interaction.channel, auction)

    async def handle_buyout(self, interaction: discord.Interaction, auction_id: str):
        guild = interaction.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await interaction.response.send_message("This auction is not active.", ephemeral=True)
                return

            if not auction.get('buy_out_price'):
                await interaction.response.send_message("This auction doesn't have a buy-out option.", ephemeral=True)
                return

            if not await bank.can_spend(interaction.user, auction['buy_out_price']):
                await interaction.response.send_message(f"You don't have enough funds to buy out this auction. You need ${auction['buy_out_price']:,}.", ephemeral=True)
                return

            await bank.withdraw_credits(interaction.user, auction['buy_out_price'])
            auction['current_bid'] = auction['buy_out_price']
            auction['current_bidder'] = interaction.user.id
            auction['status'] = 'completed'
            auctions[auction_id] = auction

            await interaction.response.send_message(f"Congratulations! You've bought out the auction for ${auction['buy_out_price']:,}!", ephemeral=True)
            await self.end_auction(guild, auction_id)

    async def update_auction_message(self, channel: discord.TextChannel, auction: Dict[str, Any]):
        message = await channel.fetch_message(auction['message_id'])
        embed = await self.create_auction_embed(auction)
        await message.edit(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def pruneauctionhistory(self, ctx: commands.Context, days: int):
        """Remove auction history older than the specified number of days."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            current_time = datetime.utcnow().timestamp()
            original_length = len(history)
            history[:] = [auction for auction in history if current_time - auction['end_time'] <= days * 86400]
            pruned_count = original_length - len(history)

        await ctx.send(f"Pruned {pruned_count} auctions from the history.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def resetauctions(self, ctx: commands.Context):
        """Reset all auction data. Use with caution!"""
        confirm = await ctx.send("Are you sure you want to reset all auction data? This action cannot be undone. Reply with 'yes' to confirm.")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == 'yes'

        try:
            await self.bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send("Reset cancelled.")
            return

        await self.config.guild(ctx.guild).clear()
        await self.config.guild(ctx.guild).set(self.config.guild(ctx.guild).defaults)
        self.analytics = AuctionAnalytics()  # Reset analytics
        await ctx.send("All auction data has been reset.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionsettings(self, ctx: commands.Context):
        """Display current auction settings."""
        settings = await self.config.guild(ctx.guild).get_raw()
        embed = discord.Embed(title="Auction Settings", color=discord.Color.blue())
        
        for key, value in settings.items():
            if isinstance(value, dict):
                embed.add_field(name=key, value="\n".join(f"{k}: {v}" for k, v in value.items()), inline=False)
            else:
                embed.add_field(name=key, value=str(value), inline=True)
        
        await ctx.send(embed=embed)

async def setup(bot):
    """Setup function to add the cog to the bot."""
    cog = AdvancedAuctionSystem(bot)
    await bot.add_cog(cog)
    await cog.initialize()