from urllib.parse import urlparse
from keycloak import KeycloakOpenID, KeycloakAdmin
from console.config import (
    SSO_HOST, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWD,
    SSO_REALM,
)


class SSO(object):
    _INSTANCE = None

    def __init__(self, host_or_url, admin_user, admin_passwd, realm="kae", admin_realm="master"):
        o = urlparse(host_or_url)
        if not o.scheme:
            # url is a host
            url = f'https://{host_or_url}/auth/'
        else:
            url = host_or_url    
        self._admin = KeycloakAdmin(server_url=url,
                                    username=admin_user,
                                    password=admin_passwd,
                                    user_realm_name=admin_realm,
                                    realm_name=realm,
                                    verify=True)
        self._fetch_all_user_group()

    @classmethod
    def instance(cls, host=SSO_HOST, admin_user=KEYCLOAK_ADMIN_USER, admin_passwd=KEYCLOAK_ADMIN_PASSWD, realm=SSO_REALM):
        if cls._INSTANCE is None:
            cls._INSTANCE = SSO(host, admin_user, admin_passwd, realm)
        return cls._INSTANCE

    def get_groups(self):
        return self._admin.get_groups()

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

        groups = self._admin.get_groups()
        for grp in groups:
            handle_group(grp)

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
    
    def refresh(self):
        self._fetch_all_user_group()


