# Copyright 2011 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Manages information about the qemu guest agent.

This class encapsulates libvirt qemu provides certain
higher level APIs around the raw libvirt qemu API.
These APIs are then used by all the other libvirt
related classes
"""

import base64
import os

from oslo_serialization import jsonutils
from oslo_utils import importutils

from nova import exception
from nova.i18n import _

libvirt = None
libvirt_qemu = None

COMMAND = '{"execute": "%(cmd)s", "arguments": %(arg)s}'
FILE_READ_COUNT = 102400


def to_dict(s):
    return jsonutils.loads(s)

def strip(s):
    return to_dict(s)["return"]


class QemuGuestAgent(object):

    def __init__(self):
        global libvirt
        if libvirt is None:
            libvirt = importutils.import_module('libvirt')

        global libvirt_qemu
        if libvirt_qemu is None:
            libvirt_qemu = importutils.import_module('libvirt_qemu')

    def qemu_agent_command(self, domain, cmd, timeout=1000, flags=0):
        return libvirt_qemu.qemuAgentCommand(domain, cmd, timeout, flags)

    def guest_file_read(self, domain, path):
        # open file
        cmd = self._guest_file_open_command(path, "r")
        ret = self.qemu_agent_command(domain, cmd)
        handle = strip(ret)

        try:
            # read file
            cmd = self._guest_file_read_command(handle, FILE_READ_COUNT)
            ret = self.qemu_agent_command(domain, cmd)
            ret = strip(ret)
            eof = ret["eof"]
            content = base64.b64decode(ret["buf-b64"])
            while not eof:
                cmd = self._guest_file_read_command(handle, FILE_READ_COUNT)
                ret = self.qemu_agent_command(domain, cmd)
                ret = strip(ret)
                eof = ret["eof"]
                content += base64.b64decode(ret["buf-b64"])
        finally:
            # close file
            cmd = self._guest_file_close_command(handle)
            self.qemu_agent_command(domain, cmd)

        return content

    def _guest_file_open_command(self, path, mode):
        arg = '{"path": "%s", "mode": "%s"}' % (path, mode)
        return COMMAND % {"cmd": "guest-file-open",
                          "arg": arg}

    def _guest_file_close_command(self, handle):
        arg = '{"handle": %d}' % handle
        return COMMAND % {"cmd": "guest-file-close",
                          "arg": arg}

    def _guest_file_read_command(self, handle, count=4096):
        arg = '{"handle": %d, "count": %d}' % (handle, count)
        return COMMAND % {"cmd": "guest-file-read",
                          "arg": arg}
        
    def memory_usage(self, domain):
        memory_states = self.guest_file_read(domain, '/proc/meminfo')
        memory = {}
        for memory_state in memory_states.split('\n'):
            if not memory_state:
                continue
            memory_info = memory_state.split(':')
            memory[memory_info[0]] = memory_info[1][:-3]
        memory_usage = int(memory['MemTotal']) - int(memory['MemFree']) \
            - int(memory['Buffers']) - int(memory['Cached'])
        return memory_usage
