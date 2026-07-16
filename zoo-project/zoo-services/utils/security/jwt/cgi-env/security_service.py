# -*- coding: utf-8 -*-
#
# Author : Gérald FENOY
#
# Copyright 2023-2026 GeoLabs SARL. All rights reserved.
# 
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including with
# out limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

import zoo
import jwt
import sys
import json
import os
import redis
import requests

def addHeader(conf,name):
    if "headers" not in conf:
        conf["headers"]={}
    key="Powered-By"
    if "X-"+key in conf["headers"]:
        while "X-"+key in conf["headers"]:
            key="Also-"+key
    conf["headers"]["X-"+key]=name

class JWTSecurityService:

    @staticmethod
    def _get_env(name: str, default: str | None = None, required: bool = False) -> str:
        value = os.environ.get(name, default)
        if required and not value:
            zoo.debug(f"Missing required environment variable: {name}")
            return name
        return value

    def __init__(self, conf):
        self.conf = conf
        self.KEYCLOAK_ISSUER = self._get_env("ZOO_KEYCLOAK_ISSUER", required=True)
        self.JWKS_URL = f"{self.KEYCLOAK_ISSUER.rstrip('/')}/protocol/openid-connect/certs"
        self.EXPECTED_AUDIENCE = self._get_env("ZOO_KEYCLOAK_AUDIENCE", required=True)
        configured_algorithms = self._get_env("ZOO_KEYCLOAK_ALGORITHMS", default="RS256")
        self.JWT_ALGORITHMS = [a.strip() for a in configured_algorithms.split(",") if a.strip()]
        if not self.JWT_ALGORITHMS:
            self.JWT_ALGORITHMS = ["RS256"]

        self.REDIS_HOST = self._get_env("ZOO_REDIS_HOST", default="redis")
        self.REDIS_PORT = int(self._get_env("ZOO_REDIS_PORT", default="6379"))
        self.REDIS_DB = int(self._get_env("ZOO_REDIS_DB", default="0"))

        self.CACHE_KEY = self._get_env("ZOO_KEYCLOAK_JWKS_CACHE_KEY", default="zoo:keycloak:jwks")
        self.CACHE_TTL = int(self._get_env("ZOO_KEYCLOAK_JWKS_CACHE_TTL", default="3600"))

        self._redis_client = None

    def get_redis_client(self) -> redis.Redis:
        if self._redis_client is None:
            self._redis_client = redis.Redis(
                host=self.REDIS_HOST,
                port=self.REDIS_PORT,
                db=self.REDIS_DB,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._redis_client

    def _fetch_jwks(self) -> dict:
        resp = requests.get(self.JWKS_URL, timeout=5)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            zoo.debug(f"Failed to fetch JWKS from {self.JWKS_URL}: {e}")
            return {}
        return resp.json()

    def get_jwks(self, force_refresh: bool = False) -> dict:
        r = self.get_redis_client()

        if not force_refresh:
            try:
                cached = r.get(self.CACHE_KEY)
                if cached:
                    return json.loads(cached)
            except redis.RedisError:
                zoo.debug("Redis unavailable, fetching JWKS directly")
                pass

        jwks = self._fetch_jwks()
        try:
            r.setex(self.CACHE_KEY, self.CACHE_TTL, json.dumps(jwks))
        except redis.RedisError:
            zoo.debug("Redis unavailable, unable to cache JWKS")
            pass

        return jwks

    def decode_token(self, cJWT: str) -> dict:
        unverified_header = jwt.get_unverified_header(cJWT)
        kid = unverified_header.get("kid")
        if kid is None:
            zoo.debug("No 'kid' found in JWT header")
            return None

        jwks = self.get_jwks()
        key_data = next((k for k in jwks["keys"] if k["kid"] == kid), None)

        if key_data is None:
            jwks = self.get_jwks(force_refresh=True)
            key_data = next((k for k in jwks["keys"] if k["kid"] == kid), None)
            if key_data is None:
                zoo.error(f"Key with kid={kid} not found in JWKS after refresh")
                return None

        signing_key = jwt.PyJWK(key_data)

        return jwt.decode(
            cJWT,
            signing_key.key,
            algorithms=self.JWT_ALGORITHMS,
            audience=self.EXPECTED_AUDIENCE,
            issuer=self.KEYCLOAK_ISSUER,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
            },
        )

