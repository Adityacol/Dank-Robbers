import discord
from discord.ext import commands
from redbot.core import commands, Config, data_manager
import random
import asyncio
from datetime import datetime, timedelta
import json

ELEMENT_BOT_ID = 957635842631950379
LOTTERY_DURATION = 60 * 5  # 5 minutes for testing

class Lottery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(
            channel_id=None,
            end_time=None,
            start_time=None
        )
        self.tickets_path = data_manager.cog_data_path(self) / "guild_tickets.json"
        print(f"Tickets path: {self.tickets_path}")
        self.lottery_running = set()
        self.bot.loop.create_task(self.check_lottery_on_startup())

    def cog_unload(self):
        print("Lottery cog unloaded")

    async def check_lottery_on_startup(self):
        await self.bot.wait_until_ready()
        now = datetime.utcnow()
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_config in all_guilds.items():
            end_time = guild_config.get('end_time')
            if end_time:
                end_time = datetime.fromisoformat(end_time)
                if now < end_time:
                    guild = self.bot.get_guild(guild_id)
                    if guild and guild_id not in self.lottery_running:
                        self.lottery_running.add(guild_id)
                    await self.start_lottery(guild, (end_time - now).total_seconds())
                else:
                    await self.end_lottery(guild)

    async def start_lottery(self, guild, duration=LOTTERY_DURATION):
        if not guild:
            return
        
        guild_config = await self.config.guild(guild).all()
        end_time = guild_config.get('end_time')
        if not end_time:
            start_time = datetime.utcnow()
            end_time = start_time + timedelta(seconds=duration)
            await self.config.guild(guild).end_time.set(end_time.isoformat())
            channel_id = guild_config.get('channel_id')
            if channel_id:
                channel = self.bot.get_channel(channel_id)
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

            await asyncio.sleep(duration)
            await self.end_lottery(guild)
        else:
            channel_id = guild_config.get('channel_id')
            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send("Lottery is already running!")

    async def end_lottery(self, guild):
        if not guild:
            return
        
        if guild.id in self.lottery_running:
            self.lottery_running.remove(guild.id)

        await self.config.guild(guild).end_time.clear()
        winner_id, winner_data, prize_amount = await self.draw_winner(guild)
        guild_config = await self.config.guild(guild).all()
        channel_id = guild_config.get('channel_id')

        if channel_id:
            channel = self.bot.get_channel(channel_id)
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

    async def draw_winner(self, guild):
        guild_data = self.load_guild_data()
        print(f"Guild Data Before Draw: {guild_data}")
        if str(guild.id) not in guild_data:
            return None, None, 0

        ticket_pool = []
        total_donations = 0

        for user_id, user_data in guild_data[str(guild.id)].items():
            ticket_pool.extend([user_id] * user_data['tickets'])
            total_donations += user_data['donation']

        if not ticket_pool:
            return None, None, 0

        winning_ticket = random.choice(ticket_pool)
        winner_id = winning_ticket
        winner_data = guild_data[str(guild.id)][winner_id]

        prize_amount = int(total_donations * 0.89)

        # Clear the guild_tickets.json file
        self.clear_guild_tickets()
        print(f"Guild Data After Draw: {self.load_guild_data()}")

        return winner_id, winner_data, prize_amount

    def clear_guild_tickets(self):
        print("Clearing guild tickets...")
        with self.tickets_path.open('w') as f:
            json.dump({}, f, indent=4)
        print(f"Contents of {self.tickets_path} after clearing: {self.load_guild_data()}")

    @commands.command()
    @commands.guild_only()
    async def set_lottery_channel(self, ctx):
        await self.config.guild(ctx.guild).channel_id.set(ctx.channel.id)
        await ctx.send(f'This channel has been set for the lottery!')

    @commands.command()
    @commands.guild_only()
    async def set_lottery_time(self, ctx, start_time: str):
        try:
            datetime.strptime(start_time, "%H:%M")
        except ValueError:
            await ctx.send("Invalid time format! Please provide the time in HH:MM format.")
            return

        await self.config.guild(ctx.guild).start_time.set(start_time)
        await ctx.send(f'The lottery start time has been set to {start_time}!')

        now = datetime.utcnow()
        start_time_dt = datetime.strptime(start_time, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
        if now < start_time_dt:
            await asyncio.sleep((start_time_dt - now).total_seconds())
            await self.start_lottery(ctx.guild, (start_time_dt - now).total_seconds())

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        guild = message.guild
        guild_config = await self.config.guild(guild).all()
        channel_id = guild_config.get('channel_id')

        if channel_id and message.channel.id == channel_id:
            if message.author.id == ELEMENT_BOT_ID and message.embeds:
                embed = message.embeds[0].to_dict()
                description = embed.get('description', '')
                if "Donation Added" in description:
                    description_lines = description.split('\n')
                    amount_donated = 0
                    for line in description_lines:
                        if "Donation Added" in line:
                            try:
                                amount_donated = int(line.split('-')[1].strip().replace(',', '').replace('**', ''))
                            except ValueError as e:
                                print(f"Error parsing donation amount: {e}")
                                continue

                    if message.mentions:
                        user = message.mentions[0]
                        tickets = amount_donated // 10000
                        total_tickets = await self.add_tickets(guild, user, tickets)

                        ticket_embed = discord.Embed(
                            title="Tickets Received",
                            description=f'{user.mention} received {tickets} tickets! Total tickets: {total_tickets}',
                            color=discord.Color.green()
                        )
                        ticket_embed.set_footer(text="Built by renivier")
                        await message.channel.send(embed=ticket_embed)

    async def add_tickets(self, guild, user, tickets):
        guild_data = self.load_guild_data()
        print(f"Guild Data Before Adding Tickets: {guild_data}")

        if str(guild.id) not in guild_data:
            guild_data[str(guild.id)] = {}

        if str(user.id) not in guild_data[str(guild.id)]:
            guild_data[str(guild.id)][str(user.id)] = {
                'tickets': 0,
                'donation': 0,
                'username': user.name,
                'guild_id': str(guild.id)
            }

        guild_data[str(guild.id)][str(user.id)]['tickets'] += tickets
        guild_data[str(guild.id)][str(user.id)]['donation'] += tickets * 10000  # Each ticket costs 10,000 coins

        self.save_guild_data(guild_data)
        print(f"Guild Data After Adding Tickets: {guild_data}")
        return guild_data[str(guild.id)][str(user.id)]['tickets']

    def load_guild_data(self):
        print(f"Loading guild data from {self.tickets_path}")
        if self.tickets_path.exists():
            with self.tickets_path.open('r') as f:
                data = json.load(f)
                print(f"Data loaded: {data}")
                return data
        return {}

    def save_guild_data(self, data):
        print(f"Saving guild data to {self.tickets_path}")
        with self.tickets_path.open('w') as f:
            json.dump(data, f, indent=4)
        print(f"Data saved: {data}")

    @commands.command()
    @commands.guild_only()
    async def start_lottery_now(self, ctx):
        if ctx.author.guild_permissions.administrator:
            await self.start_lottery(ctx.guild)
            await ctx.send("Lottery started manually.")
        else:
            await ctx.send("You do not have permission to start the lottery.")

    @commands.command()
    @commands.guild_only()
    async def end_lottery_now(self, ctx):
        if ctx.author.guild_permissions.administrator:
            await self.end_lottery(ctx.guild)
            await ctx.send("Lottery ended manually.")
        else:
            await ctx.send("You do not have permission to end the lottery.")
