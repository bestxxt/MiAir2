"""小米账号登录逻辑，移植自 hass-xiaomi-miot"""

import asyncio
import base64
import hashlib
import json
import logging
import random
import string
import time
from urllib import parse

import aiohttp

log = logging.getLogger("miair")

ACCOUNT_BASE = 'https://account.xiaomi.com'


class NeedVerifyException(Exception):
    def __init__(self, message, verify_url=None):
        super().__init__(message)
        self.verify_url = verify_url


class MiCloudException(Exception):
    pass


class MiCloudAccessDenied(MiCloudException):
    pass


def get_random_string(length):
    return "".join(random.sample(string.ascii_letters + string.digits, length))


class MiCloudAuth:
    def __init__(self, username, password, session=None):
        self.username = username
        self.password = password
        self.client_id = get_random_string(16).upper()
        self.useragent = f"APP/com.xiaomi.mihome APPV/7.5.703 iosMinorVersion/15.5 iosBuildVersion/19F77 h2/0 aiohttp"
        self.session = session or aiohttp.ClientSession()
        
        self.cookies = {}
        self.attrs = {}
        
        self.user_id = ""
        self.cuser_id = ""
        self.pass_token = ""
        self.ssecurity = ""
        self.service_token = ""
        
        self.sid = "micoapi"  # 默认小爱音箱服务

    async def _account_request(self, method, url, **kwargs):
        if url[:4] != 'http':
            url = f'{ACCOUNT_BASE}{url}'
            
        request_cookies = {**self.cookies, **kwargs.pop('cookies', {})}
        
        headers = kwargs.setdefault('headers', {})
        if 'User-Agent' not in headers:
            headers['User-Agent'] = self.useragent
            
        return_response = kwargs.pop('return_response', False)
        allow_redirects = kwargs.pop('allow_redirects', True)
        
        def sync_request():
            import requests
            # 使用 requests.Session 以便在自动处理重定向时原生保持 cookie
            with requests.Session() as req_session:
                resp = req_session.request(
                    method, url, cookies=request_cookies, allow_redirects=allow_redirects, **kwargs
                )
                # 提取整个 session 过程中的所有 cookie（包括重定向中间页面的）
                return resp, req_session.cookies.get_dict()
                
        loop = asyncio.get_event_loop()
        resp, session_cookies = await loop.run_in_executor(None, sync_request)
        
        # update cookies
        self.cookies.update(session_cookies)
        
        if return_response:
            # Return a dict containing needed response properties
            return {
                'status_code': resp.status_code,
                'text': resp.text,
                'cookies': {**self.cookies}
            }
        
        try:
            # Remove &&&START&&&
            clean_text = resp.text.replace('&&&START&&&', '')
            data = json.loads(clean_text)
        except Exception:
            data = {
                'code': resp.status_code,
                'response': resp.text,
            }
            
        return data

    async def _account_get(self, url, **kwargs):
        return await self._account_request('GET', url, **kwargs)

    async def _account_post(self, url, **kwargs):
        return await self._account_request('POST', url, **kwargs)

    async def get_login_qrcode(self):
        """获取登录二维码"""
        url = "https://account.xiaomi.com/longPolling/loginUrl"
        data = {
            "_qrsize": "480",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "callback": "https://sts.api.io.mi.com/sts",
            "_hasLogo": "false",
            "sid": "xiaomiio",
            "serviceParam": "",
            "_locale": "en_GB",
            "_dc": str(int(time.time() * 1000))
        }

        resp_data = await self._account_get(url, params=data)
        if isinstance(resp_data, dict) and "qr" in resp_data:
            return {
                "qr_url": resp_data["qr"],
                "login_url": resp_data.get("loginUrl"),
                "lp_url": resp_data.get("lp"),
                "timeout": resp_data.get("timeout", 60)
            }
        log.error(f"获取二维码失败: {resp_data}")
        return None

    async def poll_qrcode_login(self, lp_url, timeout=60):
        """轮询二维码扫码状态"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 使用 return_response=True 获取原始响应
                # 为了防止阻塞太久，我们在 requests.Session.request 里可以传递 timeout，但这需要修改底层
                resp_data = await self._account_get(lp_url, return_response=True)
                status_code = resp_data.get('status_code')
                
                if status_code == 200:
                    text = resp_data.get('text', '')
                    clean_text = text.replace('&&&START&&&', '')
                    data = json.loads(clean_text)
                    
                    # 小米长轮询接口中，如果 code 不是 0（例如扫码未确认），可能是其他状态
                    if data.get('code') == 0 or 'location' in data:
                        self.user_id = data.get("userId")
                        self.ssecurity = data.get("ssecurity")
                        self.cuser_id = data.get("cUserId")
                        self.pass_token = data.get("passToken")
                        location = data.get("location")
                        
                        if location:
                            log.info(f"扫码登录成功! passToken: {self.pass_token[:10]}...")
                            # 成功获取到了 passToken, 保存到全局 cookies 中
                            self.cookies['passToken'] = self.pass_token
                            self.cookies['userId'] = str(self.user_id)
                            
                            # 立即利用这个 location 获取 xiaomiio 的 serviceToken
                            sts_resp = await self._account_get(location, return_response=True)
                            if sts_resp.get('status_code') == 200:
                                return True
                    # 如果返回 200 但没有 location，继续轮询
                    await asyncio.sleep(2)
                else:
                    # HTTP 408 或其他错误，继续轮询
                    await asyncio.sleep(2)
            except Exception as e:
                # 遇到超时或者网络错误，忽略并继续轮询
                log.debug(f"轮询请求异常(可能超时): {e}")
                await asyncio.sleep(2)
                
        return False

    async def login(self, sid="micoapi", login_data=None):
        self.sid = sid
        
        location = ''
        auth = self.attrs.pop('login_data', {})
        
        if login_data and 'verify_ticket' in login_data:
            ticket = login_data['verify_ticket']
            resp = await self.verify_ticket(ticket)
            location = resp.get('location', '')
            if location:
                # Need to allow redirects to update session state implicitly?
                await self._account_get(location, allow_redirects=True)
                auth = await self._login_step1(sid=sid)
                # 只有验证成功 (code == 0) 才提取 location，防止提取到 70016 错误的网页登录页
                location = auth.get('location', '') if auth.get('code') == 0 else ''
                log.info(f"verify_ticket path: after _login_step1, auth: {auth}, location: {location}")
            else:
                log.warning(f"验证码成功提交，但未获取到 location。响应为: {resp}")
        else:
            if login_data:
                auth.update(login_data)
            auth = await self._login_step1(sid=sid)
            location = auth.get('location', '') if auth.get('code') == 0 else ''
            log.info(f"normal path: after _login_step1, auth: {auth}")
            
        if not location:
            log.info("location is empty, calling _login_step2")
            auth['sid'] = sid
            location = await self._login_step2(**auth)
        elif sid != 'xiaomiio' and 'clientSign' not in location:
            # 如果是扫码/已有 passToken 的情况跳过了 step2，需要手动拼接 clientSign
            sign = f'nonce={auth.get("nonce")}&{auth.get("ssecurity")}'
            sign = base64.b64encode(hashlib.sha1(sign.encode()).digest()).decode()
            location += '&clientSign=' + parse.quote(sign)
            
        log.info(f"Proceeding to _login_step3 with location: {location}")
            
        response = await self._login_step3(location)
        http_code = response.get('status_code')
        if http_code == 200:
            return True
        elif http_code == 403:
            raise MiCloudAccessDenied(f"Login to xiaomi error: {response.get('text')} ({http_code})")
        else:
            raise MiCloudException(f"Login to xiaomi error: {response.get('text')} ({http_code})")

    async def _login_step1(self, sid=None):
        req_sid = sid or self.sid
        self.cookies.update({'sdkVersion': '3.8.6', 'deviceId': self.client_id})
        try:
            auth = await self._account_get(
                '/pass/serviceLogin',
                params={'sid': req_sid, '_json': 'true'}
            )
        except Exception as exc:
            raise MiCloudException(f"Error getting xiaomi login sign: {exc}")
            
        if auth.get('code') == 0:
            self.user_id = str(auth.get('userId', self.user_id))
            self.cuser_id = auth.get('cUserId', self.cuser_id)
            self.ssecurity = auth.get('ssecurity', self.ssecurity)
            self.pass_token = auth.get('passToken', self.pass_token)
            
        return auth

    async def _login_step2(self, captcha=None, **kwargs):
        url = '/pass/serviceLoginAuth2'
        req_sid = kwargs.get('sid', self.sid)
        post_data = {
            'user': self.username,
            'hash': hashlib.md5(self.password.encode()).hexdigest().upper(),
            'callback': kwargs.get('callback', ''),
            'sid': req_sid,
            'qs': kwargs.get('qs', ''),
            '_sign': kwargs.get('_sign', ''),
        }
        params = {'_json': 'true'}
        req_cookies = {}
        
        if captcha:
            post_data['captCode'] = captcha
            params['_dc'] = int(time.time() * 1000)
            req_cookies['ick'] = self.attrs.pop('captchaIck', '')
            
        resp_data = await self._account_post(
            url, data=post_data, params=params, cookies=req_cookies, return_response=True
        )
        
        try:
            auth = json.loads(resp_data['text'].replace('&&&START&&&', ''))
        except Exception:
            auth = {}
            
        location = auth.get('location')
        if not location:
            if ntf := auth.get('notificationUrl'):
                if ntf[:4] != 'http':
                    ntf = f'{ACCOUNT_BASE}{ntf}'
                self.attrs['verify_url'] = ntf
                # 保存登录数据，以防重试需要
                self.attrs['login_data'] = kwargs
                raise NeedVerifyException('need_verify', verify_url=ntf)
                
            raise MiCloudAccessDenied(f"Login to xiaomi error: {resp_data.get('text')}")
            
        self.user_id = str(auth.get('userId', ''))
        self.cuser_id = auth.get('cUserId')
        self.ssecurity = auth.get('ssecurity')
        self.pass_token = auth.get('passToken')
        
        if req_sid != 'xiaomiio':
            sign = f'nonce={auth.get("nonce")}&{auth.get("ssecurity")}'
            sign = base64.b64encode(hashlib.sha1(sign.encode()).digest()).decode()
            location += '&clientSign=' + parse.quote(sign)
            
        log.info(f"Step 2 finished, generated location: {location}")
        return location

    async def _login_step3(self, location):
        headers = {'content-type': 'application/x-www-form-urlencoded'}
        log.info(f"Executing Step 3 with location: {location}")
        resp_data = await self._account_get(location, headers=headers, return_response=True)
        
        cookies = resp_data.get('cookies', {})
        service_token = cookies.get('serviceToken')
        
        if service_token:
            self.service_token = service_token
            self.user_id = str(cookies.get('userId', self.user_id))
            self.cuser_id = cookies.get('cUserId', self.cuser_id)
        else:
            log.error(f"Login step 3 failed, no serviceToken. Response status: {resp_data.get('status_code')}, text: {resp_data.get('text')}, cookies: {cookies}")
            
        return resp_data

    async def check_identity_list(self, url, path='fe/service/identity/authStart'):
        if path not in url:
            return None
            
        list_url = url.replace(path, 'identity/list')
        resp_data = await self._account_get(list_url, return_response=True)
        
        cookies = resp_data.get('cookies', {})
        identity_session = cookies.get('identity_session')
        if not identity_session:
            log.warning("获取身份列表失败：未获取到 identity_session cookie")
            return None
            
        self.attrs['identity_session'] = identity_session
        
        try:
            clean_text = resp_data['text'].replace('&&&START&&&', '')
            data = json.loads(clean_text)
        except Exception:
            data = {}
            
        flag = data.get('flag', 4)
        options = data.get('options', [flag])
        log.info(f"成功获取 identity_session, 允许的验证方式选项: {options}")
        return options

    async def verify_ticket(self, ticket):
        url = self.attrs.get('verify_url')
        if not url:
            return {}
            
        options = await self.check_identity_list(url)
        if not options:
            log.warning("无法获取验证选项(options)，验证可能失败")
            return {}
            
        for flag in options:
            api = {
                4: '/identity/auth/verifyPhone',
                8: '/identity/auth/verifyEmail',
            }.get(flag)
            if not api:
                continue
                
            data = await self._account_post(
                api,
                params={'_dc': int(time.time() * 1000)},
                data={
                    '_flag': str(flag),
                    'ticket': ticket,
                    'trust': 'true',
                    '_json': 'true',
                },
                cookies={'identity_session': self.attrs.get('identity_session', '')}
            )
            log.info(f"提交验证码(Ticket)到 {api}: 响应结果: {data}")
            if data.get('code') == 0:
                self.attrs.pop('identity_session', None)
                return data
                
        log.warning(f"验证码提交失败，所有途径({options})均未成功通过校验。")
        return {}
