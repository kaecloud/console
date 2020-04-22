from urllib.parse import urlparse
from keycloak import KeycloakOpenID, KeycloakAdmin

class SSO(object):
    def __init__(self, host_or_url, admin_user, admin_passwd, realm="kae", admin_realm="master"):
        o = urlparse(url)
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
        self._fetch_all_user_group

    def get_groups(self):
        return self._admin.get_groups()

    def _fetch_all_user_group(self):
        self.group_map = {}
        self.user_map = {}

        groups = self._admin.get_groups()
        for grp in groups:
            self.group_map[grp['id'] = grp
            memebers = self._admin.get_group_members(grp['id'])
            for mem in memebers:
                mem['group_id'] = grp['id']
                self.user_map[mem['username']] = mem

    def get_group(self, group_id):
        return self.group_map.get(group_id)

    def get_group_by_user(self, username):
        user = self.user_map.get(username)
        if user is None:
            return None 
        return self.group_map.get(user['group_id'])

    def get_user(self, username):
        return self.user_map.get(username)
    
    def refresh(self):
        self._fetch_all_user_group()

