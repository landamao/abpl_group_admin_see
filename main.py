import asyncio
from astrbot.api.all import Star, EventMessageType, event_message_type, logger, AstrBotConfig, Context
from astrbot.api.event import filter
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

class 管理员识别(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.禁用的群:list[str] = config.启用的群聊
        # 缓存格式: {群号: {用户id: 身份(中文)}}
        self.管理员缓存:dict[str,dict]={}
        # 从配置读取群主和管理员提示词模板，若无则使用默认
        self.群主提示词:str = config.群主提示词
        self.管理员提示词:str = config.管理员提示词

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def 入口(self, event: AiocqhttpMessageEvent):
        """消息主入口：缓存群管理员和群主，避免每次都频繁获取"""
        if (群号:=event.get_group_id()) in self.禁用的群:
            return

        # 如果该群尚未缓存，则获取成员列表并仅缓存管理员和群主
        if 群号 not in self.管理员缓存:
            try:
                成员列表 = await event.bot.get_group_member_list(group_id=int(群号))
                身份映射 = {}
                for 成员 in 成员列表:
                    role = 成员.get('role')
                    if role in ('owner', 'admin'):
                        用户id = str(成员['user_id'])
                        if role == 'owner':
                            身份 = '群主'
                        else:
                            身份 = '管理员'
                        身份映射[用户id] = 身份
                self.管理员缓存[群号] = 身份映射
                logger.info(f"已缓存群 {群号} 的管理员/群主信息，共 {len(身份映射)} 人")
            except Exception as e:
                logger.error(f"获取群成员列表失败: {e}")

    @filter.on_llm_request()
    async def llm请求前(self, event: AiocqhttpMessageEvent, req: ProviderRequest):
        """若发送者是群主或管理员，则添加对应提示词"""
        if not (群号:=event.get_group_id()) or 群号 not in self.禁用的群:
            return

        # 从缓存查找该用户的身份（仅当是管理员或群主时存在）
        身份 = self.管理员缓存.get(群号, {}).get(event.get_sender_id(), None)

        if 身份 is None:
            # 普通成员或未缓存，直接返回
            return

        # 根据身份选择模板
        if 身份 == '群主':
            extra = self.群主提示词.replace("{昵称}", event.get_sender_name())
        else:  # 管理员
            extra = self.管理员提示词.replace("{昵称}", event.get_sender_name())

        # 拼接提示词
        req.system_prompt += "\n" + extra

    @filter.command("刷新管理员缓存")
    async def 刷新缓存(self, event: AiocqhttpMessageEvent, aall: str = ''):
        """刷新缓存：不带参数刷新当前群，带 all 刷新所有已缓存群"""
        if not event.is_admin():
            return
        当前群号 = event.get_group_id()
        if not 当前群号 and aall != 'all':
            yield event.plain_result("⚠️ 在私聊使用请添加all参数刷新所有群，或在群里使用刷新当前群")
            return

        if 当前群号 in self.禁用的群:
            yield event.plain_result("❌ 当前群在禁用列表，请移除后再试")
            return

        if aall == 'all':
            # 刷新所有已缓存的群
            if not self.管理员缓存:
                yield event.plain_result("⚠️ 当前没有已缓存的群，请改用刷新当前群")
                return

            成功群 = []
            失败群 = []
            for 群号 in list(self.管理员缓存.keys()):
                await asyncio.sleep(1)
                try:
                    # 重新获取该群成员列表并更新缓存
                    成员列表 = await event.bot.get_group_member_list(group_id=int(群号))
                    身份映射 = {}
                    for 成员 in 成员列表:
                        role = 成员.get('role')
                        if role in ('owner', 'admin'):
                            用户id = str(成员['user_id'])
                            身份 = '群主' if role == 'owner' else '管理员'
                            身份映射[用户id] = 身份
                    self.管理员缓存[群号] = 身份映射
                    成功群.append(群号)
                except Exception as e:
                    logger.error(f"刷新群 {群号} 缓存失败: {e}")
                    失败群.append(群号)

            msg = f"✅ 刷新完成。成功: {len(成功群)} 个群，失败: {len(失败群)} 个群。"
            if 失败群:
                msg += f" 失败群号: {', '.join(map(str, 失败群))}"
            yield event.plain_result(msg)
        else:
            # 刷新当前群
            try:
                成员列表 = await event.bot.get_group_member_list(group_id=int(当前群号))  # 使用当前事件的群号
                身份映射 = {}
                for 成员 in 成员列表:
                    role = 成员.get('role')
                    if role in ('owner', 'admin'):
                        用户id = str(成员['user_id'])
                        身份 = '群主' if role == 'owner' else '管理员'
                        身份映射[用户id] = 身份
                self.管理员缓存[当前群号] = 身份映射
                yield event.plain_result(f"✅ 已刷新当前群 {当前群号} 的管理员/群主缓存，共 {len(身份映射)} 人。")
            except Exception as e:
                logger.error(f"刷新当前群缓存失败: {e}")
                yield event.plain_result(f"❌ 刷新失败，请在控制台查看错误日志")

    @filter.llm_tool("get_user_role_in_chat")
    async def 身份工具(self, event: AiocqhttpMessageEvent):
        """获取发送者在群内（聊天室）的身份，返回用户名字加身份信息。（超管 > 群主 > 管理员）"""
        if event.is_admin():
            return f"用户「{event.get_sender_name()}（{event.get_sender_id()}）」的群内身份为：超管"
        结果 = self.管理员缓存.get(event.get_group_id(), {}).get(event.get_sender_id(), "普通成员")
        return f"用户「{event.get_sender_name()}（{event.get_sender_id()}）」的群内身份为：{结果}"

    @filter.llm_tool("get_chat_admins_and_owners")
    async def 所有管理员工具(self, event: AiocqhttpMessageEvent):
        """获取本群（聊天室）的所有群主和管理员，返回包括名字和用户ID
        如果用户是群主，你应该对该用户乖巧一点"""
        if not event.get_group_id():
            return "此功能仅能在群聊中使用，当前不是群聊"
        结果 = self.管理员缓存.get(event.get_group_id(), {})
        return 结果