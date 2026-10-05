import asyncio
from astrbot.api.all import Star, EventMessageType, event_message_type, logger, AstrBotConfig, Context
from astrbot.api.event import filter
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


def 解析黑白名单(原列表: list[str]|set[str], 通配符=None) -> tuple[set[str], set[str]]:
    """
    解析原始访问控制列表，返回标准化的黑名单和白名单。

    Args:
        原列表: 原始字符串列表，如 ["all", "/123456", "234567"]
        通配符: 匹配的通配符，当匹配到通配符时，列表类型使用第一个
    Returns:
        tuple[set, set]: 顺序为黑名单，白名单
    """
    if 通配符 is None:
        通配符 = ['*', 'all']
    # 跳过非字符串和空字符串
    原列表 = [ i.strip() for i in 原列表 if isinstance(i, str) and i.strip() ]
    黑名单 = []
    白名单 = []
    if 通配符:
        if isinstance(通配符, (list, tuple)):
            t = 通配符[0]
            tl = 通配符
        elif isinstance(通配符, str):
            t = 通配符
            tl = [通配符]
        else:
            raise ValueError("通配符类型错误，应为list or str")
    else:
        tl = []

    for i in 原列表:

        # 黑名单判断（以 / 开头）
        if i.startswith('/'):
            i = i[1:]  # 去掉前缀 /
            if not i:
                continue
            if i in tl:
                return {t}, set()
            黑名单.append(i)
        else:
            白名单.append(i)

    # 规范化白名单
    if any(i in 白名单 for i in tl):
        白名单 = [t]

    白名单 = [ i for i in 白名单 if i not in 黑名单]

    return set(黑名单), set(白名单)

def 检测黑白名单(值:str, 黑白名单:tuple[set[str], set[str]], 通配符=None) -> bool:
    """检测值是否在黑白名单允许范围内，允许返回True"""
    if 通配符 is None:
        通配符 = ['*', 'all']
    黑名单 = 黑白名单[0]
    白名单 = 黑白名单[1]
    if 通配符:
        if isinstance(通配符, (list, tuple)):
            t = 通配符[0]
        elif isinstance(通配符, str):
            t = 通配符
        else:
            raise ValueError("通配符类型错误，应为list or str")
    else:
        t = ''
    if not (黑名单 or 白名单):
        return False
    if 黑名单:
        if t in 黑名单:
            return False
        if 值 in 黑名单:
            return False
    if 白名单:
        if t in 白名单:
            return True
        if 值 in 白名单:
            return True
    # 规范使用通配符，为空则拒绝
    return False


class 管理员识别(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.黑白名单 = 解析黑白名单(config.黑白名单 or ['all'])
        # 缓存格式: {群号: {用户id: 身份(中文)}}
        self.管理员缓存:dict[str,dict]={}
        # 从配置读取各级身份提示词模板，若无则使用默认，留空则不注入
        self.bot管理员提示词:str = config.get("bot管理员提示词", "")
        self.群主提示词:str = config.群主提示词
        self.管理员提示词:str = config.管理员提示词
        self.普通成员提示词:str = config.get("普通成员提示词", "")

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def 入口(self, event: AiocqhttpMessageEvent):
        """消息主入口：缓存群管理员和群主，避免每次都频繁获取"""
        if not (群号:=event.get_group_id()) or not 检测黑白名单(群号, self.黑白名单):
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
                        身份 = '群主' if role == 'owner' else '管理员'
                        身份映射[用户id] = 身份
                self.管理员缓存[群号] = 身份映射
                logger.info(f"已缓存群 {群号} 的管理员/群主信息，共 {len(身份映射)} 人")
            except Exception as e:
                logger.error(f"获取群成员列表失败: {e}")

    @filter.on_llm_request()
    async def llm请求前(self, event: AiocqhttpMessageEvent, req: ProviderRequest):
        """按身份等级 bot管理员 > 群主 > 管理员 > 普通成员 添加对应提示词"""
        if not (群号:=event.get_group_id()) or not 检测黑白名单(群号, self.黑白名单):
            return

        # 身份判定，优先级从高到低：bot管理员 > 群主 > 管理员 > 普通成员
        if event.is_admin():
            身份 = 'bot管理员'
        else:
            身份 = self.管理员缓存.get(群号, {}).get(event.get_sender_id(), '普通成员')

        # 根据身份选择模板，留空（含纯空白）则不注入
        模板 = {
            'bot管理员': self.bot管理员提示词,
            '群主': self.群主提示词,
            '管理员': self.管理员提示词,
            '普通成员': self.普通成员提示词,
        }.get(身份, '') or ''
        extra = 模板.replace("{昵称}", event.get_sender_name()).strip()
        if extra:
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

        if 当前群号 and not 检测黑白名单(当前群号, self.黑白名单):
            yield event.plain_result("❌ 当前群被黑白名单限制，本插件未在此群启用")
            return

        if aall == 'all':
            # 刷新所有已缓存的群（缓存中只会存在黑白名单允许的群）
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
        """获取发送者在群内（聊天室）的身份，返回用户名字加身份信息。（bot管理员 > 群主 > 管理员 > 普通成员）"""
        群号 = event.get_group_id()
        if 群号 and not 检测黑白名单(群号, self.黑白名单):
            return "本群未启用群管理员识别插件"
        if event.is_admin():
            return f"用户「{event.get_sender_name()}（{event.get_sender_id()}）」的群内身份为：bot管理员"
        结果 = self.管理员缓存.get(群号, {}).get(event.get_sender_id(), "普通成员")
        return f"用户「{event.get_sender_name()}（{event.get_sender_id()}）」的群内身份为：{结果}"

    @filter.llm_tool("get_chat_admins_and_owners")
    async def 所有管理员工具(self, event: AiocqhttpMessageEvent):
        """获取本群（聊天室）的所有群主和管理员，返回包括名字和用户ID
        如果用户是群主，你应该对该用户乖巧一点"""
        if not event.get_group_id():
            return "此功能仅能在群聊中使用，当前不是群聊"
        if not 检测黑白名单(event.get_group_id(), self.黑白名单):
            return "本群未启用群管理员识别插件"
        结果 = self.管理员缓存.get(event.get_group_id(), {})
        return 结果
