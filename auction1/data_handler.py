from redbot.core import Config
import aiohttp
from typing import Dict, List, Any
import asyncio

class DataHandler:
    def __init__(self, config: Config, bot):
        self.config = config
        self.bot = bot
        self.default_guild = {
            "auctions": {},
            "auction_history": [],
            "auction_queue": [],
            "settings": {
                "auction_channel": None,
                "log_channel": None,
                "payout_channel": None,
                "blacklist_role": None,
                "auction_duration": 3600,
                "max_active_auctions": 5,
            },
        }
        self.default_member = {
            "reputation": {
                "score": 0,
                "total_auctions": 0,
                "successful_auctions": 0,
                "history": []
            },
            "watchlist": []
        }
        self.config.register_guild(**self.default_guild)
        self.config.register_member(**self.default_member)

    async def get_item_value(self, item_name: str) -> int:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.example.com/items/{item_name}") as response:
                if response.status == 200:
                    data = await response.json()
                    return data['value']
        return 0

    async def create_auction(self, guild_id: int, auction_data: dict) -> int:
        async with self.config.guild_from_id(guild_id).auctions() as auctions:
            auction_id = len(auctions) + 1
            auction_data['id'] = auction_id
            auctions[auction_id] = auction_data
        
        async with self.config.guild_from_id(guild_id).auction_queue() as queue:
            queue.append(auction_id)
        
        return auction_id

    async def get_auction(self, guild_id: int, auction_id: int) -> Dict[str, Any]:
        auctions = await self.config.guild_from_id(guild_id).auctions()
        return auctions.get(auction_id)

    async def update_auction(self, guild_id: int, auction_id: int, auction_data: dict):
        async with self.config.guild_from_id(guild_id).auctions() as auctions:
            auctions[auction_id] = auction_data

    async def get_current_auction(self, guild_id: int) -> Dict[str, Any]:
        auctions = await self.config.guild_from_id(guild_id).auctions()
        return next((a for a in auctions.values() if a['status'] == 'active'), None)

    async def update_bid(self, guild_id: int, auction_id: int, user_id: int, amount: int):
        async with self.config.guild_from_id(guild_id).auctions() as auctions:
            auction = auctions[auction_id]
            auction['current_bid'] = amount
            auction['top_bidder'] = user_id
            auction['bid_history'].append({"user_id": user_id, "amount": amount})

    async def get_settings(self, guild_id: int) -> Dict[str, Any]:
        return await self.config.guild_from_id(guild_id).settings()

    async def update_setting(self, guild_id: int, key: str, value: Any):
        async with self.config.guild_from_id(guild_id).settings() as settings:
            settings[key] = value

    async def get_setting(self, guild_id: int, key: str) -> Any:
        settings = await self.config.guild_from_id(guild_id).settings()
        return settings.get(key)

    async def get_auction_queue(self, guild_id: int) -> List[int]:
        return await self.config.guild_from_id(guild_id).auction_queue()

    async def remove_from_queue(self, guild_id: int, auction_id: int):
        async with self.config.guild_from_id(guild_id).auction_queue() as queue:
            if auction_id in queue:
                queue.remove(auction_id)

    async def get_user_auctions(self, guild_id: int, user_id: int) -> List[Dict[str, Any]]:
        auctions = await self.config.guild_from_id(guild_id).auctions()
        return [a for a in auctions.values() if a['creator_id'] == user_id and a['status'] in ['pending', 'active']]

    async def cancel_auction(self, guild_id: int, auction_id: int):
        async with self.config.guild_from_id(guild_id).auctions() as auctions:
            if auction_id in auctions:
                auctions[auction_id]['status'] = 'cancelled'
        await self.remove_from_queue(guild_id, auction_id)

    async def complete_auction(self, guild_id: int, auction_id: int):
        async with self.config.guild_from_id(guild_id).auctions() as auctions:
            if auction_id in auctions:
                auction = auctions[auction_id]
                auction['status'] = 'completed'
                
                async with self.config.guild_from_id(guild_id).auction_history() as history:
                    history.append(auction)
                
                del auctions[auction_id]

    async def get_auction_history(self, guild_id: int) -> List[Dict[str, Any]]:
        return await self.config.guild_from_id(guild_id).auction_history()

    async def clear_auction_data(self, guild_id: int):
        await self.config.guild_from_id(guild_id).clear()
        await self.config.guild_from_id(guild_id).set(self.default_guild)

    async def get_active_auctions(self, guild_id: int, category: str = None) -> List[Dict[str, Any]]:
        auctions = await self.config.guild_from_id(guild_id).auctions()
        active_auctions = [a for a in auctions.values() if a['status'] == 'active']
        if category:
            return [a for a in active_auctions if a['category'].lower() == category.lower()]
        return active_auctions

    async def get_blacklisted_users(self, guild_id: int) -> List[int]:
        settings = await self.get_settings(guild_id)
        blacklist_role_id = settings.get('blacklist_role')
        if not blacklist_role_id:
            return []
        
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return []
        
        blacklist_role = guild.get_role(blacklist_role_id)
        if not blacklist_role:
            return []
        
        return [member.id for member in blacklist_role.members]

    async def add_to_blacklist(self, guild_id: int, user_id: int):
        settings = await self.get_settings(guild_id)
        blacklist_role_id = settings.get('blacklist_role')
        if not blacklist_role_id:
            return False
        
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False
        
        blacklist_role = guild.get_role(blacklist_role_id)
        if not blacklist_role:
            return False
        
        member = guild.get_member(user_id)
        if not member:
            return False
        
        await member.add_roles(blacklist_role)
        return True

    async def remove_from_blacklist(self, guild_id: int, user_id: int):
        settings = await self.get_settings(guild_id)
        blacklist_role_id = settings.get('blacklist_role')
        if not blacklist_role_id:
            return False
        
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False
        
        blacklist_role = guild.get_role(blacklist_role_id)
        if not blacklist_role:
            return False
        
        member = guild.get_member(user_id)
        if not member:
            return False
        
        await member.remove_roles(blacklist_role)
        return True