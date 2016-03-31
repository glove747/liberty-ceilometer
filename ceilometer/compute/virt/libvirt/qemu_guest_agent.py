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
import time

from oslo_serialization import jsonutils
from oslo_utils import importutils

from nova import exception
from nova.i18n import _
from oslo_log import log as logging

libvirt = None
libvirt_qemu = None

LOG = logging.getLogger(__name__)

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

    def _guest_exec_command(self, cmd, parms):
        arg = '{"path": "%s", "capture-output": true' % (cmd)
        # parms type is listG eg. '"arg":["-h"]'
        if parms:
            arg = arg + parms
        arg = arg + '}'
        return COMMAND % {"cmd": "guest-exec",
                          "arg": arg}

    def _guest_exec_status_command(self, pid):
        arg = '{"pid": %d}' % (pid)
        return COMMAND % {"cmd": "guest-exec-status",
                          "arg": arg}

    def memory_usage(self, domain):
        memory_usage = 0
        try:
            memory_states = self.guest_file_read(domain, "/proc/meminfo")
            memory = {}
            for memory_state in memory_states.split('\n'):
                if not memory_state:
                    continue
                memory_infos = memory_state.split(':')
                memory[memory_infos[0]] = memory_infos[1][:-3]
            memory_usage = int(memory["MemTotal"]) - int(memory["MemFree"]) - \
                int(memory["Buffers"]) - int(memory["Cached"])
        except Exception as ex:
             LOG.warn(_("qemu-gust-agent collect memory failed: %s"), ex)
        return memory_usage

    def guest_exec_command(self, domain, guest_command, parms=None):
        result = {}
        cmd = self._guest_exec_command(guest_command, parms)
        ret = self.qemu_agent_command(domain, cmd)
        ret = strip(ret)
        pid = ret['pid']
        cmd = self._guest_exec_status_command(pid)
        t = 0
        while True:
            result = self.qemu_agent_command(domain, cmd)
            result = strip(result)
            if t == 5:
                return err("execute command timeout")
            if result['exited']:
                break
            time.sleep(1)
            t += 1
        return result

    def disk_info(self, domain, device):
        disk_infos = {}
        disk_infos['total_size'] = 0
        disk_infos['used_size'] = 0
        try:
            result = self.guest_exec_command(domain, "df")
            if result['exitcode'] == 1:
                disk_infos['exitcode'] = 1
                disk_infos['err-data'] = result['err-data']
                return disk_infos

            outdata = base64.b64decode(result['out-data'])
            for line in outdata.split('\n'):
                if line.startswith('/dev/' + device):
                    disk_infos['total_size'] += int(line.split()[1])
                    disk_infos['used_size'] += int(line.split()[2])
        except Exception as ex:
             LOG.warn(_("qemu-gust-agent exec guest-exec failed: %s"), ex)
             return disk_infos
        return disk_infos

