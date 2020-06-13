import asyncio
import itertools
import random
import math
import discord
from discord.ext import commands


class Chance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send('An error occurred: {}'.format(str(error)))

    @commands.command(name = 'flip')
    async def _flip(self, ctx:commands.Context):
        '''Flips a coin : Heads / Tails'''

        rand = random.randint(1,100)
        if rand <=50:
            await ctx.send('**{}** has coin flipped a **Heads!**'.format(ctx.author))
        else:
            await ctx.send('**{}** has coin flipped a **Tails!**'.format(ctx.author))

    @commands.command(name = 'roll')
    async def _roll(self, ctx:commands.Context, *, dice:str):
        '''Rolls a dice. Specify no. of sides eg. !roll d20'''
        split = dice.split(' ')
        calls = [call for call in split if 'd' in call]
        print(calls)
        if not calls:
            await ctx.send('Please specify num of sides with prefix \'d\'. eg: \'d20\'')
            return

        rolls = []
        for call in calls:
            d_pos = call.find('d')
            print(d_pos)
            if d_pos == 0:
                rolls += [(1,call[1:])]
            else:
                rolls += [(call[:d_pos],call[d_pos+1:])]
                print(rolls)
        
        for roll in rolls:
            num_rolls = int(roll[0])
            numsides = int(roll[1])
            results = ', '.join([str(random.randint(1,numsides)) for i in range(num_rolls)])
            await ctx.send('**{}** has rolled **{}** on a **D{}!**'.format(ctx.author.display_name,results,numsides))



