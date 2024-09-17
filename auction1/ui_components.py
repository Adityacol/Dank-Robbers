import discord
from discord.ui import View, Button, Modal, TextInput

class AuctionEmbed(discord.Embed):
    def __init__(self, auction_data):
        super().__init__(title="🎉 Exciting Auction! 🎉", color=discord.Color.gold())
        self.set_thumbnail(url="https://example.com/auction_gif.gif")
        self.add_field(name="Item", value=f"{auction_data['quantity']}x {auction_data['item_name']}")
        self.add_field(name="Current Bid", value=f"${auction_data['current_bid']:,}")
        self.add_field(name="Top Bidder", value=auction_data['top_bidder'] or "No bids yet")
        self.add_field(name="Category", value=auction_data['category'])
        self.set_footer(text="Click the buttons below to place your bid!")

class AdminPanel(View):
    def __init__(self, bot, data_handler, auction_manager, analytics):
        super().__init__()
        self.bot = bot
        self.data_handler = data_handler
        self.auction_manager = auction_manager
        self.analytics = analytics

    @discord.ui.button(label="Auction Settings", style=discord.ButtonStyle.primary)
    async def auction_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await self.data_handler.get_settings(interaction.guild_id)
        embed = discord.Embed(title="Auction Settings", color=discord.Color.blue())
        for key, value in settings.items():
            embed.add_field(name=key.replace('_', ' ').title(), value=str(value))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="View Queue", style=discord.ButtonStyle.secondary)
    async def view_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = await self.data_handler.get_auction_queue(interaction.guild_id)
        embed = discord.Embed(title="Auction Queue", color=discord.Color.green())
        for idx, auction_id in enumerate(queue, start=1):
            auction = await self.data_handler.get_auction(interaction.guild_id, auction_id)
            embed.add_field(name=f"#{idx} - Auction {auction_id}", 
                            value=f"{auction['quantity']}x {auction['item_name']} - ${auction['min_bid']:,}", 
                            inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Analytics", style=discord.ButtonStyle.primary)
    async def view_analytics(self, interaction: discord.Interaction, button: discord.ui.Button):
        analytics_data = await self.analytics.get_analytics(interaction.guild_id)
        embed = discord.Embed(title="Auction Analytics", color=discord.Color.purple())
        embed.add_field(name="Total Auctions", value=str(analytics_data['total_auctions']))
        embed.add_field(name="Total Value", value=f"${analytics_data['total_value']:,}")
        embed.add_field(name="Average Value", value=f"${analytics_data['average_value']:,.2f}")
        embed.add_field(name="Most Popular Category", value=analytics_data['most_popular_category'])
        embed.add_field(name="Most Active Bidder", value=f"<@{analytics_data['most_active_bidder']}>")
        embed.add_field(name="Most Successful Seller", value=f"<@{analytics_data['most_successful_seller']}>")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Send analytics graphs
        await interaction.followup.send(file=await self.analytics.generate_value_distribution_graph(interaction.guild_id), ephemeral=True)
        await interaction.followup.send(file=await self.analytics.generate_category_distribution_graph(interaction.guild_id), ephemeral=True)

    async def send(self, ctx):
        embed = discord.Embed(title="Admin Control Panel", description="Select an option below:", color=discord.Color.blue())
        await ctx.send(embed=embed, view=self)

class AuctionCreationForm(Modal):
    def __init__(self, bot, data_handler, auction_manager):
        super().__init__(title="Create Auction")
        self.bot = bot
        self.data_handler = data_handler
        self.auction_manager = auction_manager

        self.item_name = TextInput(label="Item Name")
        self.item_quantity = TextInput(label="Quantity")
        self.min_bid = TextInput(label="Minimum Bid")
        self.category = TextInput(label="Category", required=False, placeholder="Leave blank for auto-categorization")

        self.add_item(self.item_name)
        self.add_item(self.item_quantity)
        self.add_item(self.min_bid)
        self.add_item(self.category)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.item_quantity.value)
            min_bid = int(self.min_bid.value)
        except ValueError:
            await interaction.response.send_message("Quantity and Minimum Bid must be valid numbers.", ephemeral=True)
            return

        auction_data = {
            "item_name": self.item_name.value,
            "quantity": quantity,
            "min_bid": min_bid,
            "category": self.category.value or self.determine_category(min_bid),
            "creator_id": interaction.user.id,
            "guild_id": interaction.guild_id,
        }
        
        await self.auction_manager.create_auction(interaction, auction_data)

    def determine_category(self, value):
        if value < 10000:
            return "Common"
        elif value < 100000:
            return "Uncommon"
        elif value < 1000000:
            return "Rare"
        elif value < 10000000:
            return "Epic"
        else:
            return "Legendary"

