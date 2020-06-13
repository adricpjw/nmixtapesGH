import asyncio
import functools
import itertools
import math
import random
import re
import inspect
from FlipCoin import Chance


import discord
import youtube_dl
from async_timeout import timeout
from discord.ext import commands

import urllib.request
from bs4 import BeautifulSoup

import os
TOKEN = os.getenv('DISCORD_TOKEN')

# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ''


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass


class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': True,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
    }

    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)

    def __init__(self, ctx: commands.Context, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.parse_duration(int(data.get('duration')))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')

    def __str__(self):
        return '**{0.title}** by **{0.uploader}**'.format(self)

    @classmethod
    async def url_source(cls, ctx: commands.Context, webpage_url: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()
        
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Couldn\'t fetch `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Couldn\'t retrieve any matches for `{}`'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS), data=info)

    @classmethod
    async def create_source(cls, ctx: commands.Context, search: str, *, loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info, search, download=False, process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('Couldn\'t find anything that matches `{}`'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info, webpage_url, download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Couldn\'t fetch `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Couldn\'t retrieve any matches for `{}`'.format(webpage_url))

        return cls(ctx, discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS), data=info)

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if days > 0:
            duration.append('{} days'.format(days))
        if hours > 0:
            duration.append('{} hours'.format(hours))
        if minutes > 0:
            duration.append('{} minutes'.format(minutes))
        if seconds > 0:
            duration.append('{} seconds'.format(seconds))

        return ', '.join(duration)


class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self):
        embed = (discord.Embed(title='Now playing',
                               description='```css\n{0.source.title}\n```'.format(self),
                               color=discord.Color.blurple())
                 .add_field(name='Duration', value=self.source.duration)
                 .add_field(name='Requested by', value=self.requester.mention)
                 .add_field(name='Uploader', value='[{0.source.uploader}]({0.source.uploader_url})'.format(self))
                 .add_field(name='URL', value='[Click]({0.source.url})'.format(self))
                 .set_thumbnail(url=self.source.thumbnail))

        return embed


class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]

class Radio:
    def __init__(self,voice_state,bot:commands.Bot,ctx:commands.Context):
        self.bot = bot
        self._ctx = ctx
        self.status = False
        self.artists = []
        self.voice_state = voice_state

    async def _radioadd(self,split):
        ''' adds artist to radio list'''
        self.artists.extend(split)
    

    async def _radioremove(self,split):
        '''removes artist from radio list'''
        for artist in split:
            try:
                self.artists.remove(artist)
            except:
                pass

    async def _radioclear(self,split):
        '''clears the radio list and sets status to False'''
        self.artists = []
        self.status = False

    async def _radioplay(self,*args):
        '''plays the first song found by quering random artist name'''
        if not self.artists:
            await self._ctx.send('No Artist added.')
            return
        
        self.status = True
        maxi = len(self.artists) - 1
        artist = self.artists[random.randint(0,maxi)]
        source = await YTDLSource.create_source(self._ctx, artist, loop=self.bot.loop)
        song = Song(source)
        self.voice_state.play = True
        await self.voice_state.songs.put(song)
        await self._ctx.send('Enqueued {}'.format(str(source)))
    
    @classmethod
    async def _radiohelp(cls):
        '''Shows this message'''
        embed = discord.Embed(
            title ='Neil\'s Hottest Radio Mix Generator',
            description = 'Generates radio playlist from your favorite artists!'
        )
        methods = [(name.replace('_radio','-'),f.__doc__) \
            for name,f in inspect.getmembers(cls) if not name.startswith('__')]
        full_text = ''
        for method in methods:
            full_text += '\n'
            full_text += method[0]
            try:
                full_text += ': ' + method[1]
            except:
                full_text += ': ' + 'None'
        embed.add_field(name = 'Commands', value = full_text, inline=False)
        return embed

    async def add_rec_artist(self):
        if self.status:
            try:
                print('getting artist')
                match = await self.voice_state.song_regex(self.voice_state.current.source.title)
                artist = match.group('artist').strip()
                print(artist)
            except IndexError:
                print('no artist')
            else:
                if not re.search(artist,', '.join(self.artists),re.I) \
                    and not re.search(artist, ', '.join([' '.join(i) for i in self.voice_state.songnames]), re.I):
                    await self._ctx.send('Add ' + artist + ' to artists ? : y/n')
                    try:
                        confirmation = await self.bot.wait_for('message', \
                            check= lambda msg: (msg.content == 'y' or msg.content == 'n' or msg.content == '!skip') and msg.author != self.bot.user, timeout=10)
                        if confirmation.content == '!skip':
                            raise Exception('Skip called')
                    except asyncio.TimeoutError:
                        print('no response')
                    except:
                        print('moving on')
                    else:
                        if confirmation.content.lower() == 'y':
                            self.artists.append(artist)


