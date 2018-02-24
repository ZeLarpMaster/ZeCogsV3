from redbot.core.bot import Red
from .react_roles import ReactRoles


def setup(bot: Red):
    bot.add_cog(ReactRoles(bot))
