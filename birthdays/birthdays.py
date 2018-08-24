import inspect
import logging
import typing
import hashlib
import asyncio
import contextlib
import datetime
import discord
import itertools

from discord.ext import commands
from redbot.core import Config, checks
from redbot.core.bot import Red
from redbot.core.i18n import Translator, get_locale
from redbot.core.config import Group
from redbot.core.commands import Context

_ = Translator("Birthdays", __file__)  # pygettext3 -a -n -p locales birthdays.py


class Birthdays:
    """Announces people's birthdays and gives them a birthday role for the whole UTC day"""
    __author__ = "ZeLarpMaster#0818"

    # Behavior related constants
    DATE_GROUP = "DATE"

    def __init__(self, bot: Red):
        self.bot = bot
        self.logger = logging.getLogger("red.ZeCogsV3.birthdays")
        self.inject_before_invokes()
        self.previous_locale = None
        self.reload_translations()
        # force_registration is for weaklings
        unique_id = int(hashlib.sha512((self.__author__ + "@" + self.__class__.__name__).encode()).hexdigest(), 16)
        self.config = Config.get_conf(self, identifier=unique_id)
        self.config.register_global(yesterdays=[])
        self.config.register_guild(channel=None, role=None)
        self.bday_loop = asyncio.ensure_future(self.initialise())  # Starts a loop which checks daily for birthdays

    # Events
    async def initialise(self):
        await self.bot.wait_until_ready()
        with contextlib.suppress(RuntimeError):
            while self == self.bot.get_cog(self.__class__.__name__):  # Stops the loop when the cog is reloaded
                now = datetime.datetime.utcnow()
                tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                await self.clean_yesterday_bdays()
                await self.do_today_bdays()
                await asyncio.sleep((tomorrow - now).total_seconds())
                await self.clean_yesterday_bdays()
                await self.do_today_bdays()

    def __unload(self):
        self.bday_loop.cancel()  # Forcefully cancel the loop when unloaded

    # Commands
    @commands.group(pass_context=True, invoke_without_command=True)
    async def bday(self, ctx: Context):
        """Birthday settings"""
        await ctx.send_help()

    @bday.command(name="channel", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def bday_channel(self, ctx: Context, channel: discord.TextChannel):
        """Sets the birthday announcement channel for this server"""
        message = ctx.message
        guild = message.guild
        await self.config.guild(channel.guild).channel.set(channel.id)
        await message.channel.send(self.CHANNEL_SET.format(g=guild.name, c=channel.name))

    @bday.command(name="role", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def bday_role(self, ctx: Context, role: discord.Role):
        """Sets the birthday role for this server"""
        message = ctx.message
        guild = message.guild
        await self.config.guild(role.guild).role.set(role.id)
        await message.channel.send(self.ROLE_SET.format(g=guild.name, r=role.name))

    @bday.command(name="remove", aliases=["del", "clear", "rm"], pass_context=True)
    async def bday_remove(self, ctx: Context):
        """Unsets your birthday date"""
        message = ctx.message
        await self.remove_user_bday(message.author.id)
        await message.channel.send(self.BDAY_REMOVED)

    @bday.command(name="set", pass_context=True)
    async def bday_set(self, ctx: Context, date, year: int=None):
        """Sets your birthday date

        The given date must be given as: MM-DD
        Year is optional. If ungiven, the age won't be displayed."""
        message = ctx.message
        channel = message.channel
        author = message.author
        birthday = self.parse_date(date)
        if birthday is None:
            await channel.send(self.BDAY_INVALID)
        else:
            await self.remove_user_bday(author.id)
            await self.get_date_config(birthday.toordinal()).get_attr(author.id).set(year)
            bday_month_str = birthday.strftime("%B")
            bday_day_str = birthday.strftime("%d").lstrip("0")  # To remove the zero-capped
            await channel.send(self.BDAY_SET.format(bday_month_str + " " + bday_day_str))

    @bday.command(name="list", pass_context=True)
    async def bday_list(self, ctx: Context):
        """Lists the birthdays

        If a user has their year set, it will display the age they'll get after their birthday this year"""
        message = ctx.message
        await self.clean_bdays()
        bdays = await self.get_all_date_configs()
        this_year = datetime.date.today().year
        embed = discord.Embed(title=self.BDAY_LIST_TITLE, color=discord.Colour.lighter_grey())
        for k, g in itertools.groupby(sorted(datetime.datetime.fromordinal(int(o)) for o in bdays.keys()),
                                      lambda i: i.month):
            # Basically separates days with "\n" and people on the same day with ", "
            value = "\n".join(date.strftime("%d").lstrip("0") + ": "
                              + ", ".join("<@!{}>".format(u_id)
                                          + ("" if year is None else " ({})".format(this_year - int(year)))
                                          for u_id, year in bdays.get(str(date.toordinal()), {}).items())
                              for date in g if len(bdays.get(str(date.toordinal()))) > 0)
            if not value.isspace():  # Only contains whitespace when there's no birthdays in that month
                embed.add_field(name=datetime.datetime(year=1, month=k, day=1).strftime("%B"), value=value)
        await message.channel.send(embed=embed)

    # Utilities
    async def clean_bday(self, user_id: int):
        all_guild_configs = await self.config.all_guilds()
        for guild_id, guild_config in all_guild_configs.items():
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                role = discord.utils.get(guild.roles, id=guild_config.get("role"))
                # If discord.Server.roles was an OrderedDict instead...
                member = guild.get_member(user_id)
                if member is not None and role is not None and role in member.roles:
                    # If the user and the role are still on the server and the user has the bday role
                    await member.remove_roles(role)

    async def handle_bday(self, user_id: int, year: str):
        embed = discord.Embed(color=discord.Colour.gold())
        if year is not None:
            age = datetime.date.today().year - int(year)  # Doesn't support non-eastern age counts but whatever
            embed.description = self.BDAY_WITH_YEAR.format(user_id, age)
        else:
            embed.description = self.BDAY_WITHOUT_YEAR.format(user_id)
        all_guild_configs = await self.config.all_guilds()
        for guild_id, guild_config in all_guild_configs.items():
            guild = self.bot.get_guild(guild_id)
            if guild is not None:  # Ignore unavailable servers or servers the bot isn't in anymore
                member = guild.get_member(user_id)
                if member is not None:
                    role_id = guild_config.get("role")
                    if role_id is not None:
                        role = discord.utils.get(guild.roles, id=role_id)
                        if role is not None:
                            try:
                                await member.add_roles(role)
                            except (discord.Forbidden, discord.HTTPException):
                                pass
                            else:
                                async with self.config.yesterdays() as yesterdays:
                                    yesterdays.append(member.id)
                    channel = guild.get_channel(guild_config.get("channel"))
                    if channel is not None:
                        await channel.send(embed=embed)

    async def clean_bdays(self):
        """Cleans the birthday entries with no user's birthday
        Also removes birthdays of users who aren't in any visible server anymore

        Happens when someone changes their birthday and there's nobody else in the same day"""
        birthdays = await self.get_all_date_configs()
        for date, bdays in birthdays.copy().items():
            for user_id, year in bdays.copy().items():
                if not any(g.get_member(int(user_id)) is not None for g in self.bot.guilds):
                    async with self.get_date_config(date)() as config_bdays:
                        del config_bdays[user_id]
            config_bdays = await self.get_date_config(date)()
            if len(config_bdays) == 0:
                await self.get_date_config(date).clear()

    async def remove_user_bday(self, user_id: int):
        user_id = str(user_id)
        birthdays = await self.get_all_date_configs()
        for date, user_ids in birthdays.items():
            if user_id in user_ids:
                await self.get_date_config(date).get_attr(user_id).clear()
        # Won't prevent the cleaning problem here cause the users can leave so we'd still want to clean anyway

    async def clean_yesterday_bdays(self):
        yesterdays = await self.config.yesterdays()
        for user_id in yesterdays:
            asyncio.ensure_future(self.clean_bday(user_id))
        await self.config.yesterdays.clear()

    async def do_today_bdays(self):
        this_date = datetime.datetime.utcnow().date().replace(year=1)
        todays_bday_config = await self.get_date_config(this_date.toordinal()).all()
        for user_id, year in todays_bday_config.items():
            asyncio.ensure_future(self.handle_bday(int(user_id), year))

    def parse_date(self, date_str: str):
        result = None
        try:
            result = datetime.datetime.strptime(date_str, "%m-%d").date().replace(year=1)
        except ValueError:
            pass
        return result

    # Utilities - Config
    def get_date_config(self, date: int) -> Group:
        return self.config.custom(self.DATE_GROUP, str(date))

    async def get_all_date_configs(self) -> Group:
        return await self.config.custom(self.DATE_GROUP).all()

    def reload_translations(self):
        if self.previous_locale == get_locale():
            return  # Don't care if the locale hasn't changed

        # Embed constants
        self.BDAY_LIST_TITLE = _("Birthday List")

        # Logging message constants

        # Message constants
        self.BDAY_WITH_YEAR = _("<@!{}> is now **{} years old**. :tada:")
        self.BDAY_WITHOUT_YEAR = _("It's <@!{}>'s birthday today! :tada:")
        self.ROLE_SET = _(":white_check_mark: The birthday role on **{g}** has been set to: **{r}**.")
        self.BDAY_INVALID = _(":x: The birthday date you entered is invalid. It must be `MM-DD`.")
        self.BDAY_SET = _(":white_check_mark: Your birthday has been set to: **{}**.")
        self.CHANNEL_SET = _(":white_check_mark: "
                             "The channel for announcing birthdays on **{g}** has been set to: **{c}**.")
        self.BDAY_REMOVED = _(":put_litter_in_its_place: Your birthday has been removed.")

    def log(self, logging_func: typing.Callable, *args, **kwargs):
        self.reload_translations()
        logging_func(*args, **kwargs)

    def info(self, msg: typing.Callable[[], str], *args, **kwargs):
        self.log(self.logger.info, msg().format(*args, **kwargs))

    def warn(self, msg: typing.Callable[[], str], *args, **kwargs):
        self.log(self.logger.warning, msg().format(*args, **kwargs))

    def inject_before_invokes(self):
        for name, value in inspect.getmembers(self, lambda o: isinstance(o, commands.Command)):
            async def wrapped_reload(*_):
                self.reload_translations()
            value.before_invoke(wrapped_reload)
