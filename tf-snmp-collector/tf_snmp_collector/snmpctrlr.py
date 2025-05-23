#
# Copyright (c) 2015 Juniper Networks, Inc. All rights reserved.
#
from gevent.lock import Semaphore
import os
import subprocess
import time
import gevent
import socket
from tempfile import mkdtemp
import pickle as pickle
from .snmpuve import SnmpUve
from libpartition.consistent_schdlr import ConsistentScheduler
from .device_config import DeviceConfig, DeviceDict
from .snmp_config_handler import SnmpConfigHandler
import configparser
import signal
import random
import hashlib
from .sandesh.snmp_collector_info.ttypes import SnmpCollectorInfo, \
    SnmpCollectorUVE
from gevent import signal_handler as gevent_signal

class MaxNinTtime(object):
    def __init__(self, n, t, default=0):
        self._n = n
        self._t = t
        self._default = default
        self._slots = [0] * self._n
        self._pointer = 0

    def add(self):
        rt = self._default
        t = time.time()
        diff = t - self._slots[self._pointer]
        if diff < self._t:
            rt = self._t - diff
        self._add(t)
        return rt

    def _add(self, t):
        self._slots[self._pointer] = t
        self._pointer += 1
        self._pointer %= self._n

    def ready4full_scan(self):
        t = time.time()
        diff = t - self._slots[self._pointer - 1]
        if diff >= self._t:
            self._add(t)
            return True
        return False

