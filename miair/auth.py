"""小米账号认证管理"""

import logging
import os
import re

import aiohttp
from miservice import MiAccount, MiIOService, MiNAService

from miair.config import Config

log = logging.getLogger("miair")


def parse_cookie_string(cookie_str: str) -> dict:
    """解析 cookie 字符串，提取 userId 和 passToken"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in ("userId", "passToken"):
                result[key] = value
    return result


class AuthManager:
    """管理小米账号认证和设备服务"""

    def __init__(self, config: Config):
        self.config = config
        self.session: aiohttp.ClientSession | None = None
        self.account: MiAccount | None = None
        self.mina_service: MiNAService | None = None
        self.miio_service: MiIOService | None = None
        self._logged_in = False
        
        # 验证码流程相关状态
        self.need_verify = False
        self.verify_url = ""
        self.cloud_auth = None

    async def login(self):
        """登录小米账号并初始化服务"""
        os.makedirs(self.config.conf_path, exist_ok=True)

        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            )

        token_store = self.config.mi_token_home

        # 初始化基础 MiAccount（供后续服务使用）
        if self.account is None:
            self.account = MiAccount(
                self.session,
                self.config.account,
                self.config.password,
                token_store=token_store,
            )
            if not hasattr(self.account, 'token') or self.account.token is None:
                self.account.token = {"deviceId": "miair_device"}

        # 如果有 cookie，直接当做已登录（兼容历史配置，但仍可能有签名缺陷）
        if self.config.cookie:
            token_data = parse_cookie_string(self.config.cookie)
            if token_data.get("userId") and token_data.get("passToken"):
                self.account.token.update({
                    "userId": token_data["userId"],
                    "passToken": token_data["passToken"],
                    "ssecurity": "",
                    "serviceToken": "",
                })
                self._logged_in = True
                log.info("使用 cookie 登录（跳过底层验证）")
                self._init_services()
                return

        # 使用云端原生登录逻辑
        from miair.cloud import MiCloudAuth, NeedVerifyException, MiCloudException
        
        if self.cloud_auth is None:
            self.cloud_auth = MiCloudAuth(self.config.account, self.config.password, session=self.session)
            
            # 如果 miservice 的 token 库中保存了上次扫码的 passToken，我们将其注入到 cloud_auth 中
            if self.account and hasattr(self.account, 'token') and self.account.token:
                if 'passToken' in self.account.token:
                    self.cloud_auth.pass_token = self.account.token['passToken']
                    self.cloud_auth.cookies['passToken'] = self.account.token['passToken']
                if 'userId' in self.account.token:
                    self.cloud_auth.user_id = str(self.account.token['userId'])
                    self.cloud_auth.cookies['userId'] = str(self.account.token['userId'])
            
        try:
            # 尝试登录小爱服务
            await self.cloud_auth.login("micoapi")
            self._populate_account_token("micoapi")
            
            # 为了米家设备，尝试获取 xiaomiio 的 token
            try:
                await self.cloud_auth.login("xiaomiio")
                self._populate_account_token("xiaomiio")
            except Exception as e:
                log.warning(f"获取米家(xiaomiio) token 失败: {e}，可能影响设备控制")
                
            self._logged_in = True
            self.need_verify = False
            self.verify_url = ""
            log.info("小米账号登录成功")
            
            # 保存合并后的 token 到 miservice 的缓存
            if self.account.token_store:
                self.account.token_store.save_token(self.account.token)
                
        except NeedVerifyException as e:
            self._logged_in = False
            self.need_verify = True
            self.verify_url = e.verify_url
            log.warning(f"登录需要验证码! 验证链接: {self.verify_url}")
            
        except Exception as e:
            self._logged_in = False
            log.error(f"登录失败: {e}")
            if self.config.auto_restart:
                log.warning("检测到登录失败，正在尝试自动重启程序以恢复服务...")
                from miair.web.api import _restart_process
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(5, _restart_process)
                except RuntimeError:
                    _restart_process()

        self._init_services()

    async def submit_verify_ticket(self, ticket: str) -> bool:
        """提交二次验证码(ticket)以完成登录"""
        if not self.cloud_auth or not self.need_verify:
            log.warning("当前不需要验证码，或者未初始化登录")
            return False
            
        from miair.cloud import MiCloudException
        try:
            log.info(f"正在提交验证码进行验证...")
            # 利用之前保存的上下文恢复登录
            await self.cloud_auth.login("micoapi", login_data={"verify_ticket": ticket})
            self._populate_account_token("micoapi")
            
            # 尝试获取 xiaomiio 的 token
            try:
                await self.cloud_auth.login("xiaomiio")
                self._populate_account_token("xiaomiio")
            except Exception as e:
                log.warning(f"获取米家(xiaomiio) token 失败: {e}")
                
            self._logged_in = True
            self.need_verify = False
            self.verify_url = ""
            log.info("验证码校验通过，小米账号登录成功")
            
            if self.account and self.account.token_store:
                self.account.token_store.save_token(self.account.token)
                
            self._init_services()
            return True
            
        except Exception as e:
            log.error(f"验证码校验失败或后续登录失败: {e}")
            return False

    async def poll_qrcode_login(self, lp_url: str) -> bool:
        """轮询二维码扫码状态并在成功后初始化服务"""
        if not self.cloud_auth:
            return False
            
        try:
            log.info(f"正在轮询扫码登录状态...")
            success = await self.cloud_auth.poll_qrcode_login(lp_url)
            if success:
                # 扫码成功后，目前 cloud_auth 内部拥有了全局 passToken
                # 我们只需要分别显式调用 login() 获取对应的 serviceToken 即可
                
                # 获取 micoapi Token
                await self.cloud_auth.login("micoapi")
                self._populate_account_token("micoapi")
                
                # 获取 xiaomiio Token
                await self.cloud_auth.login("xiaomiio")
                self._populate_account_token("xiaomiio")
                
                self._logged_in = True
                self.need_verify = False
                self.verify_url = ""
                log.info("扫码登录完成，获取服务 Token 成功")
                
                if self.account.token_store:
                    self.account.token_store.save_token(self.account.token)
                    
                self._init_services()
                return True
            else:
                log.warning("扫码登录未成功或已超时")
                return False
        except Exception as e:
            log.error(f"扫码登录过程中发生错误: {e}")
            return False

    def _populate_account_token(self, sid: str):
        """将 cloud_auth 获取到的 token 填充到 miservice 的 account 中"""
        if not self.account:
            return
        t = self.account.token
        t["userId"] = self.cloud_auth.user_id
        t["passToken"] = self.cloud_auth.pass_token
        t[sid] = (self.cloud_auth.ssecurity, self.cloud_auth.service_token)

    def _init_services(self):
        """初始化底层服务"""
        self.mina_service = MiNAService(self.account)
        self.miio_service = MiIOService(self.account)

    def clear_login_state(self):
        """清除当前登录状态，强制重新登录"""
        self._logged_in = False
        self.need_verify = False
        self.verify_url = ""
        self.cloud_auth = None
        self.account = None
        self.mina_service = None
        self.miio_service = None

    async def ensure_login(self):
        """确保已登录，未登录则尝试登录"""
        if self.need_verify:
            return
            
        if self.mina_service is None or not self._logged_in:
            await self.login()

    @staticmethod
    def _extract_error_code(err_msg: str) -> str:
        """从异常消息中提取数字错误码"""
        m = re.search(r'\b(\d{4,6})\b', err_msg)
        return m.group(1) if m else ""

    async def get_device_list(self) -> list[dict]:
        """获取账号下所有设备列表"""
        await self.ensure_login()
        if not self._logged_in:
            if getattr(self, "need_verify", False):
                log.debug("等待输入验证码，无法获取设备列表")
            else:
                log.debug("未成功登录，无法获取设备列表")
            return []
        try:
            devices = await self.mina_service.device_list()
            return devices or []
        except Exception as e:
            log.warning(f"获取设备列表失败: {e}")
            # 可能 token 过期，尝试重新登录
            # 但如果使用 cookie 登录，不要重新调用 login（避免 KeyError）
            if self.config.cookie:
                log.error(f"Cookie 可能已过期，请重新获取: {e}")
                return []
            await self.close()
            await self.login()
            if not self._logged_in:
                return []
            try:
                devices = await self.mina_service.device_list()
                return devices or []
            except Exception as e2:
                log.error(f"重新登录后仍然失败: {e2}")
                return []

    async def update_speakers_info(self):
        """从云端获取设备信息，更新 speakers 配置"""
        devices = await self.get_device_list()
        did_list = self.config.get_did_list()

        for device in devices:
            miot_did = device.get("miotDID", "")
            if miot_did in did_list:
                speaker = self.config.get_speaker(miot_did)
                speaker.device_id = device.get("deviceID", "")
                speaker.hardware = device.get("hardware", "")
                if not speaker.name:
                    speaker.name = device.get("name", "")
                speaker.ensure_udn()
                log.info(
                    f"已更新设备信息: {speaker.name} "
                    f"(did={miot_did}, device_id={speaker.device_id}, "
                    f"hardware={speaker.hardware})"
                )

    def is_logged_in(self) -> bool:
        """是否已成功登录"""
        return self._logged_in

    async def close(self):
        """关闭 session"""
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None
        self.account = None
        self.mina_service = None
        self.miio_service = None
        self._logged_in = False
