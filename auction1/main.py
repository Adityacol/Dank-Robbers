import discord
from redbot.core import commands, checks, Config
from .auction_manager import AuctionManager
from .bidding_system import BiddingSystem
from .ui_components import AdminPanel, AuctionCreationForm, AuctionBrowser, AuctionModerationPanel
from .data_handler import DataHandler
from .analytics import AnalyticsManager
from .notification_system import NotificationSystem
from .reputation_system import ReputationSystem
from discord.ui import View, Button

class PersistentView(View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Create Auction", style=discord.ButtonStyle.green, custom_id="create_auction")
    async def create_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
        creation_form = AuctionCreationForm(self.cog.bot, self.cog.data_handler, self.cog.auction_manager)
        await interaction.response.send_modal(creation_form)

class AdvancedAuctionSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=95932766180)
        self.data_handler = DataHandler(self.config, bot)
        self.analytics = AnalyticsManager(self.data_handler)
        self.notification_system = NotificationSystem(bot, self.data_handler)
        self.reputation_system = ReputationSystem(self.data_handler)
        self.auction_manager = AuctionManager(bot, self.data_handler, self.notification_system, self.reputation_system)
        self.bidding_system = BiddingSystem(bot, self.data_handler, self.notification_system, self.reputation_system)
        self.persistent_views_added = False

    async def cog_load(self):
        if not self.persistent_views_added:
            self.bot.add_view(PersistentView(self))
            self.persistent_views_added = True

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def spawnauction(self, ctx):
        """Spawn the auction creation button."""
        view = PersistentView(self)
        embed = discord.Embed(
            title="Create an Auction",
            description="Click the button below to create a new auction.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed, view=view)

    @commands.group(invoke_without_command=True)
    async def auction(self, ctx):
        """Advanced Auction System commands"""
        if ctx.invoked_subcommand is None:
            await self.auctionhelp(ctx)

    @auction.command(name="help")
    async def auction_help(self, ctx):
        """Display help information for the advanced auction system."""
        await self.auctionhelp(ctx)

    @auction.command(name="browse")
    async def auction_browse(self, ctx, category: str = None):
        """Browse active auctions with optional category filter."""
        browser = AuctionBrowser(self.bot, self.data_handler, category)
        await browser.send(ctx)

    @auction.command(name="myauctions")
    async def auction_myauctions(self, ctx):
        """View your active auctions."""
        auctions = await self.data_handler.get_user_auctions(ctx.guild.id, ctx.author.id)
        if not auctions:
            await ctx.send("You don't have any active auctions.")
            return

        embed = discord.Embed(title="Your Active Auctions", color=discord.Color.blue())
        for auction in auctions:
            embed.add_field(
                name=f"Auction #{auction['id']}",
                value=f"Item: {auction['item_name']} (x{auction['quantity']})\n"
                      f"Current Bid: ${auction['current_bid']:,}\n"
                      f"Status: {auction['status'].capitalize()}",
                inline=False
            )
        await ctx.send(embed=embed)

    @auction.command(name="watch")
    async def auction_watch(self, ctx, auction_id: int):
        """Add an auction to your watch list."""
        success = await self.notification_system.add_to_watchlist(ctx.author.id, auction_id)
        if success:
            await ctx.send(f"Auction #{auction_id} has been added to your watch list.")
        else:
            await ctx.send("Failed to add the auction to your watch list. Please check the auction ID.")

    @auction.command(name="unwatch")
    async def auction_unwatch(self, ctx, auction_id: int):
        """Remove an auction from your watch list."""
        success = await self.notification_system.remove_from_watchlist(ctx.author.id, auction_id)
        if success:
            await ctx.send(f"Auction #{auction_id} has been removed from your watch list.")
        else:
            await ctx.send("Failed to remove the auction from your watch list. Please check the auction ID.")

    @auction.command(name="watchlist")
    async def auction_watchlist(self, ctx):
        """View your auction watch list."""
        watchlist = await self.notification_system.get_watchlist(ctx.author.id)
        if not watchlist:
            await ctx.send("Your watch list is empty.")
            return

        embed = discord.Embed(title="Your Auction Watch List", color=discord.Color.blue())
        for auction_id in watchlist:
            auction = await self.data_handler.get_auction(ctx.guild.id, auction_id)
            if auction:
                embed.add_field(
                    name=f"Auction #{auction_id}",
                    value=f"Item: {auction['item_name']} (x{auction['quantity']})\n"
                          f"Current Bid: ${auction['current_bid']:,}\n"
                          f"Status: {auction['status'].capitalize()}",
                    inline=False
                )
        await ctx.send(embed=embed)

    @auction.command(name="reputation")
    async def auction_reputation(self, ctx):
        """View your auction reputation."""
        reputation = await self.reputation_system.get_reputation(ctx.author.id)
        embed = discord.Embed(title="Your Auction Reputation", color=discord.Color.gold())
        embed.add_field(name="Score", value=str(reputation['score']))
        embed.add_field(name="Total Auctions", value=str(reputation['total_auctions']))
        embed.add_field(name="Successful Auctions", value=str(reputation['successful_auctions']))
        await ctx.send(embed=embed)

    @auction.command(name="info")
    async def auction_info(self, ctx, auction_id: int):
        """View detailed information about a specific auction."""
        auction = await self.data_handler.get_auction(ctx.guild.id, auction_id)
        if not auction:
            await ctx.send("Auction not found.")
            return

        embed = discord.Embed(title=f"Auction #{auction_id} Details", color=discord.Color.gold())
        embed.add_field(name="Item", value=f"{auction['item_name']} (x{auction['quantity']})")
        embed.add_field(name="Current Bid", value=f"${auction['current_bid']:,}")
        embed.add_field(name="Top Bidder", value=f"<@{auction['top_bidder']}>" if auction['top_bidder'] else "No bids yet")
        embed.add_field(name="Status", value=auction['status'].capitalize())
        embed.add_field(name="Created By", value=f"<@{auction['creator_id']}>")
        embed.add_field(name="Category", value=auction['category'])
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionadmin(self, ctx):
        """Open the admin control panel for the auction system."""
        admin_panel = AdminPanel(self.bot, self.data_handler, self.auction_manager, self.analytics)
        await admin_panel.send(ctx)

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionanalytics(self, ctx):
        """View auction analytics."""
        analytics_data = await self.analytics.get_analytics(ctx.guild.id)
        embed = discord.Embed(title="Auction Analytics", color=discord.Color.purple())
        embed.add_field(name="Total Auctions", value=str(analytics_data['total_auctions']))
        embed.add_field(name="Total Value", value=f"${analytics_data['total_value']:,}")
        embed.add_field(name="Average Value", value=f"${analytics_data['average_value']:,.2f}")
        embed.add_field(name="Most Popular Category", value=analytics_data['most_popular_category'])
        embed.add_field(name="Most Active Bidder", value=f"<@{analytics_data['most_active_bidder']}>")
        embed.add_field(name="Most Successful Seller", value=f"<@{analytics_data['most_successful_seller']}>")
        await ctx.send(embed=embed)

        # Send analytics graphs
        await ctx.send(file=await self.analytics.generate_value_distribution_graph(ctx.guild.id))
        await ctx.send(file=await self.analytics.generate_category_distribution_graph(ctx.guild.id))

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def moderateauction(self, ctx, auction_id: int):
        """Open the moderation panel for a specific auction."""
        auction = await self.data_handler.get_auction(ctx.guild.id, auction_id)
        if not auction:
            await ctx.send("Auction not found.")
            return

        moderation_panel = AuctionModerationPanel(self.bot, self.data_handler, self.auction_manager, auction)
        await moderation_panel.send(ctx)

    class HelpView(View):
        def __init__(self, cog):
            super().__init__()
            self.cog = cog

        @discord.ui.button(label="General Commands", style=discord.ButtonStyle.primary)
        async def general_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(embed=await self.cog.get_general_help(), ephemeral=True)

        @discord.ui.button(label="Admin Commands", style=discord.ButtonStyle.primary)
        async def admin_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(embed=await self.cog.get_admin_help(), ephemeral=True)

        @discord.ui.button(label="Auction Process", style=discord.ButtonStyle.secondary)
        async def auction_process(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(embed=await self.cog.get_auction_process_help(), ephemeral=True)

        @discord.ui.button(label="Reputation System", style=discord.ButtonStyle.secondary)
        async def reputation_system(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message(embed=await self.cog.get_reputation_help(), ephemeral=True)

    @commands.command()
    async def auctionhelp(self, ctx):
        """Display help information for the auction system."""
        embed = discord.Embed(title=" Auction System Help", 
                              description="Click the buttons below to view detailed help for each category.",
                              color=discord.Color.blue())
        
        embed.add_field(name="General Commands", value="Basic commands for all users", inline=False)
        embed.add_field(name="Admin Commands", value="Commands for server administrators", inline=False)
        embed.add_field(name="Auction Process", value="Learn how the auction system works", inline=False)
        embed.add_field(name="Reputation System", value="Understand the reputation mechanics", inline=False)

        view = self.HelpView(self)
        await ctx.send(embed=embed, view=view)

    async def get_general_help(self):
        embed = discord.Embed(title="General Auction Commands", color=discord.Color.green())
        commands = [
            ("auction browse [category]", "Browse active auctions with optional category filter"),
            ("auction myauctions", "View your active auctions"),
            ("auction watch <auction_id>", "Add an auction to your watch list"),
            ("auction unwatch <auction_id>", "Remove an auction from your watch list"),
            ("auction watchlist", "View your auction watch list"),
            ("auction reputation", "View your auction reputation"),
            ("auction info <auction_id>", "View detailed information about a specific auction")
        ]
        for cmd, desc in commands:
            embed.add_field(name=f"`{cmd}`", value=desc, inline=False)
        return embed

    async def get_admin_help(self):
        embed = discord.Embed(title="Admin Auction Commands", color=discord.Color.red())
        commands = [
            ("spawnauction", "Spawn the auction creation button"),
            ("auctionadmin", "Open the admin control panel for the auction system"),
            ("auctionanalytics", "View auction analytics"),
            ("moderateauction <auction_id>", "Open the moderation panel for a specific auction")
        ]
        for cmd, desc in commands:
            embed.add_field(name=f"`{cmd}`", value=desc, inline=False)
        return embed

    async def get_auction_process_help(self):
        embed = discord.Embed(title="Auction Process", color=discord.Color.gold())
        steps = [
            ("Creation", "An auction is created using the persistent button"),
            ("Queuing", "The auction is added to the queue"),
            ("Activation", "When it's the auction's turn, it becomes active"),
            ("Bidding", "Users can place bids on the active auction"),
            ("Extension", "If a bid is placed in the last minute, the auction is extended"),
            ("Completion", "The auction ends, and the highest bidder wins"),
            ("Payment", "The winner must confirm payment"),
            ("Delivery", "The auctioned item is delivered to the winner")
        ]
        for step, desc in steps:
            embed.add_field(name=step, value=desc, inline=False)
        return embed

    async def get_reputation_help(self):
        embed = discord.Embed(title="Reputation System", color=discord.Color.purple())
        info = [
            ("Reputation Score", "A numerical value representing a user's trustworthiness"),
            ("Gaining Reputation", "Successfully complete auctions as a buyer or seller"),
            ("Losing Reputation", "Fail to pay for won auctions or cancel auctions"),
            ("Reputation Tiers", "Different levels of reputation unlock new privileges"),
            ("Tier Benefits", "Higher tiers may allow participation in high-value auctions"),
            ("Viewing Reputation", "Use the `auction reputation` command to see your current standing")
        ]
        for topic, desc in info:
            embed.add_field(name=topic, value=desc, inline=False)
        return embed

async def setup(bot):
    cog = AdvancedAuctionSystem(bot)
    await bot.add_cog(cog)
    await cog.cog_load()