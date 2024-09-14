import discord
from discord.ext import commands, tasks
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
            "auction_channel": None,
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
                    if 'thread_id' not in auction:
                        auction['thread_id'] = None
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
                auction_channel_id = await self.config.guild(guild).auction_channel()
                if not auction_channel_id:
                    continue
                
                auction_channel = guild.get_channel(auction_channel_id)
                if not auction_channel:
                    continue

                active_auctions = len([thread for thread in auction_channel.threads if thread.name.startswith("Auction #") and not thread.archived])
                max_concurrent_auctions = await self.config.guild(guild).global_auction_settings.max_concurrent_auctions()
                
                if active_auctions < max_concurrent_auctions:
                    queue = await self.config.guild(guild).auction_queue()
                    if queue:
                        next_auction = queue.pop(0)
                        await self.start_auction(guild, next_auction)
                        await self.config.guild(guild).auction_queue.set(queue)

    async def check_auction_end(self):
        for guild in self.bot.guilds:
            auction_channel_id = await self.config.guild(guild).auction_channel()
            if not auction_channel_id:
                continue
            
            auction_channel = guild.get_channel(auction_channel_id)
            if not auction_channel:
                continue

            for thread in auction_channel.threads:
                if thread.name.startswith("Auction #"):
                    auction_id = thread.name.split("#")[1]
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
        
        auction_channel_id = await self.config.guild(guild).auction_channel()
        auction_channel = guild.get_channel(auction_channel_id)
        
        if auction_channel:
            thread = await auction_channel.create_thread(
                name=f"Auction #{auction['auction_id']}",
                type=discord.ChannelType.public_thread,
                reason="New auction started"
            )
            
            embed = await self.create_auction_embed(auction)
            message = await thread.send("New auction started!", embed=embed, view=self.AuctionControls(self, auction))
            await message.pin()
            
            # Create and send bid history chart
            chart = await self.visualization.create_bid_history_chart(auction)
            await thread.send("Current bid history:", file=chart)
            
            # Notify subscribers
            await self.notify_subscribers(guild, auction, thread)
        
        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction['auction_id']] = auction

    async def end_auction(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

            auction_channel_id = await self.config.guild(guild).auction_channel()
            auction_channel = guild.get_channel(auction_channel_id)
            
            if auction_channel:
                thread = discord.utils.get(auction_channel.threads, name=f"Auction #{auction_id}")
                if thread:
                    if auction['current_bidder']:
                        winner = guild.get_member(auction['current_bidder'])
                        await thread.send(f"Auction ended! The winner is {winner.mention} with a bid of {auction['current_bid']:,}.")
                        await self.handle_auction_completion(guild, auction, winner, auction['current_bid'])
                    else:
                        await thread.send("Auction ended with no bids.")
                    
                    # Archive the thread
                    await thread.edit(archived=True, locked=True)

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

    async def notify_subscribers(self, guild: discord.Guild, auction: Dict[str, Any], thread: discord.Thread):
        async with self.config.all_members(guild)() as all_members:
            for member_id, member_data in all_members.items():
                if auction['category'] in member_data.get('subscribed_categories', []):
                    member = guild.get_member(member_id)
                    if member:
                        try:
                            items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
                            await member.send(f"New auction started in your subscribed category '{auction['category']}': {items_str}\n{thread.jump_url}")
                        except discord.HTTPException:
                            pass

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionset(self, ctx: commands.Context):
        """Configure the advanced auction system."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @auctionset.command(name="channels")
    async def set_auction_channels(self, ctx: commands.Context, auction: discord.TextChannel, queue: discord.TextChannel, log: discord.TextChannel):
        """Set the channels for auctions, queue, and logging."""
        await self.config.guild(ctx.guild).auction_channel.set(auction.id)
        await self.config.guild(ctx.guild).queue_channel.set(queue.id)
        await self.config.guild(ctx.guild).log_channel.set(log.id)
        await ctx.send(f"Auction channel set to {auction.mention}, queue channel set to {queue.mention}, and log channel set to {log.mention}.")

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
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. A new thread will be created for your auction.", inline=False)
        embed.add_field(name="Features", value="• Multi-item auctions\n• Automatic categorization\n• Proxy bidding\n• Buy-out option", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    class AuctionRequestView(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green, custom_id="request_auction")
        async def request_auction_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(self.AuctionDetailsModal(self.cog))

        class AuctionDetailsModal(discord.ui.Modal, title="Auction Details"):
            def __init__(self, cog):
                super().__init__()
                self.cog = cog

            items = discord.ui.TextInput(label="Items (name:amount, separate with ;)", style=discord.TextStyle.long, placeholder="e.g. Rare Pepe:1;Golden Coin:5")
            minimum_bid = discord.ui.TextInput(label="Minimum Bid", style=discord.TextStyle.short, placeholder="e.g. 1000000")

            async def on_submit(self, interaction: discord.Interaction):
                items = [item.strip().split(':') for item in self.items.value.split(';')]
                items = [{"name": item[0], "amount": int(item[1])} for item in items]
                min_bid = int(self.minimum_bid.value)

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
                }

                thread = await interaction.channel.create_thread(
                    name=f"Auction-{auction_data['auction_id']}",
                    type=discord.ChannelType.private_thread,
                    reason=f"Auction request by {interaction.user.name}"
                )
                await thread.add_user(interaction.user)
                
                embed = await self.cog.create_auction_embed(auction_data)
                await thread.send(f"{interaction.user.mention}, here are the details of your auction request:", embed=embed)
                
                async with self.cog.config.guild(interaction.guild).auctions() as auctions:
                    auctions[auction_data['auction_id']] = auction_data
                
                await interaction.response.send_message(f"Your auction request has been created. Please check the new thread: {thread.mention}", ephemeral=True)

    async def create_auction_embed(self, auction: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎉 Advanced Auction #{auction['auction_id']} 🎉",
            description=f"Category: {auction['category']}",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url="https://example.com/auction_icon.png")
        
        items_str = "\n".join(f"• {item['amount']}x {item['name']}" for item in auction['items'])
        embed.add_field(name="📦 Items", value=items_str, inline=False)
        
        embed.add_field(name="💰 Minimum Bid", value=f"${auction['min_bid']:,}", inline=True)
        if auction.get('reserve_price'):
            embed.add_field(name="🔒 Reserve Price", value=f"${auction['reserve_price']:,}", inline=True)
        if auction.get('buy_out_price'):
            embed.add_field(name="💎 Buy-out Price", value=f"${auction['buy_out_price']:,}", inline=True)
        
        embed.add_field(name="💵 Current Bid", value=f"${auction['current_bid']:,}", inline=True)
        embed.add_field(name="🏆 Top Bidder", value=f"<@{auction['current_bidder']}>" if auction['current_bidder'] else "No bids yet", inline=True)
        
        if auction['status'] == 'active':
            time_left = auction['end_time'] - datetime.utcnow().timestamp()
            embed.add_field(name="⏳ Time Left", value=f"<t:{int(auction['end_time'])}:R>", inline=True)
        else:
            embed.add_field(name="📊 Status", value=auction['status'].capitalize(), inline=True)
        
        embed.set_footer(text=f"Auction ID: {auction['auction_id']} | Created by: {self.bot.get_user(auction['user_id'])}")
        return embed

    def determine_category(self, total_value: int) -> str:
        if total_value < 1000000:  # Less than 1M
            return "Common"
        elif total_value < 10000000:  # 1M to 10M
            return "Uncommon"
        elif total_value < 100000000:  # 10M to 100M
            return "Rare"
        elif total_value < 1000000000:  # 100M to 1B
            return "Epic"
        else:  # 1B and above
            return "Legendary"

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
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("Bids can only be placed in auction threads.")
            return

        auction_id = ctx.channel.name.split('-')[1]
        async with self.config.guild(ctx.guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("There is no active auction in this thread.")
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
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("Proxy bids can only be set in auction threads.")
            return

        auction_id = ctx.channel.name.split('-')[1]
        async with self.config.guild(ctx.guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("There is no active auction in this thread.")
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
                auction['bid_history'].append({'user_id': int(top_bidder_id),
                    'amount': new_bid,
                    'timestamp': datetime.utcnow().timestamp()
                })

                auctions[auction_id] = auction

                # Notify about the new bid
                channel = guild.get_channel(auction['thread_id'])
                if channel:
                    await channel.send(embed=await self.create_auction_embed(auction))

    @commands.command()
    async def auctioninfo(self, ctx: commands.Context, auction_id: Optional[str] = None):
        """Display information about the current or a specific auction."""
        if not auction_id and isinstance(ctx.channel, discord.Thread):
            auction_id = ctx.channel.name.split('-')[1]

        if not auction_id:
            await ctx.send("Please provide an auction ID or use this command in an auction thread.")
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

        channel = ctx.guild.get_channel(auction['thread_id'])
        if channel:
            await channel.send("This auction has been cancelled by an administrator.")
            await channel.edit(archived=True, locked=True)

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
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("This command can only be used in auction threads.")
            return
        
        guild = ctx.guild
        auction_id = ctx.channel.name.split('-')[1]
        
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['user_id'] != ctx.author.id:
                await ctx.send("You don't have an active auction in this thread.")
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
    async def exportauctiondata(self, ctx: commands.Context, format: str = "csv"):
        """Export detailed auction data in CSV or JSON format."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("There is no auction data to export.")
                return

            if format.lower() == "csv":
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow([
                    "Auction ID", "Category", "Seller", "Winner", "Winning Bid", "Start Time", "End Time",
                    "Items", "Reserve Price", "Buy-out Price", "Total Bids", "Initial Bid", "Bid Increment"
                ])
                for auction in history:
                    writer.writerow([
                        auction['auction_id'],
                        auction['category'],
                        auction['user_id'],
                        auction.get('current_bidder', 'N/A'),
                        auction['current_bid'],
                        datetime.fromtimestamp(auction['start_time']),
                        datetime.fromtimestamp(auction['end_time']),
                        '; '.join(f"{item['amount']}x {item['name']}" for item in auction['items']),
                        auction.get('reserve_price', 'N/A'),
                        auction.get('buy_out_price', 'N/A'),
                        len(auction['bid_history']),
                        auction['min_bid'],
                        await self.get_bid_increment(guild, auction['current_bid'])
                    ])
                file = discord.File(fp=io.BytesIO(output.getvalue().encode()), filename="detailed_auction_data.csv")
            elif format.lower() == "json":
                file = discord.File(fp=io.BytesIO(json.dumps(history, indent=2).encode()), filename="detailed_auction_data.json")
            else:
                await ctx.send("Invalid format. Please choose 'csv' or 'json'.")
                return

            await ctx.send("Here's your exported detailed auction data:", file=file)

    @commands.command()
    async def auctionhelp(self, ctx: commands.Context):
        """Display help information for the advanced auction system."""
        embed = discord.Embed(title="Advanced Auction System Help", color=discord.Color.blue())
        
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
            "`buyauctioninsurance`: Buy insurance for your auction"
        ]
        
        admin_commands = [
            "`auctionset`: Configure auction settings",
            "`spawnauction`: Create a new auction request button",
            "`cancelauction <auction_id>`: Cancel an auction",
            "`setmoderatorrole <role>`: Set the auction moderator role",
            "`listmoderatorroles`: List auction moderator roles",
            "`auctionreport [days]`: Generate an auction report",
            "`toggleauctionfeature <feature>`: Toggle auction features",
            "`setauctioninsurance <rate>`: Set the insurance rate",
            "`exportauctiondata [format]`: Export auction data"
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

    @commands.command()
    async def myauctionstats(self, ctx: commands.Context):
        """View your personal auction statistics."""
        user_id = ctx.author.id
        guild = ctx.guild
        
        async with self.config.guild(guild).auction_history() as history:
            user_auctions = [a for a in history if a['user_id'] == user_id or a['current_bidder'] == user_id]
        
        total_sold = sum(a['current_bid'] for a in user_auctions if a['user_id'] == user_id and a['status'] == 'completed')
        total_bought = sum(a['current_bid'] for a in user_auctions if a['current_bidder'] == user_id)
        auctions_won = len([a for a in user_auctions if a['current_bidder'] == user_id])
        auctions_created = len([a for a in user_auctions if a['user_id'] == user_id])
        
        embed = discord.Embed(title=f"Auction Statistics for {ctx.author.name}", color=discord.Color.blue())
        embed.add_field(name="Total Value Sold", value=f"${total_sold:,}", inline=True)
        embed.add_field(name="Total Value Bought", value=f"${total_bought:,}", inline=True)
        embed.add_field(name="Auctions Won", value=str(auctions_won), inline=True)
        embed.add_field(name="Auctions Created", value=str(auctions_created), inline=True)
        
        reputation = await self.config.member(ctx.author).reputation_score()
        embed.add_field(name="Reputation Score", value=str(reputation), inline=True)
        
        await ctx.send(embed=embed)

    @commands.command()
    async def togglenotifications(self, ctx: commands.Context, setting: str):
        """Toggle specific notification settings."""
        valid_settings = ['outbid', 'auction_start', 'auction_end', 'won_auction', 'price_threshold', 'auction_extension']
        if setting not in valid_settings:
            await ctx.send(f"Invalid setting. Valid settings are: {', '.join(valid_settings)}")
            return

        async with self.config.member(ctx.author).notification_settings() as settings:
            settings[setting] = not settings.get(setting, True)
            state = "enabled" if settings[setting] else "disabled"

        await ctx.send(f"Notifications for {setting} have been {state}.")

    @commands.command()
    async def notificationsettings(self, ctx: commands.Context):
        """View your current notification settings."""
        settings = await self.config.member(ctx.author).notification_settings()
        embed = discord.Embed(title="Your Notification Settings", color=discord.Color.blue())
        
        for setting, value in settings.items():
            embed.add_field(name=setting.replace('_', ' ').title(), value="Enabled" if value else "Disabled", inline=True)
        
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        """Handle deletion of auction channels."""
        if isinstance(channel, discord.TextChannel):
            guild = channel.guild
            auction_channel_id = await self.config.guild(guild).auction_channel()
            if channel.id == auction_channel_id:
                await self.config.guild(guild).auction_channel.set(None)
                log.info(f"Auction channel {channel.name} ({channel.id}) was deleted in guild {guild.name} ({guild.id}).")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        """Handle deletion of auction-related roles."""
        guild = role.guild
        auction_role_id = await self.config.guild(guild).auction_role()
        if role.id == auction_role_id:
            await self.config.guild(guild).auction_role.set(None)
            log.info(f"Auction role {role.name} ({role.id}) was deleted in guild {guild.name} ({guild.id}).")

    async def create_auction_bundle(self, ctx: commands.Context, *items: str):
        """Create an auction bundle from multiple items."""
        if len(items) < 2:
            await ctx.send("You need to specify at least two items to create a bundle.")
            return

        bundle_items = []
        for item in items:
            name, amount = item.split(':')
            bundle_items.append({"name": name.strip(), "amount": int(amount)})

        bundle_name = f"Bundle: {', '.join(item['name'] for item in bundle_items)}"
        bundle_value = sum(await self.get_item_value(item['name']) * item['amount'] for item in bundle_items)

        auction_data = {
            "auction_id": await self.get_next_auction_id(ctx.guild),
            "user_id": ctx.author.id,
            "items": bundle_items,
            "min_bid": int(bundle_value * 0.8),  # Set minimum bid to 80% of total value
            "category": "Bundle",
            "status": "pending",
            "total_value": bundle_value,
            "current_bid": 0,
            "current_bidder": None,
            "bid_history": [],
            "start_time": None,
            "end_time": None,
            "proxy_bids": {},
        }

        await self.create_auction_thread(ctx.guild, auction_data, ctx.author)
        await ctx.send(f"Bundle auction created: {bundle_name}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def generateauctionreport(self, ctx: commands.Context, days: int = 30):
        """Generate a comprehensive auction report for the specified number of days."""
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
        total_unique_bidders = len(set(bid['user_id'] for a in relevant_auctions for bid in a['bid_history']))
        total_unique_sellers = len(set(a['user_id'] for a in relevant_auctions))

        category_stats = defaultdict(lambda: {"count": 0, "value": 0})
        user_stats = defaultdict(lambda: {"sold": 0, "bought": 0})
        item_stats = defaultdict(lambda: {"count": 0, "total_value": 0})

        for auction in relevant_auctions:
            category_stats[auction['category']]["count"] += 1
            category_stats[auction['category']]["value"] += auction['current_bid']
            user_stats[auction['user_id']]["sold"] += auction['current_bid']
            if auction['current_bidder']:
                user_stats[auction['current_bidder']]["bought"] += auction['current_bid']
            for item in auction['items']:
                item_stats[item['name']]["count"] += item['amount']
                item_stats[item['name']]["total_value"] += auction['current_bid'] * (item['amount'] / sum(i['amount'] for i in auction['items']))

        embed = discord.Embed(title=f"Comprehensive Auction Report (Last {days} Days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=len(relevant_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"${total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"${avg_value:,.2f}", inline=True)
        embed.add_field(name="Total Unique Bidders", value=str(total_unique_bidders), inline=True)
        embed.add_field(name="Total Unique Sellers", value=str(total_unique_sellers), inline=True)
        
        most_valuable_items = ', '.join(f"{item['amount']}x {item['name']}" for item in most_valuable['items'])
        embed.add_field(name="Most Valuable Auction", value=f"${most_valuable['current_bid']:,} ({most_valuable_items})", inline=False)
        
        most_bids_items = ', '.join(f"{item['amount']}x {item['name']}" for item in most_bids['items'])
        embed.add_field(name="Most Bids", value=f"{len(most_bids['bid_history'])} bids ({most_bids_items})", inline=False)

        await ctx.send(embed=embed)

        # Generate and send additional charts
        category_chart = await self.create_category_performance_chart(category_stats)
        user_chart = await self.create_user_performance_chart(user_stats)
        item_chart = await self.create_item_popularity_chart(item_stats)
        
        await ctx.send(files=[category_chart, user_chart, item_chart])

    async def create_user_performance_chart(self, user_stats: Dict[int, Dict[str, int]]) -> discord.File:
        top_users = sorted(user_stats.items(), key=lambda x: x[1]['sold'] + x[1]['bought'], reverse=True)[:10]
        users = [str(uid) for uid, _ in top_users]
        sold_values = [stats['sold'] for _, stats in top_users]
        bought_values = [stats['bought'] for _, stats in top_users]

        plt.figure(figsize=(12, 6))
        plt.bar(users, sold_values, label='Sold')
        plt.bar(users, bought_values, bottom=sold_values, label='Bought')
        plt.title("Top 10 Users by Auction Performance")
        plt.xlabel("User ID")
        plt.ylabel("Value")
        plt.legend()
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="user_performance.png")

    async def create_item_popularity_chart(self, item_stats: Dict[str, Dict[str, int]]) -> discord.File:
        top_items = sorted(item_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
        items = [item for item, _ in top_items]
        counts = [stats['count'] for _, stats in top_items]

        plt.figure(figsize=(12, 6))
        plt.bar(items, counts)
        plt.title("Top 10 Most Popular Auction Items")
        plt.xlabel("Item")
        plt.ylabel("Count")
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="item_popularity.png")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionanalytics(self, ctx: commands.Context):
        """Display advanced analytics for the auction system."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("No auction history available for analysis.")
                return

            total_auctions = len(history)
            total_value = sum(a['current_bid'] for a in history)
            avg_value = total_value / total_auctions
            median_value = sorted(a['current_bid'] for a in history)[total_auctions // 2]
            total_unique_bidders = len(set(bid['user_id'] for a in history for bid in a['bid_history']))
            total_unique_sellers = len(set(a['user_id'] for a in history))
            avg_bids_per_auction = sum(len(a['bid_history']) for a in history) / total_auctions

            category_performance = defaultdict(lambda: {"count": 0, "value": 0})
            for auction in history:
                category_performance[auction['category']]["count"] += 1
                category_performance[auction['category']]["value"] += auction['current_bid']

            top_categories = sorted(category_performance.items(), key=lambda x: x[1]["value"], reverse=True)[:5]

            embed = discord.Embed(title="Advanced Auction Analytics", color=discord.Color.blue())
            embed.add_field(name="Total Auctions", value=str(total_auctions), inline=True)
            embed.add_field(name="Total Value", value=f"${total_value:,}", inline=True)
            embed.add_field(name="Average Value", value=f"${avg_value:,.2f}", inline=True)
            embed.add_field(name="Median Value", value=f"${median_value:,}", inline=True)
            embed.add_field(name="Unique Bidders", value=str(total_unique_bidders), inline=True)
            embed.add_field(name="Unique Sellers", value=str(total_unique_sellers), inline=True)
            embed.add_field(name="Avg. Bids per Auction", value=f"{avg_bids_per_auction:.2f}", inline=True)

            top_categories_str = "\n".join(f"{cat}: {stats['count']} auctions, ${stats['value']:,} total value" for cat, stats in top_categories)
            embed.add_field(name="Top 5 Categories", value=top_categories_str, inline=False)

            await ctx.send(embed=embed)

            # Generate and send analytics charts
            time_series_chart = await self.create_auction_time_series_chart(history)
            category_distribution_chart = await self.create_category_distribution_chart(category_performance)
            await ctx.send(files=[time_series_chart, category_distribution_chart])

    async def create_auction_time_series_chart(self, history: List[Dict[str, Any]]) -> discord.File:
        dates = [datetime.fromtimestamp(a['end_time']) for a in history]
        values = [a['current_bid'] for a in history]

        plt.figure(figsize=(12, 6))
        plt.plot(dates, values, marker='o')
        plt.title("Auction Value Over Time")
        plt.xlabel("Date")
        plt.ylabel("Value")
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="auction_time_series.png")

    async def create_category_distribution_chart(self, category_performance: Dict[str, Dict[str, int]]) -> discord.File:
        categories = list(category_performance.keys())
        values = [stats['value'] for stats in category_performance.values()]

        plt.figure(figsize=(10, 10))
        plt.pie(values, labels=categories, autopct='%1.1f%%', startangle=90)
        plt.title("Category Distribution by Total Value")
        plt.axis('equal')
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="category_distribution.png")

    async def dynamic_pricing(self, item_name: str) -> int:
        """Calculate a dynamic price for an item based on recent auction history."""
        guild = self.bot.guilds[0]  # Assuming the cog is used in a single guild
        async with self.config.guild(guild).auction_history() as history:
            relevant_auctions = [
                a for a in history 
                if any(item['name'] == item_name for item in a['items']) 
                and (datetime.utcnow().timestamp() - a['end_time']) <= 30 * 24 * 3600  # Last 30 days
            ]

        if not relevant_auctions:
            return await self.get_item_value(item_name)

        prices = [a['current_bid'] / sum(item['amount'] for item in a['items'] if item['name'] == item_name) for a in relevant_auctions]
        avg_price = sum(prices) / len(prices)
        
        # Apply a small random factor to add some variability
        dynamic_price = int(avg_price * (1 + (random.random() - 0.5) * 0.1))
        
        return max(dynamic_price, 1)  # Ensure the price is at least 1

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setreputationtiers(self, ctx: commands.Context):
        """Set reputation tiers for auction participation."""
        await ctx.send("Please enter reputation tiers in the format: 'tier_name:min_reputation:max_auction_value', one per line. Type 'done' when finished.")

        tiers = {}
        while True:
            try:
                response = await self.bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
            except asyncio.TimeoutError:
                await ctx.send("Reputation tier setup timed out.")
                return

            if response.content.lower() == 'done':
                break

            try:
                tier_name, min_reputation, max_auction_value = response.content.split(':')
                tiers[tier_name] = {
                    "min_reputation": int(min_reputation),
                    "max_auction_value": int(max_auction_value)
                }
            except ValueError:
                await ctx.send("Invalid format. Please use 'tier_name:min_reputation:max_auction_value'.")

        await self.config.guild(ctx.guild).reputation_tiers.set(tiers)
        await ctx.send("Reputation tiers have been updated.")

    async def check_reputation_tier(self, member: discord.Member, auction_value: int) -> bool:
        """Check if a member's reputation allows them to participate in an auction."""
        reputation = await self.config.member(member).reputation_score()
        tiers = await self.config.guild(member.guild).reputation_tiers()

        for tier_info in sorted(tiers.values(), key=lambda x: x['min_reputation'], reverse=True):
            if reputation >= tier_info['min_reputation']:
                return auction_value <= tier_info['max_auction_value']

        return False  # If no tier matches, user cannot participate

    @commands.command()
    async def myreputationtier(self, ctx: commands.Context):
        """Display your current reputation tier and auction limits."""
        reputation = await self.config.member(ctx.author).reputation_score()
        tiers = await self.config.guild(ctx.guild).reputation_tiers()

        user_tier = None
        for tier_name, tier_info in sorted(tiers.items(), key=lambda x: x[1]['min_reputation'], reverse=True):
            if reputation >= tier_info['min_reputation']:
                user_tier = tier_name
                break

        if user_tier:
            await ctx.send(f"Your current reputation tier is: {user_tier}\n"
                           f"You can participate in auctions up to ${tiers[user_tier]['max_auction_value']:,} in value.")
        else:
            await ctx.send("You don't meet the minimum reputation for any tier.")

    @commands.Cog.listener()
    async def on_auction_start(self, guild: discord.Guild, auction: Dict[str, Any]):
        """Event listener for when an auction starts."""
        log_channel_id = await self.config.guild(guild).log_channel()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(title="Auction Started", color=discord.Color.green())
                embed.add_field(name="Auction ID", value=auction['auction_id'], inline=True)
                embed.add_field(name="Seller", value=f"<@{auction['user_id']}>", inline=True)
                embed.add_field(name="Starting Bid", value=f"${auction['min_bid']:,}", inline=True)
                items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
                embed.add_field(name="Items", value=items_str, inline=False)
                await log_channel.send(embed=embed)

        await self.send_auction_notification(guild, auction, 'auction_start')

    @commands.Cog.listener()
    async def on_auction_end(self, guild: discord.Guild, auction: Dict[str, Any]):
        """Event listener for when an auction ends."""
        log_channel_id = await self.config.guild(guild).log_channel()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(title="Auction Ended", color=discord.Color.red())
                embed.add_field(name="Auction ID", value=auction['auction_id'], inline=True)
                embed.add_field(name="Seller", value=f"<@{auction['user_id']}>", inline=True)
                embed.add_field(name="Winner", value=f"<@{auction['current_bidder']}>" if auction['current_bidder'] else "No winner", inline=True)
                embed.add_field(name="Final Bid", value=f"${auction['current_bid']:,}", inline=True)
                items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
                embed.add_field(name="Items", value=items_str, inline=False)
                await log_channel.send(embed=embed)

        await self.send_auction_notification(guild, auction, 'auction_end')

    async def send_auction_notification(self, guild: discord.Guild, auction: Dict[str, Any], notification_type: str):
        """Send notifications to subscribed users."""
        async with self.config.all_members(guild)() as all_members:
            for member_id, member_data in all_members.items():
                if auction['category'] in member_data.get('subscribed_categories', []):
                    if member_data['notification_settings'].get(notification_type, True):
                        member = guild.get_member(member_id)
                        if member:
                            try:
                                items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
                                if notification_type == 'auction_start':
                                    await member.send(f"New auction started in your subscribed category '{auction['category']}': {items_str}")
                                elif notification_type == 'auction_end':
                                    await member.send(f"Auction ended in your subscribed category '{auction['category']}': {items_str}")
                            except discord.HTTPException:
                                pass  # Unable to send DM to the user

    @commands.command()
    async def auctionreminder(self, ctx: commands.Context, auction_id: str, minutes: int):
        """Set a reminder for an auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await ctx.send("Invalid auction ID.")
                return

            if auction['status'] != 'active':
                await ctx.send("Reminders can only be set for active auctions.")
                return

            reminder_time = auction['end_time'] - (minutes * 60)
            current_time = datetime.utcnow().timestamp()

            if reminder_time <= current_time:
                await ctx.send("The specified reminder time has already passed.")
                return

            async with self.config.member(ctx.author).auction_reminders() as reminders:
                reminders.append({
                    'auction_id': auction_id,
                    'reminder_time': reminder_time
                })

            await ctx.send(f"Reminder set for {minutes} minutes before the auction ends.")

    @tasks.loop(minutes=1)
    async def check_auction_reminders(self):
        current_time = datetime.utcnow().timestamp()
        for guild in self.bot.guilds:
            async with self.config.all_members(guild)() as all_members:
                for member_id, member_data in all_members.items():
                    reminders = member_data.get('auction_reminders', [])
                    for reminder in reminders:
                        if reminder['reminder_time'] <= current_time:
                            await self.send_auction_reminder(guild, member_id, reminder['auction_id'])
                            reminders.remove(reminder)
                    member_data['auction_reminders'] = reminders

    async def send_auction_reminder(self, guild: discord.Guild, member_id: int, auction_id: str):
        member = guild.get_member(member_id)
        if not member:
            return

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

        try:
            items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
            await member.send(f"Reminder: The auction for {items_str} is ending soon! Current bid: ${auction['current_bid']:,}")
        except discord.HTTPException:
            pass  # Unable to send DM to the user

    @commands.command()
    async def auctionstatistics(self, ctx: commands.Context):
        """Display overall auction statistics."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("No auction history available.")
                return

            total_auctions = len(history)
            total_value = sum(a['current_bid'] for a in history)
            avg_value = total_value / total_auctions
            unique_sellers = len(set(a['user_id'] for a in history))
            unique_buyers = len(set(a['current_bidder'] for a in history if a['current_bidder']))
            most_expensive = max(history, key=lambda a: a['current_bid'])

            embed = discord.Embed(title="Auction Statistics", color=discord.Color.gold())
            embed.add_field(name="Total Auctions", value=str(total_auctions), inline=True)
            embed.add_field(name="Total Value", value=f"${total_value:,}", inline=True)
            embed.add_field(name="Average Value", value=f"${avg_value:,.2f}", inline=True)
            embed.add_field(name="Unique Sellers", value=str(unique_sellers), inline=True)
            embed.add_field(name="Unique Buyers", value=str(unique_buyers), inline=True)
            
            most_expensive_items = ", ".join(f"{item['amount']}x {item['name']}" for item in most_expensive['items'])
            embed.add_field(name="Most Expensive Auction", value=f"${most_expensive['current_bid']:,} for {most_expensive_items}", inline=False)

            await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionfees(self, ctx: commands.Context, listing_fee: int, success_fee: float):
        """Set auction fees."""
        if listing_fee < 0 or success_fee < 0 or success_fee > 100:
            await ctx.send("Invalid fees. Listing fee must be non-negative, and success fee must be between 0 and 100.")
            return

        await self.config.guild(ctx.guild).auction_fees.set({
            "listing_fee": listing_fee,
            "success_fee": success_fee / 100
        })
        await ctx.send(f"Auction fees set. Listing fee: ${listing_fee:,}, Success fee: {success_fee}%")

    async def apply_auction_fees(self, guild: discord.Guild, auction: Dict[str, Any]):
        """Apply auction fees to the seller."""
        fees = await self.config.guild(guild).auction_fees()
        listing_fee = fees["listing_fee"]
        success_fee = int(auction['current_bid'] * fees["success_fee"])

        seller = guild.get_member(auction['user_id'])
        if seller:
            await bank.withdraw_credits(seller, listing_fee + success_fee)
            await seller.send(f"Auction fees for auction #{auction['auction_id']}:\n"
                              f"Listing fee: ${listing_fee:,}\n"
                              f"Success fee: ${success_fee:,}\n"
                              f"Total fees: ${listing_fee + success_fee:,}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionlimits(self, ctx: commands.Context, max_active: int, cooldown_hours: int):
        """Set limits on user auction activities."""
        if max_active < 1 or cooldown_hours < 0:
            await ctx.send("Invalid limits. Maximum active auctions must be at least 1, and cooldown must be non-negative.")
            return

        await self.config.guild(ctx.guild).auction_limits.set({
            "max_active": max_active,
            "cooldown": cooldown_hours * 3600
        })
        await ctx.send(f"Auction limits set. Max active auctions: {max_active}, Cooldown between auctions: {cooldown_hours} hours")

    async def check_auction_limits(self, guild: discord.Guild, user_id: int) -> bool:
        """Check if a user can create a new auction based on set limits."""
        limits = await self.config.guild(guild).auction_limits()
        async with self.config.guild(guild).auctions() as auctions:
            user_active_auctions = sum(1 for a in auctions.values() if a['user_id'] == user_id and a['status'] == 'active')
            if user_active_auctions >= limits["max_active"]:
                return False

        async with self.config.guild(guild).auction_history() as history:
            user_history = [a for a in history if a['user_id'] == user_id]
            if user_history:
                last_auction_time = max(a['end_time'] for a in user_history)
                if (datetime.utcnow().timestamp() - last_auction_time) < limits["cooldown"]:
                    return False

        return True

    @commands.command()
    async def auctionrules(self, ctx: commands.Context):
        """Display the current auction rules and limits."""
        guild = ctx.guild
        fees = await self.config.guild(guild).auction_fees()
        limits = await self.config.guild(guild).auction_limits()
        
        embed = discord.Embed(title="Auction Rules and Limits", color=discord.Color.blue())
        embed.add_field(name="Listing Fee", value=f"${fees['listing_fee']:,}", inline=True)
        embed.add_field(name="Success Fee", value=f"{fees['success_fee']*100}%", inline=True)
        embed.add_field(name="Max Active Auctions per User", value=str(limits['max_active']), inline=True)
        embed.add_field(name="Cooldown Between Auctions", value=f"{limits['cooldown'] // 3600} hours", inline=True)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def toggleauctioninsurance(self, ctx: commands.Context):
        """Toggle the auction insurance feature."""
        current_state = await self.config.guild(ctx.guild).auction_insurance_enabled()
        new_state = not current_state
        await self.config.guild(ctx.guild).auction_insurance_enabled.set(new_state)
        await ctx.send(f"Auction insurance has been {'enabled' if new_state else 'disabled'}.")

    @commands.command()
    async def buyauctioninsurance(self, ctx: commands.Context, auction_id: str):
        """Buy insurance for a specific auction."""
        guild = ctx.guild
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['user_id'] != ctx.author.id:
                await ctx.send("Invalid auction ID or you're not the owner of this auction.")
                return

            if auction.get('insurance_bought', False):
                await ctx.send("Insurance has already been bought for this auction.")
                return

            insurance_enabled = await self.config.guild(guild).auction_insurance_enabled()
            if not insurance_enabled:
                await ctx.send("Auction insurance is not enabled on this server.")
                return

            insurance_rate = await self.config.guild(guild).auction_insurance_rate()
            insurance_cost = int(auction['min_bid'] * insurance_rate)

            if not await bank.can_spend(ctx.author, insurance_cost):
                await ctx.send(f"You don't have enough funds to buy insurance. Cost: ${insurance_cost:,}")
                return

            await bank.withdraw_credits(ctx.author, insurance_cost)
            auction['insurance_bought'] = True
            auctions[auction_id] = auction

        await ctx.send(f"You've successfully bought insurance for auction #{auction_id}. Cost: ${insurance_cost:,}")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionpingroles(self, ctx: commands.Context, regular: discord.Role, massive: discord.Role):
        """Set roles to be pinged for regular and massive auctions."""
        await self.config.guild(ctx.guild).auction_ping_role.set(regular.id)
        await self.config.guild(ctx.guild).massive_auction_ping_role.set(massive.id)
        await ctx.send(f"Auction ping roles set. Regular: {regular.name}, Massive: {massive.name}")

    async def ping_auction_roles(self, guild: discord.Guild, auction: Dict[str, Any]):
        """Ping appropriate roles when a new auction starts."""
        channel_id = await self.config.guild(guild).auction_channel()
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

        await self.create_auction_thread(ctx.guild, auction_data, ctx.author)
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
        # This part would depend on how you structure your templates
        # Here's a basic example:
        lines = formatted_template.split('\n')
        auction_data = {}
        for line in lines:
            key, value = line.split(':')
            auction_data[key.strip()] = value.strip()

        # Convert the parsed data into the format expected by create_auction_thread
        # This is a simplified version and may need to be adjusted based on your actual implementation
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

        await self.create_auction_thread(ctx.guild, formatted_auction_data, ctx.author)
        await ctx.send("Auction created using the template.")

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

    async def create_category_performance_chart(self, category_performance: Dict[str, Dict[str, int]]) -> discord.File:
        categories = list(category_performance.keys())
        values = [stats['value'] for stats in category_performance.values()]
        
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
    async def auctioninsights(self, ctx: commands.Context, days: int = 30):
        """Display insights about your personal auction activity."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            now = datetime.utcnow().timestamp()
            user_auctions = [a for a in history if (a['user_id'] == ctx.author.id or a['current_bidder'] == ctx.author.id) and now - a['end_time'] <= days * 86400]

        if not user_auctions:
            await ctx.send(f"No auction activity found for you in the last {days} days.")
            return

        sold_auctions = [a for a in user_auctions if a['user_id'] == ctx.author.id]
        won_auctions = [a for a in user_auctions if a['current_bidder'] == ctx.author.id]

        total_sold = sum(a['current_bid'] for a in sold_auctions)
        total_bought = sum(a['current_bid'] for a in won_auctions)
        net_profit = total_sold - total_bought

        embed = discord.Embed(title=f"Your Auction Insights (Last {days} Days)", color=discord.Color.blue())
        embed.add_field(name="Auctions Sold", value=str(len(sold_auctions)), inline=True)
        embed.add_field(name="Auctions Won", value=str(len(won_auctions)), inline=True)
        embed.add_field(name="Total Value Sold", value=f"${total_sold:,}", inline=True)
        embed.add_field(name="Total Value Bought", value=f"${total_bought:,}", inline=True)
        embed.add_field(name="Net Profit", value=f"${net_profit:,}", inline=True)

        if sold_auctions:
            most_valuable_sold = max(sold_auctions, key=lambda a: a['current_bid'])
            items_str = ', '.join(f"{item['amount']}x {item['name']}" for item in most_valuable_sold['items'])
            value_str = f"${most_valuable_sold['current_bid']:,} for {items_str}"
            embed.add_field(name="Most Valuable Auction Sold", value=value_str, inline=False)

        if won_auctions:
            most_valuable_won = max(won_auctions, key=lambda a: a['current_bid'])
            items_str = ', '.join(f"{item['amount']}x {item['name']}" for item in most_valuable_won['items'])
            value_str = f"${most_valuable_won['current_bid']:,} for {items_str}"
            embed.add_field(name="Most Valuable Auction Won", value=value_str, inline=False)

        # Generate and send personal auction activity chart
        activity_chart = await self.create_personal_activity_chart(user_auctions, ctx.author.id)
        await ctx.send(file=activity_chart)

    async def create_personal_activity_chart(self, auctions: List[Dict[str, Any]], user_id: int) -> discord.File:
        plt.figure(figsize=(12, 6))
        
        dates = [datetime.fromtimestamp(a['end_time']) for a in auctions]
        sold_values = [a['current_bid'] if a['user_id'] == user_id else 0 for a in auctions]
        bought_values = [a['current_bid'] if a['current_bidder'] == user_id else 0 for a in auctions]

        plt.plot(dates, sold_values, label='Sold', color='green', marker='o')
        plt.plot(dates, bought_values, label='Bought', color='red', marker='o')

        plt.title("Personal Auction Activity Over Time")
        plt.xlabel("Date")
        plt.ylabel("Value")
        plt.legend()
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="personal_auction_activity.png")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionleaderboard(self, ctx: commands.Context, category: str = "all", timeframe: str = "all"):
        """Display the auction leaderboard."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if timeframe != "all":
                days = {"week": 7, "month": 30, "year": 365}.get(timeframe.lower(), 30)
                cutoff_time = datetime.utcnow().timestamp() - (days * 86400)
                history = [a for a in history if a['end_time'] >= cutoff_time]

            if category != "all":
                history = [a for a in history if a['category'].lower() == category.lower()]

            if not history:
                await ctx.send("No auction data available for the specified criteria.")
                return

            seller_stats = defaultdict(lambda: {"total_value": 0, "auctions_count": 0})
            buyer_stats = defaultdict(lambda: {"total_value": 0, "auctions_count": 0})

            for auction in history:
                if auction['status'] == 'completed':
                    seller_stats[auction['user_id']]["total_value"] += auction['current_bid']
                    seller_stats[auction['user_id']]["auctions_count"] += 1
                    if auction['current_bidder']:
                        buyer_stats[auction['current_bidder']]["total_value"] += auction['current_bid']
                        buyer_stats[auction['current_bidder']]["auctions_count"] += 1

            top_sellers = sorted(seller_stats.items(), key=lambda x: x[1]["total_value"], reverse=True)[:10]
            top_buyers = sorted(buyer_stats.items(), key=lambda x: x[1]["total_value"], reverse=True)[:10]

            embed = discord.Embed(title=f"Auction Leaderboard ({category.capitalize()} - {timeframe.capitalize()})", color=discord.Color.gold())

            seller_board = "\n".join(f"{idx+1}. <@{user_id}>: ${stats['total_value']:,} ({stats['auctions_count']} auctions)" for idx, (user_id, stats) in enumerate(top_sellers))
            embed.add_field(name="Top Sellers", value=seller_board or "No data", inline=False)

            buyer_board = "\n".join(f"{idx+1}. <@{user_id}>: ${stats['total_value']:,} ({stats['auctions_count']} auctions)" for idx, (user_id, stats) in enumerate(top_buyers))
            embed.add_field(name="Top Buyers", value=buyer_board or "No data", inline=False)

            await ctx.send(embed=embed)

    @commands.command()
    async def auctionhistogram(self, ctx: commands.Context, bin_size: int = 1000000):
        """Display a histogram of auction values."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("No auction history available.")
                return

            values = [a['current_bid'] for a in history if a['status'] == 'completed']

            plt.figure(figsize=(12, 6))
            plt.hist(values, bins=range(0, max(values) + bin_size, bin_size))
            plt.title("Histogram of Auction Values")
            plt.xlabel("Auction Value")
            plt.ylabel("Number of Auctions")
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            
            await ctx.send(file=discord.File(buf, filename="auction_histogram.png"))

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionheatmap(self, ctx: commands.Context):
        """Display a heatmap of auction activity."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("No auction history available.")
                return

            # Create a 2D array to represent the heatmap (7 days x 24 hours)
            heatmap = [[0 for _ in range(24)] for _ in range(7)]

            for auction in history:
                start_time = datetime.fromtimestamp(auction['start_time'])
                day = start_time.weekday()
                hour = start_time.hour
                heatmap[day][hour] += 1

            plt.figure(figsize=(12, 8))
            sns.heatmap(heatmap, annot=True, fmt="d", cmap="YlOrRd")
            plt.title("Auction Activity Heatmap")
            plt.xlabel("Hour of Day")
            plt.ylabel("Day of Week")
            plt.yticks(range(7), ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            
            await ctx.send(file=discord.File(buf, filename="auction_heatmap.png"))

    @commands.command()
    async def auctionitemtrend(self, ctx: commands.Context, item_name: str):
        """Display the price trend of a specific item over time."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            relevant_auctions = [a for a in history if any(item['name'].lower() == item_name.lower() for item in a['items']) and a['status'] == 'completed']

        if not relevant_auctions:
            await ctx.send(f"No auction history found for item: {item_name}")
            return

        dates = [datetime.fromtimestamp(a['end_time']) for a in relevant_auctions]
        prices = [a['current_bid'] / sum(item['amount'] for item in a['items'] if item['name'].lower() == item_name.lower()) for a in relevant_auctions]

        plt.figure(figsize=(12, 6))
        plt.plot(dates, prices, marker='o')
        plt.title(f"Price Trend for {item_name}")
        plt.xlabel("Date")
        plt.ylabel("Price per Unit")
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        
        await ctx.send(file=discord.File(buf, filename=f"{item_name}_trend.png"))

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionanalysis(self, ctx: commands.Context):
        """Perform a comprehensive analysis of auction data."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            if not history:
                await ctx.send("No auction history available for analysis.")
                return

            total_auctions = len(history)
            completed_auctions = [a for a in history if a['status'] == 'completed']
            total_value = sum(a['current_bid'] for a in completed_auctions)
            avg_value = total_value / len(completed_auctions) if completed_auctions else 0
            
            category_performance = defaultdict(lambda: {"count": 0, "value": 0})
            item_popularity = defaultdict(int)
            user_participation = defaultdict(int)

            for auction in history:
                category_performance[auction['category']]["count"] += 1
                if auction['status'] == 'completed':
                    category_performance[auction['category']]["value"] += auction['current_bid']
                
                for item in auction['items']:
                    item_popularity[item['name']] += item['amount']
                
                user_participation[auction['user_id']] += 1
                if auction['current_bidder']:
                    user_participation[auction['current_bidder']] += 1

            embed = discord.Embed(title="Comprehensive Auction Analysis", color=discord.Color.blue())
            embed.add_field(name="Total Auctions", value=str(total_auctions), inline=True)
            embed.add_field(name="Total Value", value=f"${total_value:,}", inline=True)
            embed.add_field(name="Average Value", value=f"${avg_value:,.2f}", inline=True)

            top_categories = sorted(category_performance.items(), key=lambda x: x[1]["value"], reverse=True)[:5]
            category_str = "\n".join(f"{cat}: {stats['count']} auctions, ${stats['value']:,} total value" for cat, stats in top_categories)
            embed.add_field(name="Top 5 Categories", value=category_str, inline=False)

            top_items = sorted(item_popularity.items(), key=lambda x: x[1], reverse=True)[:5]
            items_str = "\n".join(f"{item}: {count}" for item, count in top_items)
            embed.add_field(name="Top 5 Items", value=items_str, inline=False)

            top_users = sorted(user_participation.items(), key=lambda x: x[1], reverse=True)[:5]
            users_str = "\n".join(f"<@{user_id}>: {count} participations" for user_id, count in top_users)
            embed.add_field(name="Top 5 Users", value=users_str, inline=False)

            await ctx.send(embed=embed)

            # Generate and send additional charts
            value_trend_chart = await self.create_value_trend_chart(history)
            category_pie_chart = await self.create_category_pie_chart(category_performance)
            await ctx.send(files=[value_trend_chart, category_pie_chart])

    async def create_value_trend_chart(self, history: List[Dict[str, Any]]) -> discord.File:
        completed_auctions = sorted([a for a in history if a['status'] == 'completed'], key=lambda x: x['end_time'])
        dates = [datetime.fromtimestamp(a['end_time']) for a in completed_auctions]
        values = [a['current_bid'] for a in completed_auctions]

        plt.figure(figsize=(12, 6))
        plt.plot(dates, values, marker='o')
        plt.title("Auction Value Trend Over Time")
        plt.xlabel("Date")
        plt.ylabel("Auction Value")
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="auction_value_trend.png")

    async def create_category_pie_chart(self, category_performance: Dict[str, Dict[str, int]]) -> discord.File:
        categories = list(category_performance.keys())
        values = [stats['value'] for stats in category_performance.values()]

        plt.figure(figsize=(10, 10))
        plt.pie(values, labels=categories, autopct='%1.1f%%', startangle=90)
        plt.title("Category Distribution by Total Value")
        plt.axis('equal')

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return discord.File(buf, filename="category_distribution.png")

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

async def setup(bot):
    """Setup function to add the cog to the bot."""
    cog = AdvancedAuctionSystem(bot)
    await bot.add_cog(cog)
    await cog.initialize()