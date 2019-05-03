from redbot.core.bot import Red
from .timezone_conversion import TimezoneConversion


def setup(bot: Red):
    bot.add_cog(TimezoneConversion(bot))
