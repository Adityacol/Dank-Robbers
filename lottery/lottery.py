import discord
from discord.ext import tasks
from redbot.core import commands, Config, data_manager
import random
import asyncio
from datetime import datetime, timedelta
import json

ELEMENT_BOT_ID = 957635842631950379
LOTTERY_DURATION = 60 * 60 * 24
PAYMENT_ROLE_ID = 1018578013140566137
NOTIFICATION_ROLE_ID = 1198618127336996914

class Lottery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.config.register_guild(
            channel_id=None,
            end_time=None,
            start_time=None,
            winner_channel_id=None,
            payout_channel_id=None
        )
        self.tickets_path = data_manager.cog_data_path(self) / "guild_tickets.json"
        print(f"Tickets path: {self.tickets_path}")
        self.lottery_running = set()
        self.sent_embeds = {}  # Dictionary to keep track of sent embeds
        self.start_lottery_task.start()

    def cog_unload(self):
        self.start_lottery_task.cancel()
        print("Lottery cog unloaded")

    @tasks.loop(seconds=60)  # Check every minute
    async def start_lottery_task(self):
        now = datetime.utcnow()
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_config in all_guilds.items():
            start_time_str = guild_config.get('start_time')
            if start_time_str:
                start_time = datetime.strptime(start_time_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
                if now >= start_time and now < start_time + timedelta(minutes=1):
                    guild = self.bot.get_guild(int(guild_id))
                    if guild and guild_id not in self.lottery_running:
                        self.lottery_running.add(guild_id)
                        await self.start_lottery(guild)

    async def check_lottery_on_startup(self):
        await self.bot.wait_until_ready()
        now = datetime.utcnow()
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_config in all_guilds.items():
            end_time_str = guild_config.get('end_time')
            if end_time_str:
                end_time = datetime.fromisoformat(end_time_str)
                if now < end_time:
                    guild = self.bot.get_guild(int(guild_id))
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
            channel_id = guild_config.get('winner_channel_id')  # Use winner_channel_id for the start embed
            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    start_embed = discord.Embed(
                        title="<a:dr_zcash:1075563572924530729> Lottery Started! <a:dr_zcash:1075563572924530729>",
                        description=(
                            "<a:dr_zarrow:1075563743477497946>The lottery is starting now! Donate to participate.\n\n"
                            "<a:dr_zarrow:1075563743477497946>Buy tickets in https://discord.com/channels/895344237204369458/1252643231888572516\n"
                            "<a:dr_zarrow:1075563743477497946>Each ticket costs 10,000 dank memer coins.\n"
                            "<a:dr_zarrow:1075563743477497946>The more tickets you get, the higher your chances of winning!\n "
                            "<a:dr_zarrow:1075563743477497946>All your tickets have numerical values, so don't worry about it bugging out!\n "
                            "<a:dr_zarrow:1075563743477497946>The prize will depend on how much money people spend on buying tickets.\n\n "
                            "<a:dh_Gold_Dot:1235540381047717908> **GOOD LUCK** !<a:dh_Gold_Dot:1235540381047717908>"
                        ),
                        color=discord.Color.green()
                    )
                    start_embed.set_thumbnail(url="https://i.imgur.com/AfFp7pu.png")  # Example thumbnail, you can replace it
                    start_embed.set_footer(text="Built by renivier")
                    await channel.send(content=f"<@&{NOTIFICATION_ROLE_ID}>", embed=start_embed, mention=True)  # Ping outside the embed

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
        winner_id, winner_data, prize_amount, total_tickets, total_users = await self.draw_winner(guild)
        guild_config = await self.config.guild(guild).all()
        winner_channel_id = guild_config.get('winner_channel_id')
        payout_channel_id = guild_config.get('payout_channel_id')
        
        if winner_id is None:
            if winner_channel_id:
                winner_channel = self.bot.get_channel(winner_channel_id)
                if winner_channel:
                    await winner_channel.send("No valid entries were found for the lottery.")
            return

        if winner_channel_id:
            winner_channel = self.bot.get_channel(winner_channel_id)
            if winner_channel:
                winner = await self.bot.fetch_user(int(winner_id))
                entries = winner_data['donation'] // 10000
                winner_embed = discord.Embed(
                    title="<a:dr_gaw:1233462035787022429> Lottery Winner <a:dr_gaw:1233462035787022429>",
                    description=f"{winner.mention} walked away with ⏣ **{prize_amount:,}**",
                    color=discord.Color.gold()
                )
                winner_embed.add_field(name="They paid:", value=f"🪙 {winner_data['donation']:,} ({entries} entries)", inline=False)
                winner_embed.add_field(name="<a:dr_zarrow:1075563743477497946> Users", value=f"<:bluedot:1233471404884885545> {total_users}", inline=False)
                winner_embed.add_field(name="<a:dr_zarrow:1075563743477497946> Total Tickets", value=f"<:bluedot:1233471404884885545> {total_tickets}", inline=False)
                winner_embed.set_thumbnail(url=winner.avatar.url)
                winner_embed.set_footer(text="Built by renivier")

                await winner_channel.send(content=f"{winner.mention}", embed=winner_embed)
                await self.start_lottery(guild)

        if payout_channel_id:
            payout_channel = self.bot.get_channel(payout_channel_id)
            if payout_channel:
                payout_command = f"/serverevents payout user:{winner.id} quantity:{prize_amount}"
                payout_embed = discord.Embed(
                    title="🏆 Payout Command 🏆",
                    description=f"Congratulations {winner.mention}!\n\nPayout Command\n```{payout_command}```",
                    color=discord.Color.blue()
                )
                payout_embed.set_footer(text="Lottery Winner")

                message = await payout_channel.send(embed=payout_embed)
                self.sent_embeds[message.id] = {"winner_id": winner.id, "prize_amount": prize_amount}
                await message.add_reaction("⏳")

                def check(reaction, user):
                    return user != self.bot.user and str(reaction.emoji) == "⏳" and reaction.message.id == message.id

                while True:
                    reaction, user = await self.bot.wait_for("reaction_add", check=check)
                    if PAYMENT_ROLE_ID in [role.id for role in user.roles]:
                        updated_embed = payout_embed.copy()
                        updated_embed.title = "🏆 Payout Confirmed 🏆"
                        updated_embed.description = f"Congratulations {winner.mention}!\n\nPaid by {user.mention}"
                        await message.edit(embed=updated_embed)
                        await message.clear_reaction("⏳")
                        await message.add_reaction("👍")
                        break
                    else:
                        await message.remove_reaction(reaction, user)

    async def draw_winner(self, guild):
        guild_data = self.load_guild_data()
        print(f"Guild Data Before Draw: {guild_data}")
        if str(guild.id) not in guild_data or not guild_data[str(guild.id)]:
            return None, None, 0, 0, 0

        ticket_pool = []
        total_donations = 0
        total_users = len(guild_data[str(guild.id)])

        for user_id, user_data in guild_data[str(guild.id)].items():
            ticket_pool.extend([user_id] * user_data['tickets'])
            total_donations += user_data['donation']

        if not ticket_pool:
            return None, None, 0, 0, 0

        total_tickets = len(ticket_pool)
        winning_ticket = random.choice(ticket_pool)
        winner_id = winning_ticket
        winner_data = guild_data[str(guild.id)][winner_id]

        prize_amount = int(total_donations * 0.89)

        # Clear the guild_tickets.json file
        self.clear_guild_tickets()
        print(f"Guild Data After Draw: {self.load_guild_data()}")

        return winner_id, winner_data, prize_amount, total_tickets, total_users

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

    @commands.command()
    @commands.guild_only()
    async def set_lottery_winner_channel(self, ctx):
        await self.config.guild(ctx.guild).winner_channel_id.set(ctx.channel.id)
        await ctx.send(f'This channel has been set for lottery winner announcements!')

    @commands.command()
    @commands.guild_only()
    async def set_payout_channel(self, ctx):
        await self.config.guild(ctx.guild).payout_channel_id.set(ctx.channel.id)
        await ctx.send(f'This channel has been set for payout messages!')

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
                            title="<a:dr_zcash:1075563572924530729> Tickets Received <a:dr_zcash:1075563572924530729>",
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
                try:
                    data = json.load(f)
                    print(f"Data loaded: {data}")
                    return data
                except json.JSONDecodeError:
                    print("Error decoding JSON file")
                    return {}
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

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        payout_channel_ids = [guild_config['payout_channel_id'] for guild_config in await self.config.all_guilds().values()]
        if payload.channel_id in payout_channel_ids and str(payload.emoji) == "⏳":
            message_id = payload.message_id
            if message_id in self.sent_embeds:
                guild = self.bot.get_guild(payload.guild_id)
                member = guild.get_member(payload.user_id)
                if member and discord.utils.get(member.roles, id=PAYMENT_ROLE_ID):
                    await self.process_payment(message_id, member.id)
                else:
                    channel = self.bot.get_channel(payload.channel_id)
                    message = await channel.fetch_message(message_id)
                    await message.remove_reaction(payload.emoji, member)

    async def process_payment(self, message_id, payer_id):
        payout_channel_id = next(guild_config['payout_channel_id'] for guild_config in await self.config.all_guilds().values() if self.sent_embeds.get(message_id))
        target_channel = self.bot.get_channel(payout_channel_id)
        if target_channel:
            embed_info = self.sent_embeds.get(message_id)
            if embed_info:
                winner_id = embed_info["winner_id"]
                prize_amount = embed_info["prize_amount"]
                payer_user = await self.bot.fetch_user(payer_id)
                embed_message = await target_channel.fetch_message(message_id)
                embed = embed_message.embeds[0]
                embed.title = "🏆 Payout Confirmed 🏆"
                embed.description = f"Congratulations <@{winner_id}>!\n\nPaid by {payer_user.mention}"
                await embed_message.edit(embed=embed)
                await embed_message.clear_reaction("⏳")
                await embed_message.add_reaction("👍")
                del self.sent_embeds[message_id]

async def setup(bot):
    cog = Lottery(bot)
    await bot.add_cog(cog)
