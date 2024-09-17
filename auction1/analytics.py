import discord
import matplotlib.pyplot as plt
import io
from collections import defaultdict

class AnalyticsManager:
    def __init__(self, data_handler):
        self.data_handler = data_handler

    async def get_analytics(self, guild_id: int):
        auctions = await self.data_handler.get_auction_history(guild_id)
        
        total_auctions = len(auctions)
        total_value = sum(a['current_bid'] for a in auctions if a['status'] == 'completed')
        average_value = total_value / total_auctions if total_auctions > 0 else 0

        category_counts = defaultdict(int)
        bidder_counts = defaultdict(int)
        seller_values = defaultdict(int)

        for auction in auctions:
            category_counts[auction['category']] += 1
            for bid in auction['bid_history']:
                bidder_counts[bid['user_id']] += 1
            if auction['status'] == 'completed':
                seller_values[auction['creator_id']] += auction['current_bid']

        most_popular_category = max(category_counts, key=category_counts.get)
        most_active_bidder = max(bidder_counts, key=bidder_counts.get)
        most_successful_seller = max(seller_values, key=seller_values.get)

        return {
            'total_auctions': total_auctions,
            'total_value': total_value,
            'average_value': average_value,
            'most_popular_category': most_popular_category,
            'most_active_bidder': most_active_bidder,
            'most_successful_seller': most_successful_seller
        }

    async def generate_value_distribution_graph(self, guild_id: int):
        auctions = await self.data_handler.get_auction_history(guild_id)
        values = [a['current_bid'] for a in auctions if a['status'] == 'completed']

        plt.figure(figsize=(10, 6))
        plt.hist(values, bins=20, edgecolor='black')
        plt.title('Auction Value Distribution')
        plt.xlabel('Auction Value')
        plt.ylabel('Number of Auctions')
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        
        return discord.File(buf, filename='value_distribution.png')

    async def generate_category_distribution_graph(self, guild_id: int):
        auctions = await self.data_handler.get_auction_history(guild_id)
        category_counts = defaultdict(int)
        for auction in auctions:
            category_counts[auction['category']] += 1

        categories = list(category_counts.keys())
        counts = list(category_counts.values())

        plt.figure(figsize=(10, 6))
        plt.bar(categories, counts)
        plt.title('Auction Category Distribution')
        plt.xlabel('Category')
        plt.ylabel('Number of Auctions')
        plt.xticks(rotation=45, ha='right')
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        
        return discord.File(buf, filename='category_distribution.png')