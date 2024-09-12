import discord
from discord.ext import commands, tasks
from redbot.core import Config, checks, bank
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
import math
import re
from collections import defaultdict
import aiohttp

log = logging.getLogger("red.economy.SuperEnhancedAdvancedAuction")

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
        self.item_popularity[auction['item']] += 1
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

class SuperEnhancedAdvancedAuction(commands.Cog):
    def __init__(self, bot):
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
            "categories": ["General", "Rare", "Limited Edition", "Event"],
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
        self.queue_lock = asyncio.Lock()
        self.analytics = AuctionAnalytics()
        self.visualization = AuctionVisualization()
        self.api_cache = {}
        self.api_cache_time = {}

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
            message = await thread.send("New auction started!", embed=embed)
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
        """Configure the super enhanced auction system."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @auctionset.command(name="auctionchannels")
    async def set_auction_channels(self, ctx: commands.Context, auction: discord.TextChannel, queue: discord.TextChannel, log: discord.TextChannel):
        """Set the channels for auctions, queue, and logging."""
        await self.config.guild(ctx.guild).auction_channel.set(auction.id)
        await self.config.guild(ctx.guild).queue_channel.set(queue.id)
        await self.config.guild(ctx.guild).log_channel.set(log.id)
        await ctx.send(f"Auction channel set to {auction.mention}, queue channel set to {queue.mention}, and log channel set to {log.mention}.")

    @auctionset.command(name="auctionrole")
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
    async def spawnauction(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Spawn the auction request embed with button in the specified channel or the current channel."""
        channel = channel or ctx.channel
        view = self.AuctionView(self)
        embed = discord.Embed(
            title="🎉 Request an Advanced Auction 🎉",
            description="Click the button below to request an auction and submit your donation details.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="How it works", value="1. Click the button below.\n2. Fill out the modal with donation details.\n3. A new thread will be created for your auction.", inline=False)
        embed.add_field(name="New Features", value="• Multi-item auctions\n• Reserve prices\n• Proxy bidding\n• Auction bundles", inline=False)
        embed.set_footer(text="Thank you for contributing to our community!")
        message = await channel.send(embed=embed, view=view)
        view.message = message
        await ctx.send(f"Advanced auction request embed spawned in {channel.mention}")

    class AuctionView(discord.ui.View):
        def __init__(self, cog: "SuperEnhancedAdvancedAuction"):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Request Auction", style=discord.ButtonStyle.green)
        async def request_auction_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                async with self.cog.config.guild(interaction.guild).banned_users() as banned_users:
                    if interaction.user.id in banned_users:
                        await interaction.response.send_message("You are banned from participating in auctions.", ephemeral=True)
                        return

                modal = self.cog.AdvancedAuctionModal(self.cog)
                await interaction.response.send_modal(modal)
            except Exception as e:
                log.error(f"An error occurred while sending the modal: {e}")
                await interaction.followup.send(f"An error occurred while sending the modal: {str(e)}", ephemeral=True)

    class AdvancedAuctionModal(discord.ui.Modal, title="Request An Advanced Auction"):
        def __init__(self, cog: "SuperEnhancedAdvancedAuction"):
            super().__init__()
            self.cog = cog

        items = discord.ui.TextInput(label="Items (name:amount, separate with ;)", placeholder="e.g., Blob:5;Pepe Trophy:1", required=True)
        minimum_bid = discord.ui.TextInput(label="Minimum Bid", placeholder="e.g., 1,000,000", required=True)
        reserve_price = discord.ui.TextInput(label="Reserve Price (optional)", placeholder="e.g., 5,000,000", required=False)
        buy_out_price = discord.ui.TextInput(label="Buy-out Price (optional)", placeholder="e.g., 10,000,000", required=False)
        category = discord.ui.TextInput(label="Category", placeholder="e.g., Rare", required=True)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                items = [item.strip().split(':') for item in self.items.value.split(';')]
                items = [{"name": item[0], "amount": int(item[1])} for item in items]
                min_bid = int(self.minimum_bid.value.replace(',', ''))
                reserve_price = int(self.reserve_price.value.replace(',', '')) if self.reserve_price.value else None
                buy_out_price = int(self.buy_out_price.value.replace(',', '')) if self.buy_out_price.value else None
                category = self.category.value

                await interaction.response.send_message("Processing your advanced auction request...", ephemeral=True)

                view = self.cog.AuctionScheduleView(self.cog)
                await interaction.followup.send("Would you like to schedule this auction?", view=view, ephemeral=True)
                await view.wait()

                await self.cog.process_advanced_auction_request(interaction, items, min_bid, reserve_price, buy_out_price, category, view.schedule_time)

            except Exception as e:
                log.error(f"An error occurred in modal submission: {e}", exc_info=True)
                await interaction.followup.send(f"An error occurred while processing your submission. Please try again or contact an administrator.", ephemeral=True)

    class AuctionScheduleView(discord.ui.View):
        def __init__(self, cog: "SuperEnhancedAdvancedAuction"):
            super().__init__(timeout=300)
            self.cog = cog
            self.schedule_time: Optional[datetime] = None

        @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
        async def schedule_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = self.ScheduleModal()
            await interaction.response.send_modal(modal)
            await modal.wait()
            self.schedule_time = modal.schedule_time
            self.stop()

        @discord.ui.button(label="No", style=discord.ButtonStyle.red)
        async def schedule_no(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message("Auction will be queued immediately.", ephemeral=True)
            self.stop()

        class ScheduleModal(discord.ui.Modal, title="Schedule Auction"):
            def __init__(self):
                super().__init__()
                self.schedule_time: Optional[datetime] = None

            time_input = discord.ui.TextInput(
                label="Enter time (YYYY-MM-DD HH:MM)",
                placeholder="e.g. 2024-09-15 14:30",
                required=True,
                style=discord.TextStyle.short
            )

            async def on_submit(self, interaction: discord.Interaction):
                try:
                    self.schedule_time = datetime.strptime(self.time_input.value, "%Y-%m-%d %H:%M")
                    if self.schedule_time < datetime.utcnow():
                        await interaction.response.send_message("Scheduled time must be in the future.", ephemeral=True)
                        self.schedule_time = None
                    else:
                        await interaction.response.send_message(f"Auction scheduled for {self.schedule_time}", ephemeral=True)
                except ValueError:
                    await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD HH:MM", ephemeral=True)
                    self.schedule_time = None

    async def process_advanced_auction_request(self, interaction: discord.Interaction, items: List[Dict[str, Union[str, int]]], min_bid: int, reserve_price: Optional[int], buy_out_price: Optional[int], category: str, schedule_time: Optional[datetime]):
        try:
            guild = interaction.guild
            total_value = 0
            for item in items:
                item_value = await self.get_item_value(item['name'])
                if item_value is None:
                    await interaction.followup.send(f"Could not fetch value for item: {item['name']}", ephemeral=True)
                    return
                total_value += item_value * item['amount']

            tax = int(total_value * 0.10)  # 10% tax

            if total_value < 50_000_000:  # 50 million
                await interaction.followup.send("The total donation value must be over 50 million.", ephemeral=True)
                return

            auction_id = await self.get_next_auction_id(guild)
            auction_data = {
                "auction_id": auction_id,
                "user_id": interaction.user.id,
                "items": items,
                "min_bid": min_bid,
                "reserve_price": reserve_price,
                "buy_out_price": buy_out_price,
                "category": category,
                "status": "pending",
                "total_value": total_value,
                "tax": tax,
                "current_bid": min_bid,
                "current_bidder": None,
                "bid_history": [],
                "start_time": None,
                "end_time": None,
                "donated_items": {item['name']: 0 for item in items},
                "donated_tax": 0,
                "proxy_bids": {},
            }

            if schedule_time:
                auction_data['scheduled_time'] = int(schedule_time.timestamp())
                auction_data['status'] = 'scheduled'
                async with self.config.guild(guild).scheduled_auctions() as scheduled:
                    scheduled[auction_id] = auction_data
                await interaction.followup.send(f"Auction scheduled for {schedule_time}")
            else:
                await self.create_auction_thread(guild, auction_data, interaction.user)
                await interaction.followup.send("Your advanced auction thread has been created.")

            # Assign the auction role
            auction_role_id = await self.config.guild(guild).auction_role()
            if auction_role_id:
                auction_role = guild.get_role(auction_role_id)
                if auction_role:
                    await interaction.user.add_roles(auction_role)

        except Exception as e:
            log.error(f"Error in process_advanced_auction_request: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while processing your auction request. Please try again or contact an administrator.", ephemeral=True)

    async def get_item_value(self, item_name: str) -> Optional[int]:
        current_time = datetime.utcnow().timestamp()
        if item_name in self.api_cache and current_time - self.api_cache_time[item_name] < 3600:  # Cache for 1 hour
            return self.api_cache[item_name]

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get("https://api.gwapes.com/items") as response:
                    if response.status != 200:
                        log.error(f"API response status: {response.status}")
                        return None
                    
                    data = await response.json()
                    items = data.get("body", [])
                    item_data = next((item for item in items if item["name"].strip().lower() == item_name.strip().lower()), None)
                    
                    if not item_data:
                        return None
                    
                    item_value = item_data.get("value", 0)
                    self.api_cache[item_name] = item_value
                    self.api_cache_time[item_name] = current_time
                    return item_value

            except aiohttp.ClientError as e:
                log.error(f"API check error: {e}", exc_info=True)
                return None

    async def create_auction_thread(self, guild: discord.Guild, auction_data: Dict[str, Any], creator: discord.Member):
        auction_channel_id = await self.config.guild(guild).auction_channel()
        auction_channel = guild.get_channel(auction_channel_id)
        
        if not auction_channel:
            log.error(f"Auction channel not found for guild {guild.id}")
            return
        
        thread = await auction_channel.create_thread(
            name=f"Auction #{auction_data['auction_id']}",
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason=f"Advanced auction request by {creator.name}"
        )
        
        await thread.add_user(creator)
        
        embed = await self.create_auction_embed(auction_data)
        await thread.send(content=creator.mention, embed=embed)
        
        # Store the thread ID in the auction data
        auction_data['thread_id'] = thread.id
        
        async with self.config.guild(guild).auctions() as auctions:
            auctions[auction_data['auction_id']] = auction_data

    async def create_auction_embed(self, auction: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(
            title=f"Advanced Auction #{auction['auction_id']}",
            description=f"Category: {auction['category']}",
            color=discord.Color.gold()
        )
        items_str = "\n".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
        embed.add_field(name="Items", value=items_str, inline=False)
        embed.add_field(name="Minimum Bid", value=f"{auction['min_bid']:,}", inline=True)
        if auction['reserve_price']:
            embed.add_field(name="Reserve Price", value=f"{auction['reserve_price']:,}", inline=True)
        if auction['buy_out_price']:
            embed.add_field(name="Buy-out Price", value=f"{auction['buy_out_price']:,}", inline=True)
        embed.add_field(name="Total Value", value=f"{auction['total_value']:,}", inline=True)
        embed.add_field(name="Tax (10%)", value=f"{auction['tax']:,}", inline=True)
        embed.add_field(name="Status", value=auction['status'].capitalize(), inline=True)
        if auction['status'] == 'active':
            embed.add_field(name="Current Bid", value=f"{auction['current_bid']:,}", inline=True)
            embed.add_field(name="Ends At", value=f"<t:{int(auction['end_time'])}:R>", inline=True)
        embed.set_footer(text=f"Auction ID: {auction['auction_id']}")
        return embed

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot and message.author.id != 270904126974590976:  # Ignore all bots except Dank Memer
            return

        if isinstance(message.channel, discord.Thread) and message.channel.parent_id == await self.config.guild(message.guild).auction_channel():
            if message.author.id == 270904126974590976:  # Dank Memer bot ID
                await self.handle_dank_memer_message(message)
            else:
                await self.handle_potential_bid(message)

    async def handle_dank_memer_message(self, message: discord.Message):
        log.info(f"Received message from Dank Memer in thread {message.channel.name}: {message.content}")

        if not message.embeds:
            log.info("No embeds in the message")
            return

        embed = message.embeds[0]
        log.info(f"Embed title: {embed.title}")
        log.info(f"Embed description: {embed.description}")

        if "Successfully donated" in embed.description:
            await self.handle_donation(message)
        elif embed.title == "Pending Confirmation":
            try:
                def check(before: discord.Message, after: discord.Message):
                    return (before.id == message.id and 
                            after.embeds and 
                            "Successfully donated" in after.embeds[0].description)

                _, edited_message = await self.bot.wait_for('message_edit', check=check, timeout=60.0)
                await self.handle_donation(edited_message)
            except asyncio.TimeoutError:
                log.info("Donation confirmation timed out")
        else:
            log.info("Not a donation message")

    async def handle_donation(self, message: discord.Message):
        guild = message.guild
        thread = message.channel
        auction_id = thread.name.split('#')[1]
        
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                log.info(f"No auction found for ID {auction_id}")
                return

            embed = message.embeds[0]
            description = embed.description
            log.info(f"Processing donation: {description}")

            try:
                parts = description.split("**")
                log.info(f"Split parts: {parts}")
            
                if len(parts) < 3:
                    raise ValueError("Unexpected donation message format")

                donation_info = parts[1].strip()
                log.info(f"Donation info: {donation_info}")

                if '⏣' in donation_info:
                    amount_str = ''.join(filter(str.isdigit, donation_info))
                    log.info(f"Parsed amount string: {amount_str}")
                
                    if not amount_str:
                        raise ValueError(f"Unable to parse amount from: {donation_info}")
                
                    donated_amount = int(amount_str)
                    is_tax_payment = True
                    auction['donated_tax'] += donated_amount
                else:
                    amount_and_item = donation_info.split(' ', 1)
                    log.info(f"Amount and item split: {amount_and_item}")
                
                    if len(amount_and_item) < 2:
                        raise ValueError(f"Unable to split amount and item from: {donation_info}")
                
                    amount_str = amount_and_item[0].replace(',', '')
                    donated_amount = int(amount_str)
                    donated_item = amount_and_item[1]
                    is_tax_payment = False
                    
                    for item in auction['items']:
                        if item['name'].lower() == donated_item.lower():
                            item['donated'] = item.get('donated', 0) + donated_amount
                            break
                    else:
                        raise ValueError(f"Donated item {donated_item} not found in auction items")

                log.info(f"Parsed donation: {donated_amount} {donated_item if not is_tax_payment else 'Tax'}")

                # Check if all items and tax have been donated
                all_donated = all(item.get('donated', 0) == item['amount'] for item in auction['items'])
                tax_paid = auction['donated_tax'] >= auction['tax']

                if all_donated and tax_paid:
                    auction['status'] = 'ready'
                    await thread.send("All items and tax have been donated. The auction is ready to be queued.")
                    await self.finalize_auction_setup(guild, auction_id)
                else:
                    remaining_items = [f"{item['amount'] - item.get('donated', 0)}x {item['name']}" for item in auction['items'] if item.get('donated', 0) < item['amount']]
                    remaining_tax = max(0, auction['tax'] - auction['donated_tax'])
                    await thread.send(f"Donation received. Remaining items to donate: {', '.join(remaining_items)}. Remaining tax: {remaining_tax:,}")

                auctions[auction_id] = auction

            except Exception as e:
                log.error(f"Error processing donation: {e}", exc_info=True)
                await thread.send(f"An error occurred while processing the donation: {str(e)}. Please contact an administrator.")

    async def finalize_auction_setup(self, guild: discord.Guild, auction_id: str):
        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                return

            auction['status'] = 'queued'
            auctions[auction_id] = auction

        await self.queue_auction(guild, auction)
        
        thread_id = auction.get('thread_id')
        if thread_id:
            thread = guild.get_thread(thread_id)
            if thread:
                await thread.send("All items and tax have been donated. Your auction has been queued.")
                await thread.edit(archived=True, locked=True)

        # Remove auction role from the user
        auction_role_id = await self.config.guild(guild).auction_role()
        if auction_role_id:
            auction_role = guild.get_role(auction_role_id)
            user = guild.get_member(auction['user_id'])
            if auction_role and user:
                await user.remove_roles(auction_role)

    async def queue_auction(self, guild: discord.Guild, auction: Dict[str, Any]):
        async with self.config.guild(guild).auction_queue() as queue:
            queue.append(auction['auction_id'])

    async def handle_potential_bid(self, message: discord.Message):
        if not self.is_valid_bid_format(message.content):
            return

        guild = message.guild
        thread = message.channel
        auction_id = thread.name.split('#')[1]

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                return

            bid_amount = self.parse_bid_amount(message.content)
            if bid_amount <= auction['current_bid']:
                await thread.send(f"Your bid must be higher than the current bid of {auction['current_bid']:,}.")
                return

            min_increment = await self.get_bid_increment(guild, auction['current_bid'])
            if bid_amount < auction['current_bid'] + min_increment:
                await thread.send(f"Your bid must be at least {min_increment:,} higher than the current bid.")
                return

            # Check bidding cooldown
            last_bid_time = await self.config.member(message.author).last_bid_time()
            current_time = datetime.utcnow().timestamp()
            cooldown = await self.config.guild(guild).global_auction_settings.bidding_cooldown()
            if current_time - last_bid_time.get(auction_id, 0) < cooldown:
                await thread.send(f"You must wait {cooldown} seconds between bids.")
                return

            # Handle proxy bidding
            max_proxy_bid = auction['proxy_bids'].get(str(message.author.id), 0)
            if bid_amount > max_proxy_bid:
                auction['proxy_bids'][str(message.author.id)] = bid_amount
            
            winning_bidder, winning_amount = await self.resolve_proxy_bids(auction)

            # Check for buy-out
            if auction['buy_out_price'] and winning_amount >= auction['buy_out_price']:
                await self.handle_buyout(guild, auction, guild.get_member(winning_bidder), winning_amount)
                auctions[auction_id] = auction
                return

            auction['current_bid'] = winning_amount
            auction['current_bidder'] = winning_bidder
            auction['bid_history'].append({
                'user_id': winning_bidder,
                'amount': winning_amount,
                'timestamp': int(current_time)
            })

            # Update last bid time
            async with self.config.member(message.author).last_bid_time() as last_bid_time:
                last_bid_time[auction_id] = current_time

            # Check for auction extension (anti-sniping)
            extension_time = await self.config.guild(guild).auction_extension_time()
            if current_time + extension_time > auction['end_time']:
                auction['end_time'] = current_time + extension_time
                await thread.send(f"Auction extended by {extension_time // 60} minutes due to last-minute bid!")

            auctions[auction_id] = auction

        await message.add_reaction("✅")
        
        embed = discord.Embed(title="New Highest Bid", color=discord.Color.green())
        embed.add_field(name="Bidder", value=message.author.mention, inline=True)
        embed.add_field(name="Amount", value=f"{winning_amount:,}", inline=True)
        await thread.send(embed=embed)

        # Update and send bid history chart
        chart = await self.visualization.create_bid_history_chart(auction)
        await thread.send("Updated bid history:", file=chart)

        # Notify outbid users
        await self.notify_outbid_users(guild, auction, winning_bidder, winning_amount)

    async def resolve_proxy_bids(self, auction: Dict[str, Any]) -> Tuple[int, int]:
        sorted_bids = sorted(auction['proxy_bids'].items(), key=lambda x: int(x[1]), reverse=True)
        if len(sorted_bids) < 2:
            return int(sorted_bids[0][0]), auction['current_bid']
        
        winning_bidder = int(sorted_bids[0][0])
        winning_amount = min(int(sorted_bids[1][1]) + 1, int(sorted_bids[0][1]))
        return winning_bidder, winning_amount

    async def handle_buyout(self, guild: discord.Guild, auction: Dict[str, Any], buyer: discord.Member, amount: int):
        thread_id = auction.get('thread_id')
        thread = guild.get_thread(thread_id) if thread_id else None
        
        auction['status'] = 'completed'
        auction['current_bid'] = amount
        auction['current_bidder'] = buyer.id
        auction['end_time'] = datetime.utcnow().timestamp()
        
        if thread:
            await thread.send(f"🎉 Auction ended! {buyer.mention} has bought out the auction for {amount:,}!")
            await thread.edit(archived=True, locked=True)
        
        await self.handle_auction_completion(guild, auction, buyer, amount)
        await self.process_auction_queue()

    async def notify_outbid_users(self, guild: discord.Guild, auction: Dict[str, Any], new_bidder_id: int, new_bid_amount: int):
        outbid_users = set(bid['user_id'] for bid in auction['bid_history'] if bid['user_id'] != new_bidder_id)
        for user_id in outbid_users:
            user = guild.get_member(user_id)
            if user:
                try:
                    user_settings = await self.config.member(user).notification_settings()
                    if user_settings['outbid']:
                        items_str = ", ".join(f"{item['amount']}x {item['name']}" for item in auction['items'])
                        await user.send(f"You've been outbid on the auction for {items_str}. The new highest bid is {new_bid_amount:,}.")
                except discord.HTTPException:
                    pass  # Unable to send DM to the user

    def is_valid_bid_format(self, content: str) -> bool:
        return content.replace(',', '').isdigit() or content.lower().endswith(('k', 'm', 'b'))

    def parse_bid_amount(self, content: str) -> int:
        content = content.lower().replace(',', '')
        if content.endswith('k'):
            return int(float(content[:-1]) * 1000)
        elif content.endswith('m'):
            return int(float(content[:-1]) * 1000000)
        elif content.endswith('b'):
            return int(float(content[:-1]) * 1000000000)
        else:
            return int(content)

    async def get_bid_increment(self, guild: discord.Guild, current_bid: int) -> int:
        bid_increment_tiers = await self.config.guild(guild).bid_increment_tiers()
        for tier, increment in sorted(bid_increment_tiers.items(), key=lambda x: int(x[0]), reverse=True):
            if current_bid >= int(tier):
                return increment
        return bid_increment_tiers['0']  # Default increment

    @commands.command()
    async def bid(self, ctx: commands.Context, amount: int):
        """Place a bid on the current auction."""
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("Bids can only be placed in auction threads.")
            return

        guild = ctx.guild
        auction_id = ctx.channel.name.split('#')[1]

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("There is no active auction in this thread.")
                return

            await self.handle_potential_bid(ctx.message)

    @commands.command()
    async def proxybid(self, ctx: commands.Context, amount: int):
        """Set a maximum proxy bid for the current auction."""
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("Proxy bids can only be set in auction threads.")
            return

        guild = ctx.guild
        auction_id = ctx.channel.name.split('#')[1]

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction or auction['status'] != 'active':
                await ctx.send("There is no active auction in this thread.")
                return

            if amount <= auction['current_bid']:
                await ctx.send(f"Your proxy bid must be higher than the current bid of {auction['current_bid']:,}.")
                return

            auction['proxy_bids'][str(ctx.author.id)] = amount
            auctions[auction_id] = auction

            await ctx.send(f"Your maximum proxy bid of {amount:,} has been set.")
            await self.handle_potential_bid(ctx.message)

    @commands.command()
    async def mybids(self, ctx: commands.Context):
        """View your bid history for the current auction."""
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("This command can only be used in auction threads.")
            return

        guild = ctx.guild
        auction_id = ctx.channel.name.split('#')[1]

        async with self.config.guild(guild).auctions() as auctions:
            auction = auctions.get(auction_id)
            if not auction:
                await ctx.send("No auction found for this thread.")
                return

        user_bids = [bid for bid in auction['bid_history'] if bid['user_id'] == ctx.author.id]
        if not user_bids:
            await ctx.send("You haven't placed any bids in this auction.")
            return

        embed = discord.Embed(title="Your Bid History", color=discord.Color.blue())
        for bid in user_bids:
            embed.add_field(
                name=f"Bid at {datetime.fromtimestamp(bid['timestamp'])}",
                value=f"{bid['amount']:,}",
                inline=False
            )
        
        proxy_bid = auction['proxy_bids'].get(str(ctx.author.id))
        if proxy_bid:
            embed.add_field(name="Your current proxy bid", value=f"{proxy_bid:,}", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionreport(self, ctx: commands.Context, days: int = 7):
        """Generate a detailed report of auction activity for the specified number of days."""
        guild = ctx.guild
        async with self.config.guild(guild).auction_history() as history:
            now = datetime.datetime.utcnow().timestamp()
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

        # Separate construction for most valuable auction
        most_valuable_items = ', '.join(f"{item['amount']}x {item['name']}" for item in most_valuable['items'])
        most_valuable_value = f"{most_valuable['current_bid']:,} ({most_valuable_items})"

        # Separate construction for most bids
        most_bids_items = ', '.join(f"{item['amount']}x {item['name']}" for item in most_bids['items'])
        most_bids_value = f"{len(most_bids['bid_history'])} bids ({most_bids_items})"

        embed = discord.Embed(title=f"Detailed Auction Report (Last {days} Days)", color=discord.Color.gold())
        embed.add_field(name="Total Auctions", value=len(relevant_auctions), inline=True)
        embed.add_field(name="Total Value", value=f"{total_value:,}", inline=True)
        embed.add_field(name="Average Value", value=f"{avg_value:,.2f}", inline=True)
        embed.add_field(name="Most Valuable Auction", value=most_valuable_value, inline=False)
        
        embed.add_field(name="Most Bids", value=most_bids_value, inline=False)

        category_report = "\n".join(f"{cat}: {stats['count']} auctions, {stats['value']:,} total value" for cat, stats in category_stats.items())
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
                        auction.get('auction_id', 'N/A'),
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
                        self.get_bid_increment(guild, auction['current_bid'])
                    ])
                file = discord.File(fp=io.BytesIO(output.getvalue().encode()), filename="detailed_auction_data.csv")
            elif format.lower() == "json":
                file = discord.File(fp=io.BytesIO(json.dumps(history, indent=2).encode()), filename="detailed_auction_data.json")
            else:
                await ctx.send("Invalid format. Please choose 'csv' or 'json'.")
                return

            await ctx.send("Here's your exported detailed auction data:", file=file)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctioninsights(self, ctx: commands.Context):
        """Display insights and analytics about the auction system."""
        summary = self.analytics.get_summary()
        
        embed = discord.Embed(title="Auction System Insights", color=discord.Color.blue())
        embed.add_field(name="Total Auctions", value=str(summary['total_auctions']), inline=True)
        embed.add_field(name="Total Value", value=f"{summary['total_value']:,}", inline=True)
        
        top_items = "\n".join(f"{item}: {count}" for item, count in summary['top_items'].items())
        embed.add_field(name="Top 5 Items", value=top_items or "No data", inline=False)
        
        top_users = "\n".join(f"<@{user_id}>: {count}" for user_id, count in summary['top_users'].items())
        embed.add_field(name="Top 5 Users", value=top_users or "No data", inline=False)
        
        category_performance = "\n".join(f"{cat}: {stats['count']} auctions, {stats['value']:,} total value" for cat, stats in summary['category_performance'].items())
        embed.add_field(name="Category Performance", value=category_performance or "No data", inline=False)
        
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
    async def setreputation(self, ctx: commands.Context, user: discord.Member, score: int):
        """Set the reputation score for a user (0-1000)."""
        if 0 <= score <= 1000:
            await self.config.member(user).reputation_score.set(score)
            await ctx.send(f"{user.name}'s reputation score has been set to {score}.")
        else:
            await ctx.send("Reputation score must be between 0 and 1000.")

    @commands.command()
    async def reputation(self, ctx: commands.Context, user: discord.Member = None):
        """View your reputation score or the score of another user."""
        target = user or ctx.author
        score = await self.config.member(target).reputation_score()
        await ctx.send(f"{target.name}'s reputation score is {score}.")

    async def update_reputation(self, guild: discord.Guild, user_id: int, action: str, reason: str):
        """Update user's reputation based on their actions."""
        async with self.config.member_from_ids(guild.id, user_id).reputation_score() as reputation:
            settings = await self.config.guild(guild).reputation_system()
            if action == 'increase':
                if reason == 'purchase':
                    reputation = min(reputation + settings['successful_purchase_bonus'], settings['max_score'])
                elif reason == 'sale':
                    reputation = min(reputation + settings['successful_sale_bonus'], settings['max_score'])
                elif reason == 'completion':
                    reputation = min(reputation + settings['auction_completion_bonus'], settings['max_score'])
            elif action == 'decrease':
                if reason == 'cancellation':
                    reputation = max(reputation - settings['auction_cancellation_penalty'], settings['min_score'])

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
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionduration(self, ctx: commands.Context, hours: int):
        """Set the default duration for auctions."""
        if hours < 1:
            await ctx.send("Auction duration must be at least 1 hour.")
            return
        
        await self.config.guild(ctx.guild).auction_duration.set(hours * 3600)
        await ctx.send(f"Default auction duration set to {hours} hours.")

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setauctionextension(self, ctx: commands.Context, minutes: int):
        """Set the auction extension time for last-minute bids."""
        if minutes < 1:
            await ctx.send("Extension time must be at least 1 minute.")
            return
        
        await self.config.guild(ctx.guild).auction_extension_time.set(minutes * 60)
        await ctx.send(f"Auction extension time set to {minutes} minutes.")

    @commands.command()
    async def createauctiontemplate(self, ctx: commands.Context, name: str, *, details: str):
        """Create an auction template for future use."""
        async with self.config.guild(ctx.guild).auction_templates() as templates:
            if name in templates:
                await ctx.send(f"A template with the name '{name}' already exists. Please choose a different name.")
                return
            
            templates[name] = details
        
        await ctx.send(f"Auction template '{name}' has been created.")

    @commands.command()
    async def useauctiontemplate(self, ctx: commands.Context, name: str):
        """Use a saved auction template to create a new auction."""
        templates = await self.config.guild(ctx.guild).auction_templates()
        if name not in templates:
            await ctx.send(f"No template found with the name '{name}'. Use `createauctiontemplate` to create one.")
            return
        
        template_details = templates[name]
        # Parse template details and create auction
        # This is a simplified version, you might want to add more sophisticated parsing
        items, min_bid, category = template_details.split('|')
        items = [{"name": item.split(':')[0], "amount": int(item.split(':')[1])} for item in items.split(',')]
        min_bid = int(min_bid)
        
        await self.process_advanced_auction_request(ctx, items, min_bid, None, None, category, None)

    @commands.command()
    async def listauctiontemplate(self, ctx: commands.Context):
        """List all saved auction templates."""
        templates = await self.config.guild(ctx.guild).auction_templates()
        if not templates:
            await ctx.send("There are no saved auction templates.")
            return
        
        embed = discord.Embed(title="Saved Auction Templates", color=discord.Color.blue())
        for name, details in templates.items():
            embed.add_field(name=name, value=details, inline=False)
        
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def deleteauctiontemplate(self, ctx: commands.Context, name: str):
        """Delete a saved auction template."""
        async with self.config.guild(ctx.guild).auction_templates() as templates:
            if name not in templates:
                await ctx.send(f"No template found with the name '{name}'.")
                return
            
            del templates[name]
        
        await ctx.send(f"Auction template '{name}' has been deleted.")

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
        auction_id = ctx.channel.name.split('#')[1]
        
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
                await ctx.send(f"You don't have enough funds to buy insurance. Cost: {insurance_cost:,}")
                return
            
            # Deduct insurance cost and mark insurance as bought
            await bank.withdraw_credits(ctx.author, insurance_cost)
            auction['insurance_bought'] = True
            auctions[auction_id] = auction
        
        await ctx.send(f"You've successfully bought insurance for your auction. Cost: {insurance_cost:,}")

    @commands.command()
    async def auctionhelp(self, ctx: commands.Context):
        """Display help information for the advanced auction system."""
        embed = discord.Embed(title="Advanced Auction System Help", color=discord.Color.blue())
        embed.add_field(name="General Commands", value="""
        • `[p]bid <amount>`: Place a bid on the current auction
        • `[p]proxybid <amount>`: Set a maximum proxy bid
        • `[p]auctioninfo [auction_id]`: Display information about the current or a specific auction
        • `[p]mybids`: View your bid history for the current auction
        • `[p]togglenotifications <setting>`: Toggle notification settings
        • `[p]notificationsettings`: View your current notification settings
        • `[p]myauctionstats`: View your personal auction statistics
        • `[p]auctionsubscribe <categories>`: Subscribe to auction categories
        • `[p]auctionunsubscribe <categories>`: Unsubscribe from auction categories
        • `[p]mysubscriptions`: View your current category subscriptions
        • `[p]reputation [user]`: View reputation score
        • `[p]auctionsearch <query>`: Search for auctions
        • `[p]savesearch <name> <query>`: Save a search query
        • `[p]runsavedsearch <name>`: Run a saved search query
        • `[p]listsavedsearches`: List all saved search queries
        • `[p]deletesavedsearch <name>`: Delete a saved search query
        • `[p]buyauctioninsurance`: Buy insurance for your current auction
        """, inline=False)
        
        embed.add_field(name="Admin Commands", value="""
        • `[p]auctionset`: Configure auction settings
        • `[p]spawnauction`: Create a new auction request button
        • `[p]auctionqueue`: Display the current auction queue
        • `[p]skipauction`: Skip the current auction
        • `[p]cancelauction <auction_id>`: Cancel a specific auction
        • `[p]auctionreport [days]`: Generate a detailed auction report
        • `[p]setauctionmoderator <user>`: Set a user as auction moderator
        • `[p]removeauctionmoderator <user>`: Remove auction moderator status
        • `[p]listauctionmoderators`: List all auction moderators
        • `[p]pruneauctionhistory <days>`: Remove old auction history
        • `[p]exportauctiondata [format]`: Export detailed auction data
        • `[p]setreputation <user> <score>`: Set user's reputation score
        • `[p]resetauctions`: Reset all auction data
        • `[p]auctionsettings`: Display current auction settings
        • `[p]setauctionduration <hours>`: Set default auction duration
        • `[p]setauctionextension <minutes>`: Set auction extension time
        • `[p]toggleauctionfeature <feature>`: Toggle auction features
        • `[p]setauctioninsurance <rate>`: Set auction insurance rate
        """, inline=False)
        
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

async def setup(bot: Red):
    """
    Setup function to add the cog to the bot.

    Args:
        bot (Red): The Red Discord bot instance.
    """
    cog = SuperEnhancedAdvancedAuction(bot)
    await cog.initialize()
    await bot.add_cog(cog)