import copy
import time
from urllib.parse import urlparse
from keycloak import KeycloakOpenID, KeycloakAdmin
from keycloak.exceptions import KeycloakError
from console.libs.utils import spawn, logger
from console.config import (
    SSO_HOST, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWD,
    SSO_REALM, FAKE_USER,
)

REFRESH_INTERVAL = 300


class AdminWrapper(object):
    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs
        self.keycloak_admin = KeycloakAdmin(*args, **kwargs)

    def __getattr__(self, item):
        obj = getattr(self.keycloak_admin, item)
        if not callable(obj):
            return obj

        def wrapper(*args, **kwargs):
            nonlocal obj
            try:
                return obj(*args, **kwargs)
            except KeycloakError:
                # retry
                logger.debug("got keycloak error, retry.")
                self.keycloak_admin = KeycloakAdmin(*self._args, **self._kwargs)
                obj = getattr(self.keycloak_admin, item)
                return obj(*args, **kwargs)
        return wrapper


class SSO(object):
    _INSTANCE = None

    def __init__(self, host_or_url, admin_user, admin_passwd, realm="kae", admin_realm="master"):
        o = urlparse(host_or_url)
        if not o.scheme:
            # url is a host
            url = f'https://{host_or_url}/auth/'
        else:
            url = host_or_url    
        self._admin = AdminWrapper(server_url=url,
                                   username=admin_user,
                                   password=admin_passwd,
                                   user_realm_name=admin_realm,
                                   realm_name=realm,
                                   verify=True,
                                   auto_refresh_token=['get', 'put', 'post', 'delete'])
        self.refresh()

    @classmethod
    def instance(cls, host=SSO_HOST, admin_user=KEYCLOAK_ADMIN_USER, admin_passwd=KEYCLOAK_ADMIN_PASSWD, realm=SSO_REALM):
        if cls._INSTANCE is None:
            cls._INSTANCE = SSO(host, admin_user, admin_passwd, realm)
        return cls._INSTANCE

    def get_groups(self):
        self._refresh_if_needed()
        return self.group_map.values()

    def _refresh_if_needed(self):
        if time.time() - self._last_refresh_ts > REFRESH_INTERVAL:
            self.refresh()

    def _fetch_all_user_group(self):
        self.group_map = {}
        self.user_map = {}

        def handle_group(grp):
            sub_groups = grp.pop('subGroups', None)
            self.group_map[grp['id']] = grp
            members = self._admin.get_group_members(grp['id'])
            for mem in members:
                username = mem['username']
                try:
                    exist_user = self.user_map[username]
                except KeyError:
                    mem['group_ids'] = []
                    exist_user = mem
                exist_user['group_ids'].append(grp['id'])
                self.user_map[username] = exist_user

            if sub_groups is not None:
                for grp in sub_groups:
                    handle_group(grp)

        users = self._admin.get_users()
        for u in users:
            u['group_ids'] = []
            self.user_map[u['username']] = u
        groups = self._admin.get_groups()
        if groups is not None:
            for grp in groups:
                handle_group(grp)
        # print(self.user_map)
        # print("++++++++++++++++")
        # print(self.group_map)

    def get_group(self, group_id):
        self._refresh_if_needed()
        return self.group_map.get(group_id)

    def get_group_by_name(self, name):
        self._refresh_if_needed()
        for grp in self.group_map.values():
            if grp['name'] == name:
                return grp

    def get_groups_by_user(self, username):
        self._refresh_if_needed()
        user = self.user_map.get(username)
        if user is None:
            return None
        group_ids = user['group_ids']
        return [self.group_map[g_id] for g_id in group_ids]

    def get_user(self, username):
        self._refresh_if_needed()
        return self.user_map.get(username)

    def get_users(self):
        self._refresh_if_needed()
        return self.user_map.values()

    def refresh(self):
        self._fetch_all_user_group()
        self._last_refresh_ts = time.time()


class SSOMocker(object):
    _INSTANCE = None

    def __init__(self, host_or_url, admin_user, admin_passwd, realm="kae", admin_realm="master"):
        self._fetch_all_user_group()

    @classmethod
    def instance(cls, host=SSO_HOST, admin_user=KEYCLOAK_ADMIN_USER, admin_passwd=KEYCLOAK_ADMIN_PASSWD, realm=SSO_REALM):
        if cls._INSTANCE is None:
            cls._INSTANCE = SSO(host, admin_user, admin_passwd, realm)
        return cls._INSTANCE

    def get_groups(self):
        return self.group_map.values()

    def _fetch_all_user_group(self):
        userinfo = copy.deepcopy(FAKE_USER)
        self.group_map = {}
        self.user_map = {
            FAKE_USER["username"]: FAKE_USER,
        }

    def get_group(self, group_id):
        return self.group_map.get(group_id)

    def get_group_by_name(self, name):
        for grp in self.group_map.values():
            if grp['name'] == name:
                return grp

    def get_groups_by_user(self, username):
        user = self.user_map.get(username)
        if user is None:
            return None
        group_ids = user['group_ids']
        return [self.group_map[g_id] for g_id in group_ids]

    def get_user(self, username):
        return self.user_map.get(username)

    def get_users(self):
        return self.user_map.values()

    def refresh(self):
        self._fetch_all_user_group()