class Controller(object):
    def __init__(self, config):
        self._config = config
        self._config.random_collectors = self._config.collectors()
        self._chksum = ""
        if self._config.collectors():
             self._chksum = hashlib.md5("".join(self._config.collectors()).encode('utf-8')).hexdigest()
             self._config.random_collectors = random.sample(self._config.collectors(), \
                                                            len(self._config.collectors()))
        if 'host_ip' in self._config._args:
            host_ip = self._config._args.host_ip
        else:
            host_ip = socket.gethostbyname(socket.getfqdn())
        self.uve = SnmpUve(self._config, host_ip)
        self._sandesh = self.uve.sandesh_instance()
        self._hostname = socket.getfqdn(host_ip)
        self._logger = self.uve.logger()
        self.sleep_time()
        self.last = set()
        self._sem = Semaphore()
        self._config.set_cb(self.notify)
        self._mnt = MaxNinTtime(3, self._sleep_time)
        self._state = 'full_scan' # replace it w/ fsm
        self._if_data = None # replace it w/ fsm
        self._cleanup = None
        self._members = None
        self._partitions = None
        self._prouters = {}

        zk_servers = self._config.zookeeper_server()
        self._config_handler = SnmpConfigHandler(self._sandesh,
            self._config.rabbitmq_params(), self._config.cassandra_params(),
            host_ip, zk_servers)
        self._consistent_scheduler = ConsistentScheduler(self._config._name,
            zookeeper=self._config.zookeeper_server(),
            delete_hndlr=self._del_uves, logger=self._logger,
            cluster_id=self._config.cluster_id())

    def _make_if_cdata(self, data):
        if_cdata = {}
        t = time.time()
        for dev in data:
            if 'snmp' in data[dev]:
                if 'ifMib' in data[dev]['snmp']:
                    if 'ifTable' in data[dev]['snmp']['ifMib']:
                        if_cdata[dev] = dict([(
                                x['ifIndex'], (x['ifOperStatus'], t)) for x in [x for x in data[dev][
                                               'snmp']['ifMib']['ifTable'] if 'ifOperStatus' in x\
                                            and 'ifDescr' in x]])
                elif 'ifOperStatus' in data[dev]['snmp']:
                    if_cdata[dev] = dict((k, (v, t)) for k, v in
                                    list(data[dev]['snmp']['ifOperStatus'].items()))
        return if_cdata

    def _delete_if_data(self, dev):
        if dev in self._if_data:
            del self._if_data[dev]

    def _set_status(self, _dict, dev, intf, val):
        if dev not in _dict:
            _dict[dev] = {}
        _dict[dev][intf] = val

    def _check_and_update_ttl(self, up2down):
        t = time.time()
        expry = 3 * self._fast_scan_freq
        for dev in self._if_data:
            for intf in self._if_data[dev]:
                if self._if_data[dev][intf][0] == 1:
                    if t - self._if_data[dev][intf][1] > expry:
                        self._set_status(up2down, dev, intf, 7) #no resp
                        self._if_data[dev][intf] = (7, t)

    def _get_if_changes(self, if_cdata):
        down2up, up2down, others = {}, {}, {}
        for dev in if_cdata:
            if dev in self._if_data:
                for intf in if_cdata[dev]:
                    if intf in self._if_data[dev]:
                        if if_cdata[dev][intf][0] != self._if_data[dev][
                                intf][0]:
                            if self._if_data[dev][intf][0] == 1:
                                self._set_status(up2down, dev, intf,
                                                 if_cdata[dev][intf][0])
                            elif if_cdata[dev][intf][0] == 1:
                                self._set_status(down2up, dev, intf,
                                                 if_cdata[dev][intf][0])
                            else:
                                self._set_status(others, dev, intf,
                                                 if_cdata[dev][intf][0])
                    self._if_data[dev][intf] = if_cdata[dev][intf]
            else:
                self._if_data[dev] = if_cdata[dev]
                for intf in self._if_data[dev]:
                    if self._if_data[dev][intf][0] == 1:
                        self._set_status(down2up, dev, intf,
                                         if_cdata[dev][intf][0])
                    else:
                        self._set_status(others, dev, intf,
                                         if_cdata[dev][intf][0])
        return down2up, up2down, others

    def _chk_if_change(self, data):
        if_cdata = self._make_if_cdata(data)
        down2up, up2down, others = self._get_if_changes(if_cdata)
        self._check_and_update_ttl(up2down)
        self._logger.debug('@chk_if_change: down2up(%s), up2down(%s), ' \
                'others(%s)' % (', '.join(list(down2up.keys())),
                                ', '.join(list(up2down.keys())),
                                ', '.join(list(others.keys()))))
        return down2up, up2down, others

    def _extra_call_params(self):
        if self._state != 'full_scan':
            return dict(restrict='ifOperStatus')
        return {}

    def _analyze(self, data):
        ret = True
        time = self._fast_scan_freq
        if self._state != 'full_scan':
            down2up, up2down, others = self._chk_if_change(data)
            if down2up:
                self._state = 'full_scan'
                time = self._mnt.add()
            elif self._mnt.ready4full_scan():
                self._state = 'full_scan'
                time = 0
            elif up2down:
                self.uve.send_ifstatus_update(self._if_data)
            ret = False
            sret = 'chngd: ' + self._state + ', time: ' + str(time)
        else:
            self._state = 'fast_scan'
            self._if_data = self._make_if_cdata(data)
            self.uve.send_ifstatus_update(self._if_data)
            sret = 'chngd: %d' % len(self._if_data)
        self._logger.debug('@do_work(analyze):State %s(%d)->%s!' % (
                    self._state, len(data), str(sret)))
        return ret, time

    def notify(self, svc, msg='', up=True, servers=''):
        self.uve.conn_state_notify(svc, msg, up, servers)

    def sleep_time(self, newtime=None):
        if newtime:
            self._sleep_time = newtime
        else:
            self._sleep_time = self._config.frequency()
        self._fast_scan_freq = self._config.fast_scan_freq()
        if self._fast_scan_freq > (self._sleep_time // 2):
            self._fast_scan_freq = (self._sleep_time // 2)
        return self._sleep_time

    def _setup_io(self):
        cdir = mkdtemp()
        input_file = os.path.join(cdir, 'in.data')
        output_file = os.path.join(cdir, 'out.data')
        return cdir, input_file, output_file

    def _create_input(self, input_file, output_file, devices, i, restrict=None):
        if isinstance(devices[0], DeviceDict):
            devices = DeviceConfig.populate_cfg(devices)
        with open(input_file, 'wb') as f:
            data = dict(out=output_file,
                        netdev=devices,
                        instance=i)
            if restrict:
                data['restrict'] = restrict
            pickle.dump(data, f)
            f.flush()

    def _run_scanner(self, input_file, output_file, i):
        proc = subprocess.Popen('tf-snmp-scanner --input %s' % (
                    input_file), shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True)
        self._cleanup = (proc, output_file)
        o,e = proc.communicate()
        self._cleanup = None
        self._logger.debug('@run_scanner(%d): scan done with %d\nstdout:' \
                '\n%s\nstderr:\n%s\n' % (i, proc.returncode, o, e))
        with open(output_file, 'rb') as f:
            d = pickle.load(f)
        self._logger.debug('@run_scanner(%d): loaded %s' % (i, output_file))
        return d

    def _cleanup_io(self, cdir, input_file, output_file):
        os.unlink(input_file)
        os.unlink(output_file)
        os.rmdir(cdir)

    def _send_uve(self, d):
        for dev, data in list(d.items()):
            if dev:
                self.uve.send(data['snmp'])
                self.uve.send_flow_uve({'name': dev,
                    'flow_export_source_ip': data['flow_export_source_ip']})
                self.find_fix_name(data['name'], dev)
        self._logger.debug('@send_uve:Processed %d!' % (len(d)))

    def _send_snmp_collector_uve(self, members, partitions, prouters):
        snmp_collector_info = SnmpCollectorInfo()
        if self._members != members:
            self._members = members
            snmp_collector_info.members = members
        if self._partitions != partitions:
            self._partitions = partitions
            snmp_collector_info.partitions = partitions
        new_prouters = {p.name: p for p in prouters}
        if list(self._prouters.keys()) != list(new_prouters.keys()):
            deleted_prouters = [v for p, v in self._prouters.items() \
                if p not in new_prouters]
            self._del_uves(deleted_prouters)
            self._prouters = new_prouters
            snmp_collector_info.prouters = list(self._prouters.keys())
        if snmp_collector_info != SnmpCollectorInfo():
            snmp_collector_info.name = self._hostname
            SnmpCollectorUVE(data=snmp_collector_info).send()
    # end _send_snmp_collector_uve

    def _del_uves(self, l):
        with self._sem:
            for dev in l:
                self._delete_if_data(dev.name)
                self.uve.delete(dev)

    def do_work(self, i, devices):
        self._logger.debug('@do_work(%d):started (%d)...' % (i, len(devices)))
        sleep_time = self._fast_scan_freq
        if devices:
            with self._sem:
                self._work_set = devices
                cdir, input_file, output_file = self._setup_io()
                self._create_input(input_file, output_file, devices,
                                   i, **self._extra_call_params())
                data = self._run_scanner(input_file, output_file, i)
                self._cleanup_io(cdir, input_file, output_file)
                do_send, sleep_time = self._analyze(data)
                if do_send:
                    self._send_uve(data)
                    gevent.sleep(0)
                del self._work_set
        self._logger.debug('@do_work(%d):Processed %d!' % (i, len(devices)))
        return sleep_time

    def find_fix_name(self, cfg_name, snmp_name):
        if snmp_name != cfg_name:
            self._logger.debug('@find_fix_name: snmp name %s differs from ' \
                    'configured name %s, fixed for this run' % (
                            snmp_name, cfg_name))
            for d in self._work_set:
                if d.name == cfg_name:
                    d.name = snmp_name
                    return

    def sighup_handler(self):
        if self._config._args.conf_file:
            config = configparser.ConfigParser(strict=False)
            config.read(self._config._args.conf_file)
            if 'DEFAULTS' in config.sections():
                try:
                    collectors = config.get('DEFAULTS', 'collectors')
                    if isinstance(collectors, str):
                        collectors = collectors.split()
                        new_chksum = hashlib.md5("".join(collectors)).encode('utf-8').hexdigest()
                        if new_chksum != self._chksum:
                            self._chksum = new_chksum
                            self._config.random_collectors = \
                                random.sample(collectors, len(collectors))
                        # Reconnect to achieve load-balance irrespective of list
                        self.uve.sandesh_reconfig_collectors(
                                self._config.random_collectors)
                except configparser.NoOptionError as e: 
                    pass
    # end sighup_handler  

    def _snmp_walker(self):
        i = 0
        while True:
            self._logger.debug('@run: ittr(%d)' % i)
            devices = [DeviceDict(e[0].split(':')[-1], e[1].obj) for e in self._config_handler.get_physical_routers()]
            if self._consistent_scheduler.schedule(devices):
                members = self._consistent_scheduler.members()
                partitions = self._consistent_scheduler.partitions()
                work_items = self._consistent_scheduler.work_items()
                self._send_snmp_collector_uve(members, partitions, work_items)
                sleep_time = self.do_work(i, work_items)
                self._logger.debug('done work %s' % str(list(self._prouters.keys())))
                i += 1
                gevent.sleep(sleep_time)
            else:
                gevent.sleep(1)
    # end _snmp_walker

    def run(self):
        """ @sighup
        SIGHUP handler to indicate configuration changes
        """
        gevent_signal(signal.SIGHUP, self.sighup_handler)

        self.gevs = [
            gevent.spawn(self._config_handler.start),
            gevent.spawn(self._snmp_walker)
        ]

        try:
            gevent.joinall(self.gevs)
        except KeyboardInterrupt:
            self._logger.error('Exiting on ^C')
        except gevent.GreenletExit:
            self._logger.error('Exiting on gevent-kill')
        finally:
            self._logger.error('stopping everything!')
            self.stop()
    # end run

    def stop(self):
        self.uve.killall()
        l = len(self.gevs)
        for i in range(0, l):
            self._logger.error('killing %d of %d' % (i+1, l))
            self.gevs[0].kill()
            self._logger.error('joining %d of %d' % (i+1, l))
            self.gevs[0].join()
            self._logger.error('stopped %d of %d' % (i+1, l))
            self.gevs.pop(0)
        self._consistent_scheduler.finish()
    # end stop
