from redbot.core.bot import Red
from .reminder import Reminder


def setup(bot: Red):
    bot.add_cog(Reminder(bot))
