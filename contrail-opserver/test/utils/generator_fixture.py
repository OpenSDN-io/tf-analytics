#!/usr/bin/python3

#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

#
# generator_fixture.py
#
# Python generator test fixtures
#

from gevent import monkey
monkey.patch_all()
import fixtures
import socket
import uuid
import time
from copy import deepcopy
from .util import retry
from pysandesh.sandesh_base import *
from pysandesh.gen_py.sandesh.constants import DEFAULT_SANDESH_SEND_RATELIMIT
from opserver.sandesh.alarmgen_ctrl.sandesh_alarm_base.ttypes import *
from sandesh.virtual_machine.ttypes import *
from sandesh.virtual_network.ttypes import *
from sandesh.flow.ttypes import *
from sandesh.alarm_test.ttypes import *
from sandesh.object_table_test.ttypes import *
from .analytics_fixture import AnalyticsFixture
from .generator_introspect_utils import VerificationGenerator
from .opserver_introspect_utils import VerificationOpsSrv

class GeneratorFixture(fixtures.Fixture):
    _BYTES_PER_PACKET = 1024
    _PKTS_PER_SEC = 100
    _INITIAL_PKT_COUNT = 20
    _VM_IF_PREFIX = 'vhost'
    _KSECINMSEC = 1000 * 1000
    _VN_PREFIX = 'default-domain:vn'
    _SENDQ_WATERMARKS = [
        # (size, sandesh_level, is_high_watermark)
        (150*1024*1024, SandeshLevel.SYS_EMERG, True),
        (100*1024*1024, SandeshLevel.SYS_ERR, True),
        (50*1024*1024, SandeshLevel.SYS_DEBUG, True),
        (125*1024*1024, SandeshLevel.SYS_ERR, False),
        (75*1024*1024, SandeshLevel.SYS_DEBUG, False),
        (25*1024*1024, SandeshLevel.INVALID, False)]

    def __init__(self, name, collectors, logger, opserver_port,
                 start_time=None, node_type="Test",
                 hostname=socket.getfqdn("127.0.0.1"), inst = "0",
                 sandesh_config=None):
        self._hostname = hostname
        self._name = name
        self._logger = logger
        self._collectors = collectors
        self._opserver_port = opserver_port
        self._start_time = start_time
        self._node_type = node_type
        self._inst = inst
        self._generator_id = self._hostname+':'+self._node_type+':'+self._name+':' + self._inst
        if sandesh_config:
            self._sandesh_config = SandeshConfig(
                keyfile = sandesh_config.get('sandesh_keyfile'),
                certfile = sandesh_config.get('sandesh_certfile'),
                server_keyfile = sandesh_config.get('sandesh_server_keyfile'),
                server_certfile = sandesh_config.get('sandesh_server_certfile'),
                ca_cert = sandesh_config.get('sandesh_ca_cert'),
                sandesh_ssl_enable = sandesh_config.get('sandesh_ssl_enable', False),
                introspect_ssl_enable = sandesh_config.get('introspect_ssl_enable', False),
                introspect_ssl_insecure = sandesh_config.get('introspect_ssl_insecure', False),
                disable_object_logs = sandesh_config.get('disable_object_logs', False),
                system_logs_rate_limit = sandesh_config.get('system_logs_rate_limit', DEFAULT_SANDESH_SEND_RATELIMIT))
        else:
            self._sandesh_config = None
        self.flow_vmi_uuid = str(uuid.uuid1())
    # end __init__

    def setUp(self):
        super(GeneratorFixture, self).setUp()
        self._sandesh_instance = Sandesh()
        self._http_port = AnalyticsFixture.get_free_port()
        sandesh_pkg = ['opserver.sandesh.alarmgen_ctrl.sandesh_alarm_base',
                       'sandesh']
        self._sandesh_instance.init_generator(
            self._name, self._hostname, self._node_type, self._inst,
            self._collectors, '', self._http_port,
            sandesh_req_uve_pkg_list=sandesh_pkg, config=self._sandesh_config)
        self._sandesh_instance.set_logging_params(enable_local_log=True,
                                                  level=SandeshLevel.UT_DEBUG)
    # end setUp

    def cleanUp(self):
        self._sandesh_instance._client._connection.set_admin_state(down=True)
        super(GeneratorFixture, self).cleanUp()
    # end tearDown

    def get_generator_id(self):
        return self._generator_id
    # end get_generator_id

    def connect_to_collector(self):
        self._sandesh_instance._client._connection.set_admin_state(down=False)
    # end connect_to_collector

    def disconnect_from_collector(self):
        self._sandesh_instance._client._connection.set_admin_state(down=True)
    # end disconnect_from_collector

    def set_sandesh_send_queue_watermarks(self):
        self._sandesh_instance.client().connection().session().\
            set_send_queue_watermarks(GeneratorFixture._SENDQ_WATERMARKS)

    def set_sandesh_config(self, sandesh_config):
        if sandesh_config:
            self._sandesh_config = SandeshConfig(
                keyfile = sandesh_config.get('sandesh_keyfile'),
                certfile = sandesh_config.get('sandesh_certfile'),
                server_keyfile = sandesh_config.get('sandesh_server_keyfile'),
                server_certfile = sandesh_config.get('sandesh_server_certfile'),
                ca_cert = sandesh_config.get('sandesh_ca_cert'),
                sandesh_ssl_enable = \
                        sandesh_config.get('sandesh_ssl_enable', False),
                introspect_ssl_enable = \
                        sandesh_config.get('introspect_ssl_enable', False),
                introspect_ssl_insecure = \
                        sandesh_config.get('introspect_ssl_insecure', False),
                disable_object_logs = \
                        sandesh_config.get('disable_object_logs', False),
                system_logs_rate_limit = \
                        sandesh_config.get('system_logs_rate_limit', \
                        DEFAULT_SANDESH_SEND_RATELIMIT))
        else:
            self._sandesh_config = None
    # end set_sandesh_send_queue_watermarks
    @retry(delay=2, tries=5)
    def verify_on_setup(self):
        try:
            vg = VerificationGenerator(socket.getfqdn("127.0.0.1"), self._http_port, \
                            self._sandesh_config)
            conn_status = vg.get_collector_connection_status()
        except:
            return False
        else:
            return conn_status['status'] == "Established"
    # end verify_on_setup

    def generate_session_samples(self):
        self.flow_cnt = 3
        self.forward_flows = []
        self.reverse_flows = []
        self.client_sessions = []
        self.server_sessions = []
        self.client_session_cnt = self.flow_cnt
        self.server_session_cnt = self.flow_cnt
        self.session_start_time = self._start_time
        self.session_end_time = self._start_time + 40*self._KSECINMSEC
        self.client_vmi = 'domain1:' + str(uuid.uuid1())
        self.server_vmi = 'domain1:' + str(uuid.uuid1())

        for i in range(self.flow_cnt*self.flow_cnt):
            self.forward_flows.append(SessionFlowInfo(flow_uuid=uuid.uuid1(),
                sampled_bytes=((i // 3)+1)*20,
                sampled_pkts=((i // 3)+1)*2,
                action='pass',
                sg_rule_uuid=uuid.uuid1(),
                nw_ace_uuid=uuid.uuid1(),
                underlay_source_port=(i // 3)))
            self.reverse_flows.append(SessionFlowInfo(flow_uuid=uuid.uuid1(),
                sampled_bytes=((i // 3)+1)*10,
                sampled_pkts=(i // 3)+1,
                action='pass',
                sg_rule_uuid=uuid.uuid1(),
                nw_ace_uuid=uuid.uuid1(),
                underlay_source_port=(10 + (i // 3))))

        for i in range(self.client_session_cnt):
            session_agg_info = {}
            cnt = 0
            for j in range(self.flow_cnt):
                session_map = {}
                for k in range(self.flow_cnt):
                    sess_ip_port = SessionIpPort(
                        ip=netaddr.IPAddress('2001:db8::1:2'),
                        port=cnt*10+32747)
                    session_map[sess_ip_port] = SessionInfo(
                        forward_flow_info=deepcopy(self.forward_flows[cnt]),
                        reverse_flow_info=deepcopy(self.reverse_flows[cnt]))
                    if (i == self.client_session_cnt - 1):
                        session_map[sess_ip_port].forward_flow_info.action \
                                = 'drop'
                        session_map[sess_ip_port].forward_flow_info.teardown_bytes \
                                = 3*(((i // 3)+1)*20)
                        session_map[sess_ip_port].forward_flow_info.teardown_pkts \
                                = 3*(((i // 3)+1)*2)
                        session_map[sess_ip_port].reverse_flow_info.action \
                                = 'drop'
                        session_map[sess_ip_port].reverse_flow_info.teardown_bytes \
                                = 3*(((i // 3)+1)*10)
                        session_map[sess_ip_port].reverse_flow_info.teardown_pkts \
                                = 3*(((i // 3)+1)*1)
                    cnt += 1
                sess_ip_port_proto = SessionIpPortProtocol(
                    local_ip=netaddr.IPAddress('10.10.10.1'),
                    service_port=j+100, protocol=(j // 2))
                session_agg_info[sess_ip_port_proto] = SessionAggInfo(
                    sampled_forward_bytes = (j+1)*60,
                    sampled_forward_pkts = (j+1)*6,
                    sampled_reverse_bytes = (j+1)*30,
                    sampled_reverse_pkts = (j+1)*3,
                    sessionMap = deepcopy(session_map))
            client_session = SessionEndpoint(vmi = self.client_vmi,
                vn='domain1:admin:vn1', deployment='Dep'+str(i),
                application="App"+str(i), tier='Tier'+str(i),
                site='Site'+str(i), remote_deployment='RDep'+str(i),
                remote_application='RApp'+str(i), remote_tier='RTier'+str(i),
                remote_site='RSite'+str(i), remote_vn='domain1:admin:vn2',
                labels=['Label1'+str(i), 'Label2'+str(i)],
                remote_labels=['Label1'+str(i)],
                custom_tags=['custom_tag1=ct1'+str(i)],
                remote_custom_tags=['custom_tag1=ct1'+str(i), 'custom_tag2=ct2'+str(i)],
                is_client_session = 1, is_si = 0, vrouter_ip=netaddr.IPAddress('10.0.0.1'),
                sess_agg_info = deepcopy(session_agg_info))
            self._logger.info(str(client_session))
            session_object = SessionEndpointObject(session_data=[client_session],
                sandesh=self._sandesh_instance)
            session_object._timestamp = self.session_start_time + \
                i*10*self._KSECINMSEC
            session_object.send(sandesh=self._sandesh_instance)
            self.client_sessions.append(session_object)

        for i in range(self.server_session_cnt):
            session_agg_info = {}
            cnt = 0
            for j in range(self.flow_cnt):
                session_map = {}
                for k in range(self.flow_cnt):
                    sess_ip_port = SessionIpPort(
                        ip=netaddr.IPAddress('10.10.10.1'),
                        port=cnt*10+32747)
                    session_map[sess_ip_port] = SessionInfo(
                        forward_flow_info=deepcopy(self.forward_flows[cnt]),
                        reverse_flow_info=deepcopy(self.reverse_flows[cnt]))
                    if (i == self.server_session_cnt - 1):
                        session_map[sess_ip_port].forward_flow_info.action \
                                = 'drop'
                        session_map[sess_ip_port].forward_flow_info.teardown_bytes \
                                = 3*(((i // 3)+1)*20)
                        session_map[sess_ip_port].forward_flow_info.teardown_pkts \
                                = 3*(((i // 3)+1)*2)
                        session_map[sess_ip_port].reverse_flow_info.action \
                                = 'drop'
                        session_map[sess_ip_port].reverse_flow_info.teardown_bytes \
                                = 3*(((i // 3)+1)*10)
                        session_map[sess_ip_port].reverse_flow_info.teardown_pkts \
                                = 3*(((i // 3)+1)*1)
                    cnt += 1
                sess_ip_port_proto = SessionIpPortProtocol(
                    local_ip=netaddr.IPAddress('2001:db8::1:2'),
                    service_port=j+100, protocol=(j // 2))
                session_agg_info[sess_ip_port_proto] = SessionAggInfo(
                    sampled_forward_bytes = (j+1)*60,
                    sampled_forward_pkts = (j+1)*6,
                    sampled_reverse_bytes = (j+1)*30,
                    sampled_reverse_pkts = (j+1)*3,
                    sessionMap = deepcopy(session_map))
            server_session = SessionEndpoint(vmi = self.server_vmi,
                vn='domain1:admin:vn2', deployment='Dep'+str(i),
                application="App"+str(i), tier='Tier'+str(i),
                site='Site'+str(i), remote_deployment='RDep'+str(i),
                remote_application='RApp'+str(i), remote_tier='RTier'+str(i),
                remote_site='RSite'+str(i), remote_vn='domain1:admin:vn1',
                labels=['Label1'+str(i), 'Label2'+str(i)],
                remote_labels=['Label1'+str(i)],
                custom_tags=['custom_tag1=ct1'+str(i)],
                remote_custom_tags=['custom_tag1=ct1'+str(i), 'custom_tag2=ct2'+str(i)],
                is_client_session = 0, is_si = 0, vrouter_ip=netaddr.IPAddress('10.0.0.1'),
                sess_agg_info = deepcopy(session_agg_info))
            self._logger.info(str(server_session))
            session_object = SessionEndpointObject(session_data=[server_session],
                sandesh=self._sandesh_instance)
            session_object._timestamp = self.session_start_time + \
                i*10*self._KSECINMSEC
            session_object.send(sandesh=self._sandesh_instance)
            self.server_sessions.append(session_object)

    def send_vn_uve(self, vrouter, vn_id, num_vns):
        intervn_list = []
        for num in range(num_vns):
            intervn = InterVnStats()
            intervn.other_vn = self._VN_PREFIX + str(num)
            intervn.vrouter = vrouter
            intervn.in_tpkts = num
            intervn.in_bytes = num * self._BYTES_PER_PACKET
            intervn.out_tpkts = num
            intervn.out_bytes = num * self._BYTES_PER_PACKET
            intervn_list.append(intervn)
        vn_agent = UveVirtualNetworkAgent(vn_stats=intervn_list)
        vn_agent.name = self._VN_PREFIX + str(vn_id)
        uve_agent_vn = UveVirtualNetworkAgentTrace(
            data=vn_agent,
            sandesh=self._sandesh_instance)
        uve_agent_vn.send(sandesh=self._sandesh_instance)
        self._logger.info(
                'Sent UveVirtualNetworkAgentTrace:%s .. %d .. size %d' % (vn_id, num, len(vn_agent.vn_stats)))

    def generate_intervn(self):
        self.send_vn_uve(socket.getfqdn("127.0.0.1"), 0, 2)
        time.sleep(1)
        self.send_vn_uve(socket.getfqdn("127.0.0.1"), 1, 3)
        time.sleep(1)
        self.send_vn_uve(socket.getfqdn("127.0.0.1"), 0, 3)
        time.sleep(1)

        self.vn_all_rows = {}
        self.vn_all_rows['whereclause'] = 'vn_stats.vrouter=' + socket.getfqdn("127.0.0.1")
        self.vn_all_rows['rows'] = 8

        self.vn_sum_rows = {}
        self.vn_sum_rows['select'] = ['name','COUNT(vn_stats)','SUM(vn_stats.in_tpkts)']
        self.vn_sum_rows['whereclause'] = 'vn_stats.other_vn=' + self._VN_PREFIX + str(1) 
        self.vn_sum_rows['rows'] = 2

    def send_vm_uve(self, vm_id, num_vm_ifs, msg_count):
        vm_if_list = []
        vm_if_stats_list = []
        for num in range(num_vm_ifs):
            vm_if = VmInterfaceAgent()
            vm_if.name = self._VM_IF_PREFIX + str(num)
            vm_if_list.append(vm_if)

        for num in range(msg_count):
            vm_agent = UveVirtualMachineAgent(interface_list=vm_if_list)
            vm_agent.name = vm_id
            uve_agent_vm = UveVirtualMachineAgentTrace(
                data=vm_agent,
                sandesh=self._sandesh_instance)
            uve_agent_vm.send(sandesh=self._sandesh_instance)
            self._logger.info(
                'Sent UveVirtualMachineAgentTrace:%s .. %d' % (vm_id, num))
    # end send_uve_vm

    def delete_vm_uve(self, vm_id):
        vm_agent = UveVirtualMachineAgent(name=vm_id, deleted=True)
        uve_agent_vm = UveVirtualMachineAgentTrace(data=vm_agent, 
                            sandesh=self._sandesh_instance)
        uve_agent_vm.send(sandesh=self._sandesh_instance)
        self._logger.info('Delete VM UVE: %s' % (vm_id))
    # end delete_vm_uve

    def find_vm_entry(self, vm_uves, vm_id):
        if vm_uves is None:
            return False
        if type(vm_uves) is not list:
            vm_uves = [vm_uves]
        for uve in vm_uves:
            if uve['name'] == vm_id:
                return uve
        return None
    # end find_vm_entry

    @retry(delay=2, tries=5)
    def verify_vm_uve(self, vm_id, num_vm_ifs, msg_count, opserver_port=None):
        if opserver_port is not None:
            vns = VerificationOpsSrv(socket.getfqdn("127.0.0.1"), opserver_port, sandesh_config=self._sandesh_config)
        else:
            vns = VerificationOpsSrv(socket.getfqdn("127.0.0.1"), self._opserver_port, sandesh_config=self._sandesh_config)
        res = vns.get_ops_vm(vm_id)
        if res == {}:
            return False
        else:
            assert(len(res) > 0)
            self._logger.info(str(res))
            anum_vm_ifs = len(res.get_attr('Agent', 'interface_list'))
            assert anum_vm_ifs == num_vm_ifs
            for i in range(num_vm_ifs):
                vm_if_dict = res.get_attr('Agent', 'interface_list')[i]
                evm_if_name = self._VM_IF_PREFIX + str(i)
                avm_if_name = vm_if_dict['name']
                assert avm_if_name == evm_if_name
            return True
    # end verify_uve_vm

    @retry(delay=2, tries=5)
    def verify_vm_uve_cache(self, vm_id, delete=False):
        try:
            vg = VerificationGenerator(socket.getfqdn("127.0.0.1"), self._http_port)
            vm_uves = vg.get_uve('UveVirtualMachineAgent')
        except Exception as e:
            self._logger.info('Failed to get vm uves: %s' % (e))
            return False
        else:
            if vm_uves is None:
                self._logger.info('vm uve list empty')
                return False
            self._logger.info('%s' % (str(vm_uves)))
            vm_uve = self.find_vm_entry(vm_uves, vm_id)
            if vm_uve is None:
                return False
            if delete is True:
                try:
                    return vm_uve['deleted'] \
                                    == 'true'
                except:
                    return False
            else:
                try:
                    return vm_uve['deleted'] \
                                   == 'false'
                except:
                    return True
        return False
    # end verify_vm_uve_cache

    def send_vn_agent_uve(self, name, num_acl_rules=None, if_list=None,
                          ipkts=None, ibytes=None, opkts=None, obytes=None,
                          vm_list=None, vn_stats=None):
        vn_agent = UveVirtualNetworkAgent(name=name,
                    total_acl_rules=num_acl_rules, interface_list=if_list,
                    in_tpkts=ipkts, in_bytes=ibytes, out_tpkts=opkts,
                    out_bytes=obytes, virtualmachine_list=vm_list,
                    vn_stats=vn_stats)
        vn_uve = UveVirtualNetworkAgentTrace(data=vn_agent,
                    sandesh=self._sandesh_instance)
        self._logger.info('send uve: %s' % (vn_uve.log()))
        vn_uve.send(sandesh=self._sandesh_instance)
    # end send_vn_agent_uve

    def send_vn_config_uve(self, name, conn_nw=None, partial_conn_nw=None,
                           ri_list=None, num_acl_rules=None):
        vn_config = UveVirtualNetworkConfig(name=name,
                        connected_networks=conn_nw,
                        partially_connected_networks=partial_conn_nw,
                        routing_instance_list=ri_list,
                        total_acl_rules=num_acl_rules)
        vn_uve = UveVirtualNetworkConfigTrace(data=vn_config,
                    sandesh=self._sandesh_instance)
        self._logger.info('send uve: %s' % (vn_uve.log()))
        vn_uve.send(sandesh=self._sandesh_instance)
    # end send_vn_config_uve

    def send_vrouterinfo(self, name, b_info = False, deleted = False,
                         non_ascii = False):
        vinfo = None

        if deleted:
            vinfo = VrouterAgent(name=name,
                                 deleted = True)
        else:
            if b_info:
                build_info="testinfo"
                if non_ascii:
                    build_info += ' ' + chr(201) + chr(203) + chr(213) + ' ' + build_info
                # Python3 doesn't support unicode
                try:
                    if isinstance(build_info, unicode):
                        build_info = build_info.encode('utf-8')
                except:
                    build_info = str(build_info)
                vinfo = VrouterAgent(name=name,
                                     build_info=build_info,
                                     state="OK")
            else:
                vinfo = VrouterAgent(name=name, state="OK")
        v_uve = VrouterAgentTest(data=vinfo,
                    sandesh=self._sandesh_instance)
        self._logger.info('send uve: %s' % (v_uve.log()))
        v_uve.send(sandesh=self._sandesh_instance)

    def create_alarm(self, type, name=None, ack=None):
        alarms = []
        alarm_rules=None
        if name:
            alarm_rules = AlarmRules(or_list=[
                AlarmAndList(and_list=[
                    AlarmConditionMatch(condition=AlarmCondition(
                        operation='!=', operand1="test",
                        operand2=AlarmOperand2(json_value="\"UP\"")),
                        match=[AlarmMatch(json_operand1_value="\"DOWN\"")])])])
        alarms.append(UVEAlarmInfo(type=type, ack=ack,
            alarm_rules=alarm_rules))
        return alarms
    # end create_alarm

    def create_process_state_alarm(self, process):
        return self.create_alarm('ProcessStatus', process)
    # end create_process_state_alarm

    def send_alarm(self, name, alarms, table):
        alarm_data = UVEAlarms(name=name, alarms=alarms)
        alarm = AlarmTrace(data=alarm_data, table=table,
                           sandesh=self._sandesh_instance)
        self._logger.info('send alarm: %s' % (alarm.log()))
        alarm.send(sandesh=self._sandesh_instance)
        # store the alarm for verification
        if not hasattr(self, 'alarms'):
            self.alarms = {}
        if self.alarms.get(table) is None:
            self.alarms[table] = {}
        self.alarms[table][name] = alarm
    # end send_alarm

    def delete_alarm(self, name, table):
        alarm_data = UVEAlarms(name=name, deleted=True)
        alarm = AlarmTrace(data=alarm_data, table=table,
                           sandesh=self._sandesh_instance)
        self._logger.info('delete alarm: %s' % (alarm.log()))
        alarm.send(sandesh=self._sandesh_instance)
        del self.alarms[table][name]
    # end delete_alarm

    def send_sandesh_types_object_logs(self, name,
            types=[SandeshType.SYSTEM, SandeshType.OBJECT, SandeshType.UVE,
                SandeshType.ALARM]):
        # send all sandesh types that should be returned in the Object query.
        msg_types = []
        for stype in types:
            if stype == SandeshType.SYSTEM:
                systemlog = ObjectTableSystemLogTest(name=name,
                        sandesh=self._sandesh_instance)
                msg_types.append(systemlog.__class__.__name__)
                self._logger.info('send systemlog: %s' % (systemlog.log()))
                systemlog.send(sandesh=self._sandesh_instance)
            if stype == SandeshType.OBJECT:
                objlog = ObjectTableObjectLogTest(name=name,
                    sandesh=self._sandesh_instance)
                msg_types.append(objlog.__class__.__name__)
                self._logger.info('send objectlog: %s' % (objlog.log()))
                objlog.send(sandesh=self._sandesh_instance)
            if stype == SandeshType.UVE:
                uve_data = ObjectTableUveData(name=name)
                uve = ObjectTableUveTest(data=uve_data,
                    sandesh=self._sandesh_instance)
                msg_types.append(uve.__class__.__name__)
                self._logger.info('send uve: %s' % (uve.log()))
                uve.send(sandesh=self._sandesh_instance)
            if stype == SandeshType.ALARM:
                alarm_data = UVEAlarms(name=name)
                alarm = AlarmTrace(data=alarm_data,
                           table='ObjectBgpRouter',
                           sandesh=self._sandesh_instance)
                msg_types.append(alarm.__class__.__name__)
                self._logger.info('send alarm: %s' % (alarm.log()))
                alarm.send(sandesh=self._sandesh_instance)
        return msg_types
    # end send_sandesh_types_object_logs

# end class GeneratorFixture