def securityIn(main_conf,inputs,outputs):
    if "servicesNamespace" in main_conf and "debug" in main_conf["servicesNamespace"]:
        zoo.info("JWT securityIn!")
    addHeader(main_conf,"jwt.securityIn")
    hasAuth=False
    for i in main_conf["renv"].keys():
        if "HTTP_AUTHORIZATION" in i:
            zoo.info("HTTP Authorization header found")
            sToken=main_conf["renv"][i].split(' ')[1]
            if sToken.count(".")>=2:
                zoo.info("JWT token found") 
                cJWT=main_conf["renv"][i].split(' ')[1]
                if "osecurity" in main_conf and "realm" in main_conf["osecurity"]:
                    if main_conf["renv"][i].count("oidc/"+main_conf["osecurity"]["realm"]+"/")>0:
                        cJWT=cJWT.replace("oidc/"+main_conf["osecurity"]["realm"]+"/","")
                try:
                    if os.getenv("ZOO_INSECURE_JWT", "false").lower() == "true":
                        jsonObj=jwt.decode(cJWT, options={"verify_signature": False,"verify_aud": False})
                    else:
                        jsonDecoder = JWTSecurityService(main_conf)
                        jsonObj=jsonDecoder.decode_token(cJWT)
                        #jsonObj = decode_jwt(cJWT)
                except Exception as e:
                    zoo.error(f"JWT decoding error: {e}")
                    if "lenv" not in main_conf:
                        main_conf["lenv"] = {}
                    main_conf["lenv"]["message"]=zoo._("Invalid JWT token (jwt.securityIn).")
                    main_conf["lenv"]["code"]="Unauthorized"
                    main_conf["lenv"]["status"]="401 Unauthorized"
                    if "headers" in main_conf:
                        main_conf["headers"]["status"]="401 Unauthorized"
                    else:
                        main_conf["headers"]={"status":"401 Unauthorized"}
                    return zoo.SERVICE_FAILED
                if jsonObj is None:
                    if "lenv" not in main_conf:
                        main_conf["lenv"] = {}
                    main_conf["lenv"]["message"]=zoo._("Invalid JWT token (jwt.securityIn).")
                    main_conf["lenv"]["code"]="Unauthorized"
                    main_conf["lenv"]["status"]="401 Unauthorized"
                    if "headers" in main_conf:
                        main_conf["headers"]["status"]="401 Unauthorized"
                    else:
                        main_conf["headers"]={"status":"401 Unauthorized"}
                    return zoo.SERVICE_FAILED
                hasAuth=True
                myKeys=list(jsonObj.keys())
                for k in jsonObj.keys():
                    if k.count("username")>0 or k.count("user_name")>0:
                        if "osecurity" in main_conf and \
                           "allowed_users" in main_conf["osecurity"] and \
                           "preferred_username" in jsonObj and \
                           main_conf["osecurity"]["allowed_users"].split(",").count(jsonObj["preferred_username"])==0:
                            if "lenv" not in main_conf:
                                main_conf["lenv"] = {}
                            main_conf["lenv"]["message"]=zoo._("You are not authorized to perform the requested operation on the resource (jwt.securityIn).")
                            main_conf["lenv"]["code"]="Forbidden"
                            main_conf["lenv"]["status"]="403 Forbidden"
                            if "headers" in main_conf:
                                main_conf["headers"]["status"]="403 Forbidden"
                            else:
                                main_conf["headers"]={"status":"403 Forbidden"}
                        main_conf["auth_env"]={"user": jsonObj[k] }
                        break
                if "auth_env" not in main_conf:
                    main_conf["auth_env"] = {}
                if "email" in jsonObj.keys():
                    main_conf["auth_env"]["email"]=jsonObj["email"]
                for l in range(len(myKeys)):
                    main_conf["auth_env"][myKeys[l]]=str(jsonObj[myKeys[l]])
                main_conf["auth_env"]["jwt"]=cJWT
                if "lenv" not in main_conf:
                    main_conf["lenv"] = {}
                main_conf["lenv"]["json_user"]=json.dumps(jsonObj)
            else:
                if "osecurity" in main_conf and \
                   "userinfoUrl" not in main_conf["osecurity"]:
                    import base64
                    b64 = ""
                    if "client_id" in main_conf["osecurity"] and \
                       "client_secret" in main_conf["osecurity"]:
                        try:
                            b64=base64.b64encode((main_conf["osecurity"]["client_id"]+":"+main_conf["osecurity"]["client_secret"]).encode("ascii")).decode('ascii')
                        except Exception as e:
                            print(e,file=sys.stderr)
                            if "servicesNamespace" in main_conf and "debug" in main_conf["servicesNamespace"]:
                                print("b64 encoding error: " + str(e), file=sys.stderr)
                                print(traceback.format_exc())
                    headers = {"Authorization" : "Basic "+b64, "Acccept": "application/json","Content-Type": "application/x-www-form-urlencoded"}
                    data= {"token": sToken,"token_type_hint": "access_token"}
                    response=requests.post("https://www.authenix.eu/oauth/tokeninfo",data=data,headers=headers)
                    userObject=json.loads(response.text)
                    if userObject["active"] and "sub" in userObject:
                        if "auth_env" not in main_conf:
                            main_conf["auth_env"] = {}
                        main_conf["auth_env"]={"user": userObject["sub"]}
                        hasAuth=True
                else:
                    if "osecurity" in main_conf and "userinfoUrl" in main_conf["osecurity"]:
                        headers = {"Authorization" : "Bearer "+sToken, "Acccept": "application/json"}
                        response=requests.get(main_conf["osecurity"]["userinfoUrl"],headers=headers)
                        userObject=json.loads(response.text)
                        if "auth_env" not in main_conf:
                            main_conf["auth_env"] = {}
                        main_conf["auth_env"]={"jwt": sToken}
                        for a in userObject.keys():
                            main_conf["auth_env"][a]=userObject[a]
                        if "user_name" not in main_conf["auth_env"]:
                            if "sub" in userObject:
                                main_conf["auth_env"]["user"]=userObject["sub"]
                                hasAuth=True
                        else:
                            main_conf["auth_env"]["user"]=main_conf["auth_env"]["user_name"]
                            hasAuth=True
            break
    if "auth_env" in main_conf and "user" in main_conf["auth_env"]:
        # Ensure no other SERVICES_NAMESPACE variable is set
        for a in main_conf["auth_env"].keys():
            if a.count("SERVICES_NAMESPACE")>0:
                main_conf["renv"][a]=main_conf["auth_env"]["user"]
        main_conf["renv"]["SERVICES_NAMESPACE"]=main_conf["auth_env"]["user"]
    if hasAuth or \
       ("lenv" in main_conf and "secured_url" in main_conf["lenv"] and main_conf["lenv"]["secured_url"]=="false"):
        return zoo.SERVICE_SUCCEEDED
    else:
        status_code="401 Unauthorized"
        message="Authentication is required to perform the requested operation on the resource."
        for a in main_conf["renv"].keys():
            if a.count("HTTP_AUTHORIZATION")>0:
                status_code="403 Forbidden"
                message="You are not authorized to perform the requested operation on the resource."
                break
        if "headers" in main_conf:
            main_conf["headers"]["status"]=status_code
        else:
            main_conf["headers"]={"status":status_code}
        if "lenv" in main_conf:
            main_conf["lenv"]["code"]="NotAllowed"
            main_conf["lenv"]["message"]=message
        return zoo.SERVICE_FAILED
