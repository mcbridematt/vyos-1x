#!/usr/bin/env python3
#
# Copyright (C) 2021 VyOS maintainers and contributors
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

import os

from sys import exit

from vyos.config import Config
from vyos.configdict import dict_merge
from vyos.configverify import verify_route_maps
from vyos.util import call
from vyos.util import dict_search
from vyos.xml import defaults
from vyos.template import render_to_string
from vyos import ConfigError
from vyos import frr
from vyos import airbag
airbag.enable()

frr_daemon = 'ripd'

def get_config(config=None):
    if config:
        conf = config
    else:
        conf = Config()
    base = ['protocols', 'rip']
    rip = conf.get_config_dict(base, key_mangling=('-', '_'), get_first_key=True)

    # Bail out early if configuration tree does not exist
    if not conf.exists(base):
        return rip

    # We have gathered the dict representation of the CLI, but there are default
    # options which we need to update into the dictionary retrived.
    default_values = defaults(base)
    # merge in remaining default values
    rip = dict_merge(default_values, rip)

    # We also need some additional information from the config, prefix-lists
    # and route-maps for instance. They will be used in verify()
    base = ['policy']
    tmp = conf.get_config_dict(base, key_mangling=('-', '_'))
    # Merge policy dict into OSPF dict
    rip = dict_merge(tmp, rip)

    return rip

def verify(rip):
    if not rip:
        return None

    acl_in = dict_search('distribute_list.access_list.in', rip)
    if acl_in and acl_in not in  (dict_search('policy.access_list', rip) or []):
        raise ConfigError(f'Inbound ACL "{acl_in}" does not exist!')

    acl_out = dict_search('distribute_list.access_list.out', rip)
    if acl_out and acl_out not in (dict_search('policy.access_list', rip) or []):
        raise ConfigError(f'Outbound ACL "{acl_out}" does not exist!')

    prefix_list_in = dict_search('distribute_list.prefix_list.in', rip)
    if prefix_list_in and prefix_list_in.replace('-','_') not in (dict_search('policy.prefix_list', rip) or []):
        raise ConfigError(f'Inbound prefix-list "{prefix_list_in}" does not exist!')

    prefix_list_out = dict_search('distribute_list.prefix_list.out', rip)
    if prefix_list_out and prefix_list_out.replace('-','_') not in (dict_search('policy.prefix_list', rip) or []):
        raise ConfigError(f'Outbound prefix-list "{prefix_list_out}" does not exist!')

    if 'interface' in rip:
        for interface, interface_options in rip['interface'].items():
            if 'authentication' in interface_options:
                if {'md5', 'plaintext_password'} <= set(interface_options['authentication']):
                    raise ConfigError('Can not use both md5 and plaintext-password at the same time!')
            if 'split_horizon' in interface_options:
                if {'disable', 'poison_reverse'} <= set(interface_options['split_horizon']):
                    raise ConfigError(f'You can not have "split-horizon poison-reverse" enabled ' \
                                      f'with "split-horizon disable" for "{interface}"!')

    verify_route_maps(rip)

def generate(rip):
    if not rip:
        rip['new_frr_config'] = ''
        return None

    rip['new_frr_config'] = render_to_string('frr/rip.frr.tmpl', rip)

    return None

def apply(rip):
    # Save original configuration prior to starting any commit actions
    frr_cfg = frr.FRRConfig()
    frr_cfg.load_configuration(frr_daemon)
    frr_cfg.modify_section(r'key chain \S+', '')
    frr_cfg.modify_section(r'interface \S+', '')
    frr_cfg.modify_section('router rip', '')
    frr_cfg.add_before(r'(ip prefix-list .*|route-map .*|line vty)', rip['new_frr_config'])
    frr_cfg.commit_configuration(frr_daemon)

    # If FRR config is blank, rerun the blank commit x times due to frr-reload
    # behavior/bug not properly clearing out on one commit.
    if rip['new_frr_config'] == '':
        for a in range(5):
            frr_cfg.commit_configuration(frr_daemon)

    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
