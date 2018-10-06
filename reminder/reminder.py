import inspect
import logging
import typing
import hashlib
import re
import collections
import discord
import datetime
import asyncio

from redbot.core import commands
from redbot.core import Config
from redbot.core.bot import Red
from redbot.core.i18n import Translator, get_locale
from redbot.core.commands import Context

_ = Translator("Reminder", __file__)  # pygettext3 -a -n -p locales reminder.py
BaseCog = getattr(commands, "Cog", object)


class Reminder(BaseCog):
    """Utilities to remind yourself of whatever you want"""
    __author__ = "ZeLarpMaster#0818"

    # Behavior related constants
    TIME_AMNT_REGEX = re.compile("([1-9][0-9]*)([a-z]+)", re.IGNORECASE)
    TIME_QUANTITIES = collections.OrderedDict([("seconds", 1), ("minutes", 60),
                                               ("hours", 3600), ("days", 86400),
                                               ("weeks", 604800), ("months", 2.628e+6),
                                               ("years", 3.154e+7)])  # (amount in seconds, max amount)
    MAX_SECONDS = TIME_QUANTITIES["years"] * 2

    def __init__(self, bot: Red):
        self.bot = bot
        self.logger = logging.getLogger("red.ZeCogsV3.reminder")
        self.inject_before_invokes()
        self.previous_locale = None
        self.reload_translations()
        # force_registration is for weaklings
        unique_id = int(hashlib.sha512((self.__author__ + "@" + self.__class__.__name__).encode()).hexdigest(), 16)
        self.config = Config.get_conf(self, identifier=unique_id)
        self.config.register_user(reminders=[])
        self.futures = []
        asyncio.ensure_future(self.start_saved_reminders())

    # Events
    def __unload(self):
        for future in self.futures:
            future.cancel()

    # Commands
    @commands.command(pass_context=True)
    async def remind(self, ctx: Context, time, *, text):
        """Remind yourself of something in a specific amount of time

        Examples for time: `5d`, `10m`, `10m30s`, `1h`, `1y1mo2w5d10h30m15s`
        Abbreviations: s for seconds, m for minutes, h for hours, d for days, w for weeks, mo for months, y for years
        Any longer abbreviation is accepted. `m` assumes minutes instead of months.
        One month is counted as exact 365/12 days.
        Ignores all invalid abbreviations."""
        message = ctx.message
        seconds = self.get_seconds(time)
        if seconds is None:
            response = self.INVALID_TIME_FORMAT
        elif seconds >= self.MAX_SECONDS:
            response = self.TOO_MUCH_TIME.format(round(self.MAX_SECONDS))
        else:
            user = message.author
            time_now = datetime.datetime.utcnow()
            days, secs = divmod(seconds, 3600*24)
            end_time = time_now + datetime.timedelta(days=days, seconds=secs)
            reminder = {"content": text, "start_time": time_now.timestamp(), "end_time": end_time.timestamp()}
            async with self.config.user(user).reminders() as user_reminders:
                user_reminders.append(reminder)
            self.futures.append(asyncio.ensure_future(self.remind_later(user, seconds, text, reminder)))
            response = self.WILL_REMIND.format(seconds)
        await message.channel.send(response)

    # Utilities
    async def start_saved_reminders(self):
        await self.bot.wait_until_ready()
        user_configs = await self.config.all_users()
        for user_id, user_config in list(user_configs.items()):  # Making a copy
            for reminder in user_config["reminders"]:
                user = self.bot.get_user(user_id)
                if user is None:
                    self.config.remove(reminder)  # Delete the reminder if the user doesn't have a mutual server anymore
                else:
                    time_diff = datetime.datetime.fromtimestamp(reminder["end_time"]) - datetime.datetime.utcnow()
                    time = max(0, time_diff.total_seconds())
                    self.futures.append(asyncio.ensure_future(self.remind_later(user, time, reminder["content"], reminder)))

    async def remind_later(self, user: discord.User, time: float, content: str, reminder):
        """Reminds the `user` in `time` seconds with a message containing `content`"""
        await asyncio.sleep(time)
        embed = discord.Embed(title=self.REMINDER_TITLE, description=content, color=discord.Colour.blue())
        await user.send(embed=embed)
        async with self.config.user(user).reminders() as user_reminders:
            user_reminders.remove(reminder)

    def get_seconds(self, time):
        """Returns the amount of converted time or None if invalid"""
        seconds = 0
        for time_match in self.TIME_AMNT_REGEX.finditer(time):
            time_amnt = int(time_match.group(1))
            time_abbrev = time_match.group(2)
            time_quantity = discord.utils.find(lambda t: t[0].startswith(time_abbrev), self.TIME_QUANTITIES.items())
            if time_quantity is not None:
                seconds += time_amnt * time_quantity[1]
        return None if seconds == 0 else seconds

    def reload_translations(self):
        if self.previous_locale == get_locale():
            return  # Don't care if the locale hasn't changed

        # Embed constants
        self.REMINDER_TITLE = _("Reminder")

        # Logging message constants

        # Message constants
        self.INVALID_TIME_FORMAT = _(":x: Invalid time format.")
        self.TOO_MUCH_TIME = _(":x: Too long amount of time. Maximum: {} total seconds")
        self.WILL_REMIND = _(":white_check_mark: I will remind you in {} seconds.")

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
