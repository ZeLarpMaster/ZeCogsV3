import asyncio
import discord
import itertools
import logging
import math
import re
import traceback
import typing
import hashlib
import collections
import time

from discord.raw_models import RawReactionActionEvent, RawMessageDeleteEvent, RawBulkMessageDeleteEvent
from redbot.core import commands, Config, checks
from redbot.core.config import Group
from redbot.core.bot import Red
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.commands import Context, Cog

T_ = Translator("ReactRoles", __file__)  # pygettext3 -Dnp locales react_roles.py


def _(s):
    def func(*args, **kwargs):
        real_args = list(args)
        real_args.pop(0)
        return T_(s).format(*real_args, **kwargs)
    return func


@cog_i18n(T_)
class ReactRoles(Cog):
    """Associate emojis on messages with roles to gain/lose roles when clicking on reactions

    RedBot V3 edition"""
    __author__ = "ZeLarpMaster#0818"

    # Behavior related constants
    MAXIMUM_PROCESSED_PER_SECOND = 5
    PROCESSING_WAIT_TIME = 0 if MAXIMUM_PROCESSED_PER_SECOND == 0 else 1 / MAXIMUM_PROCESSED_PER_SECOND
    EMOTE_REGEX = re.compile("<a?:[a-zA-Z0-9_]{2,32}:(\d{1,20})>")
    MESSAGE_GROUP = "MESSAGE"

    # Embed constants
    LINK_LIST_TITLE = _("Role Links")
    LINK_LIST_NO_LINKS = _("There are no links in this server")

    # Logging message constants
    LOG_MESSAGE_NOT_FOUND = _("Could not find message {msg_id} in {channel}.")
    LOG_CHANNEL_NOT_FOUND = _("Could not find channel {channel_id}.")
    LOG_SERVER_NOT_FOUND = _("Could not find server with id {guild_id}.")
    LOG_PROCESSING_LOOP_ENDED = _("The processing loop has ended.")
    LOG_BINDINGS = _("Cached bindings: {bindings}")
    LOG_MEMBER_NOT_FOUND = _("Could not find user with id {user_id} in {guild}.")

    # Message constants
    PROGRESS_FORMAT = _("Checked {c} out of {r} reactions out of {t} emojis.")
    PROGRESS_COMPLETE_FORMAT = _(":white_check_mark: Completed! Checked a total of {c} reactions.\n"
                                 "Gave a total of {g} roles.")
    MESSAGE_NOT_FOUND = _(":x: Message not found.")
    ALREADY_BOUND = _(":x: The emoji is already bound on that message.")
    NOT_IN_SERVER = _(":x: The channel must be in a server.")
    ROLE_NOT_FOUND = _(":x: Role not found on the given channel's server.")
    EMOJI_NOT_FOUND = _(":x: Emoji not found in any of my servers or in unicode emojis.")
    CANT_ADD_REACTIONS = _(":x: I don't have the permission to add reactions in that channel.")
    CANT_MANAGE_ROLES = _(":x: I don't have the permission to manage users' roles in the channel's server.")
    ROLE_SUCCESSFULLY_BOUND = _(":white_check_mark: The role has been bound to {} on the message in {}.")
    ROLE_NOT_BOUND = _(":x: The role is not bound to that message.")
    INITIALIZING = _("Initializing...")
    ROLE_UNBOUND = _(":put_litter_in_its_place: Unbound the role on the message.\n{}")
    REACTION_CLEAN_START = _("Removing linked reactions...")
    PROGRESS_REMOVED = _("Removed **{} / {}** reactions...")
    REACTION_CLEAN_DONE = _("Removed **{}** reactions.")
    LINK_MESSAGE_NOT_FOUND = _("The following messages weren't found: {}")
    LINK_CHANNEL_NOT_FOUND = _("The following channels weren't found: {}")
    LINK_PAIR_INVALID = _("The following channel-message pairs were invalid: {}")
    LINK_MUST_SPECIFY = _("You must specify at least one message to be linked.")
    LINK_FAILED = _(":x: Failed to link reactions.\n{reasons}")
    LINK_SUCCESSFUL = _(":white_check_mark: Successfully linked the reactions.")
    LINK_NAME_TAKEN = _(":x: That link name is already used in the current server. "
                        "Remove it before assigning to it.")
    UNLINK_NOT_FOUND = _(":x: Could not find a link with that name in this server.")
    UNLINK_SUCCESSFUL = _(":white_check_mark: The link has been removed from this server.")
    CANT_CHECK_LINKED = _(":x: Cannot run a check on linked messages.")
    REACTION_NOT_FOUND = _(":x: Could not find the reaction of that message.")
    CANT_GIVE_ROLE = _(":x: I can't give that role! Maybe it's higher than my own highest role?")

    def __init__(self, bot: Red):
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("red.ZeCogsV3.react_roles")
        self.logger.setLevel(logging.INFO)
        # force_registration is for weaklings
        unique_id = int(hashlib.sha512((self.__author__ + "@" + self.__class__.__name__).encode()).hexdigest(), 16)
        self.config = Config.get_conf(self, identifier=unique_id)
        if "init_custom" in dir(self.config):
            self.config.init_custom(self.MESSAGE_GROUP, 1)
        self.config.register_guild(links={})
        self.role_queue = asyncio.Queue()
        self.role_map = {}
        self.role_cache = {}
        self.links = {}  # {server.id: {channel.id_message.id: [role]}}
        asyncio.ensure_future(self._init_bot_manipulation())
        asyncio.ensure_future(self.process_role_queue())

    # Events
    @Cog.listener()
    async def on_raw_reaction_add(self, payload: RawReactionActionEvent):
        # noinspection PyBroadException
        try:
            await self.check_add_role(payload)
        except:  # To prevent the listener from exploding if an exception happens
            traceback.print_exc()

    @Cog.listener()
    async def on_raw_reaction_remove(self, payload: RawReactionActionEvent):
        # noinspection PyBroadException
        try:
            await self.check_remove_role(payload)
        except:  # To prevent the listener from exploding if an exception happens
            traceback.print_exc()

    @Cog.listener()
    async def on_raw_message_delete(self, payload: RawMessageDeleteEvent):
        await self.check_delete_message(payload)

    @Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: RawBulkMessageDeleteEvent):
        new_payload = {"channel_id": payload.channel_id, "guild_id": payload.guild_id}
        for message_id in payload.message_ids:
            new_payload["id"] = message_id
            await self.check_delete_message(RawMessageDeleteEvent(new_payload))

    async def _init_bot_manipulation(self):
        await self.bot.wait_until_ready()

        counter = collections.Counter()

        # Caching roles
        channel_configs = await self.get_all_message_configs()
        for channel_id, channel_conf in channel_configs.items():
            channel = self.bot.get_channel(int(channel_id))
            if channel is not None:
                for msg_id, msg_conf in channel_conf.items():
                    msg = await self.safe_get_message(channel, msg_id)
                    if msg is not None:
                        for emoji_str, role_id in msg_conf.items():
                            role = discord.utils.get(channel.guild.roles, id=role_id)
                            if role is not None:
                                self.add_to_cache(channel.guild.id, channel.id, msg.id, emoji_str, role)
                                counter.update((channel.name, ))
                    else:
                        self.logger.warning(self.LOG_MESSAGE_NOT_FOUND(msg_id=msg_id, channel=channel.mention))
            else:
                self.logger.warning(self.LOG_CHANNEL_NOT_FOUND(channel_id=channel_id))

        # Caching links
        guild_configs = await self.config.all_guilds()
        for guild_id, guild_conf in guild_configs.items():
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                link_list = guild_conf.get("links")
                if link_list is not None:
                    self.parse_links(guild_id, link_list.values())
            else:
                self.logger.warning(self.LOG_SERVER_NOT_FOUND(guild_id=guild_id))

        self.logger.info(self.LOG_BINDINGS(bindings=", ".join(": ".join(map(str, pair)) for pair in counter.items())))

    # Commands
    @commands.group(name="reactroles")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles(self, ctx: Context):
        """Roles giving configuration"""
        pass

    @_roles.command(name="linklist")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_link_list(self, ctx: Context):
        """Lists all reaction links in the current server"""
        guild = ctx.guild
        server_links = await self.config.guild(guild).links()
        embed = discord.Embed(title=self.LINK_LIST_TITLE(), colour=discord.Colour.light_grey())
        for name, pairs in server_links.items():
            value = ""
            for channel, messages in itertools.groupby(pairs, key=lambda p: p.split("_")[0]):
                value += "<#{}>: ".format(channel) + ", ".join(p.split("_")[1] for p in messages)
            if len(value) > 0:
                embed.add_field(name=name, value=value)
        if len(embed.fields) == 0:
            embed.description = self.LINK_LIST_NO_LINKS()
        await ctx.send(embed=embed)

    @_roles.command(name="unlink")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_unlink(self, ctx: Context, name: str):
        """Remove a link of messages by its name"""
        guild = ctx.message.guild
        server_links = await self.config.guild(guild).links()
        name = name.lower()
        if name not in server_links:
            response = self.UNLINK_NOT_FOUND()
        else:
            await self.remove_links(guild, name)
            response = self.UNLINK_SUCCESSFUL()
        await ctx.send(response)

    @_roles.command(name="link")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_link(self, ctx: Context, name: str, *linked_messages):
        """Link messages together to allow only one role from those messages to be given to a member

        name is the name of the link; used to make removal easier
        linked_messages is an arbitrary number of channelid-messageid
        You can get those channelid-messageid pairs with a right click on messages and shift + click on "Copy ID"
        Users can only get one role out of all the reactions in the linked messages
        The bot will NOT remove the user's other reaction(s) when clicking within linked messages"""
        guild = ctx.guild
        pairs = []
        messages_not_found = []
        channels_not_found = []
        invalid_pairs = []
        # Verify pairs
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
        # Generate response message
        failure_reasons = []
        if len(linked_messages) == 0:
            failure_reasons.append(self.LINK_MUST_SPECIFY())
        if len(invalid_pairs) > 0:
            failure_reasons.append(self.LINK_PAIR_INVALID(", ".join(invalid_pairs)))
        if len(channels_not_found) > 0:
            failure_reasons.append(self.LINK_CHANNEL_NOT_FOUND(", ".join(channels_not_found)))
        if len(messages_not_found) > 0:
            failure_reasons.append(self.LINK_MESSAGE_NOT_FOUND(
                ", ".join("{} in <#{}>".format(p[0], p[1]) for p in messages_not_found)))
        if len(failure_reasons) > 0:
            response = self.LINK_FAILED(reasons="\n".join(failure_reasons))
        else:
            # Save configs
            async with self.config.guild(guild).links() as server_links:
                name = name.lower()
                if name in server_links:
                    response = self.LINK_NAME_TAKEN()
                else:
                    server_links[name] = pairs
                    self.parse_links(guild.id, [pairs])
                    response = self.LINK_SUCCESSFUL()
        await ctx.send(response)

    @_roles.command(name="add")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    @checks.bot_has_permissions(manage_roles=True, add_reactions=True, manage_messages=True)
    async def _roles_add(self, ctx: Context, message_id: int, channel: discord.TextChannel, emoji, *,
                         role: discord.Role):
        """Add a role on a message

        Requires the bot to have permissions: Manage Roles, Add Reactions and Manage Messages

        `message_id` must be found in `channel`
            To get a message's id: Settings > Appearance > Developer mode then
            Right click a message > Copy ID
        `emoji` can either be a Unicode emoji or a server emote
        `role` must be found in the channel's server"""
        guild = channel.guild
        message = await self.safe_get_message(channel, message_id)
        if message is None:
            response = self.MESSAGE_NOT_FOUND()
        elif guild is None:
            response = self.NOT_IN_SERVER()
        elif role.guild != channel.guild:
            response = self.ROLE_NOT_FOUND()
        elif channel.guild.me.guild_permissions.manage_roles is False:
            response = self.CANT_MANAGE_ROLES()
        elif channel.permissions_for(channel.guild.me).add_reactions is False:
            response = self.CANT_ADD_REACTIONS()
        else:
            msg_conf = self.get_message_config(channel.id, message.id)
            emoji_match = self.EMOTE_REGEX.fullmatch(emoji)
            emoji_id = emoji if emoji_match is None else emoji_match.group(1)
            if emoji_id in await msg_conf({}):
                response = self.ALREADY_BOUND()
            else:
                emoji = None
                if emoji_id.isdigit():
                    for emoji_server in self.bot.guilds:
                        if emoji is None:
                            emoji = discord.utils.get(emoji_server.emojis, id=int(emoji_id))
                try:
                    await message.add_reaction(emoji or emoji_id)
                except discord.HTTPException:  # Failed to find the emoji
                    response = self.EMOJI_NOT_FOUND()
                else:
                    try:
                        await ctx.message.author.add_roles(role)
                        await ctx.message.author.remove_roles(role)
                    except (discord.Forbidden, discord.HTTPException):
                        response = self.CANT_GIVE_ROLE()
                        await message.remove_reaction(emoji or emoji_id, self.bot.user)
                    else:
                        self.add_to_cache(guild.id, channel.id, message_id, emoji_id, role)
                        await msg_conf.get_attr(emoji_id).set(role.id)
                        response = self.ROLE_SUCCESSFULLY_BOUND(emoji or emoji_id, channel.mention)
        await ctx.send(response)

    @_roles.command(name="remove")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_remove(self, ctx: Context, message_id: int, channel: discord.TextChannel, *,
                            role: discord.Role):
        """Remove a role from a message

        `message_id` must be found in `channel` and be bound to `role`
            To get a message's id: Settings > Appearance > Developer mode then
            Right click a message > Copy ID"""
        guild = channel.guild
        msg_config = self.get_message_config(channel.id, message_id)
        all_emojis = await msg_config.all()
        emoji_config = discord.utils.find(lambda o: o[1] == role.id, all_emojis.items())
        if emoji_config is None:
            await ctx.send(self.ROLE_NOT_BOUND())
        else:
            emoji_str = emoji_config[0]
            await msg_config.get_attr(emoji_str).clear()
            self.remove_role_from_cache(guild.id, channel.id, message_id, emoji_str)
            msg = await self.safe_get_message(channel, message_id)
            if msg is None:
                await ctx.send(self.ROLE_UNBOUND(self.MESSAGE_NOT_FOUND()))
            else:
                reaction = discord.utils.find(
                    lambda r: r.emoji.id == emoji_str if r.custom_emoji else r.emoji == emoji_str, msg.reactions)
                if reaction is None:
                    await ctx.send(self.ROLE_UNBOUND(self.REACTION_NOT_FOUND()))
                else:
                    answer = await ctx.send(self.ROLE_UNBOUND(self.REACTION_CLEAN_START()))
                    count = 0
                    start_time = time.perf_counter()
                    async for user in reaction.users():
                        await msg.remove_reaction(reaction.emoji, user)
                        count += 1
                        if time.perf_counter() - start_time > 1:
                            await answer.edit(content=self.ROLE_UNBOUND(self.PROGRESS_REMOVED(count, reaction.count)))
                            start_time = time.perf_counter()
                    await answer.edit(content=self.ROLE_UNBOUND(self.REACTION_CLEAN_DONE(count)))

    @_roles.command(name="check")
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def _roles_check(self, ctx: Context, message_id: int, channel: discord.TextChannel):
        """Goes through all reactions of a message and gives the roles accordingly

        This does NOT work with messages in a link"""
        guild = channel.guild
        msg = await self.safe_get_message(channel, message_id)
        server_links = self.links.get(guild.id, {})
        if str(channel.id) + "_" + str(message_id) in server_links:
            await ctx.send(self.CANT_CHECK_LINKED())
        elif msg is None:
            await ctx.send(self.MESSAGE_NOT_FOUND())
        else:
            msg_conf = self.get_message_config(channel.id, message_id)
            if await msg_conf(...) is not ...:
                progress_msg = await ctx.send(self.INITIALIZING())
                given_roles = 0
                checked_count = 0
                total_count = sum(map(lambda r: r.count, msg.reactions)) - len(msg.reactions)  # Remove the bot's
                total_reactions = 0
                for react in msg.reactions:  # Go through all reactions on the message and add the roles if needed
                    total_reactions += 1
                    emoji_str = str(react.emoji.id) if react.custom_emoji else react.emoji
                    role = self.get_from_cache(guild.id, channel.id, message_id, emoji_str)
                    if role is not None:
                        start_time = time.perf_counter()
                        async for user in react.users():
                            member = guild.get_member(user.id)
                            if member is not None and member != self.bot.user and \
                                    discord.utils.get(member.roles, id=role.id) is None:
                                await member.add_roles(role)
                                given_roles += 1
                            checked_count += 1
                            if time.perf_counter() - start_time > 1:
                                await progress_msg.edit(content=self.PROGRESS_FORMAT(c=checked_count, r=total_count,
                                                                                     t=total_reactions))
                                start_time = time.perf_counter()
                    else:
                        checked_count += react.count
                        await progress_msg.edit(content=self.PROGRESS_FORMAT(c=checked_count, r=total_count,
                                                                             t=total_reactions))
                await progress_msg.edit(content=self.PROGRESS_COMPLETE_FORMAT(c=checked_count, g=given_roles))

    # Utilities
    async def check_add_role(self, payload: RawReactionActionEvent):
        emoji_str = str(payload.emoji.id) if payload.emoji.is_custom_emoji() else payload.emoji.name
        role = self.get_from_cache(payload.guild_id, payload.channel_id, payload.message_id, emoji_str)
        if role is not None and payload.user_id != self.bot.user.id:
            link = self.get_link(payload.guild_id, payload.channel_id, payload.message_id)

            await self.add_role_queue(payload.guild_id, payload.user_id, role, True, linked_roles=link)

    async def check_remove_role(self, payload: RawReactionActionEvent):
        emoji_str = str(payload.emoji.id) if payload.emoji.is_custom_emoji() else payload.emoji.name
        role = self.get_from_cache(payload.guild_id, payload.channel_id, payload.message_id, emoji_str)
        if role is not None:
            if payload.user_id == self.bot.user.id:
                # Safeguard in case someone removes the bot's reaction by accident
                # noinspection PyProtectedMember
                await self.bot.http.add_reaction(payload.message_id, payload.channel_id, payload.emoji._as_reaction())
            else:
                await self.add_role_queue(payload.guild_id, payload.user_id, role, False)

    async def check_delete_message(self, payload: RawMessageDeleteEvent):
        # Remove the message's config
        message_conf = self.get_message_config(payload.channel_id, payload.message_id)
        if await message_conf(...) is not ...:  # Because for whatever reason this returns {} instead of None
            await message_conf.clear()
        # And the cache
        self.remove_message_from_cache(payload.guild_id, payload.channel_id, payload.message_id)
        # And the links' cache
        pair = str(payload.channel_id) + "_" + str(payload.message_id)
        if pair in self.links.get(payload.guild_id, {}):
            del self.links[payload.guild_id][pair]
        # And the links' config
        async with self.get_guild(payload.guild_id).links({}) as server_links:
            for links in server_links.values():
                if pair in links:
                    links.remove(pair)

    async def add_role_queue(self, guild_id: int, user_id: int, role: discord.Role, add_bool: bool, *,
                             linked_roles: set=None):
        linked_roles = linked_roles if linked_roles is not None else set()
        key = "{}_{}".format(guild_id, user_id)
        q = self.role_map.get(key)
        if q is None:  # True --> add   False --> remove
            # Always remove the @everyone role to prevent the bot from trying to give it to members
            q = {True: set(), False: set(), "guild_id": guild_id, "user_id": user_id}
            await self.role_queue.put(key)
        q[True].difference_update(linked_roles)  # Remove the linked roles from the roles to add
        q[False].update(linked_roles)  # Add the linked roles to remove them if the user has any of them
        q[not add_bool] -= {role}
        q[add_bool] |= {role}
        self.role_map[key] = q

    async def process_role_queue(self):  # This exists to update multiple roles at once when possible
        """Loops until the cog is unloaded and processes the role assignments when it can"""
        await self.bot.wait_until_ready()
        while self == self.bot.get_cog(self.__class__.__name__):  # TODO: Make this work better for multiple guilds
            key = await self.role_queue.get()
            q = self.role_map.pop(key)
            if q is not None:
                guild_id = q.get("guild_id")
                user_id = q.get("user_id")
                if guild_id is not None and user_id is not None:
                    guild = self.bot.get_guild(guild_id)
                    await self.maybe_update_guild(guild)
                    mem = guild.get_member(user_id)
                    if mem is not None:
                        all_roles = set(mem.roles)
                        add_set = q.get(True, set())
                        del_set = q.get(False, set())
                        del_set.add(guild.default_role)
                        try:
                            # Basically, the user's roles + the added - the removed
                            await mem.edit(roles=((all_roles | add_set) - del_set))
                        except (discord.Forbidden, discord.HTTPException):
                            self.role_map[key] = q  # Try again when it fails
                            await self.role_queue.put(key)
                        else:
                            self.role_queue.task_done()
                        finally:
                            await asyncio.sleep(self.PROCESSING_WAIT_TIME)
                    else:
                        self.logger.warning(self.LOG_MEMBER_NOT_FOUND(user_id=user_id, guild=guild.name))
        self.logger.info(self.LOG_PROCESSING_LOOP_ENDED())

    # Utilities
    async def safe_get_message(self, channel: discord.TextChannel, message_id: typing.Union[str, int]) \
            -> typing.Optional[discord.Message]:
        try:
            result = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            result = None
        return result

    # Provided by <@78631113035100160>
    async def maybe_update_guild(self, guild: discord.Guild):
        # ctx.guild.chunked is innaccurate, discord.py#1638
        if not guild.unavailable and guild.large:
            if not guild.chunked or any(m.joined_at is None for m in guild.members):
                await self.bot.request_offline_members(guild)

    def get_guild(self, guild_id: int) -> Group:
        # noinspection PyProtectedMember
        return self.config._get_base_group(self.config.GUILD, str(guild_id))

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
                role_list.update(self.get_all_roles_from_message(server_id, int(channel_id), int(message_id)))
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
    def add_to_cache(self, server_id: int, channel_id: int, message_id: int, emoji_str: str, role: discord.Role):
        """Adds an entry to the role cache"""
        self.role_cache.setdefault(server_id, {}) \
            .setdefault(channel_id, {}) \
            .setdefault(message_id, {})[emoji_str] = role

    def get_all_roles_from_message(self, server_id: int, channel_id: int, message_id: int) \
            -> typing.Iterable[discord.Role]:
        """Fetches all roles from a given message returns an iterable"""
        return self.role_cache.get(server_id, {}).get(channel_id, {}).get(message_id, {}).values()

    def get_from_cache(self, server_id: int, channel_id: int, message_id: int, emoji_str: str) -> discord.Role:
        """Fetches the role associated with an emoji on the given message"""
        return self.role_cache.get(server_id, {}).get(channel_id, {}).get(message_id, {}).get(emoji_str)

    def remove_role_from_cache(self, server_id: int, channel_id: int, message_id: int, emoji_str: str):
        """Removes an entry from the role cache"""
        server_conf = self.role_cache.get(server_id)
        if server_conf is not None:
            channel_conf = server_conf.get(channel_id)
            if channel_conf is not None:
                message_conf = channel_conf.get(message_id)
                if message_conf is not None and emoji_str in message_conf:
                    del message_conf[emoji_str]

    def remove_message_from_cache(self, server_id: int, channel_id: int, message_id: int):
        """Removes a message from the role cache"""
        server_conf = self.role_cache.get(server_id)
        if server_conf is not None:
            channel_conf = server_conf.get(channel_id)
            if channel_conf is not None and message_id in channel_conf:
                del channel_conf[message_id]

    def get_message_config(self, channel_id: int, message_id: int) -> Group:
        return self.config.custom(self.MESSAGE_GROUP, str(channel_id), str(message_id))

    async def get_all_message_configs(self) -> Group:
        return await self.config.custom(self.MESSAGE_GROUP).all()