class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx
        self.play = True #changed to False with stop function and True with play command

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()
        self.lastplayed = None #save last source used
        self.songnames = [] # save search terms last 10 songs played
        self.radio = Radio(self,self.bot,self._ctx)

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    async def song_regex(self,song):
        '''Returns match object with artist and song groups. Takes video title as string'''
        p1 = r"(?P<artist>.+)[-:](?P<song>.*)\s(?=[\[(])"
        p2 = r"(?P<song>.+)by(?P<artist>.*)\s(?=[\[(])"
        p3 = r"(?P<artist>.+)[-:](?P<song>.*)"
        p4 = r"(?P<song>.+)by(?P<artist>.*)"
        p5 = r"(?P<song>.+)"
        if not song:
            return
        if re.search(p1,song):
            match = re.search(p1,song)
        elif re.search(p2,song):
            match = re.search(p2,song)
        elif re.search(p3,song):
            match = re.search(p3,song)
        elif re.search(p4,song):
            match = re.search(p4,song)
        else:
            match = re.search(p5,song)
        return match
    
    async def song_repeat_check(self,song):
        '''Returns True if song matches any list of search terms from last 20 songs, False otherwise.'''
        if self.songnames:
            return any([all([re.search(re.escape(item),song,re.I) for item in name]) for name in self.songnames])
        else:
            return False
    
    async def update_songnames(self,song):
        '''Retrieves search terms from current song and updates song names'''
        if len(self.songnames) < 10:
            match = await self.song_regex(song)
            self.songnames.append([term.strip() for term in match.groups()])
        else:
            self.songnames = self.songnames[1:]
            match = await self.song_regex(song)
            self.songnames.append([term.strip() for term in match.groups()])

    async def get_rec(self,url):
        '''Takes last video played and returns Song Object of next video in generated mix'''
        async def bs4_helper(url,repeated = False):
            if self.radio.status and self.radio.artists and not repeated:
                print('radio rec')
                maxi = len(self.radio.artists)
                rand = random.randint(0,maxi)
                if rand != maxi:
                    artist = self.radio.artists[rand]
                    print('radio 2')
                    source = await YTDLSource.create_source(self._ctx, artist, loop=self.bot.loop)
                    url = source.url
                    print('radio 3')
                    print(url)
            start = url.find('=')
            videoid = url[start+1:start+12]
            url += "&list=RD" + videoid
            recommended_url = 'https://www.youtube.com'
            n = 0
            body = urllib.request.urlopen(url)
            print(url)
            soup = BeautifulSoup(body, from_encoding=body.info().get_param('charset'), features = 'html.parser')
            reclst = []
            n = 5
            for link in soup.find_all('a', href=True):
                toAppend = link['href']
                if n > 0:
                    if "/watch" in toAppend :
                        reclst.append(toAppend)
                        n -= 1
                else:
                    break
            
            rand = random.randint(1,4)
            recommended_url += reclst[rand]
            print(recommended_url)
            if recommended_url == 'https://www.youtube.com':
                recommended_url = await bs4_helper(self.lastplayed.url)
            return recommended_url
        recommended_url = await bs4_helper(url)
        async with self._ctx.typing():
            try:
                source = await YTDLSource.url_source(self._ctx,recommended_url,loop=self.bot.loop)
            except YTDLError as e:
                await self._ctx.send('An error occurred while processing this request: {}'.format(str(e)))
            else:
                song = Song(source)
                check = await self.song_repeat_check(source.title)
                max_tries = 5
                while check and max_tries > 0:
                    max_tries -= 1
                    print("Repeated!")
                    try:
                        new_url = await bs4_helper(song.source.url,repeated = True)
                        source = await YTDLSource.url_source(self._ctx,new_url,loop=self.bot.loop)
                    except YTDLError as e:
                        await self._ctx.send('An error occurred while processing this request: {}'.format(str(e)))
                    else:
                        song = Song(source)                    
                    print(song.source.title)
                    check = await self.song_repeat_check(song.source.title)
                return song

    async def audio_player_task(self):

        while self.play: #check if has been stopped
            self.next.clear()

            if not self.loop:
                # If no song get be get, plays recommmendation
                check = len(self.songs)
                if self.lastplayed and not check:
                    print('wewewewewewe')
                    song = await self.get_rec(self.lastplayed.url)
                    await self.songs.put(song)
                    await self._ctx.send('Recommended Song Enqueued {}'.format(str(song.source)))
                self.current = await self.songs.get()
            
            self.current.source.volume = self._volume
            self.lastplayed = self.current.source
            self.voice.play(self.current.source, after=self.play_next_song)
            await self.current.source.channel.send(embed=self.current.create_embed())            
            await self.radio.add_rec_artist()         
            await self.update_songnames(self.current.source.title)
            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()
        self.play = False

        if self.voice:
            await self.voice.disconnect()
            self.voice = None


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}

    def get_voice_state(self, ctx: commands.Context):
        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage('This command can\'t be used in DM channels.')

        return True

    async def cog_before_invoke(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send('An error occurred: {}'.format(str(error)))

    @commands.command(name='join', invoke_without_subcommand=True)
    async def _join(self, ctx: commands.Context):
        """Joins a voice channel."""

        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='summon')
    async def _summon(self, ctx: commands.Context, *, channel: discord.VoiceChannel = None):
        """Summons the bot to a voice channel.

        If no channel was specified, it joins your channel.
        """

        if not channel and not ctx.author.voice:
            raise VoiceError('You are neither connected to a voice channel nor specified a channel to join.')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='leave', aliases=['disconnect'])
    async def _leave(self, ctx: commands.Context):
        """Clears the queue and leaves the voice channel."""

        if not ctx.voice_state.voice:
            return await ctx.send('Not connected to any voice channel.')

        await ctx.voice_state.stop()
        del self.voice_states[ctx.guild.id]

    @commands.command(name='volume')
    async def _volume(self, ctx: commands.Context, *, volume: int):
        """Sets the volume of the player."""

        if not ctx.voice_state.is_playing:
            return await ctx.send('Nothing being played at the moment.')

        if 0 > volume > 100:
            return await ctx.send('Volume must be between 0 and 100')

        ctx.voice_state.volume = volume / 100
        await ctx.send('Volume of the player set to {}%'.format(volume))

    @commands.command(name='now', aliases=['current', 'playing'])
    async def _now(self, ctx: commands.Context):
        """Displays the currently playing song."""

        await ctx.send(embed=ctx.voice_state.current.create_embed())

    @commands.command(name='pause')
    async def _pause(self, ctx: commands.Context):
        """Pauses the currently playing song."""

        if ctx.voice_state.voice.is_playing():
            ctx.voice_state.voice.pause()
            await ctx.message.add_reaction('⏯')

    @commands.command(name='resume')
    async def _resume(self, ctx: commands.Context):
        """Resumes a currently paused song."""

        if ctx.voice_state.voice.is_paused():
            ctx.voice_state.voice.resume()
            await ctx.message.add_reaction('⏯')

    @commands.command(name='clear')
    async def _clear(self, ctx: commands.Context):
        """Stops playing song and clears the queue."""

        ctx.voice_state.songs.clear()

        if ctx.voice_state.is_playing:
            ctx.voice_state.voice.stop()
            await ctx.message.add_reaction('⏹')

    @commands.command(name='skip')
    async def _skip(self, ctx: commands.Context):
        """Vote to skip a song. Anyone can automatically skip.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.send('Not playing any music right now...')

        voter = ctx.message.author
        if voter == ctx.voice_state.current.requester:
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()

        elif voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= 1:
                await ctx.message.add_reaction('⏭')
                ctx.voice_state.skip()
            else:
                await ctx.send('Skip vote added, currently at **{}/3**'.format(total_votes))

        else:
            await ctx.send('You have already voted to skip this song.')

    @commands.command(name='queue')
    async def _queue(self, ctx: commands.Context, *, page: int = 1):
        """Shows the player's queue.

        You can optionally specify the page to show. Each page contains 10 elements.
        """

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

        embed = (discord.Embed(description='**{} tracks:**\n\n{}'.format(len(ctx.voice_state.songs), queue))
                 .set_footer(text='Viewing page {}/{}'.format(page, pages)))
        await ctx.send(embed=embed)

    @commands.command(name='shuffle')
    async def _shuffle(self, ctx: commands.Context):
        """Shuffles the queue."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        ctx.voice_state.songs.shuffle()
        await ctx.message.add_reaction('✅')

    @commands.command(name='remove')
    async def _remove(self, ctx: commands.Context, index: int):
        """Removes a song from the queue at a given index."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.message.add_reaction('✅')

    @commands.command(name='loop')
    async def _loop(self, ctx: commands.Context):
        """Loops the currently playing song.

        Invoke this command again to unloop the song.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.send('Nothing being played at the moment.')

        # Inverse boolean value to loop and unloop.
        ctx.voice_state.loop = not ctx.voice_state.loop
        await ctx.message.add_reaction('✅')

    @commands.command(name='play')
    async def _play(self, ctx: commands.Context, *, search: str):
        """Plays a song.

        If there are songs in the queue, this will be queued until the
        other songs finished playing.

        This command automatically searches from various sites if no URL is provided.
        A list of these sites can be found here: https://rg3.github.io/youtube-dl/supportedsites.html
        """

        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)
                
        async with ctx.typing():
            try:
                source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send('An error occurred while processing this request: {}'.format(str(e)))
            else:
                song = Song(source)
                ctx.voice_state.play = True
                await ctx.voice_state.songs.put(song)
                await ctx.send('Enqueued {}'.format(str(source)))

    @commands.command(name='radio')
    async def _radio(self, ctx: commands.Context, *, lst: str = ''):
        ''' Creates a Radio. !radio -help for more details'''
        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)

        if lst:
            newlst = lst.split()

            commands = {
                    'add': ctx.voice_state.radio._radioadd,
                    'remove': ctx.voice_state.radio._radioremove,
                    'clear': ctx.voice_state.radio._radioclear,
                    'play': ctx.voice_state.radio._radioplay
                }

            if newlst[0] == '-help':
                helpembed = await Radio._radiohelp()
                await ctx.send(embed = helpembed)
                return

            if newlst[0][0] == '-':
                try:
                    string = lst[len(newlst[0])+1:]
                    split = [i.strip() for i in string.split(',')]
                    command = newlst[0][1:]
                    run = commands[command]
                    await run(split)
                except KeyError:
                    await ctx.send('Invalid command, either -add or -remove')    
                    return
            else:
                ctx.voice_state.radio.artists = [i.strip() for i in lst.split(',')]

        page = 1
        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.radio.artists) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, artist in enumerate(ctx.voice_state.radio.artists[start:end], start=start):
            queue += '`{0}.` [**{1}**]\n'.format(i + 1, artist)

        embed = (discord.Embed(description='**{} Artists:**\n\n{}'.format(len(ctx.voice_state.radio.artists), queue))
                 .set_footer(text='Viewing page {}/{}'.format(page, pages)))
        await ctx.send(embed=embed)
    

    @commands.command(name='shutdown')
    async def _shutdown(self, ctx: commands.Context):
        await self.bot.logout()


    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('You are not connected to any voice channel.')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Bot is already in a voice channel.')

bot = commands.Bot('!', description='Neil\'s fire mixtapes.')
bot.add_cog(Music(bot))
bot.add_cog(Chance(bot))

@bot.event
async def on_ready():
    print('Logged in as:\n{0.user.name}\n{0.user.id}'.format(bot))

bot.run(TOKEN)