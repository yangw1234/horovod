# Copyright 2020 Uber Technologies, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import

import logging
import threading

from collections import defaultdict

import six

from horovod.run.common.util import safe_shell_exec


class HostState(object):
    def __init__(self):
        self._event = threading.Event()

        # TODO(travis): blacklisted hosts should have a timeout period that increases with each failure
        self._blacklisted = False

    def get_event(self):
        if self._event.is_set():
            event = threading.Event()
            self._event = event
        return self._event

    def set_event(self):
        self._event.set()

    def blacklist(self):
        self._blacklisted = True
        self.set_event()

    def is_blacklisted(self):
        return self._blacklisted


class DiscoveredHosts(object):
    def __init__(self, discovery):
        self._available_hosts = set()
        self._available_slots = {}
        self._host_state = defaultdict(HostState)
        self._discovery = discovery

    def update_available_hosts(self):
        prev_hosts = self._available_hosts
        prev_slots = self._available_slots
        available_hosts, available_slots = self._discovery.find_available_hosts_and_slots()
        if prev_hosts != available_hosts or prev_slots != available_slots:
            self._available_hosts, self._available_slots = available_hosts, available_slots
            return True
        return False

    def count_available_slots(self):
        return sum([self.get_slots(host) for host in self._available_hosts
                    if not self._host_state[host].is_blacklisted()])

    def get_slots(self, host):
        return self._available_slots[host]

    def filter_available_hosts(self, hosts):
        return [host for host in hosts
                if host in self._available_hosts and not self._host_state[host].is_blacklisted()]

    def get_available_hosts(self):
        return self.filter_available_hosts(self._available_hosts)

    def blacklist(self, host):
        if not self._host_state[host].is_blacklisted():
            logging.warning('blacklist failing host: {}'.format(host))
        self._host_state[host].blacklist()

    def is_blacklisted(self, host):
        return self._host_state[host].is_blacklisted()

    def count_blacklisted_slots(self):
        return sum([self.get_slots(host) for host, meta in self._host_state.items() if meta.is_blacklisted()])

    def get_host_event(self, host):
        return self._host_state[host].get_event()


class HostDiscovery(object):
    def find_available_hosts_and_slots(self):
        raise NotImplementedError()


class HostDiscoveryScript(HostDiscovery):
    def __init__(self, discovery_script, slots):
        self._discovery_script = discovery_script
        self._default_slots = slots
        super(HostDiscoveryScript, self).__init__()

    def find_available_hosts_and_slots(self):
        stdout = six.StringIO()
        exit_code = safe_shell_exec.execute(self._discovery_script, stdout=stdout)
        if exit_code != 0:
            raise RuntimeError('Failed to execute discovery script: {}. Exit code: {}'
                               .format(self._discovery_script, exit_code))

        availabe_hosts = set()
        available_slots = {}
        hosts_and_slots = set(stdout.getvalue().strip().split('\n'))
        for line in hosts_and_slots:
            host = line
            if ':' in line:
                host, slots = line.split(':')
                available_slots[host] = int(slots)
            else:
                available_slots[host] = self._default_slots
            availabe_hosts.add(host)
        return availabe_hosts, available_slots


class FixedHosts(HostDiscovery):
    def __init__(self, available_hosts, available_slots):
        super(FixedHosts, self).__init__()
        self.set(available_hosts, available_slots)

    def find_available_hosts_and_slots(self):
        return self._available_hosts, self._available_slots

    def set(self, available_hosts, available_slots):
        self._available_hosts = available_hosts
        self._available_slots = available_slots