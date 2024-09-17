import discord
from redbot.core import commands, checks, Config
from .auction_manager import AuctionManager
from .bidding_system import BiddingSystem
from .ui_components import AdminPanel, AuctionCreationForm, AuctionBrowser, AuctionModerationPanel
from .data_handler import DataHandler
from .analytics import AnalyticsManager
from .notification_system import NotificationSystem
from .reputation_system import ReputationSystem

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

    @commands.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def auctionadmin(self, ctx):
        """Open the admin control panel for the auction system."""
        admin_panel = AdminPanel(self.bot, self.data_handler, self.auction_manager, self.analytics)
        await admin_panel.send(ctx)

    @commands.command()
    async def createauction(self, ctx):
        """Create a new auction."""
        creation_form = AuctionCreationForm(self.bot, self.data_handler, self.auction_manager)
        await ctx.send("Please fill out the auction creation form:", view=creation_form)

    @commands.command()
    async def browseauctions(self, ctx, category: str = None):
        """Browse active auctions with optional category filter."""
        browser = AuctionBrowser(self.bot, self.data_handler, category)
        await browser.send(ctx)

    @commands.command()
    async def myauctions(self, ctx):
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

    @commands.command()
    async def watchauction(self, ctx, auction_id: int):
        """Add an auction to your watch list."""
        success = await self.notification_system.add_to_watchlist(ctx.author.id, auction_id)
        if success:
            await ctx.send(f"Auction #{auction_id} has been added to your watch list.")
        else:
            await ctx.send("Failed to add the auction to your watch list. Please check the auction ID.")

    @commands.command()
    async def unwatchauction(self, ctx, auction_id: int):
        """Remove an auction from your watch list."""
        success = await self.notification_system.remove_from_watchlist(ctx.author.id, auction_id)
        if success:
            await ctx.send(f"Auction #{auction_id} has been removed from your watch list.")
        else:
            await ctx.send("Failed to remove the auction from your watch list. Please check the auction ID.")

    @commands.command()
    async def mywatchlist(self, ctx):
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

    @commands.command()
    async def myreputation(self, ctx):
        """View your auction reputation."""
        reputation = await self.reputation_system.get_reputation(ctx.author.id)
        embed = discord.Embed(title="Your Auction Reputation", color=discord.Color.gold())
        embed.add_field(name="Score", value=str(reputation['score']))
        embed.add_field(name="Total Auctions", value=str(reputation['total_auctions']))
        embed.add_field(name="Successful Auctions", value=str(reputation['successful_auctions']))
        await ctx.send(embed=embed)

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

async def setup(bot):
    await bot.add_cog(AdvancedAuctionSystem(bot))