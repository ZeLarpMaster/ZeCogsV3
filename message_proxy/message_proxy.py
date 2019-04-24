import io
import logging

import discord
import aiohttp

from redbot.core import commands, checks
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.commands import Context, Cog

T_ = Translator("MessageProxy", __file__)  # pygettext3 -Dnp locales message_proxy.py


def _(s):
    def func(*args, **kwargs):
        real_args = list(args)
        real_args.pop(0)
        return T_(s).format(*real_args, **kwargs)
    return func


@cog_i18n(T_)
class MessageProxy(Cog):
    """Send and edit messages through the bot"""
    __author__ = "ZeLarpMaster#0818"

    # Log constants
    CANT_DELETE_MESSAGE = _("Failed to delete a message in {c_name}")

    # Message constants
    MESSAGE_SENT = _(":white_check_mark: Sent <{m_url}>.")
    FAILED_TO_FIND_MESSAGE = _(":x: Failed to find the message with id {m_id} in {c_mention}.")
    COMMAND_FORMAT = _("{p}msg edit <#{c_id}> {m_id} ```\n{content}```")
    PLACEHOLDER = _("Placeholder")

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("red.ZeCogsV3.message_proxy")

    @commands.group(name="message", aliases=["msg"])
    @commands.guild_only()
    @checks.mod_or_permissions(manage_guild=True)
    async def _messages(self, ctx: Context):
        """Message proxy"""
        pass

    @_messages.command(name="send")
    @checks.mod_or_permissions(manage_guild=True)
    async def _messages_send(self, ctx: Context, channel: discord.TextChannel, *, content=None):
        """Send a message in the given channel

        An attachment can be provided.
        If no content is provided, at least an attachment must be provided."""
        message = ctx.message
        if message.attachments:
            img = await self.get_attachment_image(message)
            msg = await channel.send(content and self.PLACEHOLDER(), file=img)
        else:
            msg = await channel.send(self.PLACEHOLDER())
        if content is not None:
            await msg.edit(content=content)
            reply = self.COMMAND_FORMAT(p=ctx.prefix, content=content, m_id=msg.id, c_id=channel.id)
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                self.logger.warning(self.CANT_DELETE_MESSAGE(c_name=ctx.message.channel.name))
        else:
            reply = self.MESSAGE_SENT(m_url=msg.jump_url)
        await ctx.send(reply)

    @_messages.command(name="edit", pass_context=True)
    @checks.mod_or_permissions(manage_guild=True)
    async def _messages_edit(self, ctx: Context, channel: discord.TextChannel, message_id: str, *, new_content):
        """Edit the message with id message_id in the given channel

        No attachment can be provided."""
        try:
            msg = await channel.fetch_message(message_id)
        except discord.errors.HTTPException:
            response = self.FAILED_TO_FIND_MESSAGE(m_id=message_id, c_mention=channel.mention)
        else:
            await msg.edit(content=new_content)
            response = self.COMMAND_FORMAT(p=ctx.prefix, content=new_content, m_id=msg.id, c_id=channel.id)
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                self.logger.warning(self.CANT_DELETE_MESSAGE(c_name=ctx.message.channel.name))
        await ctx.send(response)

    # Utilities
    async def get_attachment_image(self, message: discord.Message) -> discord.File:
        attachment = message.attachments[0]
        async with aiohttp.ClientSession() as session:
            async with session.get(url=attachment.url, headers={"User-Agent": "Mozilla"}) as response:
                img = io.BytesIO(await response.read())
                return discord.File(img, filename=attachment.filename)
