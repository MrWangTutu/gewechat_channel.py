import os
import time
import json
import web
import re
from urllib.parse import urlparse
import requests
import cv2
from PIL import Image

from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.gewechat.gewechat_message import GeWeChatMessage
from common.log import logger
from common.singleton import singleton
from common.tmp_dir import TmpDir
from config import conf, save_config
from lib.gewechat import GewechatClient
from voice.audio_convert import mp3_to_silk
import uuid

MAX_UTF8_LEN = 2048

@singleton
class GeWeChatChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()

        self.base_url = conf().get("gewechat_base_url")
        if not self.base_url:
            logger.error("[gewechat] base_url is not set")
            return
        self.token = conf().get("gewechat_token")
        self.client = GewechatClient(self.base_url, self.token)

        # 如果token为空，尝试获取token
        if not self.token:
            logger.warning("[gewechat] token is not set，trying to get token")
            token_resp = self.client.get_token()
            # {'ret': 200, 'msg': '执行成功', 'data': 'tokenxxx'}
            if token_resp.get("ret") != 200:
                logger.error(f"[gewechat] get token failed: {token_resp}")
                return
            self.token = token_resp.get("data")
            conf().set("gewechat_token", self.token)
            save_config()
            logger.info(f"[gewechat] new token saved: {self.token}")
            self.client = GewechatClient(self.base_url, self.token)

        self.app_id = conf().get("gewechat_app_id")
        if not self.app_id:
            logger.warning("[gewechat] app_id is not set，trying to get new app_id when login")

        self.download_url = conf().get("gewechat_download_url")
        if not self.download_url:
            logger.warning("[gewechat] download_url is not set, unable to download image")

        logger.info(f"[gewechat] init: base_url: {self.base_url}, token: {self.token}, app_id: {self.app_id}, download_url: {self.download_url}")

    def startup(self):
        # 如果app_id为空或登录后获取到新的app_id，保存配置
        app_id, error_msg = self.client.login(self.app_id)
        if error_msg:
            logger.error(f"[gewechat] login failed: {error_msg}")
            return

        # 如果原来的self.app_id为空或登录后获取到新的app_id，保存配置
        if not self.app_id or self.app_id != app_id:
            conf().set("gewechat_app_id", app_id)
            save_config()
            logger.info(f"[gewechat] new app_id saved: {app_id}")
            self.app_id = app_id

        # 获取回调地址，示例地址：http://172.17.0.1:9919/v2/api/callback/collect  
        callback_url = conf().get("gewechat_callback_url")
        if not callback_url:
            logger.error("[gewechat] callback_url is not set, unable to start callback server")
            return

        # 创建新线程设置回调地址
        import threading
        def set_callback():
            # 等待服务器启动（给予适当的启动时间）
            import time
            logger.info("[gewechat] sleep 3 seconds waiting for server to start, then set callback")
            time.sleep(3)

            # 设置回调地址，{ "ret": 200, "msg": "操作成功" }
            callback_resp = self.client.set_callback(self.token, callback_url)
            if callback_resp.get("ret") != 200:
                logger.error(f"[gewechat] set callback failed: {callback_resp}")
                return
            logger.info("[gewechat] callback set successfully")

        callback_thread = threading.Thread(target=set_callback, daemon=True)
        callback_thread.start()

        # 从回调地址中解析出端口与url path，启动回调服务器  
        parsed_url = urlparse(callback_url)
        path = parsed_url.path
        # 如果没有指定端口，使用默认端口80
        port = parsed_url.port or 80
        logger.info(f"[gewechat] start callback server: {callback_url}, using port {port}")
        urls = (path, "channel.gewechat.gewechat_channel.Query")
        app = web.application(urls, globals(), autoreload=False)
        web.httpserver.runsimple(app.wsgifunc(), ("0.0.0.0", port))

    def send_video(self, to_wxid, video_url, thumb_url, video_duration):
        """发送视频消息
        Args:
            to_wxid: 接收人wxid
            video_url: 视频URL
            thumb_url: 视频缩略图URL
            video_duration: 视频时长(秒)
        Returns:
            dict: 发送结果
        """
        try:
            # 下载视频到本地临时目录
            video_file_name = f"video_{str(uuid.uuid4())}.mp4"
            video_file_path = TmpDir().path() + video_file_name

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            # 下载视频
            with requests.get(video_url, headers=headers, stream=True) as r:
                r.raise_for_status()
                with open(video_file_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            # 生成缩略图
            thumb_file_name = f"thumb_{str(uuid.uuid4())}.jpg"
            thumb_file_path = TmpDir().path() + thumb_file_name

            # 使用OpenCV读取视频第一帧作为缩略图
            cap = cv2.VideoCapture(video_file_path)
            ret, frame = cap.read()
            if ret:
                # 获取视频时长
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                video_duration = int(frame_count / fps) if fps > 0 else 10

                # 保持原图尺寸
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                image.save(thumb_file_path, 'JPEG', quality=95)
            else:
                # 如果无法读取视频帧，创建一个默认的黑色缩略图
                image = Image.new('RGB', (480, 270), color='black')
                image.save(thumb_file_path, 'JPEG', quality=95)

            cap.release()

            # 构造本地URL
            callback_url = conf().get("gewechat_callback_url")
            local_thumb_url = callback_url + "?file=" + thumb_file_path

            # 发送视频
            resp = self.client.post_video(
                self.app_id,
                to_wxid,
                video_url,
                local_thumb_url,  # 使用生成的缩略图
                video_duration
            )

            if resp.get("ret")!= 200:
                logger.error(f"[gewechat] send video failed: {resp}")
                return None

            return resp.get("data")

        except Exception as e:
            logger.error(f"[gewechat] send video error: {e}")
            return None

    def send(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        gewechat_message = context.get("msg")
        
        if reply.type in [ReplyType.TEXT, ReplyType.ERROR, ReplyType.INFO]:
            reply_text = reply.content
            ats = ""
            if gewechat_message and gewechat_message.is_group:
                ats = gewechat_message.actual_user_id
                
            # 按 //n 分割消息
            messages = reply_text.split("//n")
            messages = [msg.strip() for msg in messages if msg.strip()]
            
            # 分段发送
            for i, msg in enumerate(messages):
                try:
                    # 只在第一段添加@信息
                    if i == 0:
                        self.client.post_text(self.app_id, receiver, msg, ats)
                    else:
                        self.client.post_text(self.app_id, receiver, msg, "")
                        
                    logger.info(f"[gewechat] Send message part {i+1}/{len(messages)}: {msg}")
                    
                    # 添加发送间隔，避免消息发送太快
                    if i < len(messages) - 1:
                        time.sleep(0.5)
                        
                except Exception as e:
                    logger.error(f"[gewechat] Failed to send message: {str(e)}")
                    continue
        elif reply.type == ReplyType.VOICE:
            try:
                content = reply.content
                if content.endswith('.mp3'):
                    # 如果是mp3文件，转换为silk格式
                    silk_path = content + '.silk'
                    duration = mp3_to_silk(content, silk_path)
                    callback_url = conf().get("gewechat_callback_url")
                    silk_url = callback_url + "?file=" + silk_path
                    self.client.post_voice(self.app_id, receiver, silk_url, duration)
                    logger.info(f"[gewechat] Do send voice to {receiver}: {silk_url}, duration: {duration/1000.0} seconds")
                    return
                else:
                    logger.error(f"[gewechat] voice file is not mp3, path: {content}, only support mp3")
            except Exception as e:
                logger.error(f"[gewechat] send voice failed: {e}")
        elif reply.type == ReplyType.IMAGE_URL:
            img_url = reply.content
            self.client.post_image(self.app_id, receiver, img_url)
            logger.info("[gewechat] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.IMAGE:
            image_storage = reply.content
            image_storage.seek(0)
            # Save image to tmp directory
            img_data = image_storage.read()
            img_file_name = f"img_{str(uuid.uuid4())}.png"
            img_file_path = TmpDir().path() + img_file_name
            with open(img_file_path, "wb") as f:
                f.write(img_data)
            # Construct callback URL
            callback_url = conf().get("gewechat_callback_url")
            img_url = callback_url + "?file=" + img_file_path
            self.client.post_image(self.app_id, receiver, img_url)
            logger.info("[gewechat] sendImage, receiver={}, url={}".format(receiver, img_url))
        elif reply.type == ReplyType.VIDEO_URL:
            try:
                video_url = reply.content
                # 使用视频URL作为缩略图
                thumb_url = video_url
                # 默认视频时长设为10秒
                video_duration = 10
                result = self.send_video(receiver, video_url, thumb_url, video_duration)
                if result:
                    logger.info(f"[gewechat] Video sent successfully to {receiver}: {video_url}")
                else:
                    logger.error(f"[gewechat] Failed to send video to {receiver}: {video_url}")
            except Exception as e:
                logger.error(f"[gewechat] send video failed: {e}")

class Query:
    def GET(self):
        # 搭建简单的文件服务器，用于向gewechat服务传输语音等文件，但只允许访问tmp目录下的文件
        params = web.input(file="")
        file_path = params.file
        if file_path:
            # 使用os.path.abspath清理路径
            clean_path = os.path.abspath(file_path)
            # 获取tmp目录的绝对路径
            tmp_dir = os.path.abspath("tmp")
            # 检查文件路径是否在tmp目录下
            if not clean_path.startswith(tmp_dir):
                logger.error(f"[gewechat] Forbidden access to file outside tmp directory: file_path={file_path}, clean_path={clean_path}, tmp_dir={tmp_dir}")
                raise web.forbidden()

            if os.path.exists(clean_path):
                with open(clean_path, 'rb') as f:
                    return f.read()
            else:
                logger.error(f"[gewechat] File not found: {clean_path}")
                raise web.notfound()
        return "gewechat callback server is running"

    def POST(self):
        channel = GeWeChatChannel()
        web_data = web.data()
        logger.debug("[gewechat] receive data: {}".format(web_data))
        data = json.loads(web_data)
        
        # gewechat服务发送的回调测试消息
        if isinstance(data, dict) and 'testMsg' in data and 'token' in data:
            logger.debug(f"[gewechat] 收到gewechat服务发送的回调测试消息")
            return "success"

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

        # 精确判断@所有人的群消息（不干扰正常@机器人）
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

        context = channel._compose_context(
            gewechat_msg.ctype,
            gewechat_msg.content,
            isgroup=gewechat_msg.is_group,
            msg=gewechat_msg,
        )
        if context:
            channel.produce(context)
        return "success"
