import aiohttp
import datetime
import uuid
from useragent_changer import UserAgent

ua =UserAgent('iphone')

PROXY_URL = ""

# --- send login request ---
async def login(phoneNumber: str, password: str, uuid: str):
    headers = {
        'User-Agent': ua.set(),
        'Accept' : 'application/json, text/plain, */*',
        'Content-Type' : 'application/json',
        'Origin': 'https://www.paypay.ne.jp',
        'Referer':'https://www.paypay.ne.jp/app/account/sign-in',
    }
    payload = {
        "scope":"SIGN_IN",
        "client_uuid":f"{uuid}",
        "grant_type":"password",
        "username":phoneNumber,
        "password":password,
        "add_otp_prefix": True,
        "language":"ja"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://www.paypay.ne.jp/app/v1/oauth/token", headers=headers, json=payload, proxy=PROXY_URL) as login_request_response:
            return await login_request_response.json()

# --- one-time-password authentication ---
async def login_otp_raw(set_uuid, otp, otpid, otp_pre):
    otp_number = otp
    headers = {
        'User-Agent': ua.set(),
        'Accept' : 'application/json, text/plain, */*',
        'Content-Type' : 'application/json',
        'Origin': 'https://www.paypay.ne.jp',
        'Referer':'https://www.paypay.ne.jp/app/account/sign-in',
    }
    payload = {
            "scope":"SIGN_IN",
            "client_uuid":f"{set_uuid}",
            "grant_type":"otp",
            "otp_prefix": str(otp_pre),
            "otp":otp_number,
            "otp_reference_id":otpid,
            "username_type":"MOBILE",
            "language":"ja"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://www.paypay.ne.jp/app/v1/oauth/token", headers=headers, json=payload, proxy=PROXY_URL) as response:
            return await response.json()


async def login_otp(set_uuid, otp, otpid, otp_pre):
    login_response = await login_otp_raw(set_uuid, otp, otpid, otp_pre)
    if login_response.get("response_type") == "ErrorResponse":
        return "ERR"
    return "OK"


async def refresh_access_token(refresh_token: str, uuid: str):
    headers = {
        'User-Agent': ua.set(),
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://www.paypay.ne.jp',
        'Referer': 'https://www.paypay.ne.jp/app/account/sign-in',
    }
    payload = {
        "client_uuid": f"{uuid}",
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "language": "ja"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://www.paypay.ne.jp/app/v1/oauth/token", headers=headers, json=payload, proxy=PROXY_URL) as response:
            return await response.json()


def _build_app_headers(access_token: str, uuid_value: str):
    return {
        "Accept": "*/*",
        "Accept-Charset": "UTF-8",
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {access_token}",
        "Client-Mode": "NORMAL",
        "Client-OS-Release-Version": "10",
        "Client-OS-Type": "ANDROID",
        "Client-OS-Version": "29.0.0",
        "Client-Type": "PAYPAYAPP",
        "Client-UUID": str(uuid_value),
        "Client-Version": "5.11.1",
        "Connection": "Keep-Alive",
        "Content-Type": "application/json",
        "Device-Brand-Name": "KDDI",
        "Device-Hardware-Name": "qcom",
        "Device-In-Call": "false",
        "Device-Lock-App-Setting": "false",
        "Device-Lock-Type": "NONE",
        "Device-Manufacturer-Name": "samsung",
        "Device-Name": "SCV38",
        "Device-UUID": str(uuid_value),
        "Host": "app4.paypay.ne.jp",
        "Is-Emulator": "false",
        "Network-Status": "WIFI",
        "System-Locale": "ja",
        "Timezone": "Asia/Tokyo",
        "User-Agent": "PaypayApp/5.11.1 Android10",
    }


async def _app_request(method: str, url: str, access_token: str, uuid_value: str, *, params=None, json_data=None):
    headers = _build_app_headers(access_token, uuid_value)
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
            proxy=PROXY_URL
        ) as response:
            try:
                return await response.json()
            except Exception:
                return {
                    "header": {
                        "resultCode": "HTTP_ERROR",
                        "resultMessage": await response.text()
                    }
                }


async def get_balance(access_token: str, uuid_value: str):
    params = {
        "includePendingBonusLite": "false",
        "includePending": "true",
        "noCache": "true",
        "includeKycInfo": "true",
        "includePayPaySecuritiesInfo": "true",
        "includePointInvestmentInfo": "true",
        "includePayPayBankInfo": "true",
        "includeGiftVoucherInfo": "true",
        "payPayLang": "ja"
    }
    return await _app_request(
        "GET",
        "https://app4.paypay.ne.jp/bff/v1/getBalanceInfo",
        access_token,
        uuid_value,
        params=params
    )


async def get_history(access_token: str, uuid_value: str, limit: int = 20):
    params = {
        "pageSize": str(limit),
        "orderTypes": "",
        "paymentMethodTypes": "",
        "signUpCompletedAt": "2021-01-02T10:16:24Z",
        "isOverdraftOnly": "false",
        "payPayLang": "ja"
    }
    result = await _app_request(
        "GET",
        "https://app4.paypay.ne.jp/bff/v3/getPaymentHistory",
        access_token,
        uuid_value,
        params=params
    )
    payload = result.get("payload", {})
    if "histories" not in payload:
        histories = (
            payload.get("paymentHistory")
            or payload.get("paymentHistories")
            or payload.get("transactions")
            or payload.get("orders")
            or []
        )
        payload["histories"] = histories
        result["payload"] = payload
    return result


async def get_profile(access_token: str, uuid_value: str):
    params = {
        "includeExternalProfileSync": "true",
        "completedOptionalTasks": "ENABLED_NEARBY_DEALS",
        "payPayLang": "ja"
    }
    result = await _app_request(
        "GET",
        "https://app4.paypay.ne.jp/bff/v2/getProfileDisplayInfo",
        access_token,
        uuid_value,
        params=params
    )
    payload = result.get("payload", {})
    user_profile = payload.get("userProfile", {})
    if user_profile:
        result["payload"] = {
            "displayName": user_profile.get("nickName"),
            "maskedPhoneNumber": user_profile.get("maskedPhoneNumber"),
            "externalUserId": user_profile.get("externalUserId"),
            "iconImageUrl": user_profile.get("avatarImageUrl"),
            **payload
        }
    return result


async def create_mycode(access_token: str, uuid_value: str, amount: int | None = None):
    payload = {
        "amount": None,
        "sessionId": None
    }
    if amount:
        payload["amount"] = amount
        payload["sessionId"] = str(uuid.uuid4())
    result = await _app_request(
        "POST",
        "https://app4.paypay.ne.jp/bff/v1/createP2PCode",
        access_token,
        uuid_value,
        json_data=payload
    )
    p2pcode = result.get("payload", {}).get("p2pCode")
    if p2pcode:
        result["payload"] = {
            **result.get("payload", {}),
            "deeplink": p2pcode,
            "codeUrl": p2pcode,
            "link": p2pcode,
        }
    return result


async def create_link(access_token: str, uuid_value: str, amount: int, passcode: str | None = None):
    payload = {
        "requestId": str(uuid.uuid4()),
        "amount": amount,
        "socketConnection": "P2P",
        "theme": "default-sendmoney",
        "source": "sendmoney_home_sns"
    }
    if passcode:
        payload["passcode"] = passcode
    return await _app_request(
        "POST",
        "https://app4.paypay.ne.jp/bff/v2/executeP2PSendMoneyLink",
        access_token,
        uuid_value,
        json_data=payload
    )


async def accept_link(access_token: str, uuid_value: str, cd: str, link_password: str = None):
    if "https://" in cd:
        cd = cd.replace("https://pay.paypay.ne.jp/", "")

    link_info = await _app_request(
        "GET",
        "https://app4.paypay.ne.jp/bff/v2/getP2PLinkInfo",
        access_token,
        uuid_value,
        params={
            "verificationCode": cd,
            "payPayLang": "ja"
        }
    )
    if link_info.get("header", {}).get("resultCode") != "S0000":
        return link_info

    payload_info = link_info.get("payload", {})
    pending = payload_info.get("pendingP2PInfo", {})
    message = payload_info.get("message", {})
    request_payload = {
        "requestId": str(uuid.uuid4()),
        "orderId": pending.get("orderId"),
        "verificationCode": cd,
        "passcode": None,
        "senderMessageId": message.get("messageId"),
        "senderChannelUrl": message.get("chatRoomId"),
    }
    if pending.get("isSetPasscode"):
        request_payload["passcode"] = link_password

    return await _app_request(
        "POST",
        "https://app4.paypay.ne.jp/bff/v2/acceptP2PSendMoneyLink",
        access_token,
        uuid_value,
        params={
            "payPayLang": "ja",
            "appContext": "P2PMoneyTransferDetailScreen_linkReceiver"
        },
        json_data=request_payload
    )

async def check_link(cd):
    if "https://" in cd:
        cd=cd.replace("https://pay.paypay.ne.jp/","")

    headers={
        "Accept":"application/json, text/plain, */*",
        'User-Agent': ua.set(),
        "Content-Type":"application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://www.paypay.ne.jp/app/v2/p2p-api/getP2PLinkInfo?verificationCode={cd}", headers=headers, proxy=PROXY_URL) as response:
                response.raise_for_status()
                link_info = await response.json()
            
        except aiohttp.ClientError as e:
            print(f"API_REQ_EXC: {e}") #debug :)
            return False
    
    result_code = link_info.get("header", {}).get("resultCode")
    if result_code != "S0000":
        # ãªã¶ã«ãã³ã¼ããS0000ä»¥å¤ã ã£ãå ´åã¯åºæ¬ä½ãã¨ã©ã¼èµ·ãã¦ã
        return False

    order_status = link_info.get("payload", {}).get("orderStatus")
    if order_status == "PENDING":
        # ååå¾ã¡ã ã£ããlink_infoãè¿ãããããªãã£ããåãåããã¦ãorã­ã£ã³ã»ã«ããã¦ãor...ããFalse
        return link_info
    else:
        return False
    
async def link_rev(cd: str, phoneNumber: str, password: str, uuid: str,link_password: str = None):
    if "https://" in cd:
        cd=cd.replace("https://pay.paypay.ne.jp/","")
        
    async with aiohttp.ClientSession() as session:
        base_headers = {
            "Accept": "application/json, text/plain, */*",
            'User-Agent': ua.set(),
            "Content-Type": "application/json"
        }
        
        try:
            async with session.get(f"https://www.paypay.ne.jp/app/v2/p2p-api/getP2PLinkInfo?verificationCode={cd}", headers=base_headers, proxy=PROXY_URL) as response:
                response.raise_for_status()
                link_info = await response.json()

            if link_info.get("payload", {}).get("orderStatus") != "PENDING":
                # ããã§ãååå¾ã¡ããã§ãã¯ãååå¾ã¡ãããªãã£ããå¼¾ã
                return False
            
            if link_info.get("payload", {}).get("pendingP2PInfo", {}).get("isSetPasscode") and link_password is None:
                return False

        except aiohttp.ClientError as e:
            print(f"LINK_REQ_EXC: {e}") #debug :)
            return False
        
        login_payload = {
            "scope":"SIGN_IN",
            "client_uuid":f"{uuid}",
            "grant_type":"password",
            "username":phoneNumber,
            "password":password,
            "add_otp_prefix": True,
            "language":"ja"
            }

        login_headers = {
            'User-Agent': ua.set(),
            'Accept' : 'application/json, text/plain, */*',
            'Content-Type' : 'application/json',
            'Origin': 'https://www.paypay.ne.jp',
            'Referer':'https://pay.paypay.ne.jp/'+cd,
        }

        async with session.post("https://www.paypay.ne.jp/app/v1/oauth/token", headers=login_headers, json=login_payload, proxy=PROXY_URL) as response:
            login_response = await response.json()
            try:
                login_response = (login_response["access_token"])
            except:
                try:
                    login_response["otp_reference_id"]
                    return "LOGINERR"
                except:
                    return "LOGINERR"
        
        receive_payload = {
            "verificationCode":cd,
            "client_uuid":uuid,
            "requestAt":str(datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S+0900')),
            "requestId":link_info["payload"]["message"]["data"]["requestId"],
            "orderId":link_info["payload"]["message"]["data"]["orderId"],
            "senderMessageId":link_info["payload"]["message"]["messageId"],
            "senderChannelUrl":link_info["payload"]["message"]["chatRoomId"],
            "iosMinimumVersion":"3.45.0",
            "androidMinimumVersion":"3.45.0"
            }
        
        if link_password:
            receive_payload["passcode"]=link_password

        try:
            async with session.post("https://www.paypay.ne.jp/app/v2/p2p-api/acceptP2PSendMoneyLink", json=receive_payload, headers=base_headers, proxy=PROXY_URL) as response:
                response.raise_for_status()
                receive_data = await response.json()

                if receive_data.get("header", {}).get("resultCode") == "S0000":
                    return True
                else:
                    return False

        except aiohttp.ClientError as e:
            print(f"REVERR: {e}") #debug :) 
            return False
    
