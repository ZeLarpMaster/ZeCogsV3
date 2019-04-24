from redbot.core.bot import Red
from .message_proxy import MessageProxy


def setup(bot: Red):
    bot.add_cog(MessageProxy(bot))
