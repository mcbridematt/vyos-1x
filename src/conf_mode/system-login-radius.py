#!/usr/bin/env python3
#
# Copyright (C) 2020 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
import os
import jinja2

from pwd import getpwall, getpwnam
from stat import S_IRUSR, S_IWUSR

from vyos.config import Config
from vyos.configdict import list_diff
from vyos import ConfigError

radius_config_file = "/etc/pam_radius_auth.conf"
radius_config_tmpl = """
# Automatically generated by VyOS
# RADIUS configuration file
{%- if server %}
# server[:port]         shared_secret                           timeout (s)     source_ip
{% for s in server %}
{%- if not s.disabled -%}
{{ s.address }}:{{ s.port }} {{ s.key }} {{ s.timeout }} {% if source_address -%}{{ source_address }}{% endif %}
{% endif %}
{%- endfor %}

priv-lvl 15
mapped_priv_user radius_priv_user
{% endif %}

"""

default_config_data = {
    'server': [],
    'source_address': '',
}

def get_local_users():
    """Returns list of dynamically allocated users (see Debian Policy Manual)"""
    local_users = []
    for p in getpwall():
        username = p[0]
        uid = getpwnam(username).pw_uid
        if uid in range(1000, 29999):
            if username not in ['radius_user', 'radius_priv_user']:
                local_users.append(username)

    return local_users

def get_config():
    radius = default_config_data
    conf = Config()
    base_level = ['system', 'login', 'radius']

    if not conf.exists(base_level):
        return radius

    conf.set_level(base_level)

    if conf.exists(['source-address']):
        radius['source_address'] = conf.return_value(['source-address'])

    # Read in all RADIUS servers and store to list
    for server in conf.list_nodes(['server']):
        server_cfg = {
            'address': server,
            'disabled': False,
            'key': '',
            'port': '1812',
            'timeout': '2'
        }
        conf.set_level(base_level + ['server', server])

        # Check if RADIUS server was temporary disabled
        if conf.exists(['disable']):
            server_cfg['disabled'] = True

        # RADIUS shared secret
        if conf.exists(['key']):
            server_cfg['key'] = conf.return_value(['key'])

        # RADIUS authentication port
        if conf.exists(['port']):
            server_cfg['port'] = conf.return_value(['port'])

        # RADIUS session timeout
        if conf.exists(['timeout']):
            server_cfg['timeout'] = conf.return_value(['timeout'])

        # Append individual RADIUS server configuration to global server list
        radius['server'].append(server_cfg)

    return radius

def verify(radius):
    # At lease one RADIUS server must not be disabled
    if len(radius['server']) > 0:
        fail = True
        for server in radius['server']:
            if not server['disabled']:
                fail = False
        if fail:
            raise ConfigError('At least one RADIUS server must be active.')

    return None

def generate(radius):
    if len(radius['server']) > 0:
        tmpl = jinja2.Template(radius_config_tmpl)
        config_text = tmpl.render(radius)
        with open(radius_config_file, 'w') as f:
            f.write(config_text)

        uid = getpwnam('root').pw_uid
        gid = getpwnam('root').pw_gid
        os.chown(radius_config_file, uid, gid)
        os.chmod(radius_config_file, S_IRUSR | S_IWUSR)
    else:
        os.unlink(radius_config_file)

    return None

def apply(radius):
    if len(radius['server']) > 0:
        try:
            # Enable RADIUS in PAM
            os.system("DEBIAN_FRONTEND=noninteractive pam-auth-update --package --enable radius")

            # Make NSS system aware of RADIUS, too
            cmd = "sed -i -e \'/\smapname/b\' \
                          -e \'/^passwd:/s/\s\s*/&mapuid /\' \
                          -e \'/^passwd:.*#/s/#.*/mapname &/\' \
                          -e \'/^passwd:[^#]*$/s/$/ mapname &/\' \
                          -e \'/^group:.*#/s/#.*/ mapname &/\' \
                          -e \'/^group:[^#]*$/s/: */&mapname /\' \
                          /etc/nsswitch.conf"

            os.system(cmd)

        except Exception as e:
            raise ConfigError('RADIUS configuration failed: {}'.format(e))

    else:
        try:
            # Disable RADIUS in PAM
            os.system("DEBIAN_FRONTEND=noninteractive pam-auth-update --package --remove radius")

            cmd = "sed -i -e \'/^passwd:.*mapuid[ \t]/s/mapuid[ \t]//\' \
                   -e \'/^passwd:.*[ \t]mapname/s/[ \t]mapname//\' \
                   -e \'/^group:.*[ \t]mapname/s/[ \t]mapname//\' \
                   -e \'s/[ \t]*$//\' \
                   /etc/nsswitch.conf"

            os.system(cmd)

        except Exception as e:
            raise ConfigError('Removing RADIUS configuration failed'.format(e))

    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        sys.exit(1)
