class ReputationSystem:
    def __init__(self, data_handler):
        self.data_handler = data_handler

    async def get_reputation(self, user_id: int):
        return await self.data_handler.config.member_from_ids(user_id).reputation()

    async def increase_reputation(self, user_id: int, reason: str):
        async with self.data_handler.config.member_from_ids(user_id).reputation() as reputation:
            reputation['score'] += 1
            reputation['total_auctions'] += 1
            reputation['successful_auctions'] += 1
            reputation['history'].append({'action': 'increase', 'reason': reason})

    async def decrease_reputation(self, user_id: int, reason: str):
        async with self.data_handler.config.member_from_ids(user_id).reputation() as reputation:
            reputation['score'] -= 1
            reputation['total_auctions'] += 1
            reputation['history'].append({'action': 'decrease', 'reason': reason})

    async def initialize_reputation(self, user_id: int):
        await self.data_handler.config.member_from_ids(user_id).reputation.set({
            'score': 0,
            'total_auctions': 0,
            'successful_auctions': 0,
            'history': []
        })

    async def get_reputation_tier(self, user_id: int):
        reputation = await self.get_reputation(user_id)
        score = reputation['score']
        
        if score < 10:
            return "Novice"
        elif score < 50:
            return "Apprentice"
        elif score < 100:
            return "Journeyman"
        elif score < 200:
            return "Expert"
        else:
            return "Master"

    async def can_participate_in_auction(self, user_id: int, auction_value: int):
        reputation = await self.get_reputation(user_id)
        tier = await self.get_reputation_tier(user_id)
        
        tier_limits = {
            "Novice": 10000,
            "Apprentice": 100000,
            "Journeyman": 1000000,
            "Expert": 10000000,
            "Master": float('inf')
        }
        
        return auction_value <= tier_limits[tier]

    async def get_reputation_history(self, user_id: int, limit: int = 10):
        reputation = await self.get_reputation(user_id)
        return reputation['history'][-limit:]

    async def calculate_trust_score(self, user_id: int):
        reputation = await self.get_reputation(user_id)
        total_auctions = reputation['total_auctions']
        successful_auctions = reputation['successful_auctions']
        
        if total_auctions == 0:
            return 0
        
        return (successful_auctions / total_auctions) * 100

    async def apply_reputation_bonus(self, user_id: int, auction_value: int):
        reputation = await self.get_reputation(user_id)
        tier = await self.get_reputation_tier(user_id)
        
        bonus_percentages = {
            "Novice": 0,
            "Apprentice": 0.01,
            "Journeyman": 0.02,
            "Expert": 0.03,
            "Master": 0.05
        }
        
        bonus = auction_value * bonus_percentages[tier]
        return int(auction_value + bonus)