class AuctionBrowser(View):
    def __init__(self, bot, data_handler, category=None):
        super().__init__()
        self.bot = bot
        self.data_handler = data_handler
        self.category = category
        self.current_page = 0
        self.auctions = []

    async def send(self, ctx):
        self.auctions = await self.data_handler.get_active_auctions(ctx.guild.id, self.category)
        if not self.auctions:
            await ctx.send("No active auctions found.")
            return

        self.max_pages = (len(self.auctions) - 1) // 5
        embed = await self.get_embed()
        self.message = await ctx.send(embed=embed, view=self)

    async def get_embed(self):
        start = self.current_page * 5
        end = start + 5
        current_auctions = self.auctions[start:end]

        embed = discord.Embed(title="Active Auctions", color=discord.Color.blue())
        for auction in current_auctions:
            embed.add_field(
                name=f"Auction #{auction['id']}",
                value=f"Item: {auction['item_name']} (x{auction['quantity']})\n"
                      f"Current Bid: ${auction['current_bid']:,}\n"
                      f"Category: {auction['category']}",
                inline=False
            )
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages + 1}")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            embed = await self.get_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages:
            self.current_page += 1
            embed = await self.get_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()

class AuctionModerationPanel(View):
    def __init__(self, bot, data_handler, auction_manager, auction):
        super().__init__()
        self.bot = bot
        self.data_handler = data_handler
        self.auction_manager = auction_manager
        self.auction = auction

    async def send(self, ctx):
        embed = discord.Embed(title=f"Moderate Auction #{self.auction['id']}", color=discord.Color.red())
        embed.add_field(name="Item", value=f"{self.auction['quantity']}x {self.auction['item_name']}")
        embed.add_field(name="Current Bid", value=f"${self.auction['current_bid']:,}")
        embed.add_field(name="Top Bidder", value=f"<@{self.auction['top_bidder']}>" if self.auction['top_bidder'] else "No bids")
        embed.add_field(name="Status", value=self.auction['status'].capitalize())
        
        await ctx.send(embed=embed, view=self)

    @discord.ui.button(label="Cancel Auction", style=discord.ButtonStyle.danger)
    async def cancel_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.auction_manager.cancel_auction(self.auction['id'])
        await interaction.response.send_message(f"Auction #{self.auction['id']} has been cancelled.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Extend Auction", style=discord.ButtonStyle.primary)
    async def extend_auction(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.auction_manager.extend_auction(self.auction['id'], 600)  # Extend by 10 minutes
        await interaction.response.send_message(f"Auction #{self.auction['id']} has been extended by 10 minutes.", ephemeral=True)

    @discord.ui.button(label="Warn Participants", style=discord.ButtonStyle.secondary)
    async def warn_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WarnParticipantsModal(self.auction_manager, self.auction['id'])
        await interaction.response.send_modal(modal)

class WarnParticipantsModal(Modal):
    def __init__(self, auction_manager, auction_id):
        super().__init__(title="Warn Auction Participants")
        self.auction_manager = auction_manager
        self.auction_id = auction_id

        self.warning_message = TextInput(label="Warning Message", style=discord.TextStyle.paragraph)
        self.add_item(self.warning_message)

    async def on_submit(self, interaction: discord.Interaction):
        await self.auction_manager.warn_participants(self.auction_id, self.warning_message.value)
        await interaction.response.send_message("Warning message sent to auction participants.", ephemeral=True)

class BiddingButtons(View):
    def __init__(self, bot, data_handler):
        super().__init__()
        self.bot = bot
        self.data_handler = data_handler

    @discord.ui.button(label="Place Bid", style=discord.ButtonStyle.primary)
    async def place_bid(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PlaceBidModal(self.bot, self.data_handler)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View Bid History", style=discord.ButtonStyle.secondary)
    async def view_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        auction_data = await self.data_handler.get_current_auction(interaction.guild_id)
        if not auction_data:
            await interaction.response.send_message("No active auction found.", ephemeral=True)
            return

        bid_history = auction_data.get('bid_history', [])
        embed = discord.Embed(title="Bid History", color=discord.Color.blue())
        for idx, bid in enumerate(bid_history, start=1):
            user = self.bot.get_user(bid['user_id'])
            user_name = user.name if user else f"User {bid['user_id']}"
            embed.add_field(name=f"Bid #{idx}", value=f"{user_name}: ${bid['amount']:,}", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

class PlaceBidModal(Modal):
    def __init__(self, bot, data_handler):
        super().__init__(title="Place a Bid")
        self.bot = bot
        self.data_handler = data_handler

        self.bid_amount = TextInput(label="Bid Amount")
        self.add_item(self.bid_amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.bid_amount.value)
            auction_data = await self.data_handler.get_current_auction(interaction.guild_id)
            if not auction_data:
                await interaction.response.send_message("No active auction found.", ephemeral=True)
                return

            if amount <= auction_data['current_bid']:
                await interaction.response.send_message(f"Your bid must be higher than the current bid of ${auction_data['current_bid']:,}.", ephemeral=True)
                return

            await self.data_handler.update_bid(interaction.guild_id, auction_data['id'], interaction.user.id, amount)
            await interaction.response.send_message(f"Your bid of ${amount:,} has been placed!", ephemeral=True)

            # Update auction embed
            channel = self.bot.get_channel(auction_data['channel_id'])
            message = await channel.fetch_message(auction_data['message_id'])
            embed = message.embeds[0]
            embed.set_field_at(1, name="Current Bid", value=f"${amount:,}")
            embed.set_field_at(2, name="Top Bidder", value=interaction.user.mention)
            await message.edit(embed=embed)

        except ValueError:
            await interaction.response.send_message("Invalid bid amount. Please enter a number.", ephemeral=True)