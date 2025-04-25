# 🚀 DOW项目 gewechat_channel.py 增强版

[![GitHub Stars](https://img.shields.io/badge/⭐_Star_Me-If_Useful-blue?style=flat)](https://github.com/your-repo-link)

专为微信机器人打造的增强模块，新增智能过滤@所有人消息功能，让群聊管理更清爽！

## 🛠️ 使用方法

​**1、一键替换方案**​  
1. 将本文件替换至目录：`/dify-on-wechat-gewe/channel/gewechat/gewechat_channel.py`
2. 重启项目服务

​**2、手动修改方案**​  

按以下步骤修改原`gewechat_channel.py`文件中的`Query`类：
```python
class Query:
    def POST(self):
        channel = GeWeChatChannel()
        web_data = web.data()
        logger.debug("[gewechat] receive data: {}".format(web_data))
        data = json.loads(web_data)
        
        # ... 其他代码 ...

        gewechat_msg = GeWeChatMessage(data, channel.client)
        
        # 微信客户端的状态同步消息
        if gewechat_msg.ctype == ContextType.STATUS_SYNC:
            logger.debug(f"[gewechat] ignore status sync message: {gewechat_msg.content}")
            return "success"

        # 忽略非用户消息（如公众号、系统通知等）
        if gewechat_msg.ctype == ContextType.NON_USER_MSG:
            logger.debug(f"[gewechat] ignore non-user message from {gewechat_msg.from_user_id}: {gewechat_msg.content}")
            return "success"

        # 忽略来自自己的消息
        if gewechat_msg.my_msg:
            logger.debug(f"[gewechat] ignore message from myself: {gewechat_msg.actual_user_id}: {gewechat_msg.content}")
            return "success"

        # 新增：忽略@所有人的群消息（不干扰正常@机器人）
        raw_content = data.get("Data", {}).get("Content", {}).get("string", "")
        msg_source = data.get("Data", {}).get("MsgSource", "")
        is_at_all = re.search(r'<atuserlist>(\s*|.*?all.*?)</atuserlist>', msg_source, re.IGNORECASE) or '@all' in msg_source.lower()
        content_at_all = re.search(
            r'@[\\s\xa0\u2000-\u200F\u3000]*所有[\\s\xa0\u2000-\u200F\u3000]*人',
            raw_content, 
            flags=re.UNICODE
        )
        
        if (is_at_all or content_at_all) and gewechat_msg.is_group:
            logger.debug(f"[gewechat] 精确匹配@所有人的群消息，已忽略")
            return "success"

        # 忽略过期的消息
        if int(gewechat_msg.create_time) < int(time.time()) - 60 * 5: # 跳过5分钟前的历史消息
            logger.debug(f"[gewechat] ignore expired message from {gewechat_msg.actual_user_id}: {gewechat_msg.content}")
            return "success"

        # ... 后续生成context和处理消息的代码 ...
```

## ✨ 核心升级特性
智能消息过滤系统  

​`✅精准拦截@所有人消息`

采用双正则校验机制，同时检测消息来源和内容特征  

​`🔧保持正常@机器人响应`​  

智能区分常规@指令和群发@所有人操作  

## 📦 依赖配置
确保在文件头部添加正则模块引用：  
```python
import re
```

## ⭐ 支持与反馈  

欢迎通过 Issues 提出建议或报告问题，如果这个模块为您节省了时间，请给项目点个 Star 支持我！❤️
