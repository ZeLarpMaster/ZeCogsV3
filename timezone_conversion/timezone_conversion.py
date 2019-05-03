import logging
import math
import datetime
import re
import hashlib
import typing

import discord

import pytz  # pip install pytz

from redbot.core import commands, checks, Config
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.commands import Context, Cog

T_ = Translator("TimezoneConversion", __file__)  # pygettext3 -Dnp locales timezone_conversion.py


def _(s):
    def func(*args, **kwargs):
        real_args = list(args)
        real_args.pop(0)
        return T_(s).format(*real_args, **kwargs)
    return func


@cog_i18n(T_)
class TimezoneConversion(Cog):
    """Timezone conversion tools"""
    __author__ = "ZeLarpMaster#0818"

    # Behavior constants
    TIME_REGEX = re.compile("(now|((1?[0-9])([ap]m))|(([0-9]{1,2}):([0-9]{2})))")

    # Embed constants
    ALIAS_LIST_TITLE = _("Alias List")
    ALIAS_LIST_EMPTY = _("No aliases to be listed.")

    # Message constants
    TIME_USAGE = _(""":x: Invalid command.
Usage: `{prefix}time <time> <timezone1> [timezone2]`
Where *time* is *now* or a timestamp of format 0am or 00:00 and *timezone* is the name of a tz timezone.
If timezone2 is omitted, it will only respond to *now* requests.""")
    LIST_OF_TZ = _("For a list of timezones: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>")
    INVALID_SOURCE_TZ = _(":x: Invalid __source__ timezone. ")
    INVALID_DESTINATION_TZ = _(":x: Invalid __destination__ timezone. ")
    INEXISTANT_TZ = _(":x: The timezone doesn't exist. ")
    TIME_NOW = _("It is {hsource} in **{csource}** right now.")
    ALIAS_ADDED = _(":white_check_mark: Added alias *{alias}* refering to {zone}.")
    TZ_HAS_ALIAS_NAME = _(":x: A timezone already has this name. Consider changing your alias' name.")
    ALIAS_EXISTS = _(":x: The alias already exists. Consider removing it before re-adding it.")
    ALIAS_NO_SPACE = _(":x: There cannot be spaces in aliases and timezones.")
    ALIAS_REMOVED = _(":white_check_mark: Removed alias *{alias}*.")
    ALIAS_CANT_REMOVE = _(":x: Cannot remove alias *{alias}* because it doesn't exist.")
    TIME_DIFF = _("{hsource} in **{csource}** is equal to {hdest} in **{cdest}** ({tdiff[0]:+d}:{tdiff[1]:0>2})")
    INVALID_TIME_FORMAT = _(":x: Invalid time format. Use now, 0am or 00:00.")
    MORE_THAN_24H = _(":x: Invalid time. How do you have more than 24h in your day?")

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("red.ZeCogsV3.message_proxy")

        unique_id = int(hashlib.sha512((self.__author__ + "@" + self.__class__.__name__).encode()).hexdigest(), 16)
        self.config = Config.get_conf(self, identifier=unique_id)
        self.config.register_global(aliases={})

    @commands.group(name="time", invoke_without_command=True)
    async def _time_converter(self, ctx: Context, time, timezone1, timezone2=None):
        """Convert the time from timezone1 to timezone2

        List of supported timezones: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>.
        The timezones must be the last part of the TZ column.
        For example, you would check the time in 'Africa/Abidjan' by doing `[p]time now Abidjan`"""
        if time is None and timezone1 is None:
            await ctx.send_help()
        else:
            self.aliases = await self.config.aliases()
            if None in (time, timezone1) or "" in (time, timezone1, timezone2):
                msg = self.TIME_USAGE(prefix=ctx.prefix)
            elif timezone2 is None and time == "now":
                csource, zone = self.match_timezone(timezone1)
                if zone is None:
                    msg = self.INVALID_SOURCE_TZ() + self.LIST_OF_TZ()
                else:
                    date = datetime.datetime.now(tz=zone)
                    hsource = self.format_hours_minutes(date.hour, date.minute)
                    msg = self.TIME_NOW(hsource=hsource, csource=csource)
            elif time == "to" and timezone1 == "stop":
                msg = "http://imgur.com/CoWZ05t.gif"
            else:
                msg = self._handle_time(time.lower(), timezone1, timezone2)
            await ctx.send(msg)

    @_time_converter.command(name="list")
    async def _list_zones(self, ctx: Context):
        """Print the link to the list of possible timezones"""
        await ctx.send(self.LIST_OF_TZ())

    @_time_converter.group(invoke_without_command=True)
    async def alias(self, ctx: Context):
        """Manage the timezone aliases"""
        await ctx.send_help()
    
    @alias.command(name="add")
    @checks.mod_or_permissions(manage_roles=True)
    async def _add_alias(self, ctx: Context, alias_name, timezone):
        """Add a new timezone alias

        For example, with an alias named 'PST' pointing timezone 'GMT+8',
        you would do: `[p]time now PST` and it would be the same as `[p]time now GMT+8`"""
        if " " not in alias_name and " " not in timezone:
            alias_name = alias_name.lower()
            timezone = timezone.lower()
            aliases = await self.config.aliases()
            if alias_name not in aliases:
                alias_zone = self.find_timezone(alias_name)
                if alias_zone is None:
                    zone = self.find_timezone(timezone)
                    if zone is not None:
                        async with self.config.aliases() as aliases:
                            aliases[alias_name] = zone
                        message = self.ALIAS_ADDED(alias=alias_name, zone=zone)
                    else:
                        message = self.INEXISTANT_TZ() + self.LIST_OF_TZ()
                else:
                    message = self.TZ_HAS_ALIAS_NAME()
            else:
                message = self.ALIAS_EXISTS()
        else:
            message = self.ALIAS_NO_SPACE()
        await ctx.send(message)
    
    @alias.command(name="remove", aliases=["del", "delete"])
    @checks.mod_or_permissions(manage_roles=True)
    async def _remove_alias(self, ctx: Context, alias_name):
        """Delete a timezone alias"""
        alias_name = alias_name.lower()
        async with self.config.aliases() as aliases:
            if alias_name in aliases:
                del aliases[alias_name]
                response = self.ALIAS_REMOVED(alias=alias_name)
            else:
                response = self.ALIAS_CANT_REMOVE(alias=alias_name)
        await ctx.send(response)
    
    @alias.command(name="list", aliases=["ls"])
    async def _list_alias(self, ctx: Context):
        """List all timezone aliases"""
        aliases = await self.config.aliases()
        alias_list = list(aliases.items())
        embed = discord.Embed(title=self.ALIAS_LIST_TITLE(), colour=discord.Colour.light_grey(), description="```")
        if len(alias_list) > 0:
            half = math.ceil(len(alias_list) / 2)
            for i, a in enumerate(alias_list[:half]):
                a1_name = "{} → {}".format(*a)
                if i+half < len(alias_list):
                    a2_name = "{} → {}".format(*alias_list[i+half])
                else:
                    a2_name = ""
                embed.description += "{:<30}  {:<30}\n".format(a1_name, a2_name)
            embed.description += "```"
        else:
            embed.description = self.ALIAS_LIST_EMPTY()
        await ctx.send(embed=embed)

    # Utilities
    def format_hours_minutes(self, hours: int, minutes: int) -> str:
        format_24 = hours
        format_minutes = ":{:0>2}".format(minutes)
        cropped_minutes = format_minutes if minutes > 0 else ""
        format_12 = self._get_12h_str(hours, cropped_minutes)
        return f"**{format_12}** ({format_24}{format_minutes})"

    def find_timezone(self, part: str) -> typing.Optional[str]:
        return discord.utils.find(lambda z: z.rsplit("/")[-1].lower() == part, pytz.all_timezones_set)

    def match_timezones(self, country: str) -> typing.List[datetime.tzinfo]:
        return [pytz.timezone(item) for item in pytz.all_timezones if item.lower().endswith(country)]

    def match_timezone(self, country: str) -> typing.Tuple[str, datetime.tzinfo]:
        country = country.lower()
        if country in self.aliases:
            zone = self.aliases[country]
            result = zone.rsplit("/")[-1], pytz.timezone(zone)
        else:
            timezone_name = self.find_timezone(country)
            if timezone_name is not None:
                name = timezone_name.rsplit("/")[-1]
                result = name, pytz.timezone(timezone_name)
            else:
                result = None, None
        return result

    def get_zone_offset(self, zone: datetime.tzinfo) -> datetime.timedelta:
        return datetime.datetime.now(tz=zone).utcoffset()

    def timezone_diff(self, zone_src: datetime.tzinfo, zone_dst: datetime.tzinfo) -> typing.Tuple[float, float]:
        total_offset = self.get_zone_offset(zone_dst) - self.get_zone_offset(zone_src)
        offset_seconds = total_offset.total_seconds()
        offset_minutes = offset_seconds // 60
        return divmod(offset_minutes, 60)

    def get_zone_time(self, zone: datetime.tzinfo) -> typing.Tuple[int, int]:
        dt = datetime.datetime.now(tz=zone)
        return dt.hour, dt.minute

    def format_timezone(self, hours_source: int, minutes_source: int, country_source: str, country_dest: str) -> str:
        csource, zone1 = self.match_timezone(country_source)
        cdest, zone2 = self.match_timezone(country_dest)
        if zone1 is None:  # Source timezone not found
            result = self.INVALID_SOURCE_TZ() + self.LIST_OF_TZ()
        elif zone2 is None:  # Destination timezone not found
            result = self.INVALID_DESTINATION_TZ() + self.LIST_OF_TZ()
        else:
            if hours_source is None and minutes_source is None:
                time_source = self.get_zone_time(zone1)
                hours_dest, minutes_dest = self.get_zone_time(zone2)
                time_diff = (hours_dest - time_source[0], minutes_dest - time_source[1])
            else:
                time_diff = self.timezone_diff(zone1, zone2)
                hours_dest = (hours_source + time_diff[0] + 24) % 24
                minutes_dest = (minutes_source + time_diff[1] + 60) % 60
                time_diff = (int(time_diff[0]), int(time_diff[1]))
            hsource = self.format_hours_minutes(hours_source, minutes_source)
            hdest = self.format_hours_minutes(int(hours_dest), int(minutes_dest))
            result = self.TIME_DIFF(hsource=hsource, csource=csource, hdest=hdest, cdest=cdest, tdiff=time_diff)
        return result

    def _handle_time(self, time: str, country_source: str, country_result: str) -> str:
        regex = self.TIME_REGEX.fullmatch(time)
        msg = ""
        error = False
        if regex and regex.group(2) is not None:  # 0am
            hours_source = int(regex.group(3))
            minutes_source = 0  # TODO: Make this changeable? maybe 00:00am format
            hours_source = self._convert_12h_to_24h(hours_source, regex.group(4) == "pm")
        elif regex and regex.group(5) is not None:  # 00:00
            hours_source = int(regex.group(6))
            minutes_source = int(regex.group(7))
        elif regex and regex.group(1) == "now":
            hours_source = None
            minutes_source = None
        else:  # Invalid format
            hours_source = 0
            minutes_source = 0
            error = True
            msg = self.INVALID_TIME_FORMAT()
        if hours_source is not None and hours_source >= 24:
            error = True
            msg = self.MORE_THAN_24H()
        if not error:
            msg = self.format_timezone(hours_source, minutes_source, country_source, country_result)
        return msg

    def _convert_12h_to_24h(self, hours: int, is_pm: bool) -> int:
        if hours == 12:
            if is_pm:
                result = 12
            else:
                result = 0
        else:
            result = hours + (12 if is_pm else 0)
        return result

    def _get_12h_str(self, hours: int, mins_str: str) -> str:
        pm_str = "AM" if hours < 12 else "PM"
        if hours == 0:
            hours_12 = "12"
        elif hours > 12:
            hours_12 = str(hours - 12)
        else:
            hours_12 = str(hours)
        return f"{hours_12}{mins_str} {pm_str}"
