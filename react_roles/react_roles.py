import asyncio
import discord
import itertools
import logging
import math
import re
import traceback
import typing

from discord.ext import commands
from redbot.core import RedContext, Config, checks
from redbot.core.bot import Red
from redbot.core.i18n import CogI18n

_ = CogI18n("ReactRoles", __file__)


class ReactRoles:
    """Associate emojis on messages with roles to gain/lose roles when clicking on reactions

    RedBot V3 edition"""
    __author__ = "ZeLarpMaster#0819"

    # Behavior related constants
    MAXIMUM_PROCESSED_PER_SECOND = 5
    PROCESSING_WAIT_TIME = 0 if MAXIMUM_PROCESSED_PER_SECOND == 0 else 1 / MAXIMUM_PROCESSED_PER_SECOND
    EMOTE_REGEX = re.compile("<a?:[a-zA-Z0-9_]{2,32}:(\d{1,20})>")
    LOCALIZED_ATTRIBUTE_REGEX = re.compile("([A-Z][A-Z_]+)")

    # Embed constants
    _LINK_LIST_TITLE = "Role Links"
    _LINK_LIST_NO_LINKS = "There are no links in this server"

    # Logging message constants
    _LOG_MESSAGE_NOT_FOUND = "Could not find message {msg_id} in {channel}."
    _LOG_CHANNEL_NOT_FOUND = "Could not find channel {channel_id}."
    _LOG_SERVER_NOT_FOUND = "Could not find server with id {guild_id}."
    _LOG_PROCESSING_LOOP_ENDED = "The processing loop has ended."

    # Message constants
    _PROGRESS_FORMAT = "Checked {c} out of {r} reactions out of {t} emojis."
    _PROGRESS_COMPLETE_FORMAT = """:white_check_mark: Completed! Checked a total of {c} reactions.
Gave a total of {g} roles."""
    _MESSAGE_NOT_FOUND = ":x: Message not found."
    _ALREADY_BOUND = ":x: The emoji is already bound on that message."
    _NOT_IN_SERVER = ":x: The channel must be in a server."
    _ROLE_NOT_FOUND = ":x: Role not found on the given channel's server."
    _EMOJI_NOT_FOUND = ":x: Emoji not found in any of my servers or in unicode emojis."
    _CANT_ADD_REACTIONS = ":x: I don't have the permission to add reactions in that channel."
    _CANT_MANAGE_ROLES = ":x: I don't have the permission to manage users' roles in the channel's server."
    _ROLE_SUCCESSFULLY_BOUND = ":white_check_mark: The role has been bound to {} on the message in {}."
    _ROLE_NOT_BOUND = ":x: The role is not bound to that message."
    _INITIALIZING = "Initializing..."
    _ROLE_UNBOUND = ":put_litter_in_its_place: Unbound the role on the message.\n"
    _REACTION_CLEAN_START = _ROLE_UNBOUND + "Removing linked reactions..."
    _PROGRESS_REMOVED = _ROLE_UNBOUND + "Removed **{} / {}** reactions..."
    _REACTION_CLEAN_DONE = _ROLE_UNBOUND + "Removed **{}** reactions."
    _LINK_MESSAGE_NOT_FOUND = "The following messages weren't found: {}"
    _LINK_CHANNEL_NOT_FOUND = "The following channels weren't found: {}"
    _LINK_PAIR_INVALID = "The following channel-message pairs were invalid: {}"
    _LINK_FAILED = ":x: Failed to link reactions.\n"
    _LINK_SUCCESSFUL = ":white_check_mark: Successfully linked the reactions."
    _LINK_NAME_TAKEN = ":x: That link name is already used in the current server. Remove it before assigning to it."
    _UNLINK_NOT_FOUND = ":x: Could not find a link with that name in this server."
    _UNLINK_SUCCESSFUL = ":white_check_mark: The link has been removed from this server."
    _CANT_CHECK_LINKED = ":x: Cannot run a check on linked messages."
    _REACTION_NOT_FOUND = ":x: Could not find the reaction of that message."

    def __init__(self, bot: Red):
        self.bot = bot
        self.logger = logging.getLogger("red.ZeCogsV3.react_roles")
        # force_registration is for weaklings
        self.config = Config.get_conf(self, identifier=self.__author__ + "@" + self.__class__.__name__)
        self.config.register_guild(links={})
        self.role_queue = asyncio.Queue()
        self.role_map = {}
        self.role_cache = {}
        self.links = {}  # {server.id: {channel.id_message.id: [role]}}
        self.message_cache = {}  # {channel.id_message.id: message}
        asyncio.ensure_future(self._init_bot_manipulation())
        asyncio.ensure_future(self.process_role_queue())

    # Events
    async def on_raw_reaction_add(self, emoji: discord.PartialEmoji, message_id: int, channel_id: int, user_id: int):
        # noinspection PyBroadException
        try:
            await self.check_add_role(emoji, message_id, channel_id, user_id)
        except:  # To prevent the listener from exploding if an exception happens
            traceback.print_exc()

    async def on_raw_reaction_remove(self, emoji: discord.PartialEmoji, message_id: int, channel_id: int, user_id: int):
        # noinspection PyBroadException
        try:
            await self.check_remove_role(emoji, message_id, channel_id, user_id)
        except:  # To prevent the listener from exploding if an exception happens
            traceback.print_exc()

    async def on_message_delete(self, message: discord.Message):
        # Remove the config too
        channel = message.channel
        if isinstance(channel, discord.TextChannel):
            guild = message.guild
            channel_conf = await self.config.channel(channel).all()  # TODO: Find something even better?
            if str(message.id) in channel_conf:
                del channel_conf[str(message.id)]
                await self.config.channel(channel).set(channel_conf)
            # And the caches
            self.remove_from_message_cache(channel.id, message.id)
            self.remove_message_from_cache(guild.id, channel.id, message.id)
            # And the links
            pair = str(channel.id) + "_" + str(message.id)
            if pair in self.links.get(guild.id, {}):
                del self.links[guild.id][pair]
            server_links = await self.config.guild(guild).links()
            if server_links is not None:
                for links in server_links.values():
                    if pair in links:
                        links.remove(pair)

    async def _init_bot_manipulation(self):
        await self.bot.wait_until_ready()

        # Caching roles
        channel_configs = await self.config.all_channels()
        for channel_id, channel_conf in channel_configs.items():
            channel = self.bot.get_channel(channel_id)
            if channel is not None:
                for msg_id, msg_conf in channel_conf.items():
                    msg = await self.safe_get_message(channel, msg_id)
                    if msg is not None:
                        for emoji_str, role_id in msg_conf.items():
                            role = discord.utils.get(channel.guild.roles, id=role_id)
                            if role is not None:
                                self.add_to_message_cache(channel_id, msg_id, msg)
                                self.add_to_cache(channel.guild.id, channel_id, msg_id, emoji_str, role)
                    else:
                        self.logger.warning(self.LOG_MESSAGE_NOT_FOUND.format(msg_id=msg_id, channel=channel.mention))
            else:
                self.logger.warning(self.LOG_CHANNEL_NOT_FOUND.format(channel_id=channel_id))

        # Caching links
        guild_configs = await self.config.all_guilds()
        for guild_id, guild_conf in guild_configs.items():
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                link_list = guild_conf.get("links")
                if link_list is not None:
                    self.parse_links(guild_id, link_list.values())
            else:
                self.logger.warning(self.LOG_SERVER_NOT_FOUND.format(guild_id=guild_id))

    # Commands
    @commands.group(name="roles", pass_context=True, no_pm=True, invoke_without_command=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles(self, ctx: RedContext):
        """Roles giving configuration"""
        await ctx.send_help()

    @_roles.command(name="linklist", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_link_list(self, ctx: RedContext):
        """Lists all reaction links in the current server"""
        guild = ctx.guild
        server_links = await self.config.guild(guild).links()
        embed = discord.Embed(title=self.LINK_LIST_TITLE, colour=discord.Colour.light_grey())
        for name, pairs in server_links.items():
            value = ""
            for channel, messages in itertools.groupby(pairs, key=lambda p: p.split("_")[0]):
                value += "<#{}>: ".format(channel) + ", ".join(p.split("_")[1] for p in messages)
            if len(value) > 0:
                embed.add_field(name=name, value=value)
        if len(embed.fields) == 0:
            embed.description = self.LINK_LIST_NO_LINKS
        await ctx.send(embed=embed)

    @_roles.command(name="unlink", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_unlink(self, ctx: RedContext, name: str):
        """Remove a link of messages by its name"""
        guild = ctx.message.guild
        server_links = await self.config.guild(guild).links()
        name = name.lower()
        if server_links is None or name not in server_links:
            response = self.UNLINK_NOT_FOUND
        else:
            await self.remove_links(guild, name)
            response = self.UNLINK_SUCCESSFUL
        await ctx.send(response)

    @_roles.command(name="link", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_link(self, ctx: RedContext, name: str, *linked_messages):
        """Link messages together to allow only one role from those messages to be given to a member

        name is the name of the link; used to make removal easier
        linked_messages is an arbitrary number of channelid-messageid
        You can get those channelid-messageid pairs with a shift right click on messages
        Users can only get one role out of all the reactions in the linked messages
        The bot will NOT remove the user's other reaction(s) when clicking within linked messages"""
        guild = ctx.guild
        pairs = []
        messages_not_found = []
        channels_not_found = []
        invalid_pairs = []
        for pair in linked_messages:
            split_pair = pair.split("-", 1)
            if len(split_pair) == 2 and split_pair[-1].isdigit():
                channel_id, message_id = split_pair
                channel = guild.get_channel(int(channel_id))
                if channel is not None:
                    message = await self.safe_get_message(channel, message_id)
                    if message is not None:
                        pairs.append("_".join(split_pair))
                    else:
                        messages_not_found.append(split_pair)
                else:
                    channels_not_found.append(channel_id)
            else:
                invalid_pairs.append(pair)
        confimation_msg = ""
        if len(invalid_pairs) > 0:
            confimation_msg += self.LINK_PAIR_INVALID.format(", ".join(invalid_pairs)) + "\n"
        if len(channels_not_found) > 0:
            confimation_msg += self.LINK_CHANNEL_NOT_FOUND.format(", ".join(channels_not_found)) + "\n"
        if len(messages_not_found) > 0:
            confimation_msg += self.LINK_MESSAGE_NOT_FOUND.format(
                ", ".join("{} in <#{}>".format(p[0], p[1]) for p in messages_not_found)) + "\n"
        if len(confimation_msg) > 0:
            response = self.LINK_FAILED + confimation_msg
        else:
            async with self.config.guild(guild).links() as server_links:
                name = name.lower()
                if name in server_links:
                    response = self.LINK_NAME_TAKEN
                else:
                    server_links[name] = pairs
                    self.parse_links(guild.id, [pairs])
                    response = self.LINK_SUCCESSFUL
        await ctx.send(response)

    @_roles.command(name="add", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_add(self, ctx: RedContext, message_id, channel: discord.TextChannel, emoji, *, role: discord.Role):
        """Add a role on a message

        `message_id` must be found in `channel`
            To get a message's id: Settings > Appearance > Developer mode then
            Right click a message > Copy ID
        `emoji` can either be a Unicode emoji or a server emote
        `role` must be found in the channel's server"""
        guild = channel.guild
        message = await self.safe_get_message(channel, message_id)
        if message is None:
            response = self.MESSAGE_NOT_FOUND
        elif guild is None:
            response = self.NOT_IN_SERVER
        elif role.guild != channel.guild:
            response = self.ROLE_NOT_FOUND
        elif channel.guild.me.guild_permissions.manage_roles is False:
            response = self.CANT_MANAGE_ROLES
        elif channel.permissions_for(channel.guild.me).add_reactions is False:
            response = self.CANT_ADD_REACTIONS
        else:
            async with self.config.channel(channel).get_attr(message_id, {}) as msg_conf:
                emoji_match = self.EMOTE_REGEX.fullmatch(emoji)
                emoji_id = emoji if emoji_match is None else emoji_match.group(1)
                if emoji_id in msg_conf:
                    response = self.ALREADY_BOUND
                else:
                    emoji = None
                    if emoji_id.isdigit():
                        for emoji_server in self.bot.guilds:
                            if emoji is None:
                                emoji = discord.utils.get(emoji_server.emojis, id=int(emoji_id))
                    try:
                        await message.add_reaction(emoji or emoji_id)
                    except discord.HTTPException:  # Failed to find the emoji
                        response = self.EMOJI_NOT_FOUND
                    else:
                        self.add_to_message_cache(channel.id, message_id, message)
                        self.add_to_cache(guild.id, channel.id, message_id, emoji_id, role)
                        msg_conf[emoji_id] = role.id
                        response = self.ROLE_SUCCESSFULLY_BOUND.format(emoji or emoji_id, channel.mention)
        await ctx.send(response)

    @_roles.command(name="remove", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_remove(self, ctx: RedContext, message_id, channel: discord.TextChannel, *, role: discord.Role):
        """Remove a role from a message

        `message_id` must be found in `channel` and be bound to `role`"""
        guild = channel.guild
        msg_config = await self.config.channel(channel).get_attr(message_id, {})
        emoji_config = discord.utils.find(lambda o: o[1] == role.id, msg_config.items())
        if emoji_config is None:
            await ctx.send(self.ROLE_NOT_BOUND)
        else:
            emoji_str = emoji_config[0]
            del msg_config[emoji_str]
            await self.config.channel(channel).set(msg_config)
            self.remove_from_message_cache(channel.id, message_id)
            self.remove_role_from_cache(guild.id, channel.id, message_id, emoji_str)
            msg = await self.safe_get_message(channel, message_id)
            if msg is None:
                await ctx.send(self.ROLE_UNBOUND + self.MESSAGE_NOT_FOUND)
            else:
                reaction = discord.utils.find(
                    lambda r: r.emoji.id == emoji_str if r.custom_emoji else r.emoji == emoji_str, msg.reactions)
                if reaction is None:
                    await ctx.send(self.ROLE_UNBOUND + self.REACTION_NOT_FOUND)
                else:
                    answer = await ctx.send(self.REACTION_CLEAN_START)
                    after = None
                    count = 0
                    user = None
                    for page in range(math.ceil(reaction.count / 100)):
                        async for user in reaction.users(after=after):
                            await msg.remove_reaction(reaction.emoji, user)
                            count += 1
                        after = user
                        await answer.edit(content=self.PROGRESS_REMOVED.format(count, reaction.count))
                    await answer.edit(content=self.REACTION_CLEAN_DONE.format(count))

    @_roles.command(name="check", pass_context=True, no_pm=True)
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_check(self, ctx: RedContext, message_id, channel: discord.TextChannel):
        """Goes through all reactions of a message and gives the roles accordingly

        This does NOT work with messages in a link"""
        guild = channel.guild
        msg = await self.safe_get_message(channel, message_id)
        server_links = self.links.get(guild.id, {})
        if str(channel.id) + "_" + message_id in server_links:
            await ctx.send(self.CANT_CHECK_LINKED)
        elif msg is None:
            await ctx.send(self.MESSAGE_NOT_FOUND)
        else:
            msg_conf = await self.config.channel(channel).get_attr(message_id, None)
            if msg_conf is not None:
                progress_msg = await ctx.send(self.INITIALIZING)
                given_roles = 0
                checked_count = 0
                total_count = sum(map(lambda r: r.count, msg.reactions)) - len(msg.reactions)  # Remove the bot's
                total_reactions = 0
                for react in msg.reactions:  # Go through all reactions on the message and add the roles if needed
                    total_reactions += 1
                    emoji_str = str(react.emoji.id) if react.custom_emoji else react.emoji
                    role = self.get_from_cache(guild.id, channel.id, message_id, emoji_str)
                    if role is not None:
                        before = 0
                        after = None
                        user = None
                        while before != after:
                            before = after
                            async for user in react.users(after=after):
                                member = guild.get_member(user.id)
                                if member is not None and member != self.bot.user and \
                                        discord.utils.get(member.roles, id=role.id) is None:
                                    await member.add_roles(role)
                                    given_roles += 1
                                checked_count += 1
                            after = user
                            await progress_msg.edit(content=self.PROGRESS_FORMAT.format(
                                c=checked_count, r=total_count, t=total_reactions))
                    else:
                        checked_count += react.count
                        await progress_msg.edit(content=self.PROGRESS_FORMAT.format(
                            c=checked_count, r=total_count, t=total_reactions))
                await progress_msg.edit(content=self.PROGRESS_COMPLETE_FORMAT.format(c=checked_count, g=given_roles))

    # Utilities
    async def check_add_role(self, emoji: discord.PartialEmoji, message_id: int, channel_id: int, user_id: int):
        message = self.get_from_message_cache(channel_id, message_id)
        if message is not None:
            guild = message.guild
            emoji_str = str(emoji.id) if emoji.is_custom_emoji() else emoji.name
            role = self.get_from_cache(guild.id, channel_id, message_id, emoji_str)
            member = guild.get_member(user_id)
            if member is not None and member != guild.me and role is not None:
                await self.add_role_queue(member, role, True,
                                          linked_roles=self.get_link(guild.id, channel_id, message_id))

    async def check_remove_role(self, emoji: discord.PartialEmoji, message_id: int, channel_id: int, user_id: int):
        message = self.get_from_message_cache(channel_id, message_id)
        if message is not None:
            guild = message.guild
            if user_id == guild.me.id:  # Safeguard in case a mod removes the bot's reaction by accident
                await message.add_reaction(emoji)
            else:
                emoji_str = str(emoji.id) if emoji.is_custom_emoji() else emoji.name
                member = guild.get_member(user_id)
                role = self.get_from_cache(guild.id, channel_id, message_id, emoji_str)
                if role is not None:
                    await self.add_role_queue(member, role, False)

    async def add_role_queue(self, member: discord.Member, role: discord.Role, add_bool: bool, *,
                             linked_roles: set=set()):
        key = "_".join((str(member.guild.id), str(member.id)))  # Doing it this way to make it simpler a bit
        q = self.role_map.get(key)
        if q is None:  # True --> add   False --> remove
            # Always remove the @everyone role to prevent the bot from trying to give it to members
            q = {True: set(), False: {member.guild.default_role}, "mem": member}
            await self.role_queue.put(key)
        q[True].difference_update(linked_roles)  # Remove the linked roles from the roles to add
        q[False].update(linked_roles)  # Add the linked roles to remove them if the user has any of them
        q[not add_bool] -= {role}
        q[add_bool] |= {role}
        self.role_map[key] = q

    async def process_role_queue(self):  # This exists to update multiple roles at once when possible
        """Loops until the cog is unloaded and processes the role assignments when it can"""
        await self.bot.wait_until_ready()
        while self == self.bot.get_cog(self.__class__.__name__):
            key = await self.role_queue.get()
            q = self.role_map.pop(key)
            if q is not None and q.get("mem") is not None:
                mem = q["mem"]
                all_roles = set(mem.roles)
                add_set = q.get(True, set())
                del_set = q.get(False, {mem.guild.default_role})
                try:
                    await mem.edit(roles=((all_roles | add_set) - del_set))
                    # Basically, the user's roles + the added - the removed
                except (discord.Forbidden, discord.HTTPException):
                    self.role_map[key] = q  # Try again when it fails
                    await self.role_queue.put(key)
                else:
                    self.role_queue.task_done()
                finally:
                    await asyncio.sleep(self.PROCESSING_WAIT_TIME)
        self.logger.info(self.LOG_PROCESSING_LOOP_ENDED)

    # Utilities
    async def safe_get_message(self, channel: discord.TextChannel, message_id: str) -> typing.Optional[discord.Message]:
        try:
            result = await channel.get_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            result = None
        return result

    # Links
    def get_link(self, server_id: int, channel_id: int, message_id: int) -> set:
        return self.links.get(server_id, {}).get(str(channel_id) + "_" + str(message_id), set())

    def parse_links(self, server_id: int, links_list: typing.Iterable[typing.List[str]]):
        """Parses the links of a server into self.links
        links_list is a list of links each link being a list of channel.id_message.id linked together"""
        link_dict = {}
        for link in links_list:
            role_list = set()
            for entry in link:
                channel_id, message_id = entry.split("_", 1)
                role_list.update(self.get_all_roles_from_message(server_id, int(channel_id), message_id))
                link_dict[entry] = role_list
        self.links[server_id] = link_dict

    async def remove_links(self, guild: discord.Guild, name: str):
        async with self.config.guild(guild).links() as entries:
            entry_list = entries.get(name, [])
            link_dict = self.links.get(guild.id, {})
            for entry in entry_list:
                if entry in link_dict:
                    del link_dict[entry]
            del entries[name]

    # Cache -- Needed to keep the actual role object in cache instead of looking for it every time in the server's roles
    def add_to_cache(self, server_id: int, channel_id: int, message_id: str, emoji_str: str, role: discord.Role):
        """Adds an entry to the role cache"""
        self.role_cache.setdefault(server_id, {}) \
            .setdefault(channel_id, {}) \
            .setdefault(message_id, {})[emoji_str] = role

    def get_all_roles_from_message(self, server_id: int, channel_id: int, message_id: str) \
            -> typing.Iterable[discord.Role]:
        """Fetches all roles from a given message returns an iterable"""
        return self.role_cache.get(server_id, {}).get(channel_id, {}).get(message_id, {}).values()

    def get_from_cache(self, server_id: int, channel_id: int, message_id: int, emoji_str: str) -> discord.Role:
        """Fetches the role associated with an emoji on the given message"""
        return self.role_cache.get(server_id, {}).get(channel_id, {}).get(str(message_id), {}).get(emoji_str)

    def remove_role_from_cache(self, server_id: int, channel_id: int, message_id: str, emoji_str: str):
        """Removes an entry from the role cache"""
        server_conf = self.role_cache.get(server_id)
        if server_conf is not None:
            channel_conf = server_conf.get(channel_id)
            if channel_conf is not None:
                message_conf = channel_conf.get(message_id)
                if message_conf is not None and emoji_str in message_conf:
                    del message_conf[emoji_str]

    def remove_message_from_cache(self, server_id: int, channel_id: int, message_id: str):
        """Removes a message from the role cache"""
        server_conf = self.role_cache.get(server_id)
        if server_conf is not None:
            channel_conf = server_conf.get(channel_id)
            if channel_conf is not None and message_id in channel_conf:
                del channel_conf[message_id]

    def add_to_message_cache(self, channel_id: int, message_id: typing.Union[int, str], message: discord.Message):
        self.message_cache["{}_{}".format(channel_id, message_id)] = message

    def get_from_message_cache(self, channel_id: int, message_id: int) -> discord.Message:
        return self.message_cache.get("{}_{}".format(channel_id, message_id))

    def remove_from_message_cache(self, channel_id: int, message_id: int):
        self.message_cache.pop("{}_{}".format(channel_id, message_id), None)

    def __getattr__(self, item: str) -> object:
        match = self.LOCALIZED_ATTRIBUTE_REGEX.fullmatch(item)
        if match is not None:
            underscored = getattr(self, "_" + item, None)
            if underscored is not None:
                return _(underscored)
        raise AttributeError("'{}' object has no attribute '{}'".format(self.__class__.__name__, item))
