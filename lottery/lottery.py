import discord
from discord.ext import tasks
from redbot.core import commands
from redbot.core.data_manager import cog_data_path
import random
import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

ELEMENT_BOT_ID = 957635842631950379
LOTTERY_DURATION = 60 * 5  # 5 minutes for testing

class Lottery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_path = cog_data_path(self) / "config.json"
        self.tickets_path = cog_data_path(self) / "guild_tickets.json"
        self.schedule_lottery.start()
        self.bot.loop.create_task(self.check_lottery_on_startup())
        print(f"Lottery cog initialized for bot: {bot.user}")

    def cog_unload(self):
        self.schedule_lottery.cancel()
        print("Lottery cog unloaded")

    def load_config(self):
        if self.config_path.exists():
            try:
                with self.config_path.open('r') as file:
                    return json.load(file)
            except json.JSONDecodeError:
                return {}
        return {}

    def save_config(self, data):
        with self.config_path.open('w') as file:
            json.dump(data, file, indent=4)

    def load_guild_data(self):
        if self.tickets_path.exists():
            try:
                with self.tickets_path.open('r') as file:
                    return json.load(file)
            except json.JSONDecodeError:
                return {}
        return {}

    def save_guild_data(self, data):
        with self.tickets_path.open('w') as file:
            json.dump(data, file, indent=4)

    async def check_lottery_on_startup(self):
        await self.bot.wait_until_ready()
        config = self.load_config()
        now = datetime.utcnow()
        for guild_id, guild_config in config.items():
            if 'end_time' in guild_config:
                end_time = datetime.fromisoformat(guild_config['end_time'])
                if now < end_time:
                    await asyncio.sleep((end_time - now).total_seconds())
                    await self.end_lottery(guild_id)
                else:
                    await self.end_lottery(guild_id)

    @tasks.loop(seconds=240)  # Adjust the frequency as needed
    async def schedule_lottery(self):
        print("Checking lottery schedule...")
        now = datetime.utcnow()  # Get current datetime
        config = self.load_config()
        for guild_id, guild_config in config.items():
            if 'start_time' in guild_config:
                start_time_str = guild_config['start_time']
                start_time = datetime.strptime(start_time_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
                if start_time <= now < (start_time + timedelta(seconds=LOTTERY_DURATION)):
                    print(f"Starting a new lottery in guild {guild_id}...")
                    await self.start_lottery(guild_id)
                elif now >= (start_time + timedelta(seconds=LOTTERY_DURATION)):
                    if 'end_time' in guild_config:
                        await self.end_lottery(guild_id)
                        del guild_config['end_time']
                        self.save_config(config)

    async def start_lottery(self, guild_id):
        config = self.load_config()
        guild_config = config.get(str(guild_id), {})
        
        if 'end_time' not in guild_config:
            start_time = datetime.utcnow()
            end_time = start_time + timedelta(seconds=LOTTERY_DURATION)
            guild_config['end_time'] = end_time.isoformat()
            config[str(guild_id)] = guild_config
            self.save_config(config)

            channel_id = guild_config.get('channel_id')
            if channel_id:
                channel = self.bot.get_channel(int(channel_id))
                if channel:
                    start_embed = discord.Embed(
                        title="Lottery Started!",
                        description=("The lottery is starting now! Donate to participate.\n\n"
                                     "Each ticket costs 10,000 dank memer coins, "
                                     "the more tickets you get, the more chance you're going to have! "
                                     "All your tickets have numeral values so don't worry about it bugging out! "
                                     "The prize will depend on how much money people spend on buying tickets. "
                                     "GOOD LUCK!"),
                        color=discord.Color.green()
                    )
                    start_embed.set_footer(text="Built by renivier")
                    await channel.send(embed=start_embed)

            await asyncio.sleep(LOTTERY_DURATION)
            await self.end_lottery(guild_id)
        else:
            channel_id = guild_config.get('channel_id')
            if channel_id:
                channel = self.bot.get_channel(int(channel_id))
                if channel:
                    await channel.send("Lottery is already running!") 

    async def end_lottery(self, guild_id):
        config = self.load_config()
        guild_config = config.get(str(guild_id), {})
        if 'end_time' in guild_config:
            del guild_config['end_time']
            self.save_config(config)
            winner_id, winner_data, prize_amount = self.draw_winner(guild_id)
            channel_id = guild_config.get('channel_id')

            if channel_id:
                channel = self.bot.get_channel(int(channel_id))
                if winner_id and channel:
                    winner = await self.bot.fetch_user(int(winner_id))
                    winner_embed = discord.Embed(
                        title="Lottery Winner!",
                        description=f'Congratulations {winner.mention}, you have won the lottery with one of your tickets! You have won {prize_amount} coins!',
                        color=discord.Color.gold()
                    )
                    winner_embed.set_thumbnail(url=winner.avatar.url)
                    winner_embed.set_footer(text="Built by renivier")
                    await channel.send(embed=winner_embed)

                    end_embed = discord.Embed(
                        title="Lottery Ended",
                        description="The lottery has ended and the winner has been drawn! You can now donate for the next round.",
                        color=discord.Color.purple()
                    )
                    end_embed.set_footer(text="Built by renivier")
                    await channel.send(embed=end_embed)
                else:
                    no_tickets_embed = discord.Embed(
                        title="No Tickets Purchased",
                        description="No tickets were purchased in this lottery round.",
                        color=discord.Color.red()
                    )
                    no_tickets_embed.set_footer(text="Built by renivier")
                    await channel.send(embed=no_tickets_embed)

    def draw_winner(self, guild_id):
        data = self.load_guild_data()
        ticket_pool = []
        total_donations = 0 

        if guild_id not in data:
            return None, None, 0

        for user_id, user_data in data[guild_id].items():
            ticket_pool.extend([user_id] * user_data['tickets'])
            total_donations += user_data['donation']  

        if not ticket_pool:
            return None, None, 0

        winning_ticket = random.choice(ticket_pool)
        winner_id = winning_ticket
        winner_data = data[guild_id][winner_id]

        prize_amount = int(total_donations * 0.89)  

        return winner_id, winner_data, prize_amount

    @commands.command()
    async def set_lottery_channel(self, ctx):
        print("set_lottery_channel command invoked")
        config = self.load_config()
        guild_config = config.get(str(ctx.guild.id), {})
        guild_config['channel_id'] = ctx.channel.id
        config[str(ctx.guild.id)] = guild_config
        self.save_config(config)
        await ctx.send(f'This channel has been set for the lottery!')

    @commands.command()
    async def set_lottery_time(self, ctx, start_time: str):
        print("set_lottery_time command invoked")
        try:
            datetime.strptime(start_time, "%H:%M")
        except ValueError:
            await ctx.send("Invalid time format! Please provide the time in HH:MM format.")
            return
        
        config = self.load_config()
        guild_config = config.get(str(ctx.guild.id), {})
        guild_config['start_time'] = start_time
        config[str(ctx.guild.id)] = guild_config
        self.save_config(config)
        await ctx.send(f'The lottery start time has been set to {start_time}!')

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore messages sent by the bot itself
        if message.author == self.bot.user:
            return

        config = self.load_config()
        guild_id = str(message.guild.id)
        channel_id = config.get(guild_id, {}).get('channel_id')

        if channel_id and message.channel.id == int(channel_id):
            if message.author.id == ELEMENT_BOT_ID and message.embeds:
                embed = message.embeds[0].to_dict()
                description = embed.get('description', '')
                if "Donation Added" in description:
                    description_lines = description.split('\n')
                    for line in description_lines:
                        if "Donation Added" in line:
                            try:
                                print(f"Parsing line: {line}")
                                amount_donated = int(line.split('-')[1].strip().replace(',', '').replace('**', ''))
                                print(f"Amount donated: {amount_donated}")
                            except ValueError as e:
                                print(f"Error parsing donation amount: {e}")
                                continue

                    if message.mentions:
                        user = message.mentions[0]
                        tickets = amount_donated // 10000
                        total_tickets = self.add_tickets(guild_id, user, tickets)

                        ticket_embed = discord.Embed(
                            title="Tickets Received",
                            description=f'{user.mention} received {tickets} tickets! Total tickets: {total_tickets}',
                            color=discord.Color.green()
                        )
                        ticket_embed.set_footer(text="Built by renivier")
                        await message.channel.send(embed=ticket_embed)

        await self.bot.process_commands(message)

    def add_tickets(self, guild_id, user, tickets):
        data = self.load_guild_data()
        user_data = data.get(guild_id, {}).get(str(user.id), {'tickets': 0, 'donation': 0})
        user_data['tickets'] += tickets
        user_data['donation'] += tickets * 10000
        if guild_id not in data:
            data[guild_id] = {}
        data[guild_id][str(user.id)] = user_data
        self.save_guild_data(data)
        return user_data['tickets']

async def setup(bot):
    await bot.add_cog(Lottery(bot))
    print("Lottery cog setup completed")
